"""Learn from the organisation's own past submissions.

360Giving records who won. An organisation's own bid archive records who lost, and
it is the only rejected-application data that exists anywhere in UK grantmaking.
That makes this folder the most valuable input the agent has.

Scope is deliberately narrow. This module reads ONE designated folder. It never
walks a whole drive. See `SCOPE_POLICY` below and the README section on access.
"""

from __future__ import annotations

import json
import re
import statistics
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Iterator, Literal

SCOPE_POLICY = """Granted reads one folder, designated by the organisation, and
nothing else. It does not traverse parent directories, follow symlinks out of the
folder, or read anything matching the exclusion list. Any file whose name suggests
beneficiary or personnel records is skipped and logged, even inside the folder."""

# Names that must never be read even if someone drops them in the funding folder.
EXCLUDE_PATTERNS = [
    r"safeguard", r"referral", r"beneficiar", r"service.?user", r"case.?note",
    r"dbs", r"payroll", r"hr[-_ ]", r"personnel", r"medical", r"incident",
    r"attendance", r"pupil", r"patient", r"contact.?list", r"gdpr",
]
_EXCLUDE = re.compile("|".join(EXCLUDE_PATTERNS), re.I)

READABLE_SUFFIXES = {".md", ".txt", ".docx", ".pdf", ".json"}

Outcome = Literal["awarded", "rejected", "withdrawn", "pending", "unknown"]

# A funder you won from and have not been back to in this long is a live lead.
STALE_RELATIONSHIP_DAYS = 550


@dataclass
class Submission:
    """One past application, with its result if known."""

    path: Path
    funder: str | None = None
    programme: str | None = None
    submitted: date | None = None
    amount_asked: float | None = None
    amount_awarded: float | None = None
    outcome: Outcome = "unknown"
    feedback: str | None = None
    text: str = ""

    @property
    def won(self) -> bool:
        return self.outcome == "awarded"

    @property
    def decided(self) -> bool:
        return self.outcome in ("awarded", "rejected")


@dataclass
class SkippedFile:
    path: Path
    reason: str


# Granted's own guide in the folder, and the files an operating system leaves
# behind, are not past applications. Read as one, a README that mentions a
# "successful" example becomes a won bid with no funder.
_NOT_A_BID = re.compile(r"^(readme(\.[a-z]+)?|desktop\.ini|thumbs\.db|\..+)$", re.I)


def _excluded(p: Path) -> str | None:
    if _NOT_A_BID.match(p.name):
        return "not an application: a readme or system file"
    if _EXCLUDE.search(p.name):
        return "name matches sensitive-record pattern"
    if p.is_symlink():
        return "symlink"
    if p.suffix.lower() not in READABLE_SUFFIXES:
        return f"unsupported type {p.suffix or '(none)'}"
    return None


def scan(folder: Path) -> tuple[list[Path], list[SkippedFile]]:
    """List readable files in the designated folder. Never escapes it."""
    folder = folder.resolve()
    keep: list[Path] = []
    skipped: list[SkippedFile] = []
    for p in sorted(folder.rglob("*")):
        if not p.is_file():
            continue
        try:
            p.resolve().relative_to(folder)
        except ValueError:
            skipped.append(SkippedFile(p, "resolves outside designated folder"))
            continue
        reason = _excluded(p)
        if reason:
            skipped.append(SkippedFile(p, reason))
        else:
            keep.append(p)
    return keep, skipped


# --------------------------------------------------------------------------
# Metadata extraction: sidecar first, filename fallback, then ask the human
# --------------------------------------------------------------------------

_MONEY = re.compile(r"£\s?([\d,]+(?:\.\d{2})?)")
_YEAR = re.compile(r"(20\d{2})")
_OUTCOME_HINT = {
    "awarded": r"\b(awarded|successful|approved|won|grant offer)\b",
    "rejected": r"\b(unsuccessful|declined|rejected|not funded|regret)\b",
    "withdrawn": r"\b(withdrawn|abandoned)\b",
}


