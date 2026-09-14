"""360Giving API client and revealed-preference analysis.

Data source: https://api.threesixtygiving.org/api/v1/
No authentication. Rate limit 2 req/sec. Data is CC-BY-SA, attribution required.
Updated daily overnight UK time; do not cache beyond one day.
"""

from __future__ import annotations

import re
import statistics
import time
from collections import Counter
from dataclasses import dataclass, field
import threading
from typing import Any, Iterator

import requests

API_ROOT = "https://api.threesixtygiving.org/api/v1"
HEADERS = {"Accept": "application/json"}
MIN_INTERVAL = 0.55  # stay under the documented 2 req/sec limit
# One limiter for every client in the process, so funders fetched in parallel
# still keep to 360Giving's two requests a second between them.
_RATE_LOCK = threading.Lock()
_LAST_CALL = [0.0]


class ThreeSixtyGivingError(RuntimeError):
    pass


class Client:
    """Thin paginating client. Respects the documented rate limit."""

    def __init__(self, session: requests.Session | None = None) -> None:
        self.session = session or requests.Session()
        self._last_call = 0.0

    def _get(self, url: str, params: dict | None = None, retries: int = 1) -> dict:
        with _RATE_LOCK:
            wait = MIN_INTERVAL - (time.monotonic() - _LAST_CALL[0])
            if wait > 0:
                time.sleep(wait)
            _LAST_CALL[0] = time.monotonic()
        try:
            resp = self.session.get(url, params=params, headers=HEADERS, timeout=30)
        except (requests.Timeout, requests.ConnectionError) as e:
            # One retry absorbs a slow moment. After that, this funder's history
            # is unavailable today: an error a multi-funder run reports and
            # survives, not a crash that takes every other funder down with it.
            self._last_call = time.monotonic()
            if retries > 0:
                return self._get(url, params, retries - 1)
            raise ThreeSixtyGivingError(f"360Giving did not respond: {e}") from e
        self._last_call = time.monotonic()
        if resp.status_code == 404:
            raise ThreeSixtyGivingError(f"Org ID not found: {url}")
        if resp.status_code == 429:
            time.sleep(1.0)
            return self._get(url, params, retries)
        try:
            resp.raise_for_status()
            return resp.json()
        except (requests.HTTPError, ValueError) as e:
            raise ThreeSixtyGivingError(f"360Giving returned {resp.status_code} for {url}") from e

    def org(self, org_id: str) -> dict:
        return self._get(f"{API_ROOT}/org/{org_id}/")

    def grants_made(self, org_id: str, limit: int = 1000, cap: int | None = None) -> Iterator[dict]:
        """Yield raw grant records made by a funder, following pagination.

        The page size never exceeds `cap`. A government department's 1,000-grant
        page takes 30 seconds or more to serve and 300 takes under ten, so asking
        for more than the run will read only buys a timeout.
        """
        url = f"{API_ROOT}/org/{org_id}/grants_made/"
        params: dict | None = {"limit": min(limit, cap) if cap else limit}
        seen = 0
        # How many the funder has published in all. The API returns awards in
        # no particular order, so a capped read is a sample, not the latest.
        self.published = None
        while url:
            page = self._get(url, params)
            if self.published is None:
                self.published = page.get("count")
            params = None  # subsequent `next` URLs already carry params
            for row in page.get("results", []):
                yield row
                seen += 1
                if cap and seen >= cap:
                    return
            url = page.get("next")


# --------------------------------------------------------------------------
# Revealed preferences
# --------------------------------------------------------------------------


def _first(seq: list | None, key: str) -> Any:
    if not seq:
        return None
    return seq[0].get(key)


# beneficiaryLocation entries range from country down to output area, and a
# grant may carry several at different granularities. Rank them so the coarsest
# wins: "Southwark" is a revealed preference a small charity can act on,
# "Hackney 025C" is noise that fragments top_regions into a list of ones.
_GEO_RANK = {
    "CTRY": 0,                                          # country
    "RGN": 1,                                           # region
    "CTY": 2, "UTLA": 2,                                # county / upper tier
    "LAD": 3, "LTLA": 3, "LONB": 3, "UA": 3, "MD": 3, "DIS": 3,   # district
    "WD": 4,                                            # ward
    "MSOA": 5, "LSOA": 6, "OA": 7,                      # statistical areas
}
_FINE_GRAINED = {"WD", "MSOA", "LSOA", "OA"}

# ONS names statistical areas "<district> <nnn><letter>", e.g. "Hackney 025C".
# The district prefix is the part worth counting.
_AREA_SUFFIX = re.compile(r"\s+\d{3,}[A-Z]?$")


