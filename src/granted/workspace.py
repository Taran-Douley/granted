"""One folder per opportunity, created when the gate fires.

Numbered so it reads in the order a trustee would want it: decision first, then
why, then what to write, then the evidence, then what was actually sent.

The last folder matters more than it looks. `06-submitted/outcome.meta.json` is a
stub written at creation time with the result left blank. When the funder replies
and someone fills it in, next year's archive learner gets clean data for free. The
system feeds itself instead of decaying.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

STRUCTURE: dict[str, str] = {
    "00-brief": "The call as published: guidance, criteria, forms, deadlines. Read-only copies.",
    "01-decision": "Should we apply. The fit score, the blockers, the suggested ask, the reasoning.",
    "02-direction": "The angle to lead with, and what to avoid. One page.",
    "03-drafts": "Answers, versioned. v1 is the agent's, later versions are yours.",
    "04-evidence": "Every figure cited in the drafts, with its source. Check anything here.",
    "05-timeline": "Work-back schedule with owners and internal review points.",
    "06-submitted": "What actually went in, and the result when it comes back.",
}

_SLUG = re.compile(r"[^a-z0-9]+")
# Characters Windows refuses in a file name, plus control characters.
_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')
# Long enough to name the call, short enough that Bids/<name>/04-evidence/<file>
# stays well inside Windows' 260-character path limit from a OneDrive folder.
_FRIENDLY_MAX = 72


def slug(text: str, max_len: int = 48) -> str:
    s = _SLUG.sub("-", text.lower()).strip("-")
    return s[:max_len].rstrip("-") or "opportunity"


def folder_name(funder: str, programme: str | None = None, friendly: bool = False) -> str:
    """`2026-09-10-the-clothworkers-foundation-small-grants` for tools, or
    `2026-09-10 The Clothworkers Foundation - Small Grants Programme` for people.

    Either way the date leads, so the folders sort in the order they were opened.
    """
    today = date.today().isoformat()
    if not friendly:
        name = f"{today}-{slug(funder)}"
        return name + (f"-{slug(programme, 24)}" if programme else "")
    title = funder + (f" - {programme}" if programme else "")
    title = " ".join(_UNSAFE.sub(" ", title).split())[:_FRIENDLY_MAX]
    # Windows silently drops trailing dots and spaces, and then cannot find the folder.
    return f"{today} {title.rstrip(' .') or 'Opportunity'}"


@dataclass
class Workspace:
    root: Path
    funder: str
    programme: str | None
    deadline: str | None

    def path(self, folder: str) -> Path:
        return self.root / folder

    def write(self, folder: str, filename: str, content: str) -> Path:
        p = self.path(folder) / filename
        p.parent.mkdir(parents=True, exist_ok=True)
        # Explicit, because Windows would otherwise write cp1252 and every
        # later read of a "£" or a dash would fail.
        p.write_text(content, encoding="utf-8")
        return p


def create(base: Path, funder: str, programme: str | None = None,
           deadline: str | None = None, fit_score: int | None = None,
           friendly: bool = False) -> Workspace:
    """Called when the gate fires. Never called for opportunities that fail it."""
    root = base / folder_name(funder, programme, friendly)

    ws = Workspace(root, funder, programme, deadline)
    for folder, purpose in STRUCTURE.items():
        d = root / folder
        d.mkdir(parents=True, exist_ok=True)
        (d / "README.md").write_text(f"# {folder}\n\n{purpose}\n", encoding="utf-8")

    # Index at the root so the folder explains itself without training.
    lines = [
        f"# {funder}" + (f" — {programme}" if programme else ""),
        "",
        f"Opened {date.today().isoformat()}."
        + (f" Deadline {deadline}." if deadline else "")
        + (f" Fit score {fit_score}/100." if fit_score is not None else ""),
        "",
        "| Folder | What's in it |",
        "| --- | --- |",
    ]
    lines += [f"| `{f}/` | {p} |" for f, p in STRUCTURE.items()]
    lines += [
        "",
        "The decision, the draft, the evidence and the timeline each have a Word",
        "copy (.docx) beside the plain-text original. Edit the Word copies; the",
        "plain-text ones are what the agent reads back.",
        "",
        "## When the result arrives",
        "",
        "Fill in `06-submitted/outcome.meta.json`. That one file is what teaches the",
        "agent your track record, so next year it knows which funders say yes to you.",
    ]
    (root / "README.md").write_text("\n".join(lines), encoding="utf-8")

    # The stub that closes the loop.
    stub = {
        "funder": funder,
        "programme": programme,
        "submitted": None,
        "amount_asked": None,
        "amount_awarded": None,
        "outcome": "pending",
        "feedback": None,
        "_note": "Set outcome to awarded or rejected when you hear back. "
                 "Paste any assessor feedback into feedback, verbatim, good or bad.",
    }
    (root / "06-submitted" / "outcome.meta.json").write_text(
        json.dumps(stub, indent=2), encoding="utf-8")

    return ws
