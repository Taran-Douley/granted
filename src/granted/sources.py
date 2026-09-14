"""Live funding calls: Find a grant, the National Lottery Community Fund, and
Innovate UK.

Find a grant (find-government-grants.service.gov.uk) is a GOV.UK service listing
open UK government grants with opening and closing dates, an explicit "who can
apply" field, and an award range, under the Open Government Licence v3.0. It is
a Next.js page that embeds its search results as JSON in a `__NEXT_DATA__` tag,
and that is what is read, rather than the rendered markup.

The National Lottery Community Fund is the largest funder of small charities in
the UK. Its programme list gives each programme's nation, award band and status;
only programmes open to applications are read. It publishes its award history to
360Giving, so its programmes are scored against what it has actually funded.

Innovate UK's competitions come from its Innovation Funding Service. Most are
for businesses; the eligibility text is kept, so a charity is told plainly when
only businesses can apply rather than being shown a call it cannot enter.

Every adapter degrades rather than raises on a single bad record, and raises
`SourceError` when a whole page has no data, because an empty list would pass
for a quiet day. `python -m granted.sources --check` says whether each source
still parses.
"""

from __future__ import annotations

import argparse
import html as html_lib
import json
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterator

import requests

from .watcher import Opportunity

BASE = "https://www.find-government-grants.service.gov.uk"
LISTING = f"{BASE}/grants"
ATTRIBUTION = (
    "Contains public sector information licensed under the Open Government "
    "Licence v3.0. Source: Find a grant, GOV.UK."
)
USER_AGENT = "granted/0.1 (hackathon project; contact via repo)"
MIN_INTERVAL = 1.0  # be a polite citizen on a public service
PAGE_SIZE = 10
# 118 grants at ten a page is twelve pages. The cap only stops a runaway loop if
# the site ever stops returning an empty page at the end.
MAX_PAGES = 30

LOTTERY_BASE = "https://www.tnlcommunityfund.org.uk"
LOTTERY_LISTING = f"{LOTTERY_BASE}/funding/funding-programmes"
LOTTERY_FUNDER = "The National Lottery Community Fund"
LOTTERY_ID = "GB-GOR-PB188"          # its 360Giving id, from the publisher registry

INNOVATE_BASE = "https://apply-for-innovation-funding.service.gov.uk"
INNOVATE_LISTING = f"{INNOVATE_BASE}/competition/search"
INNOVATE_FUNDER = "Innovate UK"

# The names `--calls` and granted.json use for the live sources.
LIVE = ("find-a-grant", "national-lottery", "innovate-uk")

_NEXT_DATA = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.S)
_MONEY = re.compile(r"£\s?([\d,]+(?:\.\d+)?)\s*(million|m\b|billion|bn\b|k\b)?", re.I)
_MULTIPLIER = {"million": 1e6, "m": 1e6, "billion": 1e9, "bn": 1e9, "k": 1e3}
_LONG_DATE = re.compile(r"(\d{1,2})\s+([A-Z][a-z]+)\s+(\d{4})")

# The fields a calls.json row carries. The rest of Opportunity is the register's
# own bookkeeping and has no business in a file a person edits.
CALL_FIELDS = ("funder", "funder_id", "programme", "url", "opens", "closes",
               "amount_min", "amount_max", "summary", "locations", "eligibility", "source")


class SourceError(RuntimeError):
    """A source could not be read at all: network failure or a changed page."""


class FindAGrantError(SourceError):
    """Find a grant could not be read."""


# ------------------------------------------------------------------- helpers

def _iso(stamp: str | None) -> str | None:
    """'2030-04-01T00:00' -> '2030-04-01'."""
    m = re.match(r"\d{4}-\d{2}-\d{2}", stamp or "")
    return m.group(0) if m else None


