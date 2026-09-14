"""Presentation. The only module allowed to decide what anything looks like.

Design brief, in one line: the output has to look like a colleague's note, not a
dashboard. The product's claim is restraint, so the interface has to be restrained
or the claim is just marketing.

Four rules this file follows.

1. Silence is a designed state, not an empty one. A run that surfaces nothing
   still shows what it considered and why each one failed. An empty terminal is
   indistinguishable from a crash; a short accounting is trustworthy.
2. One decision, never a ranked list. A list is the tool declining to decide and
   handing the work back. If the gate fires, one thing is on screen.
3. Numbers travel with their provenance. A fit score without its reasons is a
   horoscope.
4. Every surface renders twice from one structure: ANSI for a terminal that has
   one, plain markdown for the file and for pipes. Colour is emphasis, never
   information, so nothing is lost when it is stripped.
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass, field
from datetime import date

WIDTH = 76          # a readable measure, and it fits a default terminal
LABEL = 13          # label column, so values align down the page


# --------------------------------------------------------------------- colour

class Ink:
    """ANSI, applied only when a real terminal is attached.

    NO_COLOR is honoured because a tool that writes to someone's pipe and
    corrupts it has not earned their trust.
    """

    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled

    def _wrap(self, code: str, text: str) -> str:
        return f"\033[{code}m{text}\033[0m" if self.enabled else text

    def dim(self, t: str) -> str:      return self._wrap("2", t)
    def bold(self, t: str) -> str:     return self._wrap("1", t)
    def accent(self, t: str) -> str:   return self._wrap("36", t)      # cyan
    def warn(self, t: str) -> str:     return self._wrap("33", t)      # amber
    def rule(self) -> str:             return self.dim("─" * WIDTH)


def ink_for(stream=None) -> Ink:
    stream = stream or sys.stdout
    enabled = (
        hasattr(stream, "isatty") and stream.isatty()
        and not os.environ.get("NO_COLOR")
        and os.environ.get("TERM") != "dumb"
    )
    return Ink(enabled)


# ----------------------------------------------------------------- data model

@dataclass
class Considered:
    """One call the run looked at and did not surface."""

    funder: str
    programme: str | None = None
    fit: int | None = None
    reason: str = ""
    days_left: int | None = None
    stage: str = "triage"          # where it stopped: triage | matcher | gate

    @property
    def title(self) -> str:
        return f"{self.funder}" + (f" — {self.programme}" if self.programme else "")


@dataclass
class Decision:
    """The one thing worth a human's attention."""

    funder: str
    programme: str | None = None
    fit: int = 0
    suggested_ask: int | None = None
    closes: str | None = None
    days_left: int | None = None
    why: list[str] = field(default_factory=list)
    gaps: list[str] = field(default_factory=list)
    direction: str | None = None
    timeline: str | None = None
    timeline_steps: list[str] = field(default_factory=list)
    hours_estimate: float | None = None
    workspace: str | None = None
    question: str | None = None
    question_is_generic: bool = False
    own_record: list[str] = field(default_factory=list)
    voice_flags: list[str] = field(default_factory=list)
    voice_checked: bool = False
    grounded: int | None = None
    audit_verdict: str | None = None
    audit_ungrounded: list[str] = field(default_factory=list)
    audit_rate: float | None = None
    total_claims: int | None = None

    @property
    def title(self) -> str:
        return f"{self.funder}" + (f" — {self.programme}" if self.programme else "")


@dataclass
class Archive:
    """What the organisation's own past bids contributed, and how far to trust it.

    Small-n is the normal case: a five-year-old charity might have eighteen
    decided applications. The label travels with every number derived from them
    so nothing here can be read as more than it is.
    """

    confidence: str                 # anecdotal | indicative | reasonable
    n_decided: int
    n_awarded: int
    win_rate: float | None = None

    @property
    def line(self) -> str:
        if not self.n_decided:
            return "No decided applications on file, so this rests on funder data alone."
        rate = f", {self.win_rate:.0%} won" if self.win_rate is not None else ""
        return (f"Your own record: {self.n_decided} decided application"
                f"{'s' if self.n_decided != 1 else ''}{rate}. "
                f"Treat as {self.confidence}.")


