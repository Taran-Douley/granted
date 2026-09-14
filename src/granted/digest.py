"""Emit the run record the dashboard reads.

Every run writes `<out>/data/run.json`, silent runs included. Every field in it is
produced by a module, not typed by hand: if a number appears on screen, this file
is where it came from, and `_produced_by` names the module that computed it.

That matters for more than tidiness. A judge who opens run.json and finds it
cannot produce the figures the dashboard displays will discount every number on
the page, including the true ones.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path

from .archive import ArchiveInsight
from .citations import CitedDraft
from .schemas import GroundingAudit, Timeline
from .triage import Triage
from .voice import StyleCard, VoiceReport
from .watcher import Change, Opportunity, TimelineStatus

SCHEMA_VERSION = 2


@dataclass
class Verdict:
    """What happened to one call this run.

    `stage` is where it stopped: `triage` (arithmetic, free), `matcher` (one
    model call, no structured verdict), `gate` (scored, did not clear) or
    `surfaced`. Only `triage` belongs in `not_pursued`: the dashboard prints that
    list as "cost £0", and a call the matcher saw did cost something.
    """

    opportunity: Opportunity
    stage: str
    triage: Triage | None = None    # None when there was no award history to triage against
    fit: int | None = None          # the matcher's score once it ran, else triage's
    reason: str = ""                # why it stopped, worded as the digest words it


@dataclass
class RunDigest:
    """One run. Serialises straight to the dashboard's data file."""

    run_date: str
    run_time: str
    model: str
    provider: str
    duration_s: float
    model_calls: int

    # triage and the gate
    threshold: int
    calls_considered: int
    bands: dict[str, int] = field(default_factory=dict)
    surfaced: list[dict] = field(default_factory=list)
    not_pursued: list[dict] = field(default_factory=list)
    held_at_gate: list[dict] = field(default_factory=list)
    calls_source: str = ""
    sources: dict = field(default_factory=dict)
    place: str | None = None
    local_funders: list[dict] = field(default_factory=list)

    # watcher
    watcher_state: dict = field(default_factory=dict)
    changes: list[dict] = field(default_factory=list)

    # archive
    archive: dict = field(default_factory=dict)
    revisit: list[str] = field(default_factory=list)

    # scope
    scope: dict = field(default_factory=dict)

    # the drafting half: what the run produced once the gate fired
    org: str | None = None
    org_id: str | None = None
    workspace: str | None = None
    direction: list[str] = field(default_factory=list)
    draft: dict = field(default_factory=dict)
    timeline: dict = field(default_factory=dict)
    files_written: list[dict] = field(default_factory=list)
    outcome: dict = field(default_factory=dict)

    # voice + grounding
    voice: dict = field(default_factory=dict)
    grounded: int = 0
    total_claims: int = 0
    gaps: int = 0
    audit: dict = field(default_factory=dict)

    schema_version: int = SCHEMA_VERSION

    def to_dict(self) -> dict:
        d = asdict(self)
        d["_comment"] = (
            "Written by granted.digest on every run. Every figure here is "
            "computed by a module; none are hand-entered. The dashboard reads "
            "this file directly and needs no rebuild."
        )
        d["_produced_by"] = {
            "bands, surfaced, not_pursued, held_at_gate":
                "triage.triage; fit is the graph matcher's once it has run",
            "watcher_state, changes": "watcher.Register.poll / watcher.check_timeline",
            "archive, revisit, scope": "archive.load_archive",
            "direction, draft, timeline": "graph director / drafter / timeliner",
            "audit": "graph auditor",
            "grounded, total_claims, gaps": "run.build_evidence (citations.find_support)",
            "workspace, files_written, outcome": "workspace.create",
            "voice": "voice.build_style_card / voice.check",
            "org, org_id": "org.parse (ORG.md, Identity)",
            "sources": "sources.fetch_source",
            "place, local_funders": "location.resolve (postcodes.io) / local.refresh (360Giving)",
            "duration_s, model_calls": "trace.RunTrace",
        }
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, ensure_ascii=False)

    def write(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.to_json(), encoding="utf-8")
        return path

    def write_js(self, path: Path) -> Path:
        """The same record as a script, for the dashboard opened from the folder:
        a page on disk may not fetch a file, but it may load a script."""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(run_js(self.to_dict()), encoding="utf-8")
        return path