def _long_date(text: str | None) -> str | None:
    """'3 November 2026 11:00am' -> '2026-11-03'."""
    m = _LONG_DATE.search(text or "")
    if not m:
        return None
    for fmt in ("%d %B %Y", "%d %b %Y"):
        try:
            return datetime.strptime(" ".join(m.groups()), fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _num(value) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _text(fragment: str | None) -> str:
    """Visible text of an HTML fragment."""
    return " ".join(html_lib.unescape(re.sub(r"<[^>]+>", " ", fragment or "")).split())


def _money(text: str) -> float | None:
    m = _MONEY.search(text or "")
    if not m:
        return None
    value = float(m.group(1).replace(",", ""))
    return value * _MULTIPLIER.get((m.group(2) or "").lower().strip(), 1)


def _amounts(text: str | None) -> tuple[float | None, float | None]:
    """'£20,001 to £250,000' -> (20001, 250000); 'Up to £10,000' -> (None, 10000);
    'a share of up to £8.5 million' -> (None, 8500000)."""
    found = [(_money(m.group(0))) for m in _MONEY.finditer(text or "")]
    if not found:
        return None, None
    if len(found) >= 2 and re.search(r"\bto\b|-|–", text or ""):
        return found[0], found[1]
    return None, found[0]


def _norm(name: str) -> str:
    """Funder names drift between listings ("Environment, Food" and "Environment
    Food" both appear for Defra), so names are compared on letters and digits."""
    return re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()


class _Polite:
    """One request a second, and every failure as a SourceError."""

    def __init__(self, session: requests.Session | None = None,
                 error: type[SourceError] = SourceError) -> None:
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})
        self.error = error
        self._last = 0.0

    def get(self, url: str, params: dict | None = None) -> str:
        wait = MIN_INTERVAL - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        try:
            r = self.session.get(url, params=params, timeout=30)
            r.raise_for_status()
        except requests.RequestException as e:
            raise self.error(str(e)) from e
        finally:
            self._last = time.monotonic()
        return r.text


# --------------------------------------------------------------- Find a grant

@dataclass
class GrantListing:
    """One entry as published. Kept separate from Opportunity so the raw
    source record stays inspectable when a mapping looks wrong."""

    title: str
    url: str
    description: str
    funder: str | None = None
    applicant_types: list[str] = field(default_factory=list)
    locations: list[str] = field(default_factory=list)
    amount_min: float | None = None
    amount_max: float | None = None
    scheme_size: float | None = None
    opens: str | None = None
    closes: str | None = None

    @property
    def nonprofit_eligible(self) -> bool:
        """Hard filter. Runs before any model call, so an ineligible grant
        costs nothing."""
        return any(t.strip().lower() == "non-profit" for t in self.applicant_types)

    def to_opportunity(self, funder_id: str | None = None) -> Opportunity:
        summary = self.description
        if self.locations:
            summary = f"Location: {', '.join(self.locations)}. {summary}"
        return Opportunity(
            funder=self.funder or "Unknown funder",
            programme=self.title,
            url=self.url,
            opens=self.opens,
            closes=self.closes,
            amount_max=self.amount_max,
            summary=summary[:500],
            funder_id=funder_id,
            locations=list(self.locations),
            eligibility=", ".join(self.applicant_types),
            amount_min=self.amount_min,
            source="find-a-grant",
        )


def parse_record(row: dict) -> GrantListing | None:
    """One search result. Returns None rather than raising, so one malformed
    entry never kills a run."""
    try:
        title = " ".join(str(row["grantName"]).split())
        label = str(row["label"]).strip()
    except (KeyError, TypeError):
        return None
    if not title or not label:
        return None
    return GrantListing(
        title=title,
        url=f"{BASE}/grants/{label}",
        description=" ".join(str(row.get("grantShortDescription") or "").split())[:1200],
        # Some listings carry tabs mid-name ("UK Research and Innovation, \tBBSRC").
        funder=" ".join(str(row.get("grantFunder") or "").split()) or None,
        applicant_types=list(row.get("grantApplicantType") or []),
        locations=list(row.get("grantLocation") or []),
        amount_min=_num(row.get("grantMinimumAward")),
        amount_max=_num(row.get("grantMaximumAward")),
        scheme_size=_num(row.get("grantTotalAwardAmount")),
        opens=_iso(row.get("grantApplicationOpenDate")),
        closes=_iso(row.get("grantApplicationCloseDate")),
    )


def parse_page(html: str) -> tuple[list[GrantListing], int, int | None]:
    """Read one listing page. Returns (parsed, unreadable, total on the site).

    A page with no embedded data raises: an empty list would read as "no open
    calls today", and silence caused by a broken parser is exactly the failure
    this project cannot afford to disguise.
    """
    m = _NEXT_DATA.search(html)
    if not m:
        raise FindAGrantError("no __NEXT_DATA__ on the listing page; the site has changed shape")
    try:
        props = json.loads(m.group(1))["props"]["pageProps"]
        rows = props["searchResult"]
    except (ValueError, KeyError, TypeError) as e:
        raise FindAGrantError(f"listing data is not where it was: {e}") from e
    parsed, failed = [], 0
    for row in rows or []:
        g = parse_record(row) if isinstance(row, dict) else None
        if g:
            parsed.append(g)
        else:
            failed += 1
    total = props.get("totalGrants")
    return parsed, failed, total if isinstance(total, int) else None


