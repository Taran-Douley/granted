"""The agent graph.

Shape:

    watcher -> precedent -> matcher --[gate: fit is high enough]--> director
                                     |                              timeliner
                                     |                              drafter -> auditor -> digest
                                     `--[gate fails]--> silence (no output, no notification)

The gate is a Strands conditional edge. If it does not fire, the run ends and the
org hears nothing. Silence is the default and the common case: the agent surfaces
only when there is a real decision to make.
"""

from __future__ import annotations

from strands import Agent
from strands.multiagent import GraphBuilder

from .config import resolve_model
from .schemas import Draft, GroundingAudit, Match, Timeline

FIT_THRESHOLD = 65

MATCHER_PROMPT = """You decide whether a small organisation should spend 30 hours \
on a funding application.

You are given (a) the funder's revealed preferences, computed from their published \
award history, and (b) the organisation's own profile from ORG.md.

Weight revealed preferences above the funder's own marketing copy. If the award \
history shows they have never funded this legal form, this region, or at this \
amount, that is a blocker regardless of what the brief invites.

Your bias is towards NOT applying. A wasted application costs a four-person charity \
a week they do not have. Recommend applying only when the fit is genuine.

Anchor suggested_ask_gbp inside the funder's observed award range, and never \
above the maximum this particular call states. The observed range is aggregated \
across all of a funder's programmes, so a small fast-grant scheme sitting below \
their overall p10 is normal and not a blocker on its own -- but an ask you have \
had to place well outside what they usually give is worth saying out loud in \
your reasoning either way.

Weigh the time remaining. You are told today's date and the days left. A well \
matched call that closes before the work could realistically be done is not a \
good opportunity, it is a trap, and fit_score should reflect that rather than \
scoring the match in the abstract.

You have tools that read this funder's actual grant records. The summary you \
are given is general; the tools answer the specific question in front of you. \
Before deciding, check the things that would disqualify this organisation \
outright: whether this funder has ever funded in their area, whether it funds \
their legal form, and where the ask you are considering falls in the amounts \
they really give. Look up what you need. Do not assume a blocker without \
checking, and do not claim a match you have not verified.

One trap in that data. Funders publish recipient locations at wildly different \
levels -- some by country, some by city, some by ward. A tool answering "no \
awards in X" may only mean X is finer than what this funder records. Before \
calling geography a blocker, look up the wider place as well; a nil return at \
one level and a healthy count at the level above is a publishing convention, \
not a refusal to fund there.
"""

DIRECTOR_PROMPT = """Given the funder's revealed preferences and the org profile, \
state the single angle the application should lead with, and the two things it \
must avoid. Use the funder's own vocabulary from their classification labels. \
Be specific to this org.

Do not restate whether they should apply, do not give a recommendation, and do \
not repeat the amount. That decision is already made and printed above you; \
repeating it wastes the only three paragraphs you get.

Open with the angle itself, in one sentence, as an instruction. Then one short \
paragraph of evidence from ORG.md that supports it. Then the two things to \
avoid. No headings, no bold, no preamble. Three short paragraphs maximum.
"""

TIMELINER_PROMPT = """Produce a work-back schedule from the deadline. Steps must be \
concrete and assigned to a role that a small organisation actually has \
(e.g. trustee, coordinator, finance lead). Include internal review checkpoints, \
not just the writing. Estimate total hours honestly.

Keep each task under twelve words. It is a line on a checklist, not a brief: \
"Draft the budget and match funding schedule", not a paragraph restating the \
organisation's figures back to them. Six to eight steps is plenty.
"""

DRAFTER_PROMPT = """Draft an answer to the ONE scored question named in the task
under "QUESTION TO ANSWER". Answer that question and no other. Do not restate
whether the organisation should apply and do not summarise the funder: that
decision is made and printed elsewhere. Write the answer a trustee could paste
into the portal.

Hard rule: every factual claim about the organisation must come from ORG.md. \
For each claim, record the heading and the verbatim source line it came from.

If the answer needs a fact that ORG.md does not contain, do NOT invent it and do \
NOT write around it with vague language. Put it in unevidenced_gaps so a human can \
supply it. A gap flagged is useful; a gap papered over is a rejected application.
"""