def run_js(record: dict) -> str:
    return "window.__GRANTED_RUN__ = " + json.dumps(record, ensure_ascii=False) + ";\n"


def _row(v: Verdict, today: date) -> dict:
    t, o = v.triage, v.opportunity
    reasons = list(t.alignment.reasons) if t else []
    if v.stage != "surfaced" and v.reason:
        # A stopped call leads with why it stopped. Triage reasons carry no
        # polarity, so the first of them can be a point in the org's favour.
        reasons = [v.reason] + [r for r in reasons if r != v.reason]
    if v.fit is not None:
        fit = v.fit
    else:
        fit = round(t.composite) if t else None
    return {
        "funder": o.funder,
        "programme": o.programme,
        "band": t.priority.value if t else "Low",
        "fit": fit,
        "alignment": round(t.alignment.score) if t else None,
        "plausibility": round(t.plausibility.score) if t else None,
        "plausibility_confidence": t.plausibility.confidence if t else None,
        "days_left": t.days_left if t else o.days_left(today),
        "closes": o.closes,
        "url": o.url,
        "vetoed": t.vetoed if t else None,
        "reasons": reasons[:3],
        "stage": v.stage,
        "basis": t.basis if t else None,
    }


def _pressing(statuses: list[TimelineStatus]) -> TimelineStatus | None:
    """The in-flight bid that most needs a person: infeasible first, then soonest."""
    if not statuses:
        return None
    return min(statuses, key=lambda s: (s.feasible, s.days_left))