def _sidecar(p: Path) -> dict:
    """A `<name>.meta.json` next to a bid is the reliable path. Archives that have
    one produce clean base rates; archives that don't fall back to inference."""
    side = p.with_suffix(p.suffix + ".meta.json")
    if side.exists():
        try:
            return json.loads(side.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
    return {}


def _infer_outcome(name: str, text: str) -> Outcome:
    haystack = f"{name}\n{text[:2000]}"
    for outcome, pattern in _OUTCOME_HINT.items():
        if re.search(pattern, haystack, re.I):
            return outcome  # type: ignore[return-value]
    return "unknown"


def load_submission(p: Path, text: str = "") -> Submission:
    """Build a Submission from sidecar metadata where present, inference where not.

    Real archives are messy. Nothing here raises on a badly named file; unknown
    fields stay None and become interview questions later.
    """
    meta = _sidecar(p)
    sub = Submission(path=p, text=text)

    sub.funder = meta.get("funder")
    sub.programme = meta.get("programme")
    sub.feedback = meta.get("feedback")

    if meta.get("submitted"):
        try:
            sub.submitted = date.fromisoformat(meta["submitted"])
        except ValueError:
            pass
    elif (m := _YEAR.search(p.name)):
        sub.submitted = date(int(m.group(1)), 1, 1)

    sub.amount_asked = meta.get("amount_asked")
    sub.amount_awarded = meta.get("amount_awarded")
    if sub.amount_asked is None and (m := _MONEY.search(p.name)):
        sub.amount_asked = float(m.group(1).replace(",", ""))

    outcome = meta.get("outcome")
    sub.outcome = outcome if outcome in ("awarded", "rejected", "withdrawn", "pending") else _infer_outcome(p.name, text)
    if sub.outcome == "unknown" and sub.amount_awarded:
        sub.outcome = "awarded"
    return sub


# --------------------------------------------------------------------------
# What the archive tells you
# --------------------------------------------------------------------------


@dataclass
class ArchiveInsight:
    """The org's own track record. Small-n by nature: read as hypotheses."""

    n_files: int
    n_decided: int
    n_awarded: int
    n_unknown_outcome: int
    win_rate: float | None
    median_successful_ask: float | None
    median_unsuccessful_ask: float | None
    funders_won: list[tuple[str, int]] = field(default_factory=list)
    funders_lost: list[tuple[str, int]] = field(default_factory=list)
    never_reapplied_to: list[str] = field(default_factory=list)
    revisit_detail: list[dict] = field(default_factory=list)
    skipped: list[SkippedFile] = field(default_factory=list)

    @property
    def confidence(self) -> str:
        """Honesty gate. Do not let an agent talk like a model at n=9."""
        if self.n_decided < 10:
            return "anecdotal"
        if self.n_decided < 30:
            return "indicative"
        return "reasonable"

    def brief(self) -> str:
        if not self.n_decided:
            return (
                f"Archive: {self.n_files} files, none with a recorded outcome. "
                "No track record available; outcomes need to be supplied before "
                "any of this org's own history can inform a decision."
            )
        lines = [
            f"Own track record ({self.confidence}, n={self.n_decided} decided "
            f"applications, {self.n_unknown_outcome} outcomes still unrecorded):",
            f"Win rate: {self.win_rate:.0%}",
        ]
        if self.median_successful_ask and self.median_unsuccessful_ask:
            lines.append(
                f"Successful asks median GBP {self.median_successful_ask:,.0f}; "
                f"unsuccessful median GBP {self.median_unsuccessful_ask:,.0f}"
            )
        if self.funders_won:
            lines.append("Won from: " + ", ".join(f"{k} ({v})" for k, v in self.funders_won))
        if self.funders_lost:
            lines.append("Declined by: " + ", ".join(f"{k} ({v})" for k, v in self.funders_lost))
        if self.never_reapplied_to:
            lines.append(
                "Won from but not approached since, worth revisiting: "
                + ", ".join(self.never_reapplied_to)
            )
        lines.append(
            "Treat as hypotheses, not findings. This sample is too small to "
            "support a causal claim about what wins."
        )
        return "\n".join(lines)


def summarise(subs: list[Submission], skipped: list[SkippedFile] | None = None) -> ArchiveInsight:
    decided = [s for s in subs if s.decided]
    won = [s for s in decided if s.won]
    lost = [s for s in decided if not s.won]

    won_funders = Counter(s.funder for s in won if s.funder)
    lost_funders = Counter(s.funder for s in lost if s.funder)

    # A funder who said yes and was never approached again is money left on the
    # table. The test is chronological: no submission to them dated after the
    # one they awarded. Small orgs miss this constantly.
    latest_award: dict[str, str] = {}
    latest_any: dict[str, str] = {}
    for s in subs:
        if not (s.funder and s.submitted):
            continue
        stamp = s.submitted.isoformat()
        latest_any[s.funder] = max(latest_any.get(s.funder, ""), stamp)
        if s.won:
            latest_award[s.funder] = max(latest_award.get(s.funder, ""), stamp)
    # Recency matters: a funder you won from last month is not neglected. Flag
    # only where the winning bid is the most recent contact AND it is stale.
    cutoff = (date.today() - timedelta(days=STALE_RELATIONSHIP_DAYS)).isoformat()
    never_again = sorted(
        f for f, when in latest_award.items()
        if latest_any.get(f, "") <= when and when < cutoff
    )

    # Bare names are not enough for a reader deciding whether to pick the phone
    # up. Carry the size and recency of the relationship alongside.
    _MONTHS = ("January February March April May June July August September "
               "October November December").split()
    revisit_detail = []
    for f in never_again:
        wins = [s for s in subs if s.funder == f and s.won]
        total = sum(s.amount_awarded or 0 for s in wins)
        last = max((s.submitted for s in wins if s.submitted), default=None)
        when = f"{_MONTHS[last.month - 1]} {last.year}" if last else "date unknown"
        n = len(wins)
        count = {1: "one award", 2: "two awards", 3: "three awards",
                 4: "four awards"}.get(n, f"{n} awards")
        amount = f", \u00a3{total:,.0f} in total" if total else ""
        revisit_detail.append({
            "funder": f, "n_awards": n, "total_awarded": total or None,
            "last_award": last.isoformat() if last else None,
            "text": f"{f} \u2014 {count}{amount}, last said yes {when}",
        })

    won_asks = [s.amount_asked for s in won if s.amount_asked]
    lost_asks = [s.amount_asked for s in lost if s.amount_asked]

    return ArchiveInsight(
        n_files=len(subs),
        n_decided=len(decided),
        n_awarded=len(won),
        n_unknown_outcome=sum(1 for s in subs if s.outcome in ("unknown", "pending")),
        win_rate=len(won) / len(decided) if decided else None,
        median_successful_ask=statistics.median(won_asks) if won_asks else None,
        median_unsuccessful_ask=statistics.median(lost_asks) if lost_asks else None,
        funders_won=won_funders.most_common(5),
        funders_lost=lost_funders.most_common(5),
        never_reapplied_to=sorted(never_again),
        revisit_detail=revisit_detail,
        skipped=skipped or [],
    )


def load_archive(folder: Path, read_text=None) -> tuple[list[Submission], ArchiveInsight]:
    """Entry point. Word, PDF and plain text are read by default; `read_text`
    stays injectable so extraction can be swapped without touching this."""
    from .documents import read_text as read_document

    reader = read_text or read_document
    files, skipped = scan(folder)
    subs = []
    for p in files:
        if p.name.endswith(".meta.json"):
            continue
        subs.append(load_submission(p, reader(p)))
    return subs, summarise(subs, skipped)
