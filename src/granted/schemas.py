"""Structured outputs. Strands Agents accept `structured_output_model=`, which
forces the model to return one of these instead of prose. This is what makes the
gate condition a boolean check rather than a regex over free text.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class Match(BaseModel):
    """Matcher verdict. Drives the silence gate."""

    fit_score: int = Field(ge=0, le=100, description="0-100 fit against revealed preferences")
    recommend_apply: bool
    suggested_ask_gbp: int | None = Field(
        default=None, description="Amount to request, anchored on the funder's award distribution"
    )
    eligibility_met: list[str] = Field(default_factory=list)
    eligibility_gaps: list[str] = Field(
        default_factory=list, description="Stated requirements the org profile does not evidence"
    )
    blocking: list[str] = Field(
        default_factory=list, description="Hard disqualifiers, e.g. wrong legal form or region"
    )
    reasoning: str


class TimelineStep(BaseModel):
    days_before_deadline: int
    task: str
    owner_role: str


class Timeline(BaseModel):
    deadline: str
    steps: list[TimelineStep]
    total_hours_estimate: float


class Claim(BaseModel):
    """One factual assertion in the draft, with its source line in ORG.md."""

    text: str
    source_heading: str | None = Field(default=None, description="Heading in ORG.md this came from")
    source_line: str | None = Field(default=None, description="Verbatim line from ORG.md")
    grounded: bool


class Draft(BaseModel):
    question: str
    answer: str
    claims: list[Claim]
    unevidenced_gaps: list[str] = Field(
        default_factory=list, description="Things the answer needs that ORG.md does not contain"
    )


class GroundingAudit(BaseModel):
    total_claims: int
    grounded_claims: int
    ungrounded: list[str]
    verdict: Literal["pass", "revise"]

    @property
    def rate(self) -> float:
        return self.grounded_claims / self.total_claims if self.total_claims else 1.0
