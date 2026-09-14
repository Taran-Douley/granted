"""Build ORG.md by drafting first and asking second.

The naive design asks the coordinator forty questions. That is work, and work is
the thing this product exists to remove. Instead: read the funding folder, draft
what can be drafted, then ask only about what is genuinely missing.

A well-stocked archive should reduce a forty-question interview to six.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .archive import ArchiveInsight, Submission

# The ORG.md skeleton, and what each section needs before it is usable.
SECTIONS: dict[str, list[str]] = {
    "Identity": ["legal name", "legal form", "registered number", "area served"],
    "Scale": ["annual turnover", "paid staff", "beneficiaries reached"],
    "Programmes": ["programme names", "delivery model", "volume", "outcome"],
    "Evidence and evaluation": ["evaluation source", "how outcomes are measured"],
    "Capability": ["safeguarding policy date", "finance system", "insurance"],
    "Funding history": ["past funders", "amounts", "years", "outcomes"],
    "Constraints": ["match funding capacity", "what you cannot deliver", "reserves policy"],
}

# Questions are phrased for a coordinator with ten spare minutes, not a bid writer.
QUESTION_BANK: dict[str, str] = {
    "legal form": "Are you a charity, a CIO, a CIC, or something else?",
    "registered number": "What's your registered charity or company number?",
    "area served": "Which area do you actually serve? Wards or a local authority is fine.",
    "annual turnover": "Roughly what was your income last financial year?",
    "paid staff": "How many paid staff, and roughly what FTE?",
    "beneficiaries reached": "Roughly how many people did you reach last year?",
    "volume": "For each thing you run, how much of it did you do last year? Sessions, meals, people.",
    "outcome": "What changed for the people you served? A number you'd stand behind is better than a description.",
    "evaluation source": "Has anyone independent evaluated your work? Who, and when?",
    "how outcomes are measured": "How do you know your outcomes are real? Survey, monitoring returns, something else?",
    "safeguarding policy date": "When was your safeguarding policy last reviewed?",
    "finance system": "What do you use for accounts, and are you independently examined?",
    "insurance": "What insurance do you hold, and at what level?",
    "match funding capacity": "How much match funding could you find if a funder asked for it?",
    "what you cannot deliver": "What do funders ask for that you genuinely can't do?",
    "reserves policy": "What's your reserves policy, and are you currently meeting it?",
}


@dataclass
class Gap:
    section: str
    field_name: str
    question: str
    why: str


@dataclass
class Bootstrap:
    """Result of drafting ORG.md from the archive."""

    filled: dict[str, list[str]] = field(default_factory=dict)
    gaps: list[Gap] = field(default_factory=list)

    @property
    def questions(self) -> list[str]:
        return [g.question for g in self.gaps]

    def to_markdown(self, org_name: str = "Your organisation") -> str:
        named = org_name != "Your organisation"
        out = [f"# {org_name}" if named else "# ORG.md", "",
               f"Drafted from your funding folder on first run. "
               f"{len(self.gaps)} things still need you.", ""]
        for section in SECTIONS:
            out.append(f"## {section}")
            # The name was given at setup; without it every digest says
            # "Your organisation".
            if section == "Identity" and named:
                out.append(f"- Legal name: {org_name}")
                # Not a question, so the count of gaps does not move: a line to
                # fill in, which places the organisation for regional funding.
                out.append("- Postcode:")
            for line in self.filled.get(section, []):
                out.append(f"- {line}")
            for g in (x for x in self.gaps if x.section == section):
                out.append(f"- TODO: {g.question}")
            out.append("")
        return "\n".join(out)


def bootstrap(subs: list[Submission], insight: ArchiveInsight) -> Bootstrap:
    """Draft what the archive supports; turn everything else into a question.

    Only 'Funding history' can be filled reliably from an archive. The rest of
    ORG.md needs either document extraction (next milestone) or the human.
    """
    b = Bootstrap()

    history: list[str] = []
    for s in sorted(subs, key=lambda x: (x.submitted is None, x.submitted)):
        if not (s.funder and s.decided):
            continue
        year = s.submitted.year if s.submitted else "year unknown"
        amount = f"£{s.amount_awarded:,.0f}" if s.amount_awarded else (
            f"£{s.amount_asked:,.0f} requested" if s.amount_asked else "amount unknown")
        history.append(f"{s.funder}, {amount}, {year}, {s.outcome}")
    if history:
        b.filled["Funding history"] = history

    # Anything the archive knows about but couldn't attribute becomes a question.
    orphaned = [s for s in subs if s.decided and not s.funder]
    if orphaned:
        names = ", ".join(f'"{s.path.name}"' for s in orphaned[:4])
        b.gaps.append(Gap(
            section="Funding history",
            field_name="past funders",
            question=f"I found {len(orphaned)} past applications I couldn't attribute to a funder ({names}). Who were they to?",
            why="outcome known but funder missing",
        ))

    if insight.n_unknown_outcome:
        b.gaps.append(Gap(
            section="Funding history",
            field_name="outcomes",
            question=f"{insight.n_unknown_outcome} applications have no recorded result. Did those come back yes or no?",
            why="outcome unrecorded; blocks your own track record",
        ))

    for section, fields in SECTIONS.items():
        for f in fields:
            if section in b.filled and f in ("past funders", "amounts", "years", "outcomes"):
                continue
            if f in QUESTION_BANK and not any(g.field_name == f for g in b.gaps):
                b.gaps.append(Gap(section, f, QUESTION_BANK[f], "not present in archive"))

    return b


def prioritise(gaps: list[Gap], limit: int = 6) -> list[Gap]:
    """Ask the six that unlock the most. Everything else can wait for a real bid.

    Ordering rationale: eligibility blockers first (legal form, area, turnover),
    because getting those wrong wastes the whole application. Then the things
    almost every assessor scores (outcome numbers). Cosmetics last.
    """
    priority = [
        "legal form", "area served", "annual turnover", "outcome",
        "past funders", "outcomes", "match funding capacity",
        "beneficiaries reached", "what you cannot deliver",
    ]
    rank = {f: i for i, f in enumerate(priority)}
    return sorted(gaps, key=lambda g: rank.get(g.field_name, 99))[:limit]