class FindAGrant:
    """Paginating client for the Find a grant listing."""

    def __init__(self, session: requests.Session | None = None) -> None:
        self._http = _Polite(session, FindAGrantError)
        self.parse_failures = 0
        self.listed = 0                 # every record read, eligible or not
        self.total: int | None = None   # what the site says it holds

    def fetch(self, nonprofit_only: bool = True,
              max_pages: int = MAX_PAGES) -> Iterator[GrantListing]:
        """Yield listings. `nonprofit_only` filters before any model sees them."""
        for page in range(1, max_pages + 1):
            html = self._http.get(LISTING, {"limit": PAGE_SIZE,
                                            "skip": (page - 1) * PAGE_SIZE, "page": page})
            grants, failed, total = parse_page(html)
            self.parse_failures += failed
            self.total = total if total is not None else self.total
            if not grants:
                return
            for g in grants:
                self.listed += 1
                if nonprofit_only and not g.nonprofit_eligible:
                    continue
                yield g
            if self.total is not None and page * PAGE_SIZE >= self.total:
                return


def load_funder_ids(path: Path | None) -> dict[str, str]:
    """Find a grant funder name -> 360Giving org id, from a JSON object.

    Keys starting with an underscore are comments. Names are matched on letters
    and digits only, so punctuation drift between listings does not matter.
    """
    if path is None:
        return {}
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    return {_norm(k): v for k, v in raw.items() if not k.startswith("_") and v}


def opportunities(funder_ids: dict[str, str] | None = None,
                  client: FindAGrant | None = None) -> list[Opportunity]:
    """Every open Find a grant call a non-profit can apply for.

    `funder_ids` comes from `load_funder_ids`. A call whose funder is not in it
    gets no `funder_id` and is judged on its stated criteria instead.
    """
    client = client or FindAGrant()
    ids = funder_ids or {}
    return [g.to_opportunity(ids.get(_norm(g.funder or ""))) for g in client.fetch()]


# ---------------------------------------------------------- National Lottery

def parse_lottery(html: str) -> tuple[list[Opportunity], int, int]:
    """Programme cards from the funding-programmes page.

    Returns (open programmes, cards not open, unreadable cards). Raises when the
    page carries no cards at all.
    """
    chunks = html.split('<div class="card mb-4">')[1:]
    if not chunks:
        raise SourceError("no programme cards on the National Lottery page; it has changed shape")
    found, not_open, failed = [], 0, 0
    for chunk in chunks:
        link = re.search(r'<h2>\s*<a href="(/funding/funding-programmes/[^"]+)"[^>]*>(.*?)</a>',
                         chunk, re.S)
        if not link:
            failed += 1
            continue
        title = _text(link.group(2))
        desc = re.search(r"</h2>\s*<p>(.*?)</p>", chunk, re.S)
        fields = {}
        for key, value in re.findall(r"<li>\s*<strong>(.*?)</strong>(.*?)</li>", chunk, re.S):
            fields[_text(key).rstrip(":").strip().lower()] = _text(value).lstrip(":").strip()
        if "open" not in fields.get("programme status", "").lower():
            not_open += 1
            continue
        where = fields.get("project location", "")
        locations = [p.strip() for p in re.split(r",|\band\b|/", where) if p.strip()]
        low, high = _amounts(fields.get("amount", ""))
        summary = " ".join(filter(None, [
            f"Location: {where}." if where else "",
            _text(desc.group(1)) if desc else "",
            f"A decision in {fields['a decision in']}." if fields.get("a decision in") else "",
        ]))
        found.append(Opportunity(
            funder=LOTTERY_FUNDER, funder_id=LOTTERY_ID, programme=title,
            url=LOTTERY_BASE + link.group(1), amount_min=low, amount_max=high,
            summary=summary[:500], locations=locations, source="national-lottery"))
    return found, not_open, failed


def national_lottery(session: requests.Session | None = None) -> list[Opportunity]:
    """Programmes open to applications. Most have no closing date: they are rolling."""
    found, _, _ = parse_lottery(_Polite(session).get(LOTTERY_LISTING))
    return found


# ---------------------------------------------------------------- Innovate UK