@dataclass
class Alert:
    """Something already in flight that has changed for the worse.

    The other half of the README's promise. A new call that fits is only one of
    the two things worth interrupting someone for; the other is a bid they are
    already three weeks into that is quietly slipping.
    """

    title: str
    kind: str
    detail: str
    urgent: bool = False
    workspace: str | None = None


@dataclass
class Digest:
    org_name: str
    run_date: date
    considered: list[Considered] = field(default_factory=list)
    alerts: list[Alert] = field(default_factory=list)
    decision: Decision | None = None
    model: str = ""
    unreadable: list[str] = field(default_factory=list)
    archive: Archive | None = None
    trace: dict = field(default_factory=dict)
    threshold: int = 65
    digest_path: str | None = None
    # The published dashboard's data file (digest.RunDigest), set by run().
    record: object | None = None
    place: str | None = None
    local_funders: list = field(default_factory=list)
    place_note: str | None = None

    @property
    def n_calls(self) -> int:
        return len(self.considered) + (1 if self.decision else 0)


# -------------------------------------------------------------------- helpers

def _wrap(text: str, indent: int = 0, width: int = WIDTH) -> list[str]:
    """Wrap to the measure. Long words are left alone rather than broken."""
    pad, out, line = " " * indent, [], ""
    for word in text.split():
        candidate = f"{line} {word}".strip()
        if len(candidate) + indent > width and line:
            out.append(pad + line)
            line = word
        else:
            line = candidate
    if line:
        out.append(pad + line)
    return out or [pad]


def prose(text: str | None, max_paragraphs: int = 2, max_chars: int = 420) -> str | None:
    """Make model output fit a page that claims to value restraint.

    Agents return markdown whatever the prompt says, and `#` and `**` printed
    literally into a terminal look like a bug. They also run long: the director
    is asked for three short paragraphs and cheerfully returns fifteen. Strip the
    syntax, keep the opening paragraphs, and cut on a sentence boundary — a
    truncated argument is worse than a short one.
    """
    if not text:
        return None
    cleaned: list[str] = []
    for block in text.split("\n\n"):
        block = re.sub(r"^\s*#{1,6}\s*", "", block.strip())      # headings
        block = re.sub(r"\*\*(.+?)\*\*", r"\1", block)            # bold
        block = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"\1", block)  # italic
        block = re.sub(r"`(.+?)`", r"\1", block)                  # code
        block = re.sub(r"^\s*[-*]\s+", "— ", block, flags=re.M)   # bullets
        block = " ".join(block.split())
        if block:
            cleaned.append(block)
        if len(cleaned) >= max_paragraphs:
            break
    out = "  ".join(cleaned)
    if len(out) > max_chars:
        cut = out[:max_chars]
        stop = max(cut.rfind(". "), cut.rfind("? "), cut.rfind("! "))
        out = (cut[:stop + 1] if stop > max_chars // 2 else cut.rstrip() + "…")
    return out or None


def shorten(text: str, limit: int = 104) -> str:
    """One line per checklist item, whatever the model decides to write."""
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0]
    return cut.rstrip(" ,;:—-") + "…"


def _money(v: float | int | None) -> str:
    return f"£{v:,.0f}" if v is not None else "—"


def _deadline(closes: str | None, days: int | None) -> str:
    if not closes:
        return "no deadline published"
    if days is None:
        return f"closes {closes}"
    if days < 0:
        return f"closed {closes}"
    return f"closes {closes} · {days} day{'s' if days != 1 else ''} left"


# ------------------------------------------------------------------- terminal

