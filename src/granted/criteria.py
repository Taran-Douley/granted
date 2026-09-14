"""Judging a call on what it says, when there is no award history to read.

Most calls on Find a grant come from funders that publish no award history to
360Giving, and Innovate UK publishes none at all. Dropping those calls hid most
of the national grants a charity might want; scoring them against some other
funder's history would be worse, a number with no meaning.

So they are judged on the call's own stated criteria, and labelled that way:
where it applies, who can apply, how big the awards are, and whether it is about
what the organisation does. That is thinner evidence than an award history, and
every reason says which it rests on.

The location and eligibility checks in `gatekeep` run first on every call,
whatever its basis: a Scotland-only programme is closed to a London charity
however well its funder's history fits.
"""

from __future__ import annotations

import re
from datetime import date

from .archive import ArchiveInsight
from .location import Place
from .org import OrgProfile, _themes
from .triage import HIGH_THRESHOLD, MEDIUM_THRESHOLD, Factor, Priority, Triage, urgency
from .watcher import Opportunity

STATED = "stated criteria"

_ANYWHERE = {"national", "uk", "uk-wide", "uk wide", "united kingdom", "international",
             "nationwide", "all"}
_CHARITY_FORMS = {"GB-CHC", "GB-SC", "GB-NIC"}
_BUSINESS = re.compile(r"\bbusiness(?:es)?\b", re.I)
# UKRI fellowships and research grants, which a community charity cannot hold.
_RESEARCH_ONLY = re.compile(
    r"based at an? (?:eligible )?(?:uk )?research organisation|research organisations? eligible for|"
    r"eligible for ukri funding|in receipt of \w+ research funding", re.I)
# A topic miss caps the score: the organisation's own good record says nothing
# about whether a film or research fund suits it, and must not carry one over.
OFF_TOPIC_CAP = 30.0
# Calls open to charities and community groups whatever their cause. A topic
# miss on one of these says nothing, so it is never capped for it.
_GENERAL = re.compile(
    r"\bcharit(?:y|ies)\b|voluntary|community (?:groups?|organisations?|projects?|causes?|led)|"
    r"vcse|not-for-profit|local causes|any cause|smaller (?:uk )?(?:charities|organisations)|"
    r"\bcommunities\b", re.I)
# Below this share of charity recipients, a funder's off-topic call is not worth
# a model call: the Department for Transport's electric car grant, say.
CHARITY_SHARE_FLOOR = 0.20
_OPEN_TO_OTHERS = re.compile(
    r"charit|voluntary|non-profit|not-for-profit|third sector|social enterprise|vcse|"
    r"community (?:group|organisation)|research (?:and technology )?organisation|"
    r"academic|public sector|any (?:uk )?organisation|uk registered organisations?", re.I)


def gatekeep(opp: Opportunity, profile: OrgProfile, place: Place | None) -> str | None:
    """The reason this organisation cannot apply at all, or None. Free; runs first."""
    if place and opp.locations:
        wanted = {loc.strip().lower() for loc in opp.locations if loc.strip()}
        if wanted and not (wanted & _ANYWHERE) and not (wanted & place.funding_labels()):
            return f"this call is for {', '.join(opp.locations)}; you are in {place.describe()}"
    text = opp.eligibility or ""
    if (profile.legal_form_register in _CHARITY_FORMS and _BUSINESS.search(text)
            and not _OPEN_TO_OTHERS.search(text)):
        return "only businesses can apply or lead; you are a registered charity"
    if (profile.legal_form_register in _CHARITY_FORMS
            and _RESEARCH_ONLY.search(f"{text} {opp.summary}")):
        return "only UK research organisations eligible for UKRI funding can apply; you are a registered charity"
    return None


def topic(opp: Opportunity, profile: OrgProfile) -> tuple[str, set[str]]:
    """'match' with the themes shared, 'general' when the call is open to
    charities whatever their cause, 'miss', or 'unknown' when ORG.md names no
    themes to compare."""
    org_themes = set(profile.themes)
    text = f"{opp.programme or ''} {opp.summary}"
    shared = org_themes & set(_themes(text))
    if shared:
        return "match", shared
    if not org_themes:
        return "unknown", set()
    if _GENERAL.search(text):
        return "general", set()
    return "miss", set()


