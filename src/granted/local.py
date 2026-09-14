"""Funders active near you.

Much of the money that reaches a small charity is local: its community
foundation, its council, the regional programmes of the lottery distributors.
Their open calls are spread across a hundred websites, so this does not pretend
to track them. What it can do is read their award histories, which 360Giving
publishes, and say which of them actually fund organisations near you, how
often, and at what size.

It is a list worth a look, not a decision. Every funder on it has made awards
near you in the sample of its history read here, and each links to its website
and its record on GrantNav. The 360Giving API returns awards in no particular
order, so a sample is what it is: not necessarily the latest. Built once a week,
because award histories do not change daily and reading fifty of them is slow.
"""

from __future__ import annotations

import json
import os
import re
import statistics
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
from typing import Callable

import requests

from .location import Place
from .precedent import Client, Grant, ThreeSixtyGivingError

REGISTRY = "https://registry.threesixtygiving.org/data.json"
GRANTNAV = "https://grantnav.threesixtygiving.org/org/{org_id}"
MAX_AGE_DAYS = 7
SAMPLE = 100          # awards read per funder: a sample, in the API's own order
SHOW = 8

# Publishers worth reading for local money. Named so, rather than all 295,
# because a national research funder's history says nothing about your borough.
CANDIDATE = re.compile(r"community foundation|foundation for|council|lottery|sport england|"
                       r"arts council|heritage fund|voluntary", re.I)


def publishers(session: requests.Session | None = None) -> list[dict]:
    """360Giving's publisher registry, one entry per funder."""
    r = (session or requests.Session()).get(REGISTRY, timeout=60)
    r.raise_for_status()
    seen: dict[str, dict] = {}
    for dataset in r.json():
        p = dataset.get("publisher") or {}
        if p.get("org_id") and p["org_id"] not in seen:
            seen[p["org_id"]] = {"org_id": p["org_id"], "name": p.get("name") or p["org_id"],
                                 "website": p.get("website") or None}
    return list(seen.values())


def _recent(org_id: str) -> list[Grant]:
    client = Client()
    return [Grant.from_api(row) for row in client.grants_made(org_id, limit=SAMPLE, cap=SAMPLE)]


def near(place: Place, funders: list[dict],
         fetch: Callable[[str], list[Grant]] = _recent) -> list[dict]:
    """Candidate funders that have made awards near `place`, best first."""
    found = []
    candidates = [f for f in funders if CANDIDATE.search(f["name"])]

    def read(f: dict):
        try:
            return f, fetch(f["org_id"])
        except (ThreeSixtyGivingError, requests.RequestException):
            return f, None

    # A few at once: the 360Giving client shares one rate limiter across
    # threads, so slow responses overlap without breaking two requests a second.
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(read, candidates))
    for f, grants in results:
        if not grants:
            continue
        local = [g for g in grants if place.contains(g.region) == "local"]
        regional = [g for g in grants if place.contains(g.region) == "regional"]
        # Regional matches count only when they are a real share of the funder's
        # awards: five London awards from an Essex foundation are strays.
        if len(local) < 2 and (len(regional) < 5 or len(regional) / len(grants) < 0.10):
            continue
        nearby = local or regional
        amounts = [g.amount_awarded for g in nearby if g.amount_awarded]
        dates = [g.award_date[:10] for g in nearby if g.award_date]
        found.append({
            "name": f["name"], "org_id": f["org_id"], "website": f.get("website"),
            "grantnav": GRANTNAV.format(org_id=f["org_id"]),
            "awards_near_you": len(local), "awards_in_region": len(regional),
            "sample": len(grants),
            "median_award": statistics.median(amounts) if amounts else None,
            "latest_in_sample": max(dates) if dates else None,
        })
    found.sort(key=lambda r: (-r["awards_near_you"], -r["awards_in_region"]))
    return found[:SHOW]


def refresh(place: Place | None, cache: Path, today: date | None = None,
            fetch: Callable[[str], list[Grant]] = _recent,
            session: requests.Session | None = None) -> list[dict]:
    """The weekly list, rebuilt when it is older than a week or the place changed.

    Never raises: a registry or API failure keeps last week's list, or none.
    `GRANTED_OFFLINE` keeps it off the network.
    """
    if place is None:
        return []
    today = today or date.today()
    key = f"{place.country}|{place.region}|{place.county}|{place.district}"
    cached = None
    if cache.is_file():
        try:
            cached = json.loads(cache.read_text(encoding="utf-8"))
        except ValueError:
            cached = None
    if cached and cached.get("place") == key:
        age = (today - date.fromisoformat(cached.get("built", "1970-01-01"))).days
        if age < MAX_AGE_DAYS:
            return cached.get("funders", [])
    if fetch is _recent and session is None and os.environ.get("GRANTED_OFFLINE"):
        return (cached or {}).get("funders", []) if cached and cached.get("place") == key else []
    try:
        funders = near(place, publishers(session), fetch)
    except (requests.RequestException, ValueError):
        return (cached or {}).get("funders", []) if cached and cached.get("place") == key else []
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps({"built": today.isoformat(), "place": key, "funders": funders},
                                indent=2, ensure_ascii=False), encoding="utf-8")
    return funders
