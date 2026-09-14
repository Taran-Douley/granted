"""Watch funding calls open and close, and keep in-flight timelines honest.

Two things live here.

1. An opportunity register with memory. Polling is easy; remembering what you
   already said is what separates an agent from a cron job that spams people.
   Every run diffs against the last, and only material changes surface.

2. Timeline drift. An application already in flight that has fallen behind is a
   real decision, arguably more urgent than a new call. This gives the gate its
   second trigger.

Source note: there is no open API for UK funding calls. 360Giving publishes awards
already made, not opportunities open now. Sources here are funder pages and RSS,
hand-configured. Commercial aggregators exist and are out of scope.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from enum import Enum
from pathlib import Path
from typing import Literal

CLOSING_SOON_DAYS = 21
FINAL_CALL_DAYS = 7


class Status(str, Enum):
    UPCOMING = "upcoming"
    OPEN = "open"
    CLOSING_SOON = "closing_soon"
    FINAL_CALL = "final_call"
    CLOSED = "closed"
    WITHDRAWN = "withdrawn"


@dataclass
class Opportunity:
    funder: str
    programme: str | None
    url: str
    opens: str | None = None
    closes: str | None = None
    amount_max: float | None = None
    summary: str = ""
    first_seen: str | None = None
    last_seen: str | None = None
    status: str = Status.OPEN.value
    fit_score: int | None = None
    questions: list[str] = field(default_factory=list)
    notified_states: list[str] = field(default_factory=list)
    workspace: str | None = None
    # The 360Giving org id whose award history this call is judged against.
    funder_id: str | None = None
    # What the call itself states, for judging it when its funder publishes no
    # award history, and for the location and eligibility checks every call gets.
    locations: list[str] = field(default_factory=list)
    eligibility: str = ""
    amount_min: float | None = None
    source: str = ""

    @property
    def key(self) -> str:
        raw = f"{self.funder}|{self.programme or ''}|{self.url}"
        return hashlib.sha1(raw.encode()).hexdigest()[:12]

    def days_left(self, today: date | None = None) -> int | None:
        if not self.closes:
            return None
        try:
            return (date.fromisoformat(self.closes) - (today or date.today())).days
        except ValueError:
            return None

    def compute_status(self, today: date | None = None) -> str:
        today = today or date.today()
        if self.status == Status.WITHDRAWN.value:
            return self.status
        if self.opens:
            try:
                if date.fromisoformat(self.opens) > today:
                    return Status.UPCOMING.value
            except ValueError:
                pass
        d = self.days_left(today)
        if d is None:
            return Status.OPEN.value
        if d < 0:
            return Status.CLOSED.value
        if d <= FINAL_CALL_DAYS:
            return Status.FINAL_CALL.value
        if d <= CLOSING_SOON_DAYS:
            return Status.CLOSING_SOON.value
        return Status.OPEN.value


ChangeKind = Literal[
    "new", "deadline_moved", "now_open", "closing_soon", "final_call",
    "closed", "withdrawn", "behind_schedule",
]

# Only these reach a human. Everything else updates the register silently.
SURFACING = {"new", "deadline_moved", "final_call", "withdrawn", "behind_schedule"}


@dataclass
class Change:
    kind: ChangeKind
    opportunity: Opportunity
    detail: str
    urgent: bool = False

    @property
    def surfaces(self) -> bool:
        return self.kind in SURFACING


class Register:
    """Persisted memory of what has been seen and what has been said."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.items: dict[str, Opportunity] = {}
        if path.exists():
            raw = json.loads(path.read_text(encoding="utf-8"))
            self.items = {k: Opportunity(**v) for k, v in raw.items()}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({k: asdict(v) for k, v in self.items.items()}, indent=2),
                             encoding="utf-8")

    def poll(self, scraped: list[Opportunity], today: date | None = None,
             min_fit: int = 65, sources: set[str] | None = None) -> list[Change]:
        """Diff a fresh scrape against memory. Returns only what changed.

        `min_fit` gates new opportunities: an unscored or poorly-fitting call is
        recorded but never surfaced. Silence stays the default.
        """
        today = today or date.today()
        stamp = today.isoformat()
        changes: list[Change] = []
        seen_keys = set()

        for fresh in scraped:
            seen_keys.add(fresh.key)
            known = self.items.get(fresh.key)

            if known is None:
                fresh.first_seen = fresh.last_seen = stamp
                fresh.status = fresh.compute_status(today)
                self.items[fresh.key] = fresh
                if fresh.fit_score is not None and fresh.fit_score >= min_fit:
                    changes.append(Change("new", fresh,
                        f"fit {fresh.fit_score}/100, closes {fresh.closes or 'no stated deadline'}"))
                    fresh.notified_states.append("new")
                continue

            known.last_seen = stamp
            # A call passed over once can clear the gate on a later day. Without
            # this the register never learns its bid folder exists, and it is
            # recommended afresh every morning instead of tracked.
            if fresh.workspace and not known.workspace:
                known.workspace = fresh.workspace
            if fresh.fit_score is not None:
                known.fit_score = fresh.fit_score

            # A moved deadline invalidates any timeline already issued.
            if fresh.closes and known.closes and fresh.closes != known.closes:
                old, new = known.closes, fresh.closes
                known.closes = new
                brought_forward = new < old
                changes.append(Change(
                    "deadline_moved", known,
                    f"deadline {'brought forward' if brought_forward else 'extended'} "
                    f"from {old} to {new}; timeline needs regenerating",
                    urgent=brought_forward,
                ))
            elif fresh.closes and not known.closes:
                known.closes = fresh.closes

            if fresh.amount_max:
                known.amount_max = fresh.amount_max

            new_status = known.compute_status(today)
            if new_status != known.status:
                previous, known.status = known.status, new_status
                # Only nag about calls we actually recommended, or ones already
                # in flight. Otherwise the agent chases you about a bid it told
                # you not to make, which is how a background agent becomes spam.
                relevant = known.workspace is not None or (
                    known.fit_score is not None and known.fit_score >= min_fit)
                if relevant and new_status not in known.notified_states:
                    d = known.days_left(today)
                    changes.append(Change(
                        new_status, known,  # type: ignore[arg-type]
                        f"{previous} to {new_status}"
                        + (f", {d} days left" if d is not None else ""),
                        urgent=(new_status == Status.FINAL_CALL.value),
                    ))
                    known.notified_states.append(new_status)

        # Vanished from source while still open: pulled, or the page moved.
        for key, known in self.items.items():
            if key in seen_keys or known.status in (Status.CLOSED.value, Status.WITHDRAWN.value):
                continue
            # A source that could not be read today says nothing about its calls.
            if sources is not None and known.source not in sources:
                continue
            if known.last_seen and known.last_seen != stamp:
                known.status = Status.WITHDRAWN.value
                changes.append(Change("withdrawn", known,
                    "no longer listed at source while still shown as open; verify before working on it"))

        return [c for c in changes if c.surfaces]