def _beneficiary_region(locations: list | None) -> str | None:
    """Coarsest usable place name from a grant's beneficiaryLocation entries.

    Falls back to trimming the statistical-area suffix when nothing coarser is
    published, so an LSOA-only funder still aggregates by district instead of
    producing one bucket per area.
    """
    best: tuple[int, str] | None = None
    for loc in locations or []:
        name = (loc.get("name") or "").strip()
        if not name:
            continue
        code_type = loc.get("geoCodeType") or ""
        if code_type in _FINE_GRAINED:
            trimmed = _AREA_SUFFIX.sub("", name).strip()
            if trimmed:
                name = trimmed
        rank = _GEO_RANK.get(code_type, 4)
        if best is None or rank < best[0]:
            best = (rank, name)
    return best[1] if best else None


@dataclass
class Grant:
    """Flattened view of the fields we actually reason over."""

    grant_id: str
    title: str
    description: str
    amount_awarded: float | None
    amount_applied_for: float | None
    award_date: str | None
    duration_months: int | None
    recipient_id: str | None
    recipient_name: str | None
    region: str | None
    country: str | None
    postcode: str | None
    programme: str | None
    classifications: list[str] = field(default_factory=list)

    @classmethod
    def from_api(cls, row: dict) -> "Grant":
        d = row.get("data", {})
        rec = (d.get("recipientOrganization") or [{}])[0]
        planned = (d.get("plannedDates") or [{}])[0]
        return cls(
            grant_id=d.get("id", ""),
            title=d.get("title", "") or "",
            description=d.get("description", "") or "",
            amount_awarded=d.get("amountAwarded"),
            amount_applied_for=d.get("amountAppliedFor"),
            award_date=d.get("awardDate"),
            duration_months=planned.get("duration"),
            recipient_id=rec.get("id"),
            recipient_name=rec.get("name"),
            # Publishers split between two conventions. Grantmakers like Arts
            # Council and Wellcome put the recipient's address on
            # recipientOrganization; community foundations — the funders this
            # project is aimed at — publish no address at all and carry location
            # only in beneficiaryLocation (borough/LSOA names). Without the
            # fallback, top_regions is empty for exactly those funders and the
            # region term in score_alignment silently never fires.
            region=(rec.get("addressRegion") or rec.get("addressLocality")
                    or _beneficiary_region(d.get("beneficiaryLocation"))),
            country=rec.get("addressCountry") or _first(d.get("beneficiaryLocation"), "countryCode"),
            postcode=rec.get("postalCode"),
            programme=_first(d.get("grantProgramme"), "title"),
            classifications=[c.get("title", "") for c in (d.get("classifications") or [])],
        )

    @property
    def org_register(self) -> str | None:
        """GB-CHC, GB-COH, GB-SC etc. Tells you what legal forms this funder backs."""
        if not self.recipient_id:
            return None
        return "-".join(self.recipient_id.split("-")[:2])


@dataclass
class RevealedPreferences:
    """What a funder's award history says, as distinct from what its brief says.

    Every field here is descriptive. None of it predicts success: 360Giving
    publishes awards only, so there is no record of rejected applicants.
    See README, 'What this does not do'.
    """

    funder_id: str
    funder_name: str
    n_grants: int
    median_award: float | None
    award_p10: float | None
    award_p90: float | None
    median_duration_months: float | None
    ask_to_award_ratio: float | None
    ask_ratio_n: int
    top_regions: list[tuple[str, int]]
    registers: list[tuple[str, int]]
    top_programmes: list[tuple[str, int]]
    top_classifications: list[tuple[str, int]]
    repeat_funding_rate: float
    n_published: int | None = None        # the funder's whole history, when known
    programme_note: str | None = None     # set when the view is specific to one call

    @property
    def sampled(self) -> bool:
        """True when these figures come from part of a longer history."""
        return bool(self.n_published and self.n_published > self.n_grants)

    def brief(self) -> str:
        """Compact text block for injection into an agent prompt."""
        lines = [
            f"Funder: {self.funder_name} ({self.funder_id})",
            (f"Awards read: {self.n_grants} of {self.n_published:,} published, returned in "
             "no particular order, so a sample rather than the whole history"
             if self.sampled else f"Awards on record: {self.n_grants}"),
        ]
        if self.programme_note:
            lines.append(self.programme_note)
        if self.median_award:
            lines.append(
                f"Award size: median GBP {self.median_award:,.0f}; "
                f"typical range {self.award_p10:,.0f} to {self.award_p90:,.0f}"
            )
        if self.median_duration_months:
            lines.append(f"Typical duration: {self.median_duration_months:.0f} months")
        if self.ask_to_award_ratio is not None:
            lines.append(
                f"Awards {self.ask_to_award_ratio:.0%} of the amount requested on average "
                f"(n={self.ask_ratio_n} grants where the ask is published)"
            )
        if self.registers:
            forms = ", ".join(f"{k} {v}" for k, v in self.registers)
            lines.append(f"Recipient legal forms: {forms}")
        if self.top_regions:
            regions = ", ".join(f"{k} ({v})" for k, v in self.top_regions)
            lines.append(f"Concentrated in: {regions}")
        if self.top_programmes:
            progs = ", ".join(f"{k} ({v})" for k, v in self.top_programmes)
            lines.append(f"Programmes: {progs}")
        if self.top_classifications:
            cls = ", ".join(f"{k} ({v})" for k, v in self.top_classifications)
            lines.append(f"Themes the funder itself labels: {cls}")
        if self.sampled:
            # Read from a sample, this figure once led the model to decide that
            # past grantees could not reapply. It measures nothing of the kind.
            lines.append("Repeat funding: not measurable from a sample, and in any case "
                         "no guide to whether past grantees may apply again")
        else:
            lines.append(f"Repeat funding: {self.repeat_funding_rate:.0%} of recipients "
                         "funded more than once")
        return "\n".join(lines)


