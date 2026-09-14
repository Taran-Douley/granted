"""One run. Read the calls, decide, and in almost every case say nothing.

    python -m granted.run --funder GB-CHC-1091263 --org ORG.md \\
        --calls calls.json [--archive fixtures/easton-funding] [--out .]
    python -m granted.run --calls find-a-grant --funder-ids funders.json \\
        --org ORG.md [--archive fixtures/easton-funding] [--out .]

Single-shot by design. A daemon that holds state between runs is a different and
larger claim; a cron entry plus a persistent register on disk gets the same
behaviour and can be reasoned about.

The order below is deliberate and is mostly about not spending money or a
person's attention. Triage is arithmetic over a funder's award history and costs
nothing, so it runs first and settles most calls on its own. Only what survives
triage reaches a model. Only what clears the gate reaches a human.

    calls -> triage (free)  -> matcher (one model call) -> gate -> the rest
              |                   |                          |
              `-- most stop here  `-- some stop here         `-- silence

Every run leaves three records in --out: `digests/<date>.md` for a person,
`digests/<date>.json` for the folder's own dashboard, and `data/run.json` for the
published one.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

from . import dashboard
from . import digest as run_record
from . import home as home_mod
from . import paperwork
from . import sources
from . import criteria, local, location
from .funder_tools import funder_tools
from . import org as org_mod
from . import voice
from . import render, workspace
from .archive import load_archive
from .citations import (GRANTNAV_ORG, CitedClaim, CitedDraft, Source,
                        find_support, index_org_file)
from .config import model_id, provider
from .digest import Verdict
from .graph import FIT_THRESHOLD, _structured, build_graph, gate_passes
from .precedent import ThreeSixtyGivingError, for_call, profile_funder
from .render import Alert, Archive, Considered, Decision, Digest
from .trace import Recorder, RunTrace
from .schemas import Draft, GroundingAudit, Match, Timeline
from .triage import Priority, score_alignment, triage
from .watcher import (SURFACING, Change, Opportunity, Register,
                      check_timeline, drift_change)

# Triage below this is not worth a model call. Deliberately under the gate's own
# threshold: triage is a coarse pre-filter and should not pre-empt the matcher on
# anything borderline.
TRIAGE_FLOOR = 40

# The one value of --calls that is not a file.
FIND_A_GRANT = "find-a-grant"

# Funder ids verified against 360Giving, shipped with every install and used
# when a run names no mapping of its own.
SHIPPED_FUNDER_IDS = Path(__file__).with_name("data") / "funders.json"


class CallsError(Exception):
    """A calls file that cannot be judged as written."""


def load_calls(path: Path, default_funder: str | None = None) -> list[Opportunity]:
    """Read open calls from JSON.

    Each call is judged against its own funder's award history. A row may carry
    that funder's 360Giving id as `funder_id`; a row without one takes the run's
    `--funder`, which is how a single-funder calls file has always worked. With
    neither, the call is judged on its stated criteria.
    """
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        raw = raw.get("calls", [])
    if not isinstance(raw, list):
        raise CallsError(f"{path} should hold a list of calls, or {{\"calls\": [...]}}")
    fields = set(Opportunity.__dataclass_fields__)
    calls = [Opportunity(**{k: v for k, v in row.items() if k in fields}) for row in raw]
    for call in calls:
        # No id and no --funder: most likely a local trust's call added by hand.
        # It is judged on what it says, never against a guessed funder's history.
        call.funder_id = call.funder_id or default_funder
    return calls


def read_calls(args: argparse.Namespace) -> tuple[list[Opportunity], dict]:
    """Calls from every source `--calls` names, comma-separated: find-a-grant,
    national-lottery, innovate-uk, or a calls file. Returns (calls, a count or
    an error per source).

    A live source that cannot be read is reported and skipped, so one site
    being down does not silence the others; only when every source fails does
    the run stop. Live calls never borrow `--funder`: scoring a Ministry of
    Justice call against a charitable trust's award history would produce a
    number with no meaning. A call listed by two sources is judged once.
    """
    report: dict = {}
    calls: list[Opportunity] = []
    titles: dict[str, tuple[str, str]] = {}
    ids = None
    failure: sources.SourceError | None = None
    for token in [t.strip() for t in str(args.calls).split(",") if t.strip()]:
        if token in sources.LIVE:
            if ids is None:
                ids = sources.load_funder_ids(
                    Path(args.funder_ids) if args.funder_ids else SHIPPED_FUNDER_IDS)
            try:
                found = sources.fetch_source(token, ids)
            except sources.SourceError as e:
                print(f"{token}: {e}", file=sys.stderr)
                report[token] = f"unreadable: {e}"
                failure = e
                continue
        else:
            found = load_calls(Path(token), default_funder=args.funder)
        report[token] = len(found)
        for opp in found:
            title = re.sub(r"[^a-z0-9]+", " ", (opp.programme or "").lower()).strip()
            funder = re.sub(r"[^a-z0-9]+", " ", opp.funder.lower()).strip()
            first = titles.get(title)
            # The same competition on Innovate UK and on Find a grant, or one
            # listing published twice: the first one read is the one judged.
            if title and first is not None and (first[0] != opp.source or first[1] == funder):
                continue
            titles.setdefault(title, (opp.source, funder))
            calls.append(opp)
    if failure is not None and not calls and all(isinstance(v, str) for v in report.values()):
        raise failure
    return calls, report


class _Funders:
    """Award history, pulled once per funder per run.

    360Giving is rate limited to two requests a second and a profile is a few
    hundred grants, so a funder shared by several calls is fetched once. A
    failure is remembered too, rather than retried for every call it covers.
    """

    def __init__(self, cap: int) -> None:
        self.cap = cap
        self._seen: dict[str, tuple | ThreeSixtyGivingError] = {}

    def get(self, funder_id: str) -> tuple:
        if funder_id not in self._seen:
            try:
                self._seen[funder_id] = profile_funder(funder_id, cap=self.cap,
                                                       with_grants=True)
            except ThreeSixtyGivingError as e:
                self._seen[funder_id] = e
        found = self._seen[funder_id]
        if isinstance(found, ThreeSixtyGivingError):
            raise found
        return found

    def prefetch(self, funder_ids) -> None:
        """Pull several histories at once. 360Giving's rate limit still holds (the
        client shares one limiter), but its slow responses overlap."""
        todo = [f for f in dict.fromkeys(funder_ids) if f not in self._seen]
        if len(todo) < 2:
            return
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(self._quietly, todo))

    def _quietly(self, funder_id: str) -> None:
        try:
            self.get(funder_id)
        except ThreeSixtyGivingError:
            pass            # remembered in _seen, and reported when the call is judged


def compose_task(brief: str, org_text: str, opp: Opportunity, ask: float | None,
                 today: date | None = None, question: str | None = None,
                 stated: bool = False) -> str:
    """The prompt every node in the graph receives.

    Strands hands each node the original task, not its predecessor's output, so
    this has to carry everything the whole graph needs: the funder's revealed
    preferences, the call, and ORG.md in full. Summarising ORG.md here would
    defeat the point of the auditor, which checks claims against its verbatim
    lines.
    """
    question = question or question_for(opp)
    return "\n\n".join([
        ("FUNDER (no published award history; judge only on the call's stated criteria):"
         if stated else "FUNDER (revealed preferences, computed from published award history):"),
        brief,
        "THE CALL:",
        f"{opp.funder}" + (f" — {opp.programme}" if opp.programme else "")
        + (f"\nCloses: {opp.closes}" if opp.closes else
           "\nCloses: no closing date. This is a rolling programme that takes applications "
           "at any time, so the deadline is not a reason to hold back")
        + _window(opp, today)
        + (f"\nMaximum award: £{opp.amount_max:,.0f}" if opp.amount_max else "")
        + (f"\nWhere: {', '.join(opp.locations)}" if opp.locations else "")
        + (f"\nWho can apply: {opp.eligibility[:300]}" if opp.eligibility else "")
        + (f"\n{opp.summary}" if opp.summary else ""),
        "ORG.md (the organisation's own file, verbatim — every factual claim "
        "about the organisation must trace to a line in here):",
        org_text,
        f"The organisation is considering asking for £{ask:,.0f}." if ask else "",
        # Funder-wide figures come from a sample of every programme the funder
        # runs. Read without this line, a sample of £10,000 Awards for All
        # grants talked the model out of Reaching Communities at £20,001 and up.
        (f"This programme's own stated award range is "
         f"{'£' + format(opp.amount_min, ',.0f') if opp.amount_min else 'unstated'} to "
         f"{'£' + format(opp.amount_max, ',.0f') if opp.amount_max else 'unstated'}. Where it "
         "differs from the funder-wide figures above, the programme's own range applies."
         if (opp.amount_min or 0) >= 100 or (opp.amount_max or 0) >= 100 else ""),
        "QUESTION TO ANSWER (the drafter answers this one and no other):",
        question,
        "QUESTION: Should this organisation apply, and if so for how much?",
    ]).strip()


# Used only when a call publishes no scored questions of its own. Labelled as
# generic wherever it appears, because presenting an invented question as the
# funder's would be exactly the kind of quiet fabrication this project exists to
# avoid.
GENERIC_QUESTION = ("Describe the difference this funding will make, who it will "
                    "reach, and how you will evidence the outcomes.")


def question_for(opp: Opportunity) -> str:
    return opp.questions[0] if opp.questions else GENERIC_QUESTION


def _window(opp: Opportunity, today: date | None) -> str:
    """Tell the timeliner what day it is.

    Without this it works back from the deadline against an imagined start and
    reports every step overdue on a call that opened last week — which reads as
    a broken tool, not a tight deadline.
    """
    days = opp.days_left(today or date.today())
    if days is None:
        return ""
    return (f"\nToday is {(today or date.today()).isoformat()}, so there are "
            f"{days} days left. Every step must fall inside that window: no step "
            f"may be scheduled more than {days} days before the deadline.")


def run(args: argparse.Namespace) -> Digest:
    today = date.today()
    trace = RunTrace()
    profile = org_mod.load(Path(args.org))
    org_dict = profile.as_triage_dict()

    insight, style = None, None
    if args.archive:
        subs, insight = load_archive(Path(args.archive))
        # Measured from their own past applications, never assumed. With no
        # archive there is no card, and the drafter is simply told nothing about
        # voice rather than being handed a house style that is not theirs.
        style = voice.build_style_card(subs)

    calls, source_report = read_calls(args)
    # Where the organisation is, in the terms calls use. Looked up once and kept.
    place = location.resolve(profile, cache=Path(args.out) / ".granted" / "location.json")
    funders = _Funders(cap=args.cap)
    register = Register(Path(args.out) / ".granted" / "register.json")
    # Every history a call will need, a few at a time, before any is needed.
    funders.prefetch(o.funder_id for o in calls
                     if o.funder_id and not criteria.gatekeep(o, profile, place))

    # Before anything new is considered: is anything already underway slipping?
    flight: dict = {}
    alerts = in_flight_alerts(register, today, record=flight)

    digest = Digest(
        org_name=profile.name or "Your organisation",
        run_date=today,
        model=model_id(),
        threshold=FIT_THRESHOLD,
        unreadable=profile.unreadable,
        archive=(Archive(confidence=insight.confidence, n_decided=insight.n_decided,
                         n_awarded=insight.n_awarded, win_rate=insight.win_rate)
                 if insight else None),
    )

    graphs: dict = {}
    verdicts: dict[str, Verdict] = {}
    best: tuple[int, Decision, dict, Opportunity] | None = None

    for opp in calls:
        # Already committed to? Then the decision has been made and delivered,
        # and repeating it daily is the spam this project exists not to be. From
        # here the only thing worth saying is whether it is on track, which
        # in_flight_alerts has already answered. The exception is a moved
        # deadline: that invalidates the schedule, so the graph runs again to
        # reissue one.
        known = register.items.get(opp.key)
        if known is not None and known.workspace and known.closes == opp.closes:
            continue

        # Where it applies and who may apply: free, first, and whatever the
        # basis, because a Scotland-only programme is closed to a London charity
        # however well its funder's history fits.
        closed = criteria.gatekeep(opp, profile, place)
        if closed:
            _stop(digest, verdicts, Verdict(opp, "triage", reason=closed), today)
            continue

        prefs = grants = None
        note = None
        if opp.funder_id:
            try:
                prefs, grants = funders.get(opp.funder_id)
            except ThreeSixtyGivingError:
                # The run's own funder failing is fatal, as it always was: every
                # call in a single-funder file depends on it. One funder among
                # many falls back to what its call says.
                if opp.funder_id == args.funder:
                    raise
                note = "360Giving could not supply this funder's award history today"

        ask = float(args.ask) if args.ask else (prefs.median_award if prefs else None)
        if ask and not args.ask:
            # A funder-wide median can sit outside this programme's own range:
            # the Lottery's is an Awards for All grant, and Reaching Communities
            # starts at £20,001. Ask inside the range the call states.
            if (opp.amount_min or 0) >= 100 and ask < opp.amount_min:
                ask = opp.amount_min
            if (opp.amount_max or 0) >= 100 and ask > opp.amount_max:
                ask = opp.amount_max
        if prefs is not None:
            verdict = triage(opp, prefs, org_dict, insight=insight, ask=ask, today=today)
            # About something else, from a funder that rarely funds charities:
            # the Department for Transport's electric car grant. Free to stop.
            verdict = criteria.cap_off_topic(verdict, opp, profile, prefs)
        else:
            # No award history to read: judge the call on what it says, and say so.
            verdict = criteria.assess(opp, profile, place, insight=insight, today=today)

        # Free rejection. No model call, no tokens, no attention.
        if verdict.vetoed or verdict.composite < TRIAGE_FLOOR:
            reason = verdict.vetoed or (criteria.stop_reason(verdict, TRIAGE_FLOOR)
                                        if verdict.basis == criteria.STATED
                                        else _first_reason(verdict))
            if note:
                reason += f" ({note}, so it was judged on the call's stated criteria)"
            _stop(digest, verdicts, Verdict(
                opp, "triage", verdict, int(verdict.composite), reason), today)
            continue

        # One graph per funder: its lookup tools are bound to that funder's
        # grants. Calls judged on stated criteria share one graph with no tools.
        # A programme whose stated range lies outside its funder's sample sees no
        # funder-wide award sizes, and no tool that would show them.
        shown, view = for_call(prefs, opp) if prefs is not None else (None, criteria.STATED)
        key = (opp.funder_id, view) if prefs is not None else criteria.STATED
        if key not in graphs:
            _tools = funder_tools(grants, prefs) if prefs is not None else []
            if view == "no-amounts":
                _tools = [t for t in _tools if t.tool_name != "awards_near_amount"]
            graphs[key] = build_graph(
                style=style.prompt_block() if style else None, tools=_tools,
                hooks=[Recorder(trace, {t.tool_name for t in _tools})])
        graph = graphs[key]
        question = args.question or question_for(opp)
        brief = shown.brief() if prefs is not None else criteria.brief(opp, verdict, note)
        result = graph(compose_task(brief, profile.raw, opp, ask, today, question,
                                    stated=prefs is None))
        match = _structured(result.results.get("matcher"), Match)

        if match is None:
            _stop(digest, verdicts, Verdict(
                opp, "matcher", verdict, None,
                "the matcher returned no structured verdict"), today)
            continue

        opp.fit_score = match.fit_score

        if not gate_passes(result):
            _stop(digest, verdicts, Verdict(
                opp, "gate", verdict, match.fit_score, _silence_reason(match)), today)
            continue

        sink: dict = {}
        decision = _build_decision(result, match, opp, verdict, today,
                                   args, profile, prefs, question, style, sink=sink, ask=ask)
        verdicts[opp.key] = Verdict(opp, "surfaced", verdict, match.fit_score)
        if best is None or match.fit_score > best[0]:
            if best is not None:
                _hold_back(digest, verdicts, best[1], best[3])
            best = (match.fit_score, decision, sink, opp)
        else:
            _hold_back(digest, verdicts, decision, opp)

    produced: dict = {}
    if best:
        digest.decision = best[1]
        produced = best[2]

    # Poll last, so the fit scores the matcher just produced are on the
    # opportunities the register is diffing.
    # Only a source read today can say a call has gone; one that was down cannot.
    read_ok = {token if token in sources.LIVE else "" for token, found in source_report.items()
               if not isinstance(found, str)}
    polled = register.poll(calls, today=today, min_fit=FIT_THRESHOLD, sources=read_ok)
    for change in polled:
        if change.kind in ALERT_KINDS:
            alerts.append(_alert_of(change))

    digest.alerts = sorted(alerts, key=lambda a: not a.urgent)
    # Snapshot the trace here, not at construction: the Digest is built before
    # the graph runs, so reading it earlier records a run that has not happened.
    digest.trace = trace.as_dict()
    register.save()

    # Funders active near you: organisations' own folders only, rebuilt weekly.
    nearby = (local.refresh(place, Path(args.out) / ".granted" / "local-funders.json")
              if getattr(args, "local", False) else [])
    digest.local_funders = nearby
    digest.place = place.describe() if place else None
    if place and getattr(args, "local", False):
        digest.place_note = (f"Location: {place.describe()}, from "
                             + ("your postcode." if place.source.startswith("postcode")
                                else "the area you serve in ORG.md. A postcode there "
                                     "gives a more precise match."))

    dec = digest.decision
    digest.record = run_record.build(
        list(verdicts.values()), today=today, insight=insight,
        changes=flight.get("changes", []) + list(polled),
        register_items=list(register.items.values()),
        in_flight=flight.get("statuses", []),
        style=style, voice_report=produced.get("voice"), draft=produced.get("cited"),
        timeline_plan=produced.get("timeline"), audit=produced.get("audit"),
        workspace_path=produced.get("workspace"), org=profile.name, org_id=profile.org_id,
        direction=dec.direction if dec else None,
        grounded=(dec.grounded or 0) if dec else 0,
        total_claims=(dec.total_claims or 0) if dec else 0,
        gaps=len(dec.gaps) if dec else 0,
        trace=digest.trace, threshold=FIT_THRESHOLD, model=model_id(),
        provider=provider(), calls_source=args.calls, sources=source_report,
        place=digest.place, local_funders=nearby)
    return digest


def _stop(digest: Digest, verdicts: dict[str, Verdict], v: Verdict, today: date) -> None:
    """A call that goes no further: said once in the digest, once in the run record."""
    o = v.opportunity
    verdicts[o.key] = v
    digest.considered.append(Considered(
        funder=o.funder, programme=o.programme, fit=v.fit, reason=v.reason,
        days_left=o.days_left(today), stage=v.stage))


def _hold_back(digest: Digest, verdicts: dict[str, Verdict],
               decision: Decision, opp: Opportunity) -> None:
    held = _demote(decision)
    digest.considered.append(held)
    verdicts[opp.key].stage, verdicts[opp.key].reason = "gate", held.reason


# triage records reasons without polarity, so a call rejected on score can carry
# "funder has backed GB-CHC organisations (43% of awards)" as its first reason —
# a point in the org's favour, printed as the grounds for refusal. Match the
# phrasings that are actually disqualifying and prefer those; when none is, say
# the score plainly rather than dressing up a positive as a negative.
_DISQUALIFYING = (
    "has never funded", "no recorded awards", "outside their usual",
    "wrong funder", "has declined you", "below", "no outcome history",
    "smaller than", "larger than", "rests on funder data alone",
    "nothing in the call matches",
)


# A call that is merely new is already reported as the decision; alerting on it
# too would say the same thing twice. Alerts are for what has changed under work
# already committed to.
ALERT_KINDS = SURFACING - {"new"}


def in_flight_alerts(register: Register, today: date,
                     record: dict | None = None) -> list[Alert]:
    """Check every bid already underway against the calendar.

    An opportunity is in flight once a workspace exists for it, and the schedule
    the timeliner produced is sitting in that workspace. So this re-reads the
    plan off disk and reconciles it with today. `05-timeline/done.txt`, one task
    per line, is how a human says what is finished — the same thirty-second
    convention as the outcome stub, and the same reason: state a person maintains
    by hand beats state a tool infers wrongly.

    `record`, when given, collects every status and change for the run record,
    including the bids that are on track and so raise no alert.
    """
    alerts: list[Alert] = []
    for opp in register.items.values():
        if not opp.workspace or not opp.closes:
            continue
        schedule = Path(opp.workspace) / "05-timeline" / "schedule.json"
        if not schedule.exists():
            continue
        try:
            timeline = Timeline.model_validate_json(schedule.read_text(encoding="utf-8"))
        except Exception:                                        # noqa: BLE001
            continue

        done_file = Path(opp.workspace) / "05-timeline" / "done.txt"
        done = set()
        if done_file.exists():
            done = {line.strip() for line in
                    done_file.read_text(encoding="utf-8").splitlines() if line.strip()}

        steps = [{"days_before_deadline": s.days_before_deadline, "task": s.task}
                 for s in timeline.steps]
        status = check_timeline(opp.closes, steps, done=done, today=today)
        change = drift_change(opp, status)
        if record is not None:
            record.setdefault("statuses", []).append(status)
            if change is not None:
                record.setdefault("changes", []).append(change)
        if change is not None:
            alerts.append(_alert_of(change))
    return alerts


def _alert_of(change: Change) -> Alert:
    opp = change.opportunity
    title = f"{opp.funder}" + (f" — {opp.programme}" if opp.programme else "")
    return Alert(title=title, kind=change.kind, detail=change.detail,
                 urgent=change.urgent, workspace=opp.workspace)


def _first_reason(verdict) -> str:
    reasons = list(verdict.alignment.reasons) + list(verdict.plausibility.reasons)
    for reason in reasons:
        if any(marker in reason.lower() for marker in _DISQUALIFYING):
            return reason
    return (f"scores {verdict.composite:.0f}/100 against this funder's award "
            f"history, below the {TRIAGE_FLOOR} needed to be worth a model call")


def _silence_reason(match: Match) -> str:
    if match.blocking:
        return match.blocking[0]
    # What stands in the way, in the matcher's own words, beats the opening of
    # its reasoning, which often starts with what does fit.
    if not match.recommend_apply and match.eligibility_gaps:
        return f"the matcher recommends not applying: {match.eligibility_gaps[0]}"
    if not match.recommend_apply:
        # Its reasoning says why; a bare "recommends not applying" says nothing.
        first = re.split(r"(?<=[.!?])\s", (match.reasoning or "").strip(), maxsplit=1)[0]
        return (f"the matcher recommends not applying: {first}" if first
                else "the matcher recommends not applying")
    return f"fit {match.fit_score}/100 is below the {FIT_THRESHOLD} threshold"


def _demote(decision: Decision) -> Considered:
    """A second qualifying call is still not a second thing to do today."""
    return Considered(
        funder=decision.funder, programme=decision.programme, fit=decision.fit,
        reason="also clears the gate, held back so today has one decision",
        days_left=decision.days_left, stage="gate")


def _build_decision(result, match: Match, opp: Opportunity, verdict,
                    today: date, args, profile, prefs,
                    question: str, style=None, sink: dict | None = None,
                    ask: float | None = None) -> Decision:
    """Everything the gate opens, folded into one Decision.

    `sink`, when given, receives the structured pieces the run record needs and
    the Decision does not keep: the timeline, the audit, the voice report, the
    cited draft and the workspace path.
    """
    sink = {} if sink is None else sink
    director = result.results.get("director")
    timeline = _structured(result.results.get("timeliner"), Timeline)
    if timeline is not None:
        # The call's own date is the deadline, whatever the model wrote: for a
        # rolling programme it once wrote '<UNKNOWN>', which broke the dashboard.
        timeline.deadline = opp.closes or "rolling"
    draft = _structured(result.results.get("drafter"), Draft)
    # This node was already running and billing on every gate fire; its result
    # was simply never read.
    audit = _structured(result.results.get("auditor"), GroundingAudit)
    sink["timeline"], sink["audit"] = timeline, audit

    gaps = list(match.eligibility_gaps)
    if draft:
        gaps += [g for g in draft.unevidenced_gaps if g not in gaps]

    headline, items = None, []
    if timeline and opp.closes:
        steps = [{"days_before_deadline": s.days_before_deadline, "task": s.task}
                 for s in timeline.steps]
        status = check_timeline(opp.closes, steps, done=set(), today=today)
        headline = f"{status.days_left} days to deadline"
        if timeline.total_hours_estimate:
            headline += f", about {timeline.total_hours_estimate:.0f} hours of work"
        if not status.feasible:
            headline += ". This no longer fits the time left"
        items = [f"{s.days_before_deadline}d before — {s.task} ({s.owner_role})"
                 for s in sorted(timeline.steps,
                                 key=lambda x: -x.days_before_deadline)][:6]

    # Triage scored against the funder's median as a stand-in ask, because the
    # matcher had not chosen one yet. Now it has, so the reasons are recomputed
    # against the figure actually on screen: "£9,979 sits inside their usual
    # range" printed above "ask £2,800" is the kind of small incoherence that
    # makes a reader stop trusting the rest of the page.
    reasons = verdict.alignment.reasons
    if match.suggested_ask_gbp and prefs is not None:
        stated = ((opp.amount_min if (opp.amount_min or 0) >= 100 else None),
                  (opp.amount_max if (opp.amount_max or 0) >= 100 else None))
        reasons = score_alignment(prefs, profile.as_triage_dict(),
                                  float(match.suggested_ask_gbp),
                                  stated if any(stated) else None).reasons

    decision = Decision(
        funder=opp.funder, programme=opp.programme, fit=match.fit_score,
        # The matcher does not always name an ask; the one triage used (the
        # funder's median, kept inside the programme's range) stands in.
        suggested_ask=match.suggested_ask_gbp or (int(ask) if ask else None), closes=opp.closes,
        days_left=opp.days_left(today), why=list(reasons),
        own_record=list(verdict.plausibility.reasons),
        gaps=gaps[:5], direction=_text_of(director),
        timeline=headline, timeline_steps=items,
        hours_estimate=timeline.total_hours_estimate if timeline else None,
        question=question,
        question_is_generic=not opp.questions and not args.question,
    )

    if not args.no_workspace:
        ws = workspace.create(Path(args.out) / getattr(args, "bids_dir", "workspaces"),
                              opp.funder, programme=opp.programme, deadline=opp.closes,
                              fit_score=match.fit_score,
                              friendly=getattr(args, "friendly", False))
        ws.write("00-brief", "call.md", _brief_of(opp))
        ws.write("01-decision", "match.json", match.model_dump_json(indent=2))
        if decision.direction:
            ws.write("02-direction", "angle.md", decision.direction)
        if timeline:
            ws.write("05-timeline", "schedule.json", timeline.model_dump_json(indent=2))
        if draft:
            # Runs after generation, before the draft is filed. A tell caught
            # here is a tell a funder does not read.
            report = voice.check(draft.answer, style)
            sink["voice"] = report
            decision.voice_flags = _voice_flags(report)
            decision.voice_checked = style is not None
            if style:
                ws.write("03-drafts", "style-card.md", style.prompt_block())
            ws.write("03-drafts", "answer-1.json", draft.model_dump_json(indent=2))
            cited = build_evidence(draft, profile, prefs)
            sink["cited"] = cited
            # The README's claim, made literal: footnoted for the trustee who
            # wants to check a number, clean for pasting into a funder's portal.
            ws.write("04-evidence", "claims.md", cited.render(footnotes=True))
            ws.write("03-drafts", "answer-1.md", cited.render(footnotes=False))
            decision.grounded = sum(1 for c in cited.claims if c.grounded)
            decision.total_claims = len(cited.claims)
            if audit is not None:
                decision.audit_verdict = audit.verdict
                decision.audit_rate = audit.rate
                decision.audit_ungrounded = list(audit.ungrounded)
                ws.write("04-evidence", "audit.json", audit.model_dump_json(indent=2))
                # A claim the auditor cannot ground is a gap, wherever the
                # lexical check landed.
                for claim in audit.ungrounded:
                    gap = f"auditor could not ground: {claim}"
                    if gap not in decision.gaps:
                        decision.gaps.append(gap)
                decision.gaps = decision.gaps[:6]
            for gap in cited.gaps:
                if gap not in decision.gaps:
                    decision.gaps.append(gap)
            decision.gaps = decision.gaps[:5]
        # Word copies of what a person will open, edit or forward.
        paperwork.write_bid_documents(ws.root, decision, match, sink.get("cited"), timeline)
        decision.workspace = str(ws.root)
        sink["workspace"] = ws.root
        # Without this the register never learns the bid exists, and every later
        # run rediscovers the call as new instead of tracking it as in flight.
        opp.workspace = str(ws.root)
    return decision


def _brief_of(opp: Opportunity) -> str:
    """The call as it stood on the day the gate fired.

    Funders edit and withdraw pages. Six months on, `06-submitted/outcome.meta.json`
    is only interpretable next to what was actually being applied for, so the call
    is written down rather than left as a URL that may since have changed.
    """
    lines = [f"# {opp.funder}" + (f" — {opp.programme}" if opp.programme else ""), ""]
    facts = [("Source", opp.url), ("Opens", opp.opens), ("Closes", opp.closes),
             ("Maximum award", f"£{opp.amount_max:,.0f}" if opp.amount_max else None)]
    lines += [f"- **{k}**: {v}" for k, v in facts if v]
    if opp.summary:
        lines += ["", "## As published", "", opp.summary]
    lines += ["", f"_Recorded {date.today().isoformat()}. Funders edit and withdraw "
              "pages; this is the version the decision was made against._"]
    return "\n".join(lines)


def _voice_flags(report) -> list[str]:
    """Whatever the post-pass found, phrased for the person who has to fix it."""
    flags = [f"machine tell: “{found}”" for found, _ in report.tells_found]
    if report.uniformity_warning:
        flags.append(report.uniformity_warning)
    flags += [f"wrong term for the people you serve: {v}" for v in report.term_violations]
    return flags


def build_evidence(draft: Draft, profile, prefs) -> CitedDraft:
    """Re-derive every claim's source instead of trusting the drafter's word.

    The drafter reports which ORG.md line it used. That is a claim about its own
    work, and the whole point of citing is that a trustee can check it without
    taking anyone's word. So the line is looked up again here, independently,
    against the file on disk. A claim whose source cannot be found becomes a gap
    for a human, not a footnote nobody can follow.
    """
    index = index_org_file(profile.path) if profile.path else {}
    claims, gaps = [], list(draft.unevidenced_gaps)

    for claim in draft.claims:
        source = find_support(claim.text, index) if index else None
        if source is None and claim.source_line:
            # The drafter named a line; accept it only if it is really in the file.
            source = find_support(claim.source_line, index) if index else None
        if source is None:
            gaps.append(f"unevidenced: {claim.text}")
        claims.append(CitedClaim(text=claim.text, sources=[source] if source else []))

    # The fit score rests on the funder's award history, so that gets a locator too.
    if prefs is not None and claims:
        band = []
        if prefs.median_award:
            band.append(f"median £{prefs.median_award:,.0f}")
        if prefs.award_p10 and prefs.award_p90:
            band.append(f"p10-p90 £{prefs.award_p10:,.0f}-£{prefs.award_p90:,.0f}")
        claims.append(CitedClaim(
            text=f"Suggested ask is anchored on {prefs.funder_name}'s award history "
                 f"({prefs.n_grants} grants).",
            sources=[Source.from_external(
                f"360Giving award history, {prefs.funder_name}",
                GRANTNAV_ORG.format(org_id=prefs.funder_id),
                quoted="; ".join(band) or None)]))

    return CitedDraft(question=draft.question, body=draft.answer,
                      claims=claims, gaps=gaps)


def _text_of(node_result) -> str | None:
    """Pull prose out of a node result. The director has no schema by design."""
    if node_result is None:
        return None
    for attr in ("result", "output", "message"):
        obj = getattr(node_result, attr, None)
        if isinstance(obj, str) and obj.strip():
            return obj.strip()
        text = getattr(obj, "text", None) or getattr(obj, "content", None)
        if isinstance(text, str) and text.strip():
            return text.strip()
    text = str(node_result).strip()
    return text or None


def main(argv: list[str] | None = None, prog: str | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog=prog or "granted.run",
        description="Decide whether a small organisation should apply, and usually say nothing.")
    ap.add_argument("--funder", default=None,
                    help="360Giving org id for calls that do not name their own, "
                         "e.g. GB-CHC-1091263")
    ap.add_argument("--org", default="ORG.md", help="path to ORG.md")
    ap.add_argument("--calls", default=None,
                    help=f"JSON file of open calls, or '{FIND_A_GRANT}' to read live "
                         "GOV.UK listings; taken from granted.json when omitted")
    ap.add_argument("--home", default=None,
                    help="a Granted folder made by `granted setup`; found automatically "
                         "when run inside one")
    ap.add_argument("--funder-ids", default=None,
                    help=f"with --calls {FIND_A_GRANT}: JSON mapping funder names "
                         "to 360Giving org ids (default: the verified ids shipped "
                         "with Granted)")
    ap.add_argument("--archive", default=None, help="folder of past bids (optional)")
    ap.add_argument("--out", default=".", help="where workspaces and digests go")
    ap.add_argument("--ask", default=None, help="amount under consideration")
    ap.add_argument("--cap", type=int, default=None,
                    help="max grants to pull per funder (default 500, or granted.json's cap)")
    ap.add_argument("--question", default=None,
                    help="the scored question to draft against; overrides the call's own")
    ap.add_argument("--no-workspace", action="store_true", help="decide but write no folder")
    ap.add_argument("--quiet", action="store_true", help="write the digest, print nothing")
    args = ap.parse_args(argv)

    # Credentials live outside any synced folder; use them if this machine has some.
    home_mod.load_credentials()
    home = None
    if args.home or args.calls is None:
        root = Path(args.home) if args.home else home_mod.find()
        if root is None or not (root / home_mod.CONFIG).is_file():
            ap.error("no --calls given, and no granted.json here. Run `granted setup` "
                     "first, or pass --calls.")
        home = home_mod.load(root)
        # Every path from here on is relative to the folder, so the folder can move.
        os.chdir(home.root)
        args.calls = args.calls or home.calls
        args.funder = args.funder or home.funder
        args.funder_ids = args.funder_ids or home.funder_ids
        if not args.archive and home.archive_path.is_dir():
            args.archive = str(home.archive_path)
        args.out = "."
    args.cap = args.cap or (home.cap if home else 500)
    args.bids_dir = home_mod.BIDS if home else "workspaces"
    args.friendly = home is not None
    args.local = home is not None

    try:
        digest = run(args)
    except ThreeSixtyGivingError as e:
        print(f"360Giving: {e}", file=sys.stderr)
        return 2
    except sources.FindAGrantError as e:
        print(f"Find a grant: {e}", file=sys.stderr)
        return 2
    except CallsError as e:
        print(f"calls: {e}", file=sys.stderr)
        return 2
    except FileNotFoundError as e:
        print(f"missing file: {e}", file=sys.stderr)
        return 2

    notes = home_mod.NOTES if home else "digests"
    out = Path(args.out) / notes
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{digest.run_date.isoformat()}.md"
    digest.digest_path = str(path)
    path.write_text(render.to_markdown(digest), encoding="utf-8")
    (out / f"{digest.run_date.isoformat()}.json").write_text(
        json.dumps(render.to_json(digest), indent=2), encoding="utf-8")

    # The published dashboard's data file. Written on every run, silent ones
    # included: a page that only changes when something surfaces cannot tell a
    # quiet day from a dead agent.
    if digest.record is not None:
        digest.record.write(Path(args.out) / (home_mod.RECORD if home else "data/run.json"))
        if home:
            digest.record.write_js(Path(args.out) / home_mod.RUN_JS)

    # Rebuild the folder's dashboard from whatever is now on disk.
    try:
        dashboard.build(Path(args.out), bids=args.bids_dir, digests=notes,
                        filename=home_mod.BID_FOLDERS if home else dashboard.DASHBOARD)
        if home:
            home_mod.write_dashboard(Path(args.out))
    except Exception as e:                                       # noqa: BLE001
        print(f"dashboard not rebuilt: {e}", file=sys.stderr)

    if not args.quiet:
        print(render.to_terminal(digest))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
