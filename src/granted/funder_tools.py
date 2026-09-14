"""Let the matcher interrogate the award history instead of being handed a summary.

`RevealedPreferences.brief()` is a fixed paragraph: the same fifteen lines for
every organisation, computed before anyone knew which organisation was asking.
It has to be general, so it is shallow -- five regions, top legal forms, one
median. An organisation whose question is "have they ever funded a CIO in
Southwark for under ten thousand" cannot get that answer from it.

These tools answer that question against the grant records themselves. The
matcher decides what to look up, which means the lookups are specific to the
organisation in front of it, and every one is recorded in the run so a person
can see what the model actually checked before it decided.

Every tool reads the same in-memory list of awards. Nothing here calls the
network, so a tool loop costs nothing but tokens.
"""

from __future__ import annotations

import statistics
from collections import Counter

from strands import tool


def funder_tools(grants: list, prefs) -> list:
    """Build the tool set for one funder's award history.

    Closures over `grants` rather than a class, so each tool is a plain function
    with a docstring the model reads as its description.
    """
    awarded = [g for g in grants if g.amount_awarded]

    @tool
    def awards_in_place(place: str) -> str:
        """Count awards this funder has made to recipients in a named place.

        Use the organisation's own area (a borough, city or county) to find out
        whether this funder has any record of funding there at all.

        Args:
            place: A place name, e.g. "Southwark", "Bristol", "Lambeth".
        """
        hits = [g for g in grants if g.region and place.lower() in g.region.lower()]
        if not hits:
            counts = Counter(g.region for g in grants if g.region)
            places = ", ".join(f"{p} ({n})" for p, n in counts.most_common(6))
            # Absence at a finer granularity than the funder publishes is not
            # evidence of exclusion. Publishers record location anywhere from
            # country down to output area; a funder recording "London" has no
            # row that will ever match "Southwark", however much they fund there.
            grain = ("Note: this funder records recipient locations at the level of "
                     f"{', '.join(p for p, _ in counts.most_common(3))}. A borough, "
                     "ward or neighbourhood will not appear here even where they do "
                     "fund it, so treat this as 'not published at that level' rather "
                     "than 'never funded'. Look up the wider place before concluding "
                     "anything.")
            return (f"No awards recorded under the exact name '{place}' across "
                    f"{len(grants)} grants. Their recorded places are: {places}. {grain}")
        amounts = [g.amount_awarded for g in hits if g.amount_awarded]
        out = f"{len(hits)} of {len(grants)} awards went to recipients in {place}."
        if amounts:
            out += (f" Those ranged £{min(amounts):,.0f} to £{max(amounts):,.0f}, "
                    f"median £{statistics.median(amounts):,.0f}.")
        return out

    @tool
    def awards_to_legal_form(legal_form: str) -> str:
        """Count awards to recipients on a given register (legal form).

        Args:
            legal_form: A 360Giving register prefix: GB-CHC (registered charity),
                GB-COH (company), GB-SC (Scottish charity), GB-NIC.
        """
        forms = Counter(g.org_register for g in grants if g.org_register)
        n = forms.get(legal_form.upper(), 0)
        total = sum(forms.values()) or 1
        if not n:
            seen = ", ".join(f"{k} {v}" for k, v in forms.most_common())
            return (f"No awards to {legal_form.upper()} recipients. "
                    f"Recorded legal forms: {seen or 'none published'}.")
        return (f"{n} of {total} awards ({n / total:.0%}) went to {legal_form.upper()} "
                "recipients.")

    @tool
    def awards_near_amount(amount: float) -> str:
        """How many awards sit near a proposed ask, and where it falls in the range.

        Use this before suggesting an amount. An ask far outside what a funder
        actually gives is worth naming even when the fit is otherwise good.

        Args:
            amount: The amount in GBP being considered.
        """
        if not awarded:
            return "No award amounts published, so the ask cannot be anchored."
        amounts = sorted(g.amount_awarded for g in awarded)
        near = [a for a in amounts if abs(a - amount) <= amount * 0.25]
        below = sum(1 for a in amounts if a < amount)
        return (f"£{amount:,.0f} sits above {below / len(amounts):.0%} of their "
                f"{len(amounts)} published awards. {len(near)} awards fall within 25% "
                f"of it. Their range is £{amounts[0]:,.0f} to £{amounts[-1]:,.0f}, "
                f"median £{statistics.median(amounts):,.0f}.")

    @tool
    def has_funded(name: str) -> str:
        """Check whether this funder has previously funded a named organisation.

        Args:
            name: The organisation's name, or a distinctive part of it.
        """
        hits = [g for g in grants if g.recipient_name and name.lower() in g.recipient_name.lower()]
        if not hits:
            return f"No award to any recipient matching '{name}'."
        years = sorted({(g.award_date or "")[:4] for g in hits if g.award_date})
        return (f"{len(hits)} award(s) to '{hits[0].recipient_name}'"
                + (f", in {', '.join(y for y in years if y)}." if years else "."))

    @tool
    def funder_themes(want: str = "all") -> str:
        """What this funder labels its own grants with: its themes and programmes.

        Args:
            want: "themes", "programmes", or "all" for both.
        """
        themes = Counter(c for g in grants for c in g.classifications if c)
        progs = Counter(g.programme for g in grants if g.programme)
        want = (want or "all").lower()
        parts = []
        if themes and want in ("themes", "all"):
            parts.append("Themes they label: " + ", ".join(f"{k} ({v})" for k, v in themes.most_common(8)))
        if progs and want in ("programmes", "all"):
            parts.append("Programmes: " + ", ".join(f"{k} ({v})" for k, v in progs.most_common(5)))
        return " ".join(parts) or "No themes or programmes published on these grants."

    return [awards_in_place, awards_to_legal_form, awards_near_amount,
            has_funded, funder_themes]