def to_terminal(d: Digest, ink: Ink | None = None) -> str:
    ink = ink or ink_for()
    L: list[str] = ["", ink.bold("Granted") + ink.dim(f"  ·  {d.run_date:%d %B %Y}")]
    subject = f"{d.org_name}  ·  {d.n_calls} call{'s' if d.n_calls != 1 else ''} considered"
    L += [ink.dim(subject), ink.rule(), ""]

    if d.alerts:
        L += _terminal_alerts(d, ink)
    if d.decision is None:
        if not d.alerts:
            L += _terminal_silence(d, ink)
    else:
        L += _terminal_decision(d, ink)

    if d.local_funders:
        L.append("")
        L.append(f"  {ink.bold('Funders active near you')}")
        for f in d.local_funders[:5]:
            near = (f"{f['awards_near_you']} sampled awards near you" if f.get("awards_near_you")
                    else f"{f['awards_in_region']} sampled awards in your region")
            L += _wrap(ink.dim(f"{f['name']} · {near}"), indent=6)

    if d.trace.get("lookups"):
        L.append("")
        L.append(f"  {ink.bold('What it checked')}")
        for lookup in d.trace["lookups"][:6]:
            L += _wrap(ink.dim(lookup), indent=6)

    L.append("")
    L.append(ink.rule())
    cost = ""
    if d.trace.get("model_calls"):
        cost = (f"  ·  {d.trace['model_calls']} model calls, "
                f"{d.trace.get('tool_calls', 0)} lookups, {d.trace.get('duration_s', 0)}s")
    footer = f"{d.model}{cost}" + (f"  ·  {d.digest_path}" if d.digest_path else "")
    L += [ink.dim(footer), ""]
    return "\n".join(L)


def _terminal_alerts(d: Digest, ink: Ink) -> list[str]:
    """Printed above any new opportunity, always.

    Work already underway outranks work not yet started. A tool that leads with
    a shiny new call while the bid you are halfway through slips its deadline
    has its priorities backwards.
    """
    n = len(d.alerts)
    head = f"  {n} thing{'s' if n != 1 else ''} already in flight need"
    head += "s" if n == 1 else ""
    L = [ink.warn(ink.bold(head + " you.")), ""]
    for a in d.alerts:
        mark = ink.warn("!") if a.urgent else ink.dim("·")
        L.append(f"  {mark} {ink.bold(a.title)}")
        L += _wrap(a.detail, indent=6)
        if a.workspace:
            L.append(ink.dim(f"      {a.workspace}"))
        L.append("")
    return L


@dataclass
class Shared:
    """Several calls stopped for the same reason, said once."""

    reason: str
    calls: list[Considered] = field(default_factory=list)
    fit: int | None = None

    @property
    def title(self) -> str:
        funders = list(dict.fromkeys(c.funder for c in self.calls))
        named = ", ".join(shorten(f, 48) for f in funders[:3])
        rest = len(funders) - 3
        more = f" and {rest} other funder{'s' if rest != 1 else ''}" if rest > 0 else ""
        return f"{len(self.calls)} calls from {named}{more}"


def fold(considered: list[Considered], min_shared: int = 3) -> list[Considered | Shared]:
    """Say a shared reason once.

    Ninety live calls whose funders have no known award history are one fact,
    not ninety lines. A reason shared by `min_shared` or more calls folds into a
    single entry, placed where the first of them stood; every other call keeps
    its own line and its own score.
    """
    counts: dict[str, int] = {}
    for c in considered:
        counts[c.reason] = counts.get(c.reason, 0) + 1
    out: list[Considered | Shared] = []
    folded: dict[str, Shared] = {}
    for c in considered:
        if counts[c.reason] < min_shared:
            out.append(c)
        elif c.reason in folded:
            folded[c.reason].calls.append(c)
        else:
            folded[c.reason] = Shared(c.reason, [c])
            out.append(folded[c.reason])
    return out