def _pct(values: list[float], p: float) -> float | None:
    if not values:
        return None
    s = sorted(values)
    k = (len(s) - 1) * p
    lo, hi = int(k), min(int(k) + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def analyse(grants: list[Grant], funder_id: str, funder_name: str) -> RevealedPreferences:
    awards = [g.amount_awarded for g in grants if g.amount_awarded]
    durations = [float(g.duration_months) for g in grants if g.duration_months]

    ratios = [
        g.amount_awarded / g.amount_applied_for
        for g in grants
        if g.amount_awarded and g.amount_applied_for and g.amount_applied_for > 0
    ]

    recipients = Counter(g.recipient_id for g in grants if g.recipient_id)
    repeat = sum(1 for n in recipients.values() if n > 1) / len(recipients) if recipients else 0.0

    return RevealedPreferences(
        funder_id=funder_id,
        funder_name=funder_name,
        n_grants=len(grants),
        median_award=statistics.median(awards) if awards else None,
        award_p10=_pct(awards, 0.10),
        award_p90=_pct(awards, 0.90),
        median_duration_months=statistics.median(durations) if durations else None,
        ask_to_award_ratio=statistics.mean(ratios) if ratios else None,
        ask_ratio_n=len(ratios),
        top_regions=Counter(g.region for g in grants if g.region).most_common(5),
        registers=Counter(g.org_register for g in grants if g.org_register).most_common(),
        top_programmes=Counter(g.programme for g in grants if g.programme).most_common(5),
        top_classifications=Counter(
            c for g in grants for c in g.classifications if c
        ).most_common(8),
        repeat_funding_rate=repeat,
    )


def profile_funder(funder_id: str, cap: int = 1000, client: Client | None = None,
                   with_grants: bool = False):
    """One call: fetch a funder's award history and reduce it to preferences.

    `with_grants` returns the parsed records alongside the summary. The matcher's
    tools query those directly, and refetching them would mean a second trip
    through a rate-limited API for data already in memory.
    """
    client = client or Client()
    meta = client.org(funder_id)
    grants = [Grant.from_api(row) for row in client.grants_made(funder_id, cap=cap)]
    if not grants:
        raise ThreeSixtyGivingError(f"No published grants for {funder_id}")
    prefs = analyse(grants, funder_id, meta.get("name", funder_id))
    prefs.n_published = getattr(client, "published", None)
    return (prefs, grants) if with_grants else prefs


def for_call(prefs: RevealedPreferences, opp) -> tuple[RevealedPreferences, str]:
    """The funder figures the model should see for this call, and which view it is.

    A sample of a large funder mixes its programmes. The Lottery's 300 are almost
    all Awards for All grants of about £10,000; shown as the Lottery's preference,
    they talked the model out of Reaching Communities, which starts at £20,001.
    So when a call states its own award range and it does not overlap the
    sample's usual range at all, the funder-wide award sizes are withheld and the
    programme's own range stands. Anything that overlaps keeps the funder view.
    """
    low = opp.amount_min if (opp.amount_min or 0) >= 100 else None
    high = opp.amount_max if (opp.amount_max or 0) >= 100 else None
    if not (prefs.sampled and (low or high) and prefs.award_p10 and prefs.award_p90):
        return prefs, "funder"
    if (low and low > prefs.award_p90) or (high and high < prefs.award_p10):
        from dataclasses import replace
        mix = ", ".join(p for p, _ in prefs.top_programmes[:2]) or "its other programmes"
        shown = replace(prefs, median_award=None, award_p10=None, award_p90=None,
                        ask_to_award_ratio=None, ask_ratio_n=0,
                        programme_note=(f"Award sizes are not shown: this funder's sample is mostly "
                                        f"{mix}, which says nothing about this programme's awards. "
                                        "The programme's own stated range applies."))
        return shown, "no-amounts"
    return prefs, "funder"