def parse_innovate(html: str) -> list[Opportunity]:
    """Competitions from one search page. An empty list means past the last page."""
    found = []
    for block in re.split(r'<h2 class="govuk-heading-m[^"]*">', html)[1:]:
        link = re.search(r'<a[^>]*href="(/competition/\d+/overview[^"]*)"[^>]*>(.*?)</a>',
                         block, re.S)
        if not link:
            continue
        desc = re.search(r'</h2>\s*<div class="wysiwyg-styles[^"]*">(.*?)</div>', block, re.S)
        elig = re.search(r"Eligibility</h3>(.*?)<h3", block, re.S)
        opened = re.search(r"Opened:</dt>\s*<dd[^>]*>(.*?)</dd>", block, re.S)
        closes = re.search(r"Closes:</dt>\s*<dd[^>]*>(.*?)</dd>", block, re.S)
        description = _text(desc.group(1)) if desc else ""
        low, high = _amounts(description)
        found.append(Opportunity(
            funder=INNOVATE_FUNDER, programme=_text(link.group(2)),
            url=INNOVATE_BASE + link.group(1),
            opens=_long_date(opened.group(1)) if opened else None,
            closes=_long_date(closes.group(1)) if closes else None,
            amount_min=low, amount_max=high, summary=description[:500],
            eligibility=_text(elig.group(1)) if elig else "", source="innovate-uk"))
    return found


def innovate_uk(session: requests.Session | None = None, max_pages: int = 10) -> list[Opportunity]:
    """Every open competition. Pages are numbered from 0."""
    http = _Polite(session)
    found = []
    for page in range(max_pages):
        html = http.get(INNOVATE_LISTING, {"page": page})
        batch = parse_innovate(html)
        if not batch:
            if page == 0 and "competition" not in html.lower():
                raise SourceError("no competitions on the Innovate UK page; it has changed shape")
            break
        found += batch
    return found


# ------------------------------------------------------------------ dispatch

def fetch_source(name: str, funder_ids: dict[str, str] | None = None,
                 session: requests.Session | None = None) -> list[Opportunity]:
    """Calls from one live source, by the name `--calls` uses."""
    if name == "find-a-grant":
        return opportunities(funder_ids, FindAGrant(session) if session else None)
    if name == "national-lottery":
        return national_lottery(session)
    if name == "innovate-uk":
        return innovate_uk(session)
    raise ValueError(f"unknown source {name!r}; expected one of {', '.join(LIVE)}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m granted.sources",
        description="Read open calls from the live sources.")
    ap.add_argument("--check", action="store_true",
                    help="read each source once and say whether it still parses")
    ap.add_argument("--source", choices=LIVE, default=None, help="only this source")
    ap.add_argument("--funder-ids", type=Path, default=None,
                    help="JSON mapping funder names to 360Giving org ids")
    ap.add_argument("--out", type=Path, default=None,
                    help="write the calls as a calls.json file")
    args = ap.parse_args(argv)

    names = [args.source] if args.source else list(LIVE)
    if args.check:
        bad = 0
        for name in names:
            try:
                if name == "find-a-grant":
                    client = FindAGrant()
                    grants = list(client.fetch(nonprofit_only=False, max_pages=1))
                    eligible = sum(g.nonprofit_eligible for g in grants)
                    print(f"find-a-grant: {client.total} grants listed; page 1 read {len(grants)}, "
                          f"{client.parse_failures} unreadable, {eligible} open to non-profits.")
                    bad += not grants or client.parse_failures
                elif name == "national-lottery":
                    found, not_open, failed = parse_lottery(_Polite().get(LOTTERY_LISTING))
                    print(f"national-lottery: {len(found)} programmes open, {not_open} not open, "
                          f"{failed} unreadable.")
                    bad += not found or failed
                else:
                    found = parse_innovate(_Polite().get(INNOVATE_LISTING, {"page": 0}))
                    print(f"innovate-uk: page 1 read {len(found)} competitions.")
                    bad += not found
            except SourceError as e:
                print(f"{name}: {e}", file=sys.stderr)
                bad += 1
        return 0 if not bad else 1

    ids = load_funder_ids(args.funder_ids)
    opps = []
    for name in names:
        try:
            opps += fetch_source(name, ids)
        except SourceError as e:
            print(f"{name}: {e}", file=sys.stderr)
    print(f"{len(opps)} calls: " + ", ".join(
        f"{n} {sum(o.source == n for o in opps)}" for n in names))
    if args.out:
        rows = [{k: getattr(o, k) for k in CALL_FIELDS} for o in opps]
        args.out.write_text(json.dumps({"_source": ATTRIBUTION, "calls": rows}, indent=2,
                                       ensure_ascii=False), encoding="utf-8")
        print(f"wrote {args.out}")
    else:
        for o in opps:
            print(f"  {o.closes or 'rolling':10}  {o.funder} — {o.programme}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
