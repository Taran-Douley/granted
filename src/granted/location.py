"""Where the organisation is, in the terms funders use.

ORG.md says where an organisation works in its own words ("Peckham and Nunhead
wards, Southwark, London"). Funding calls say it in theirs: a nation, an English
region, sometimes a county. This turns one into the other through postcodes.io,
a free public service built on ONS and Ordnance Survey data, and keeps the answer
so it is looked up once rather than every morning.

A postcode is the reliable input. Without one, the "Area served" line is read
from its widest place inwards, so "Easton, ... Bristol" finds the Easton in
Bristol and not one of the other Eastons in England.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import quote

import requests

API = "https://api.postcodes.io"
TIMEOUT = 20

# ONS region names, as postcodes.io returns them, against the labels funding
# listings use. Find a grant says "Midlands" and "South West England".
_REGION_LABEL = {
    "North East": "North East England",
    "North West": "North West England",
    "South East": "South East England",
    "South West": "South West England",
    "East Midlands": "Midlands",
    "West Midlands": "Midlands",
    "Yorkshire and The Humber": "Yorkshire and the Humber",
    "East of England": "East of England",
    "London": "London",
}

# Counties that are really a whole region: a match on them is regional, not local.
_REGION_WIDE = {"greater london"}

_NOISE = re.compile(r"\b(wards?|local authority|borough|district|council|area)\b", re.I)
_CITY_OF = re.compile(r"^(?:city of |london borough of |royal borough of )|, city of$", re.I)


def _clean(name: str | None) -> str:
    return _CITY_OF.sub("", (name or "").strip()).strip()


@dataclass
class Place:
    country: str | None = None
    region: str | None = None
    county: str | None = None
    district: str | None = None
    source: str = ""

    def describe(self) -> str:
        where = [p for p in (self.district, self.region) if p]
        if len(where) == 2 and where[0].lower() == where[1].lower():
            where = where[:1]
        text = ", ".join(where) or (self.country or "an unknown place")
        return f"{text} ({self.country})" if self.country and self.country not in text else text

    def funding_labels(self) -> set[str]:
        """Every label a funding listing might use for somewhere this place is in."""
        labels: set[str] = set()
        if self.country:
            labels.add(self.country.lower())
            if self.country in ("England", "Scotland", "Wales"):
                labels.update({"great britain", "gb"})
        if self.region:
            labels.update({self.region.lower(), _REGION_LABEL.get(self.region, self.region).lower()})
        for name in (self.county, self.district):
            if name:
                labels.add(_clean(name).lower())
        return labels

    def contains(self, name: str | None) -> str | None:
        """'local' if a place name is this district or county, 'regional' if it is
        this region, else None. Used to count a funder's awards near you."""
        n = _clean(name).lower()
        if not n:
            return None
        local = {_clean(x).lower() for x in (self.district, self.county)
                 if x and _clean(x).lower() not in _REGION_WIDE}
        if any(x and (x == n or x in n or n in x) for x in local):
            return "local"
        regional = {x.lower() for x in (self.region, self.county) if x}
        if any(x == n or x in n for x in regional):
            return "regional"
        return None


def _get(session: requests.Session, path: str) -> dict | None:
    r = session.get(f"{API}{path}", timeout=TIMEOUT)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.json().get("result")


def _from_postcode(session: requests.Session, postcode: str) -> Place | None:
    pc = postcode.strip().upper()
    result = _get(session, f"/postcodes/{quote(pc)}")
    if result:
        return Place(result.get("country"), result.get("region"), result.get("admin_county"),
                     _clean(result.get("admin_district")), f"postcode {pc}")
    # Only the first half ("SE15")? That names a postcode district, which can
    # straddle boroughs; take the first and let the place lookup supply a region.
    compact = pc.replace(" ", "")
    if len(compact) <= 4:
        result = _get(session, f"/outcodes/{quote(compact)}")
        if result and result.get("admin_district"):
            district = result["admin_district"][0]
            place = _match_place(session, district)
            if place:
                place.source = f"postcode district {compact}"
                return place
            return Place((result.get("country") or [None])[0], None, None, district,
                         f"postcode district {compact}")
    return None


def _match_place(session: requests.Session, name: str, region: str | None = None,
                 county: str | None = None) -> Place | None:
    results = _get(session, f"/places?q={quote(name)}&limit=10") or []
    for r in results:
        if (r.get("name_1") or "").lower() != name.lower():
            continue
        if region and r.get("region") != region:
            continue
        # Two Eastons can share a region (Bristol's and Devon's); the county of
        # the wider place named alongside it settles which one is meant.
        if county and r.get("county_unitary") != county:
            continue
        district = r.get("district_borough")
        if not district and r.get("local_type") in ("City", "Town"):
            district = r.get("name_1")
        return Place(r.get("country"), r.get("region"), r.get("county_unitary"),
                     _clean(district), f"place {name}")
    return None


def _from_area(session: requests.Session, area: str) -> Place | None:
    """Widest place first, to learn the region; then the most specific place
    that sits inside that region."""
    parts = [p.strip() for p in _NOISE.sub(" ", area).split(",")]
    names = [" ".join(p.split()) for part in parts for p in re.split(r"\band\b", part) if p.strip()]
    if not names:
        return None
    coarse = None
    for name in reversed(names):
        coarse = _match_place(session, name)
        if coarse:
            break
    if not coarse:
        return None
    for name in names:
        fine = _match_place(session, name, region=coarse.region, county=coarse.county)
        if fine and fine.district:
            return fine
    return coarse


def resolve(profile, cache: Path | None = None,
            session: requests.Session | None = None) -> Place | None:
    """The organisation's place, or None when it cannot be worked out.

    Never raises: without a place the location checks simply do not fire, and
    the digest says so. `GRANTED_OFFLINE` stops it reaching the network.
    """
    query = getattr(profile, "postcode", None) or getattr(profile, "area_served", None) \
        or getattr(profile, "region", None)
    if not query:
        return None
    if cache and cache.is_file():
        try:
            data = json.loads(cache.read_text(encoding="utf-8"))
            if data.get("query") == query:
                return Place(**data["place"])
        except (ValueError, KeyError, TypeError):
            pass
    # Offline stops real lookups; a session handed in (a test's stand-in) is used.
    if session is None and os.environ.get("GRANTED_OFFLINE"):
        return None
    session = session or requests.Session()
    try:
        place = None
        if getattr(profile, "postcode", None):
            place = _from_postcode(session, profile.postcode)
        for text in (getattr(profile, "area_served", None), getattr(profile, "region", None)):
            if place is None and text:
                place = _from_area(session, text)
    except (requests.RequestException, ValueError):
        return None
    if place and cache:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps({"query": query, "place": asdict(place)}, indent=2),
                         encoding="utf-8")
    return place