AUDITOR_PROMPT = """You are an independent check on the drafter. You receive the \
draft and the full text of ORG.md.

For each claim, verify the quoted source line actually appears in ORG.md and \
actually supports the claim. A line that is present but does not support the claim \
counts as ungrounded. Report the count and list every ungrounded claim verbatim.

Return verdict 'revise' if any claim is ungrounded. Do not be lenient. Your output \
is published as an accuracy figure, so a false pass is worse than a false fail.
"""


def gate_passes(state) -> bool:
    """Conditional edge: has the matcher found a real decision worth surfacing?

    Strands passes GraphState. We read the matcher node's structured result.
    """
    result = state.results.get("matcher")
    if result is None:
        return False
    match = _structured(result, Match)
    if match is None:
        return False
    if match.blocking:
        return False
    return match.recommend_apply and match.fit_score >= FIT_THRESHOLD


def _structured(node_result, model):
    """Pull the pydantic object out of a node result, tolerating shape changes."""
    for attr in ("structured_output", "result", "output"):
        obj = getattr(node_result, attr, None)
        if isinstance(obj, model):
            return obj
        inner = getattr(obj, "structured_output", None)
        if isinstance(inner, model):
            return inner
    return None


def build_graph(model: str | None = None, style: str | None = None,
                tools: list | None = None, hooks: list | None = None):
    """Assemble the graph.

    `model` defaults to whatever config.model_id() resolves: Haiku while
    developing, Sonnet when GRANTED_MODE=demo. Pass an explicit id only
    to pin a run.

    `style` is the organisation's measured voice, and it goes to the DRAFTER
    ALONE. Strands hands every node the same task string, so anything put in
    there is read by all five agents -- and the matcher, asked to judge whether
    a bid is worth thirty hours, was also being handed instructions about
    sentence rhythm and what to call the people it serves. Voice belongs to
    whoever is writing prose; it is noise in a fit judgement.
    """
    model = model or resolve_model()

    # callback_handler=None silences per-token streaming. director, timeliner and
    # drafter run concurrently once the gate fires, and three agents streaming to
    # one stdout interleave mid-word into unreadable pulp. Presentation belongs to
    # render.py, which prints once, when there is something to say.
    quiet = {"model": model, "callback_handler": None, "hooks": hooks or []}

    # The matcher is the only node with tools. It is the one making a judgement
    # that depends on facts nobody could summarise in advance -- whether this
    # funder has ever funded an organisation like this one, at this size, here.
    matcher = Agent(
        name="matcher",
        system_prompt=MATCHER_PROMPT,
        structured_output_model=Match,
        tools=tools or [],
        **quiet,
    )
    director = Agent(name="director", system_prompt=DIRECTOR_PROMPT, **quiet)
    timeliner = Agent(
        name="timeliner",
        system_prompt=TIMELINER_PROMPT,
        structured_output_model=Timeline,
        **quiet,
    )
    drafter = Agent(
        name="drafter",
        system_prompt=DRAFTER_PROMPT + ("\n\n" + style if style else ""),
        structured_output_model=Draft,
        **quiet,
    )
    auditor = Agent(
        name="auditor",
        system_prompt=AUDITOR_PROMPT,
        structured_output_model=GroundingAudit,
        **quiet,
    )

    b = GraphBuilder()
    b.add_node(matcher, "matcher")
    b.add_node(director, "director")
    b.add_node(timeliner, "timeliner")
    b.add_node(drafter, "drafter")
    b.add_node(auditor, "auditor")

    b.set_entry_point("matcher")

    # The gate. Three edges, one condition. Nothing downstream runs unless it fires.
    b.add_edge("matcher", "director", condition=gate_passes)
    b.add_edge("matcher", "timeliner", condition=gate_passes)
    b.add_edge("matcher", "drafter", condition=gate_passes)

    b.add_edge("drafter", "auditor")

    return b.build()