def _terminal_silence(d: Digest, ink: Ink) -> list[str]:
    """The common case, and the one that has to feel deliberate."""
    L = [ink.bold("  Nothing to report."), ""]
    L += _wrap("No open call clears the bar today. What was looked at, and why "
               "each one stops here:", indent=2)
    L.append("")
    for c in fold(d.considered):
        marker = ink.dim("·")
        score = f"{c.fit}/100" if c.fit is not None else "—"
        L.append(f"  {marker} {ink.bold(c.title)}")
        L.append(f"      {ink.dim(score.ljust(8))} {c.reason}")
    if d.considered:
        L.append("")
    L += _wrap(f"The threshold is {d.threshold}/100. Nothing here is close enough "
               "to be worth thirty hours.", indent=2)
    if d.archive:
        L.append("")
        L += _wrap(d.archive.line, indent=2)
    if d.unreadable:
        L.append("")
        L += _wrap("ORG.md did not give a readable " + ", ".join(d.unreadable)
                   + ". Those terms were skipped rather than guessed.", indent=2)
    return L


def _terminal_decision(d: Digest, ink: Ink) -> list[str]:
    dec = d.decision
    L = [ink.accent(ink.bold("  One decision.")), ""]
    L.append(f"  {ink.bold(dec.title)}")

    facts = [f"fit {dec.fit}/100"]
    if dec.suggested_ask is not None:
        facts.append(f"ask {_money(dec.suggested_ask)}")
    facts.append(_deadline(dec.closes, dec.days_left))
    L.append("  " + ink.dim(" · ".join(facts)))
    L.append("")

    if dec.why:
        L.append(f"  {ink.bold('Why this one')}")
        for reason in dec.why:
            L += _wrap(reason, indent=6)
        L.append("")

    if dec.own_record:
        L.append(f"  {ink.bold('Your own record')}")
        for reason in dec.own_record:
            L += _wrap(reason, indent=6)
        if d.archive:
            L += _wrap(ink.dim(d.archive.line), indent=6)
        L.append("")

    angle = prose(dec.direction)
    if angle:
        L.append(f"  {ink.bold('The angle')}")
        L += _wrap(angle, indent=6)
        L.append("")

    if dec.gaps:
        L.append(f"  {ink.warn(ink.bold('Before you start'))}")
        L += _wrap("They will ask for these, and ORG.md does not evidence them:",
                   indent=6)
        for gap in dec.gaps:
            L += _wrap(f"— {gap}", indent=6)
        L.append("")

    if dec.timeline:
        L.append(f"  {ink.bold('Time')}")
        L += _wrap(dec.timeline, indent=6)
        for item in dec.timeline_steps:
            L += _wrap(shorten(item), indent=6)
        L.append("")

    if dec.question:
        label = "Drafted against a generic question" if dec.question_is_generic \
            else "Drafted against"
        L.append(f"  {ink.bold(label)}")
        L += _wrap(dec.question, indent=6)
        if dec.question_is_generic:
            L += _wrap("This call publishes no scored questions, so that one is "
                       "ours, not the funder's. Replace it with theirs before "
                       "you write.", indent=6)
        L.append("")

    if dec.voice_checked:
        if dec.voice_flags:
            L.append(f"  {ink.warn(ink.bold('Voice'))}")
            L += _wrap("Written against your measured style, but the post-pass "
                       "caught these:", indent=6)
            for flag in dec.voice_flags:
                L += _wrap(f"— {flag}", indent=6)
        else:
            L.append(f"  {ink.bold('Voice')}")
            L += _wrap("Written against your measured style. No machine tells, "
                       "no uniformity warning.", indent=6)
        L.append("")

    if dec.total_claims:
        rate = dec.grounded / dec.total_claims if dec.total_claims else 1.0
        note = f"{dec.grounded} of {dec.total_claims} claims traced to a source ({rate:.0%})"
        L.append(f"  {'Evidence'.ljust(LABEL)}{note}")

    # Two independent checks. The lookup above re-derives every source line from
    # the file on disk; the auditor is a separate agent reading the draft against
    # ORG.md. They can disagree, and when they do that is the useful signal --
    # one mechanism agreeing with itself proves nothing.
    if dec.audit_verdict:
        mark = ink.warn if dec.audit_verdict == "revise" else ink.dim
        detail = f"auditor says {dec.audit_verdict}"
        if dec.audit_rate is not None:
            detail += f", {dec.audit_rate:.0%} grounded"
        L.append(f"  {'Audit'.ljust(LABEL)}{mark(detail)}")
        for claim in dec.audit_ungrounded[:3]:
            L += _wrap(f"— {claim}", indent=15)
    if dec.workspace:
        L.append(f"  {'Workspace'.ljust(LABEL)}{dec.workspace}")

    if d.considered:
        L.append("")
        n = len(d.considered)
        L += _wrap(f"The other {n} call{'s' if n != 1 else ''} did not clear the "
                   "gate. Listed in the digest.", indent=2)
    return L