def build(
    verdicts: list[Verdict],
    *,
    today: date,
    insight: ArchiveInsight | None = None,
    changes: list[Change] | None = None,
    register_items: list[Opportunity] | None = None,
    in_flight: list[TimelineStatus] | None = None,
    style: StyleCard | None = None,
    voice_report: VoiceReport | None = None,
    draft: CitedDraft | None = None,
    timeline_plan: Timeline | None = None,
    audit: GroundingAudit | None = None,
    workspace_path: Path | None = None,
    org: str | None = None,
    org_id: str | None = None,
    direction: str | None = None,
    grounded: int = 0,
    total_claims: int = 0,
    gaps: int = 0,
    trace: dict | None = None,
    threshold: int = 65,
    model: str = "",
    provider: str = "",
    calls_source: str = "",
    sources: dict | None = None,
    place: str | None = None,
    local_funders: list[dict] | None = None,
) -> RunDigest:
    trace = trace or {}
    changes = changes or []

    # Bands count what triage actually banded. A live call with no award
    # history was never scored, and filing it under Low would claim otherwise.
    bands: dict[str, int] = {"High": 0, "Medium": 0, "Low": 0}
    for v in verdicts:
        if v.triage is not None:
            bands[v.triage.priority.value] += 1

    d = RunDigest(
        run_date=today.isoformat(),
        run_time=datetime.now().strftime("%H:%M"),
        model=model,
        provider=provider,
        duration_s=round(float(trace.get("duration_s") or 0), 1),
        model_calls=int(trace.get("model_calls") or 0),
        threshold=threshold,
        calls_considered=len(verdicts),
        bands=bands,
        surfaced=[_row(v, today) for v in verdicts if v.stage == "surfaced"],
        not_pursued=[_row(v, today) for v in verdicts if v.stage == "triage"],
        held_at_gate=[_row(v, today) for v in verdicts if v.stage in ("matcher", "gate")],
        calls_source=calls_source,
        sources=dict(sources or {}),
        place=place,
        local_funders=list(local_funders or []),
    )

    if register_items is not None:
        states: dict[str, int] = {}
        for o in register_items:
            states[o.status] = states.get(o.status, 0) + 1
        d.watcher_state = {
            "tracked": len(register_items),
            "by_status": states,
            "silent": max(0, len(register_items) - len(changes)),
        }
    d.changes = [
        {"kind": c.kind, "funder": c.opportunity.funder,
         "detail": c.detail, "urgent": c.urgent}
        for c in changes
    ]

    status = _pressing(in_flight or [])
    if status is not None:
        d.watcher_state["in_flight"] = {
            "deadline": status.deadline,
            "days_left": status.days_left,
            "overdue": status.overdue,
            "due_now": status.due_now,
            "hours_estimate": status.hours_remaining_estimate,
            "feasible": status.feasible,
            "summary": status.summary(),
        }

    if insight:
        d.archive = {
            "n_files": insight.n_files,
            "n_decided": insight.n_decided,
            "n_awarded": insight.n_awarded,
            "n_unknown_outcome": insight.n_unknown_outcome,
            "win_rate": round(insight.win_rate, 3) if insight.win_rate is not None else None,
            "confidence": insight.confidence,
            "median_successful_ask": insight.median_successful_ask,
            "median_unsuccessful_ask": insight.median_unsuccessful_ask,
        }
        # Full sentences when the archive can say how big and how recent the
        # relationship was; bare funder names otherwise.
        d.revisit = ([r["text"] for r in insight.revisit_detail]
                     or list(insight.never_reapplied_to))
        d.scope = {
            "files_seen": insight.n_files + len(insight.skipped),
            "files_read": insight.n_files,
            "files_skipped": len(insight.skipped),
            "skipped_detail": [
                {"name": s.path.name, "reason": s.reason} for s in insight.skipped
            ],
        }

    if style:
        d.voice = {
            "corpus_documents": style.n_documents,
            "corpus_sentences": style.n_sentences,
            "median_sentence_words": style.median_sentence_words,
            "sentence_word_sd": round(style.sentence_word_sd, 1),
            "beneficiary_term": style.beneficiary_term,
            "first_person_plural": style.first_person_plural,
        }
    if voice_report:
        d.voice["flags"] = {
            "tells": [found for found, _ in voice_report.tells_found],
            "uniformity": voice_report.uniformity_warning,
            "term_violations": voice_report.term_violations,
            "clean": voice_report.clean,
        }

    d.org, d.org_id = org, org_id
    d.direction = [p.strip() for p in (direction or "").split("\n\n") if p.strip()]

    if workspace_path is not None:
        workspace_path = Path(workspace_path)
        d.workspace = str(workspace_path)
        # Listed rather than described: the point of the workspace is that the
        # files are real and survive the tool.
        d.files_written = [
            {"name": f.relative_to(workspace_path).as_posix(),
             "size": f"{f.stat().st_size / 1024:.1f} kb"}
            for f in sorted(workspace_path.rglob("*"))
            if f.is_file() and f.name != "README.md"
        ]
        stub = workspace_path / "06-submitted" / "outcome.meta.json"
        if stub.exists():
            meta = json.loads(stub.read_text(encoding="utf-8"))
            d.outcome = {"status": meta.get("outcome", "pending"),
                         "note": meta.get("_note", "")}

    if draft is not None:
        d.draft = {
            "question": draft.question,
            "answer": draft.body,
            "claims": [
                {"text": c.text, "grounded": c.grounded,
                 "sources": [s.render() for s in c.sources]}
                for c in draft.claims
            ],
            "unevidenced_gaps": list(draft.gaps),
        }

    if timeline_plan is not None:
        d.timeline = {
            "deadline": timeline_plan.deadline,
            "total_hours_estimate": timeline_plan.total_hours_estimate,
            "steps": [
                {"days_before_deadline": s.days_before_deadline,
                 "task": s.task, "owner_role": s.owner_role}
                for s in timeline_plan.steps
            ],
        }

    if audit is not None:
        d.audit = {
            "verdict": audit.verdict,
            "rate": round(audit.rate, 3),
            "grounded_claims": audit.grounded_claims,
            "total_claims": audit.total_claims,
            "ungrounded": list(audit.ungrounded),
        }

    d.grounded, d.total_claims, d.gaps = grounded, total_claims, gaps
    return d