# --------------------------------------------------------------------------
# Timeline drift
# --------------------------------------------------------------------------


@dataclass
class TimelineStatus:
    deadline: str
    days_left: int
    overdue: list[str]
    due_now: list[str]
    hours_remaining_estimate: float
    feasible: bool

    def summary(self) -> str:
        if self.feasible and not self.overdue:
            return f"On track. {self.days_left} days left."
        lines = [f"{self.days_left} days to deadline."]
        if self.overdue:
            lines.append(f"Overdue: {'; '.join(self.overdue)}.")
        if self.due_now:
            lines.append(f"Due now: {'; '.join(self.due_now)}.")
        if not self.feasible:
            lines.append(
                f"The remaining work is estimated at {self.hours_remaining_estimate:.0f} hours "
                "against the time left. This is no longer realistic at the current pace. "
                "Either commit someone this week or withdraw and keep the time."
            )
        return " ".join(lines)


def check_timeline(deadline: str, steps: list[dict], done: set[str],
                   today: date | None = None, hours_per_day: float = 1.5) -> TimelineStatus:
    """Reconcile a work-back schedule against the calendar.

    `steps` are dicts with days_before_deadline, task, and optionally hours.
    Recommending withdrawal is a legitimate output. A charity that stops a doomed
    bid on day four has been served better than one nudged to finish it.
    """
    today = today or date.today()
    dl = date.fromisoformat(deadline)
    days_left = (dl - today).days

    overdue, due_now, remaining_hours = [], [], 0.0
    for s in steps:
        task = s["task"]
        if task in done:
            continue
        due = dl - timedelta(days=s["days_before_deadline"])
        remaining_hours += float(s.get("hours", 2.0))
        if due < today:
            overdue.append(f"{task} (was due {due.isoformat()})")
        elif (due - today).days <= 2:
            due_now.append(task)

    capacity = max(0, days_left) * hours_per_day
    return TimelineStatus(
        deadline=deadline,
        days_left=days_left,
        overdue=overdue,
        due_now=due_now,
        hours_remaining_estimate=remaining_hours,
        feasible=remaining_hours <= capacity,
    )


def drift_change(opp: Opportunity, ts: TimelineStatus) -> Change | None:
    """Turn timeline trouble into a gate-triggering event. Silence if on track."""
    if ts.feasible and not ts.overdue:
        return None
    return Change("behind_schedule", opp, ts.summary(), urgent=not ts.feasible)