# ----------------------------------------------------------------------- json

def to_json(d: Digest) -> dict:
    """The same digest, for machines.

    The markdown is for a person and the terminal is for right now; this is what
    the dashboard reads, and what anyone could point a spreadsheet at later. It
    is a third rendering of one structure, not a second source of truth.
    """
    return {
        "org": d.org_name,
        "run_date": d.run_date.isoformat(),
        "model": d.model,
        "threshold": d.threshold,
        "calls_considered": d.n_calls,
        "unreadable": list(d.unreadable),
        "model_calls": d.trace.get("model_calls"),
        "tool_calls": d.trace.get("tool_calls"),
        "duration_s": d.trace.get("duration_s"),
        "trace": dict(d.trace),
        "archive": ({"confidence": d.archive.confidence, "n_decided": d.archive.n_decided,
                     "n_awarded": d.archive.n_awarded, "win_rate": d.archive.win_rate,
                     "note": d.archive.line} if d.archive else None),
        "decision": _decision_json(d.decision),
        "considered": [
            {
                "funder": c.funder, "programme": c.programme, "title": c.title,
                "fit": c.fit, "reason": c.reason, "days_left": c.days_left,
                "stopped_at": c.stage,
            }
            for c in d.considered
        ],
        "alerts": [
            {"title": a.title, "kind": a.kind, "detail": a.detail,
             "urgent": a.urgent, "workspace": a.workspace}
            for a in d.alerts
        ],
        "place": d.place,
        "local_funders": list(d.local_funders),
    }


def _decision_json(dec: Decision | None) -> dict | None:
    if dec is None:
        return None
    return {
        "funder": dec.funder, "programme": dec.programme, "title": dec.title,
        "fit": dec.fit, "suggested_ask": dec.suggested_ask,
        "closes": dec.closes, "days_left": dec.days_left,
        "why": list(dec.why), "gaps": list(dec.gaps),
        "direction": dec.direction,
        "question": dec.question, "question_is_generic": dec.question_is_generic,
        "timeline": dec.timeline, "timeline_steps": list(dec.timeline_steps),
        "hours_estimate": dec.hours_estimate,
        "own_record": list(dec.own_record),
        "voice_checked": dec.voice_checked, "voice_flags": list(dec.voice_flags),
        "grounded": dec.grounded, "total_claims": dec.total_claims,
        "audit": ({"verdict": dec.audit_verdict, "rate": dec.audit_rate,
                   "ungrounded": list(dec.audit_ungrounded)} if dec.audit_verdict else None),
        "workspace": dec.workspace,
    }


# ------------------------------------------------------------------- markdown