def assess(opp: Opportunity, profile: OrgProfile, place: Place | None,
           insight: ArchiveInsight | None = None, today: date | None = None) -> Triage:
    """Score a call on its stated criteria. Reasons run worst first, so the first
    is the one to give when the call stops here."""
    score = 50.0
    against, for_, unchecked = [], [], []

    if place is None:
        unchecked.append("your location is not known (add a postcode to ORG.md), so "
                         "where this call applies was not checked")
    elif opp.locations:
        wanted = {loc.strip().lower() for loc in opp.locations}
        for_.append("open across the UK" if wanted & _ANYWHERE
                    else f"open to organisations in {place.describe()}")

    org_themes = set(profile.themes)
    kind, shared = topic(opp, profile)
    if kind == "match":
        score += min(35, 20 * len(shared))
        for_.append(f"the call is about {', '.join(sorted(shared))}, which you work on")
    elif kind == "general":
        for_.append("open to charities and community groups generally, not tied to one cause")
    elif kind == "miss":
        score -= 25
        against.append(f"nothing in the call matches what you do ({', '.join(sorted(org_themes))})")
    else:
        unchecked.append("ORG.md names no themes, so whether the call suits your work "
                         "was not checked")

    income = profile.turnover
    # Listings occasionally carry a placeholder (one says a maximum of £2).
    low = opp.amount_min if (opp.amount_min or 0) >= 100 else None
    high = opp.amount_max if (opp.amount_max or 0) >= 100 else None
    if income and low and low > income:
        score -= 25
        against.append(f"the smallest award (£{low:,.0f}) is more than your "
                       f"annual income (£{income:,.0f})")
    elif income and high:
        if high > income * 3:
            score -= 10
            against.append(f"awards run to £{high:,.0f}, over three times your "
                           "income; expect to compete with much larger organisations")
        else:
            score += 10
            for_.append(f"awards up to £{high:,.0f} suit an organisation your size")

    align = Factor("Stated criteria", max(0.0, min(100.0, score)), 0.55,
                   against + for_ + unchecked, STATED)
    plaus = _own_record(insight)

    days = opp.days_left(today)
    mult, veto = urgency(days, None)
    composite = min(100.0, (align.score * align.weight + plaus.score * plaus.weight) * mult)
    if kind == "miss":
        composite = min(composite, OFF_TOPIC_CAP)
    if veto:
        priority = Priority.LOW
    elif composite >= HIGH_THRESHOLD:
        priority = Priority.HIGH
    elif composite >= MEDIUM_THRESHOLD:
        priority = Priority.MEDIUM
    else:
        priority = Priority.LOW
    return Triage(opp, priority, composite, align, plaus, mult, days, veto, basis=STATED)


def cap_off_topic(t: Triage, opp: Opportunity, profile: OrgProfile, rp) -> Triage:
    """Stop an award-history call for free when it is about something else AND
    its funder rarely funds charities at all.

    Narrow on purpose. A topic miss alone is not enough: the Lottery's and the
    Clothworkers' general programmes name no cause, and they are exactly the
    calls that matter.
    """
    kind, _ = topic(opp, profile)
    forms = dict(rp.registers)
    total = sum(forms.values())
    if kind != "miss" or not total:
        return t
    share = sum(forms.get(r, 0) for r in _CHARITY_FORMS) / total
    if share >= CHARITY_SHARE_FLOOR:
        return t
    t.composite = min(t.composite, OFF_TOPIC_CAP)
    if not t.vetoed and t.composite < MEDIUM_THRESHOLD:
        t.priority = Priority.LOW
    t.alignment.reasons.insert(0, f"nothing in the call matches what you do "
                                  f"({', '.join(sorted(profile.themes))}), and {rp.funder_name} "
                                  f"rarely funds charities ({share:.0%} of its awards)")
    return t


def _own_record(insight: ArchiveInsight | None) -> Factor:
    reasons = ["this funder publishes no award history, so resemblance to its past "
               "recipients cannot be judged"]
    score, confidence = 50.0, "anecdotal"
    if insight and insight.n_decided and insight.win_rate is not None:
        score += max(-20, min(20, (insight.win_rate - 0.35) * 60))
        reasons.append(f"your own win rate is {insight.win_rate:.0%} across "
                       f"{insight.n_decided} decided applications ({insight.confidence})")
        confidence = insight.confidence
    return Factor("Plausibility", max(0.0, min(100.0, score)), 0.45, reasons, confidence)


def stop_reason(t: Triage, floor: int) -> str:
    """Why a stated-criteria call goes no further."""
    if t.vetoed:
        return t.vetoed
    # Reasons run worst first, and any reason against pulls the score below 50.
    if t.alignment.score < 50 and t.alignment.reasons:
        return t.alignment.reasons[0]
    return (f"scores {t.composite:.0f}/100 on the call's stated criteria, below the "
            f"{floor} needed to be worth a model call")


def brief(opp: Opportunity, t: Triage, note: str | None = None) -> str:
    """What the graph is told in place of a funder's revealed preferences."""
    stated = []
    if opp.locations:
        stated.append(f"where: {', '.join(opp.locations)}")
    if opp.eligibility:
        stated.append(f"who can apply: {opp.eligibility[:300]}")
    if opp.amount_min or opp.amount_max:
        lo = f"£{opp.amount_min:,.0f}" if opp.amount_min else "?"
        hi = f"£{opp.amount_max:,.0f}" if opp.amount_max else "?"
        stated.append(f"award size: {lo} to {hi}")
    return "\n".join([
        f"Funder: {opp.funder}",
        (f"{note}. " if note else "")
        + "This funder publishes no award history, so there are no revealed "
          "preferences to compare against. Judge fit only on what the call itself "
          "states, say so in your reasoning, and suggest an ask inside the stated range. "
          "Read the description for limits on where or who: a fund for communities "
          "along a route, in named places, or for a named group. If the organisation "
          "is outside them, that is blocking, however well the rest fits.",
        "What the call states: " + ("; ".join(stated) or "little beyond its description"),
        "Stated-criteria check: " + "; ".join(t.alignment.reasons),
    ])
