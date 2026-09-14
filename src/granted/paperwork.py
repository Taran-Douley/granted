"""The Word documents in a bid folder.

The agent reads and writes plain text; people in a charity work in Word. So each
bid folder gets Word copies of the things someone will open, edit or forward: the
decision, the angle, the draft, the evidence behind it, and the timeline. The
plain-text originals stay beside them, because they are what the next run reads.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from .citations import CitedDraft
from .documents import write_docx
from .render import Decision, _deadline, _money
from .schemas import Match, Timeline

AI_NOTE = ("Drafted by Granted from your ORG.md and your past applications. Check every "
           "figure against 'Evidence and sources' before it goes anywhere, and declare AI "
           "assistance if the funder asks.")


def _paragraphs(text: str | None) -> list[tuple[str, object]]:
    return [("p", p.strip()) for p in (text or "").split("\n\n") if p.strip()]


def write_bid_documents(root: Path, decision: Decision, match: Match,
                        cited: CitedDraft | None = None,
                        timeline: Timeline | None = None) -> list[Path]:
    """Write the Word copies into a bid folder. Returns the files written."""
    root = Path(root)
    title = decision.title
    written = []

    rows = [["", ""],
            ["Fit", f"{decision.fit}/100"],
            ["Suggested ask", _money(decision.suggested_ask)],
            ["Deadline", _deadline(decision.closes, decision.days_left)]]
    if decision.hours_estimate:
        rows.append(["Estimated effort", f"about {decision.hours_estimate:.0f} hours"])
    if decision.total_claims:
        rows.append(["Evidence", f"{decision.grounded}/{decision.total_claims} claims traced to a source"])
    if decision.audit_verdict:
        audit = decision.audit_verdict
        if decision.audit_rate is not None:
            audit += f", {decision.audit_rate:.0%} grounded"
        rows.append(["Independent audit", audit])

    blocks: list[tuple[str, object]] = [
        ("title", title),
        ("p", f"Prepared {date.today():%d %B %Y}. The decision is yours; this is the case for it."),
        ("table", rows),
    ]
    if decision.why:
        blocks += [("h1", "Why this one")] + [("bullet", r) for r in decision.why]
    if decision.own_record:
        blocks += [("h1", "Your own record")] + [("bullet", r) for r in decision.own_record]
    if match.blocking:
        blocks += [("h1", "Blockers")] + [("bullet", b) for b in match.blocking]
    if decision.gaps:
        blocks += [("h1", "Before you start"),
                   ("p", "They will ask for these, and ORG.md does not evidence them:")]
        blocks += [("bullet", g) for g in decision.gaps]
    if match.reasoning:
        blocks += [("h1", "The reasoning")] + _paragraphs(match.reasoning)
    written.append(write_docx(root / "01-decision" / "Decision.docx", blocks))

    if decision.direction:
        written.append(write_docx(root / "02-direction" / "Direction.docx",
                                  [("title", f"The angle — {title}")]
                                  + _paragraphs(decision.direction)))

    if cited is not None:
        blocks = [("title", f"Draft answer v1 — {title}"),
                  ("h2", "The question"), ("quote", cited.question),
                  ("h2", "Draft")] + _paragraphs(cited.body)
        blocks += [("h2", "Before you use this"), ("p", AI_NOTE)]
        if cited.gaps:
            blocks += [("h2", "Gaps to fill")] + [("bullet", g) for g in cited.gaps]
        written.append(write_docx(root / "03-drafts" / "Draft answer v1.docx", blocks))

        blocks = [("title", f"Evidence and sources — {title}"),
                  ("p", "Every claim in the draft, with where it came from. A claim with no "
                        "source should come out of the draft, or its evidence go into ORG.md.")]
        for i, claim in enumerate(cited.claims, 1):
            blocks.append(("h2", f"{i}. {claim.text}"))
            if claim.sources:
                blocks += [("bullet", s.render()) for s in claim.sources]
            else:
                blocks.append(("p", "No source found."))
        if cited.gaps:
            blocks += [("h1", "Needs a person")] + [("bullet", g) for g in cited.gaps]
        written.append(write_docx(root / "04-evidence" / "Evidence and sources.docx", blocks))

    if timeline is not None:
        try:
            deadline = date.fromisoformat(timeline.deadline)
        except ValueError:
            deadline = None
        table = [["Date", "Days before deadline", "Task", "Owner"]]
        for step in sorted(timeline.steps, key=lambda s: -s.days_before_deadline):
            when = (deadline - timedelta(days=step.days_before_deadline)).strftime("%a %d %b") \
                if deadline else ""
            table.append([when, str(step.days_before_deadline), step.task, step.owner_role])
        blocks = [("title", f"Timeline — {title}"),
                  ("p", (f"Deadline {timeline.deadline}." if deadline
                         else "No closing date: a rolling programme.")
                        + f" About {timeline.total_hours_estimate:.0f} hours of work in total."),
                  ("table", table),
                  ("p", "When a step is done, add its task to 05-timeline/done.txt, one per "
                        "line. The next run checks what is left against the time left.")]
        written.append(write_docx(root / "05-timeline" / "Timeline.docx", blocks))

    return written
