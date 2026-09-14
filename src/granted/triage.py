"""Triage open calls into High, Medium and Low.

Three inputs, combined deliberately rather than averaged.

  alignment    how well the org matches what this funder demonstrably buys
  plausibility how closely the org resembles organisations they have funded,
               plus the org's own win rate where it exists
  urgency      derived from the deadline AND from whether the work still fits
               in the time left

Urgency is a multiplier and a veto, not a third addend. A poorly aligned call
closing on Friday is not High priority, it is a trap. A well-aligned call that
can no longer be completed is not urgent, it is over.

On plausibility: this is not a probability of success. 360Giving publishes awards
only, so there is no rejected-applicant population to model against. What can be
measured is resemblance to actual recipients, and the organisation's own win rate
from their archive, which does contain rejections. Both are reported with their
sample size and neither is presented as a forecast.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum

from .archive import ArchiveInsight
from .precedent import RevealedPreferences
from .watcher import Opportunity, TimelineStatus


class Priority(str, Enum):
    HIGH = "High"
    MEDIUM = "Medium"
    LOW = "Low"


# Score bands. Deliberately conservative: the default answer is Low.
HIGH_THRESHOLD = 70
MEDIUM_THRESHOLD = 45


@dataclass
class Factor:
    name: str
    score: float          # 0-100
    weight: float
    reasons: list[str] = field(default_factory=list)
    confidence: str = "reasonable"


@dataclass
class Triage:
    opportunity: Opportunity
    priority: Priority
    composite: float
    alignment: Factor
    plausibility: Factor
    urgency_multiplier: float
    days_left: int | None
    vetoed: str | None = None
    # "award history" when a funder's published awards were read; "stated
    # criteria" when the call was judged on what it says (see criteria.py).
    basis: str = "award history"

    def explain(self) -> str:
        lines = [
            f"{self.opportunity.funder}"
            + (f" — {self.opportunity.programme}" if self.opportunity.programme else ""),
            f"Priority: {self.priority.value} (score {self.composite:.0f}/100)"
            + (f", {self.days_left} days left" if self.days_left is not None else ""),
        ]
        if self.vetoed:
            lines.append(f"Vetoed: {self.vetoed}")
        for f in (self.alignment, self.plausibility):
            lines.append(f"  {f.name}: {f.score:.0f}/100 ({f.confidence})")
            for r in f.reasons:
                lines.append(f"    - {r}")
        lines.append(f"  Deadline multiplier: x{self.urgency_multiplier:.2f}")
        return "\n".join(lines)


# --------------------------------------------------------------------------


def score_alignment(rp: RevealedPreferences, org: dict, ask: float | None,
                    stated: tuple[float | None, float | None] | None = None) -> Factor:
    """Does this org match what the funder demonstrably buys?

    `org` is the parsed ORG.md: legal_form, region, turnover, themes. `stated` is
    the programme's own award range, when the call gives one.
    """
    score, reasons = 50.0, []

    forms = dict(rp.registers)
    org_form = org.get("legal_form_register")
    if org_form and forms:
        if org_form in forms:
            share = forms[org_form] / sum(forms.values())
            score += 20 * share
            reasons.append(f"funder has backed {org_form} organisations ({share:.0%} of awards)")
        else:
            score -= 30
            reasons.append(f"funder has never funded a {org_form} in {rp.n_grants} recorded awards")

    regions = dict(rp.top_regions)
    org_region = org.get("region")
    if org_region and regions:
        if any(org_region.lower() in r.lower() for r in regions if r):
            score += 15
            reasons.append(f"funds in {org_region}")
        else:
            score -= 20
            reasons.append(f"no recorded awards in {org_region}; concentrated in "
                           + ", ".join(list(regions)[:3]))

    low, high = stated or (None, None)
    if ask and (low or high):
        # A programme's own stated range beats a sample of its funder's whole
        # history. The Lottery's sample is all £10,000 Awards for All grants,
        # which says nothing about Reaching Communities at £20,001 and up.
        if (not low or ask >= low) and (not high or ask <= high):
            score += 15
            reasons.append(f"£{ask:,.0f} sits inside this programme's stated range"
                           + (f" (£{low:,.0f} to £{high:,.0f})" if low and high else ""))
        else:
            score -= 30
            reasons.append(f"£{ask:,.0f} is outside this programme's stated range")
    elif ask and rp.award_p10 and rp.award_p90:
        if rp.award_p10 <= ask <= rp.award_p90:
            score += 15
            reasons.append(f"£{ask:,.0f} sits inside their usual range")
        else:
            # Scale with the overshoot. Asking 10% above their ceiling is a
            # rounding error; asking double is a different conversation and
            # should not survive as a High priority on other merits.
            ratio = ask / rp.award_p90 if ask > rp.award_p90 else rp.award_p10 / ask
            penalty = min(45.0, 15.0 + (ratio - 1.0) * 30.0)
            score -= penalty
            reasons.append(
                f"£{ask:,.0f} is {ratio:.1f}x outside their usual "
                f"£{rp.award_p10:,.0f}-£{rp.award_p90:,.0f}"
                + (". Consider asking inside the range instead of skipping the funder"
                   if ratio < 2 else ". This is the wrong funder for this amount"))

    themes = {t.lower() for t, _ in rp.top_classifications}
    org_themes = {t.lower() for t in org.get("themes", [])}
    if themes and org_themes:
        overlap = len(themes & org_themes)
        if overlap:
            score += min(15, overlap * 7)
            reasons.append(f"{overlap} theme(s) shared with the funder's own labels")

    confidence = "reasonable" if rp.n_grants >= 100 else (
        "indicative" if rp.n_grants >= 30 else "anecdotal")
    return Factor("Alignment", max(0.0, min(100.0, score)), 0.55, reasons, confidence)


def score_plausibility(rp: RevealedPreferences, insight: ArchiveInsight | None,
                       org: dict) -> Factor:
    """Resemblance to actual recipients, plus the org's own record.

    Explicitly not a probability. See module docstring.
    """
    score, reasons = 50.0, []

    if rp.sampled:
        pass        # a repeat rate from a sample of a large funder is noise
    elif rp.repeat_funding_rate > 0.5:
        score -= 12
        reasons.append(
            f"{rp.repeat_funding_rate:.0%} of their recipients are repeat, so new "
            "applicants take a smaller share than the award count suggests")
    elif rp.repeat_funding_rate < 0.2:
        score += 8
        reasons.append(f"only {rp.repeat_funding_rate:.0%} repeat funding; open to new applicants")

    if rp.ask_to_award_ratio is not None and rp.ask_to_award_ratio < 0.7:
        score -= 8
        reasons.append(
            f"typically awards {rp.ask_to_award_ratio:.0%} of the amount requested "
            f"(n={rp.ask_ratio_n}), so budget for a partial award")

    confidence = "indicative"
    if insight and insight.n_decided:
        if insight.win_rate is not None:
            delta = (insight.win_rate - 0.35) * 60
            score += max(-20, min(20, delta))
            reasons.append(
                f"your own win rate is {insight.win_rate:.0%} across {insight.n_decided} "
                f"decided applications ({insight.confidence})")
        won = {f for f, _ in insight.funders_won}
        lost = {f for f, _ in insight.funders_lost}
        if rp.funder_name in won:
            score += 18
            reasons.append(f"{rp.funder_name} has funded you before")
        elif rp.funder_name in lost:
            score -= 10
            reasons.append(f"{rp.funder_name} has declined you before; address why or skip")
        confidence = insight.confidence
    else:
        reasons.append("no outcome history on file, so this rests on funder data alone")

    return Factor("Plausibility", max(0.0, min(100.0, score)), 0.45, reasons, confidence)


# A serious application is not a short piece of work. Across generated schedules
# the estimate lands around 28 hours; 20 is a deliberate floor, so the veto below
# fires only when the time left is not arguably enough.
MIN_BID_HOURS = 20.0
HOURS_PER_DAY = 1.5          # the same realistic pace watcher.check_timeline assumes


def urgency(days_left: int | None, ts: TimelineStatus | None) -> tuple[float, str | None]:
    """Multiplier, and a veto reason if the work no longer fits the time left.

    Two ways to be out of time. Once a bid is in flight there is a real schedule
    to reconcile, and `ts` carries it. Before that there is no schedule yet --
    the timeliner only runs after the gate fires -- so a veto that waited for one
    could never fire on a new call, which is precisely the call worth stopping:
    a fortnight of work advertised three days before it closes.

    So the second test needs no model and no plan, only a calendar. What a small
    organisation can actually put in between now and the deadline, against what
    an application of this kind takes at minimum.
    """
    if ts is not None and not ts.feasible:
        return 0.0, (
            f"{ts.hours_remaining_estimate:.0f} hours of work remaining against "
            f"{ts.days_left} days. Not achievable at a realistic pace.")
    if days_left is None:
        return 0.85, None          # rolling deadline: real, but never urgent
    if days_left < 0:
        return 0.0, "closed"

    capacity = days_left * HOURS_PER_DAY
    if capacity < MIN_BID_HOURS:
        return 0.0, (
            f"{days_left} day{'s' if days_left != 1 else ''} left is about "
            f"{capacity:.0f} hours at a realistic pace, against the {MIN_BID_HOURS:.0f} "
            "hours an application of this kind takes at minimum. Starting this now "
            "costs the week and probably loses anyway.")

    # Anything that survives the capacity test has time to be done properly, so
    # what remains is only how soon it needs attention.
    if days_left <= 21:
        return 1.25, None          # doable, but it is this fortnight's work
    if days_left <= 60:
        return 1.0, None
    return 0.9, None               # months away: real, but not this week's problem


def triage(opp: Opportunity, rp: RevealedPreferences, org: dict,
           insight: ArchiveInsight | None = None, ask: float | None = None,
           ts: TimelineStatus | None = None, today: date | None = None) -> Triage:
    low = opp.amount_min if (opp.amount_min or 0) >= 100 else None
    high = opp.amount_max if (opp.amount_max or 0) >= 100 else None
    align = score_alignment(rp, org, ask, (low, high) if (low or high) else None)
    plaus = score_plausibility(rp, insight, org)

    quality = align.score * align.weight + plaus.score * plaus.weight
    days = opp.days_left(today)
    mult, veto = urgency(days, ts)

    composite = min(100.0, quality * mult)
    if veto:
        priority = Priority.LOW
    elif composite >= HIGH_THRESHOLD:
        priority = Priority.HIGH
    elif composite >= MEDIUM_THRESHOLD:
        priority = Priority.MEDIUM
    else:
        priority = Priority.LOW

    return Triage(opp, priority, composite, align, plaus, mult, days, veto)


def rank(items: list[Triage]) -> list[Triage]:
    """High first, then by score. Vetoed items sink regardless of quality."""
    order = {Priority.HIGH: 0, Priority.MEDIUM: 1, Priority.LOW: 2}
    return sorted(items, key=lambda t: (bool(t.vetoed), order[t.priority], -t.composite))