def to_markdown(d: Digest) -> str:
    """Same content, no ANSI. Written to disk and safe to paste into an email."""
    L = [f"# Granted — {d.run_date:%d %B %Y}", "",
         f"**{d.org_name}** · {d.n_calls} call{'s' if d.n_calls != 1 else ''} considered", ""]

    if d.alerts:
        L += ["## Already in flight", ""]
        for a in d.alerts:
            flag = "**urgent** — " if a.urgent else ""
            L.append(f"- **{a.title}** — {flag}{a.detail}")
            if a.workspace:
                L.append(f"  - `{a.workspace}`")
        L.append("")

    if d.decision is None and not d.alerts:
        L += ["## Nothing to report", "",
              "No open call clears the bar today. What was looked at, and why each "
              "one stops here:", ""]
        for c in fold(d.considered):
            score = f"{c.fit}/100" if c.fit is not None else "—"
            L.append(f"- **{c.title}** — {score} · {c.reason}")
        L += ["", f"The threshold is {d.threshold}/100."]
    elif d.decision is not None:
        dec = d.decision
        L += ["## One decision", "", f"### {dec.title}", ""]
        rows = [("Fit", f"{dec.fit}/100"),
                ("Suggested ask", _money(dec.suggested_ask)),
                ("Deadline", _deadline(dec.closes, dec.days_left))]
        if dec.hours_estimate:
            rows.append(("Estimated effort", f"{dec.hours_estimate:.0f} hours"))
        if dec.total_claims:
            rate = dec.grounded / dec.total_claims
            rows.append(("Evidence", f"{dec.grounded}/{dec.total_claims} claims cited ({rate:.0%})"))
        if dec.audit_verdict:
            v = f"{dec.audit_verdict}"
            if dec.audit_rate is not None:
                v += f" · {dec.audit_rate:.0%} grounded"
            rows.append(("Independent audit", v))
        if dec.workspace:
            rows.append(("Workspace", f"`{dec.workspace}`"))
        L += ["| | |", "| --- | --- |"]
        L += [f"| {k} | {v} |" for k, v in rows]
        L.append("")

        if dec.question:
            L += ["**Drafted against**", "", f"> {dec.question}", ""]
            if dec.question_is_generic:
                L += ["This call publishes no scored questions, so that one is ours, "
                      "not the funder's. Replace it with theirs before you write.", ""]
        if dec.why:
            L += ["**Why this one**", ""] + [f"- {r}" for r in dec.why] + [""]
        angle = prose(dec.direction, max_paragraphs=3, max_chars=900)
        if angle:
            L += ["**The angle**", "", angle, ""]
        if dec.own_record:
            L += ["**Your own record**", ""] + [f"- {r}" for r in dec.own_record]
            if d.archive:
                L += ["", f"_{d.archive.line}_"]
            L.append("")
        if dec.voice_checked:
            L += ["**Voice**", ""]
            if dec.voice_flags:
                L += ["Written against your measured style. The post-pass caught:", ""]
                L += [f"- {f}" for f in dec.voice_flags] + [""]
            else:
                L += ["Written against your measured style. No machine tells, no "
                      "uniformity warning.", ""]
        if dec.gaps:
            L += ["**Before you start**", "",
                  "They will ask for these, and ORG.md does not evidence them:", ""]
            L += [f"- {g}" for g in dec.gaps] + [""]
        if dec.timeline:
            L += ["**Time**", "", dec.timeline, ""]
            L += [f"- {shorten(s, 110)}" for s in dec.timeline_steps] + ([""] if dec.timeline_steps else [])
        if d.considered:
            L += ["## Not surfaced", ""]
            for c in fold(d.considered):
                score = f"{c.fit}/100" if c.fit is not None else "—"
                L.append(f"- **{c.title}** — {score} · {c.reason}")
            L.append("")

    if d.local_funders:
        L += ["## Funders active near you", "",
              f"From their published award histories, these fund organisations near "
              f"{d.place or 'you'}. Their open calls are on their own websites.", ""]
        for f in d.local_funders:
            if f.get("awards_near_you"):
                bits = [f"{f['awards_near_you']} of {f['sample']} sampled awards near you"]
            else:
                bits = [f"{f['awards_in_region']} of {f['sample']} sampled awards in your region"]
            if f.get("median_award"):
                bits.append(f"typically £{f['median_award']:,.0f}")
            link = f.get("website") or f.get("grantnav")
            L.append(f"- **{f['name']}** — " + ", ".join(bits) + (f" · {link}" if link else ""))
        L.append("")
    if d.archive and d.decision is None:
        L += [f"_{d.archive.line}_", ""]
    if d.unreadable:
        L += ["## Unread fields", "",
              "ORG.md did not give a readable " + ", ".join(d.unreadable)
              + ". Those terms were skipped rather than guessed.", ""]

    if d.place_note:
        L += [f"_{d.place_note}_", ""]
    L += ["---", "", f"Model `{d.model}`. Grant data from 360Giving, CC-BY-SA."]
    return "\n".join(L)
