"""Offline preflight. Everything that does not need Bedrock.

`preflight.py` stops dead when Bedrock is unreachable — a billing hold, a
missing use case form, a region without the profile. That leaves the other
nine tenths of the system unverified for no good reason: only the five agents
in graph.py call a model. The register, the triage arithmetic, the archive
reader, the style card, the citation index and the workspace builder are all
deterministic Python.

This runs those, and stubs the two things that normally require a live model:

  - `graph._structured()` against every result shape Strands might hand back,
    which is the check preflight.py's step 4 exists to settle
  - the silence gate across its full truth table, not the four cases inline
    in preflight.py

Run:  python scripts/preflight_offline.py [--network]

`--network` adds a live 360Giving API call. It needs the internet but no AWS.
"""

from __future__ import annotations

import inspect
import json
import shutil
import sys
import tempfile
import traceback
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

PASS, FAIL = "PASS", "FAIL"
results: list[tuple[str, str, str]] = []


def step(name: str):
    def deco(fn):
        def wrapper(*a, **k):
            try:
                detail = fn(*a, **k)
                results.append((PASS, name, detail or ""))
                print(f"  [PASS] {name}" + (f" — {detail}" if detail else ""))
                return True
            except Exception as e:  # noqa: BLE001
                msg = f"{type(e).__name__}: {e}"
                results.append((FAIL, name, msg))
                print(f"  [FAIL] {name}\n         {msg}")
                if "--verbose" in sys.argv:
                    traceback.print_exc()
                return False
        return wrapper
    return deco


# ---------------------------------------------------------------- schemas


@step("schemas accept model-shaped JSON and enforce their bounds")
def check_schemas() -> str:
    from pydantic import ValidationError
    from granted.schemas import Draft, GroundingAudit, Match, Timeline

    m = Match.model_validate({
        "fit_score": 72, "recommend_apply": True, "suggested_ask_gbp": 12000,
        "eligibility_met": ["registered charity"], "eligibility_gaps": [],
        "blocking": [], "reasoning": "Sits inside the observed award range.",
    })
    assert m.fit_score == 72
    # Round-trip through JSON the way a model would emit it.
    assert Match.model_validate_json(m.model_dump_json()) == m

    Timeline.model_validate({
        "deadline": "2026-11-30",
        "steps": [{"days_before_deadline": 21, "task": "Draft case for support",
                   "owner_role": "coordinator"}],
        "total_hours_estimate": 28.0,
    })
    Draft.model_validate({
        "question": "Describe your beneficiaries.", "answer": "...",
        "claims": [{"text": "Two paid staff.", "source_heading": "Staffing",
                    "source_line": "Two paid staff.", "grounded": True}],
        "unevidenced_gaps": [],
    })
    audit = GroundingAudit(total_claims=4, grounded_claims=3, ungrounded=["x"], verdict="revise")
    assert abs(audit.rate - 0.75) < 1e-9
    assert GroundingAudit(total_claims=0, grounded_claims=0, ungrounded=[],
                          verdict="pass").rate == 1.0

    # Bounds must actually bite, or a hallucinated 900/100 would sail past the gate.
    for bad in ({"fit_score": 101}, {"fit_score": -1}):
        try:
            Match(recommend_apply=True, reasoning="x", **bad)
        except ValidationError:
            pass
        else:
            raise AssertionError(f"fit_score bound not enforced for {bad}")
    return "Match/Timeline/Draft/GroundingAudit, bounds enforced"


@step("region picker takes the coarsest place, not the first")
def check_region_picker() -> str:
    """360Giving publishers disagree about where location lives and how fine it is.

    Getting this wrong is silent: region falls to None, top_regions empties, and
    the region term in score_alignment stops firing without any error.
    """
    from granted.precedent import Grant, _beneficiary_region

    cases = [
        ([{"name": "Southwark", "geoCodeType": "LONB"}], "Southwark"),
        ([{"name": "Hackney 025C", "geoCodeType": "LSOA"}], "Hackney"),
        # Coarser entry must win regardless of order.
        ([{"name": "Hackney 025C", "geoCodeType": "LSOA"},
          {"name": "Hackney", "geoCodeType": "LONB"}], "Hackney"),
        ([{"name": "Bristol 013D", "geoCodeType": "LSOA"},
          {"name": "South West", "geoCodeType": "RGN"}], "South West"),
        ([], None),
        (None, None),
        ([{"name": "", "geoCodeType": "LSOA"}], None),
    ]
    for locs, expected in cases:
        got = _beneficiary_region(locs)
        assert got == expected, f"{locs} -> {got!r}, expected {expected!r}"

    # Both publisher conventions must reach Grant.region.
    addressed = Grant.from_api({"data": {
        "id": "a", "recipientOrganization": [{"id": "GB-CHC-1", "addressRegion": "Somerset"}]}})
    assert addressed.region == "Somerset", "addressRegion convention regressed"
    beneficiary = Grant.from_api({"data": {
        "id": "b", "recipientOrganization": [{"id": "GB-CHC-2"}],
        "beneficiaryLocation": [{"name": "Lambeth", "geoCodeType": "LONB"}]}})
    assert beneficiary.region == "Lambeth", "beneficiaryLocation fallback regressed"
    return f"{len(cases)} granularity cases + both publisher conventions"


@step("provider switch resolves the same model on both providers")
def check_provider() -> str:
    """GRANTED_PROVIDER=anthropic was documented long before it existed.

    The Anthropic id is derived from the Bedrock one rather than kept in a second
    table, so this guards the derivation: two tables drift, and the failure shows
    up as a 404 from inside the SDK rather than anywhere near the config.
    """
    import os
    from granted import config

    saved = {k: os.environ.get(k) for k in
             ("GRANTED_MODE", "GRANTED_MODEL", "GRANTED_PROVIDER")}
    try:
        for k in saved:
            os.environ.pop(k, None)

        cases = [
            ("dev", None, "claude-haiku-4-5-20251001"),
            ("demo", None, "claude-sonnet-4-5-20250929"),
            (None, "global.anthropic.claude-haiku-4-5-20251001-v1:0",
             "claude-haiku-4-5-20251001"),
            (None, "eu.anthropic.claude-sonnet-4-5-20250929-v1:0",
             "claude-sonnet-4-5-20250929"),
        ]
        for mode, override, expected in cases:
            os.environ.pop("GRANTED_MODEL", None)
            os.environ.pop("GRANTED_MODE", None)
            if mode:
                os.environ["GRANTED_MODE"] = mode
            if override:
                os.environ["GRANTED_MODEL"] = override
            got = config.anthropic_model_id()
            assert got == expected, f"{mode or override} -> {got!r}, expected {expected!r}"
            # Bedrock must still get the profile id, prefix and all.
            assert config.resolve_model() == config.model_id()

        # A missing key must say so, not fail somewhere inside the SDK.
        os.environ["GRANTED_PROVIDER"] = "anthropic"
        os.environ.pop("ANTHROPIC_API_KEY", None)
        try:
            config.resolve_model()
        except RuntimeError as e:
            assert "pip install" in str(e) or "ANTHROPIC_API_KEY" in str(e), \
                f"unhelpful provider error: {e}"
        else:
            raise AssertionError("anthropic provider resolved with no SDK and no key")
    finally:
        for k, v in saved.items():
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v
    return f"{len(cases)} id derivations + missing-dependency error"


@step("ORG.md parses, and refuses to guess what it cannot read")
def check_org(tmp: Path) -> str:
    """Every alignment score depends on these four fields.

    The failure that matters here is silent: a legal form inferred wrongly from
    prose does not raise, it just quietly disqualifies the organisation against
    every funder for the rest of the run. So the rule is that an unreadable
    field stays None and its term does not fire.
    """
    from granted import org as org_mod

    real = org_mod.load(Path("ORG.example.md"))
    assert real.name == "Easton Community Kitchen", f"name -> {real.name!r}"
    assert real.legal_form_register == "GB-CHC", "registered number not preferred"
    assert real.region == "Bristol", f"region -> {real.region!r}"
    assert real.turnover == 148000, f"turnover -> {real.turnover!r}"
    assert "food insecurity" in real.themes, "themes not derived"
    assert not real.unreadable, f"complete file reported gaps: {real.unreadable}"
    assert set(real.as_triage_dict()) == {
        "legal_form_register", "region", "turnover", "themes"}, "triage dict shape drifted"

    # The widest place named is the one a funder's award history is aggregated
    # at, so "…, Southwark, London" must resolve to London, not the borough.
    south = org_mod.load(Path("demo/ORG.southwark.md"))
    assert south.region == "London", f"region -> {south.region!r}"

    # The registered number beats contradicting prose, because it states the register.
    mixed = org_mod.parse("## Identity\n- Legal form: Community Interest Company\n"
                          "- Registered number: GB-CHC-1234567\n")
    assert mixed.legal_form_register == "GB-CHC", "prose overrode the registered number"

    # Nothing readable: every field None, and every one named.
    blank = org_mod.parse("# Someone\n\n## Identity\n- We do good work locally.\n")
    assert blank.legal_form_register is None, "a legal form was invented from prose"
    assert blank.turnover is None and blank.region is None
    for field in ("legal form", "area served", "annual turnover", "themes"):
        assert field in blank.unreadable, f"{field} missing from unreadable"

    # Money shorthand a person actually writes.
    for text, expected in (("£1.2m", 1_200_000), ("£15k", 15_000), ("£148,000", 148_000)):
        got = org_mod.parse(f"## Scale\n- Annual turnover: {text}\n").turnover
        assert got == expected, f"{text} -> {got}"
    return "2 real files, register precedence, 4 fields refused, 3 money formats"


@step("all four renderings hold in every digest state")
def check_render(tmp: Path) -> str:
    """render.py is the only thing a user ever actually sees."""
    import json as _json

    from granted import render as R

    dec = R.Decision(funder="A Foundation", programme="Main", fit=80,
                     suggested_ask=9000, closes="2026-10-23", days_left=44,
                     why=["funds in Southwark"], gaps=["needs accounts"],
                     direction="## Lead **with** this.\n\nSecond para.",
                     question="What difference will this make?",
                     timeline="44 days", timeline_steps=["44d — draft (coordinator)"],
                     hours_estimate=28.0, voice_checked=True,
                     voice_flags=['used "attendees"'], grounded=11, total_claims=14,
                     workspace="workspaces/x")
    alert = R.Alert(title="A Foundation — Main", kind="behind_schedule",
                    detail="12 days left. Overdue: draft.", urgent=True,
                    workspace="workspaces/x")
    seen = R.Considered(funder="B Trust", programme=None, fit=12,
                        reason="no recorded awards in Bristol", days_left=9, stage="triage")

    states = {
        "silence": R.Digest(org_name="Org", run_date=date(2026, 9, 9), considered=[seen]),
        "decision": R.Digest(org_name="Org", run_date=date(2026, 9, 9),
                             considered=[seen], decision=dec),
        "alerts only": R.Digest(org_name="Org", run_date=date(2026, 9, 9), alerts=[alert]),
        "both": R.Digest(org_name="Org", run_date=date(2026, 9, 9),
                         considered=[seen], decision=dec, alerts=[alert]),
        "empty": R.Digest(org_name="Org", run_date=date(2026, 9, 9)),
    }
    for label, d in states.items():
        plain = R.to_terminal(d, R.Ink(False))
        assert "\033[" not in plain, f"{label}: ANSI leaked into a non-tty render"
        assert "Granted" in plain, f"{label}: terminal render empty"
        md = R.to_markdown(d)
        assert md.strip() and "# Granted" in md, f"{label}: markdown render empty"
        blob = R.to_json(d)
        assert _json.loads(_json.dumps(blob)) == blob, f"{label}: json not serialisable"
        assert blob["calls_considered"] == d.n_calls, f"{label}: count disagrees"
        coloured = R.to_terminal(d, R.Ink(True))
        assert len(coloured) >= len(plain), f"{label}: colour render lost content"

    # Small-n is the normal case, so anything derived from the archive has to
    # carry how far it can be trusted. A win rate from six applications printed
    # the same way as one from sixty is the exact overclaim this project exists
    # not to make.
    for n, expected in ((6, "anecdotal"), (18, "indicative"), (44, "reasonable")):
        a = R.Archive(confidence=expected, n_decided=n, n_awarded=n // 2, win_rate=0.5)
        assert expected in a.line, f"n={n} line omits its confidence: {a.line!r}"
        assert str(n) in a.line, f"n={n} line omits the sample size"
    none = R.Archive(confidence="anecdotal", n_decided=0, n_awarded=0)
    assert "funder data alone" in none.line, "an empty archive does not say so"

    withrec = R.Digest(org_name="Org", run_date=date(2026, 9, 9),
                       archive=R.Archive("indicative", 18, 10, 0.56),
                       decision=R.Decision(funder="F", fit=80,
                                           own_record=["you have won 56% of decided applications"]))
    for text in (R.to_terminal(withrec, R.Ink(False)), R.to_markdown(withrec)):
        assert "indicative" in text, "the confidence label never reaches the reader"
        assert "56%" in text, "the archive reason never reaches the reader"
    assert R.to_json(withrec)["archive"]["confidence"] == "indicative"
    assert R.to_json(withrec)["decision"]["own_record"], "own_record missing from json"

    # Silence must not be an empty screen, and must not claim a decision.
    quiet = R.to_terminal(states["silence"], R.Ink(False))
    assert "Nothing to report" in quiet and "One decision" not in quiet

    # Alerts outrank a new opportunity wherever both appear.
    both = R.to_terminal(states["both"], R.Ink(False))
    assert both.index("in flight") < both.index("One decision"), \
        "a new opportunity was printed above a bid already slipping"

    # The prose cleaner strips markdown rather than printing it.
    cleaned = R.prose(dec.direction)
    assert "**" not in cleaned and "##" not in cleaned, f"markdown survived: {cleaned!r}"
    assert R.prose(None) is None and R.prose("") is None
    assert len(R.shorten("word " * 60, 40)) <= 41, "shorten did not shorten"
    assert R.shorten("short", 40) == "short", "shorten damaged a short string"
    return f"{len(states)} states x terminal/markdown/json, ordering and prose correct"


@step("the dashboard builds from whatever the folder actually holds")
def check_dashboard(tmp: Path) -> str:
    """It is rebuilt on every run, so a crash here takes the run's output with it."""
    from granted import dashboard

    empty = tmp / "dash-empty"
    empty.mkdir(parents=True, exist_ok=True)
    data = dashboard.collect(empty)
    assert data["runs"] == [] and data["grants"] == [], "empty folder invented content"
    assert dashboard.build(empty).exists(), "build failed on an empty folder"

    root = tmp / "dash"
    (root / "digests").mkdir(parents=True, exist_ok=True)
    (root / "digests" / "2026-09-09.json").write_text(json.dumps({
        "org": "Org", "run_date": "2026-09-09", "threshold": 65, "calls_considered": 2,
        "decision": {"title": "A Foundation — Main", "fit": 80}, "considered": [], "alerts": [],
    }))
    ws = root / "workspaces" / "2026-09-09-a-foundation-main"
    (ws / "02-direction").mkdir(parents=True, exist_ok=True)
    (ws / "01-decision").mkdir(parents=True, exist_ok=True)
    # Content that would close the embedded state block early if not escaped.
    (ws / "02-direction" / "angle.md").write_text("Lead with </script><script>alert(1)</script>")
    (ws / "01-decision" / "match.json").write_text('{"fit_score": 80, "suggested_ask_gbp": 9000}')
    # A half-written workspace: the folder exists, most files do not.
    (root / "workspaces" / "2026-09-09-torn-half" / "05-timeline").mkdir(parents=True, exist_ok=True)
    # Malformed JSON must be skipped, not fatal.
    (root / "workspaces" / "2026-09-09-torn-half" / "05-timeline" / "schedule.json").write_text("{not json")

    data = dashboard.collect(root)
    assert len(data["runs"]) == 1, f"runs -> {len(data['runs'])}"
    assert len(data["grants"]) == 2, f"grants -> {len(data['grants'])}"
    full = next(g for g in data["grants"] if "main" in g["slug"])
    assert full["fit"] == 80, "decision numbers not lifted onto the card"
    assert full["title"] == "A Foundation Main", f"title -> {full['title']!r}"
    torn = next(g for g in data["grants"] if "torn" in g["slug"])
    assert torn.get("timeline") is None, "malformed JSON was not skipped"
    assert torn["fit"] is None, "a grant with no decision file reported a fit"

    out = dashboard.build(root)
    html = out.read_text(encoding="utf-8")
    assert "__GRANTED_DATA__" not in html, "placeholder left unreplaced"
    body = html.split('id="gr-data"', 1)[1].split("</script>", 1)[0]
    assert "</script>" not in body, "embedded content could close the data block"
    assert "<\\/script>" in body or "<\\/" in body, "the escape did not survive"
    return "empty folder, 2 grants, torn workspace, malformed json, script escaped"


# ------------------------------------------------- structured extraction


@step("_structured() tolerates every plausible Strands result shape")
def check_structured_shapes() -> str:
    from granted.graph import _structured
    from granted.schemas import Match

    m = Match(fit_score=70, recommend_apply=True, reasoning="x")

    class Obj:
        def __init__(self, **kw):
            self.__dict__.update(kw)

    shapes = {
        "result.structured_output":       Obj(structured_output=m),
        "result.result":                  Obj(result=m),
        "result.output":                  Obj(output=m),
        "result.result.structured_output": Obj(result=Obj(structured_output=m)),
        "result.output.structured_output": Obj(output=Obj(structured_output=m)),
        "bare pydantic in .result":       Obj(result=m),
    }
    for label, node in shapes.items():
        got = _structured(node, Match)
        assert got is m, f"{label} not extracted (got {got!r})"

    # And must NOT invent a Match out of prose or a wrong type.
    for label, node in {
        "prose only":      Obj(structured_output="the org should apply"),
        "empty":           Obj(),
        "wrong model":     Obj(structured_output=object()),
        "None throughout": Obj(structured_output=None, result=None, output=None),
    }.items():
        assert _structured(node, Match) is None, f"{label} wrongly produced a Match"
    return f"{len(shapes)} shapes extracted, 4 non-matches rejected"


@step("silence gate: full truth table")
def check_gate() -> str:
    from granted.graph import FIT_THRESHOLD, gate_passes
    from granted.schemas import Match

    class State:
        def __init__(self, match=None):
            self.results = {} if match is None else {"matcher": Node(match)}

    class Node:
        def __init__(self, match):
            self.structured_output = match

    T = FIT_THRESHOLD
    cases = [
        # (fit, recommend, blocking, expected)
        (T,     True,  [],                True),   # exactly at threshold must fire
        (T - 1, True,  [],                False),  # one below must not
        (100,   True,  [],                True),
        (100,   False, [],                False),  # model says no: respect it
        (100,   True,  ["wrong region"],  False),  # blocker overrides everything
        (T,     True,  ["wrong form"],    False),
        (0,     False, ["wrong region"],  False),
        (T + 1, True,  [],                True),
    ]
    for fit, rec, blocking, expected in cases:
        s = State(Match(fit_score=fit, recommend_apply=rec, blocking=blocking, reasoning="x"))
        got = gate_passes(s)
        assert got is expected, (
            f"fit={fit} recommend={rec} blocking={blocking}: expected {expected}, got {got}")

    # Degenerate states must be silent, never crash.
    assert gate_passes(State()) is False, "no matcher result must not fire"

    class Prose:
        structured_output = "apply!"
    s = State(); s.results = {"matcher": Prose()}
    assert gate_passes(s) is False, "unparsed prose must not fire the gate"
    return f"{len(cases)} cases + 2 degenerate, threshold {T}, boundary correct"


# ----------------------------------------------------------- the pipeline


@step("synthetic archive generates and reads back")
def check_archive(tmp: Path) -> str:
    import subprocess
    out = tmp / "archive"
    script = Path(__file__).resolve().parent / "make_synthetic_archive.py"
    r = subprocess.run([sys.executable, str(script), "--out", str(out)],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"generator failed: {r.stderr.strip()[:200]}")

    from granted.archive import load_archive
    subs, insight = load_archive(out)
    assert subs, "no submissions loaded"
    decided = [s for s in subs if s.decided]
    won = [s for s in subs if s.won]
    assert decided, "no decided submissions — outcome inference is broken"
    assert insight.brief(), "empty archive brief"
    check_archive.subs = subs
    check_archive.insight = insight
    return (f"{len(subs)} submissions, {len(decided)} decided, {len(won)} won, "
            f"confidence '{insight.confidence}'")


@step("style card measures a voice from the corpus")
def check_voice() -> str:
    from granted.voice import build_style_card, check
    card = build_style_card(check_archive.subs)
    if card is None:
        raise RuntimeError("no style card built from the synthetic corpus")
    assert card.prompt_block(), "empty prompt block"
    # A sentence stuffed with the classic tells should not come back clean.
    tells = ("We are delighted to leverage our unique holistic synergies in order to "
             "deliver impactful outcomes for our beneficiaries going forward.")
    report = check(tells, card)
    return f"style card built, tell-check on stock phrasing clean={report.clean}"


@step("the granted command dispatches to both entry points")
def check_cli() -> str:
    """A console script that imports the wrong thing fails at the user's shell,
    not here, so the dispatch is checked rather than assumed."""
    import io
    from contextlib import redirect_stderr, redirect_stdout

    from granted import cli

    out = io.StringIO()
    with redirect_stdout(out):
        assert cli.main([]) == 0, "bare invocation should print usage and succeed"
        assert cli.main(["--version"]) == 0
    text = out.getvalue()
    for expected in ("run", "onboard", __import__("granted").__version__):
        assert expected in text, f"usage does not mention {expected!r}"

    err = io.StringIO()
    with redirect_stderr(err), redirect_stdout(io.StringIO()):
        assert cli.main(["bogus"]) == 2, "unknown command must exit 2"
    assert "unknown command" in err.getvalue()

    # Both subcommands must resolve to a real callable.
    from granted.onboard import main as onboard_main
    from granted.run import main as run_main
    for fn in (run_main, onboard_main):
        params = inspect.signature(fn).parameters
        assert "prog" in params, f"{fn.__module__} cannot be renamed for the CLI"
    return "usage, --version, unknown-command exit 2, both subcommands resolve"


@step("voice and interview are reachable from a real run")
def check_wiring(tmp: Path) -> str:
    """Both modules were fully built, tested, and unreachable for weeks.

    A module nothing imports is indistinguishable from a module that does not
    exist, so this asserts the wiring rather than the behaviour: the style card
    reaches the drafter's prompt, the post-pass reaches the digest, and the
    interview has an entry point a person can actually run.
    """
    import inspect
    from granted import onboard, run, voice

    # voice: card built from the archive, injected, and checked afterwards.
    src = inspect.getsource(run)
    assert "build_style_card" in src, "run.py never builds a style card"
    assert "prompt_block()" in src, "the style card never reaches the drafter"
    assert "voice.check(" in src, "the mechanical post-pass never runs"

    card = voice.build_style_card(check_archive.subs)
    assert card is not None, "no style card from the synthetic corpus"
    assert card.prompt_block(), "empty prompt block"

    # The post-pass must actually catch the things it claims to.
    tells = voice.check(
        "We are delighted to leverage our unique holistic synergies in order to "
        "deliver impactful outcomes for our beneficiaries going forward.", card)
    assert not tells.clean, "stock consultancy phrasing passed the tell check"
    flags = run._voice_flags(tells)
    assert flags, "_voice_flags dropped everything the report found"

    # interview: an entry point that exists and refuses to clobber ORG.md.
    assert callable(onboard.main), "onboard has no main()"
    existing = tmp / "ORG.md"
    existing.write_text("# mine\n")
    rc = onboard.main(["--archive", str(tmp / "archive"), "--out", str(existing)])
    assert rc == 2, "onboard overwrote an existing ORG.md without --force"
    assert existing.read_text() == "# mine\n", "ORG.md was modified anyway"

    drafted = tmp / "ORG.new.md"
    assert onboard.main(["--archive", str(tmp / "archive"),
                         "--out", str(drafted)]) == 0, "onboard failed on a real archive"
    text = drafted.read_text()
    assert "TODO" in text, "a draft with no TODOs means gaps were invented, not asked"
    return f"style card + post-pass wired, {len(flags)} flags, onboard writes and refuses"


@step("interview finds the gaps the archive cannot fill")
def check_interview() -> str:
    from granted.interview import bootstrap, prioritise
    b = bootstrap(check_archive.subs, check_archive.insight)
    qs = b.questions
    md = b.to_markdown("Easton Community Kitchen")
    assert md.strip(), "empty ORG.md draft"
    assert prioritise(b.gaps, limit=6) is not None
    return f"{len(qs)} questions, ORG.md draft {len(md)} chars"


@step("citations index and ground claims against the real ORG.example.md")
def check_citations(tmp: Path) -> str:
    from granted.citations import find_support, index_org_file

    # Index the format the project actually ships, not a hand-rolled stand-in.
    org = Path(__file__).resolve().parents[1] / "ORG.example.md"
    if not org.exists():
        raise RuntimeError(f"{org.name} is missing from the repo root")
    idx = index_org_file(org)
    lines = sum(len(v) for v in idx.values())
    assert lines, "ORG.example.md indexed to zero citable lines"

    hit = find_support("The organisation reached 1,340 individuals in 2025-26", idx)
    assert hit is not None, "a claim supported by ORG.example.md found no source line"
    assert hit.render(), "source rendered empty"

    miss = find_support("We operate refrigerated vans across rural Cornwall", idx)
    assert miss is None, "an unsupported claim was wrongly grounded"

    # A footnote pointing at the wrong line is worse than none: it discredits
    # every other citation the moment someone checks it. These two both passed
    # under bare token overlap, matching on "awards" and "fund".
    for false_claim in (
        "Southwark received 15 awards from the London Community Foundation",
        "The funder's primary recipient legal form is GB-CHC with 86 of 200 awards",
        "The funder awarded 102% of the amount requested on average",
    ):
        wrong = find_support(false_claim, idx)
        assert wrong is None, (
            f"claim about the funder was grounded in ORG.md: {false_claim!r} "
            f"-> {wrong.line!r}")
    return (f"{len(idx)} headings, {lines} citable lines, "
            "supported hit + 4 false citations refused")


@step("register remembers, and surfaces only what changed")
def check_watcher(tmp: Path) -> str:
    from granted.watcher import Opportunity, Register, check_timeline, drift_change

    today = date(2026, 9, 9)
    closes = (today + timedelta(days=40)).isoformat()
    opp = Opportunity(funder="Quartet Community Foundation", programme="Express",
                      url="https://example.org/express", closes=closes, fit_score=80)

    reg = Register(tmp / "register.json")
    first = reg.poll([opp], today=today)
    again = reg.poll([opp], today=today)
    assert not [c for c in again if c.surfaces], "an unchanged scrape surfaced a notification"

    reg.save()
    assert Register(tmp / "register.json").items, "register did not persist"

    # A low-fit call is recorded but must stay silent.
    quiet = Opportunity(funder="Wrong Fit Trust", programme=None,
                        url="https://example.org/wrong", closes=closes, fit_score=10)
    assert not [c for c in reg.poll([opp, quiet], today=today) if c.surfaces], \
        "a below-threshold opportunity surfaced"

    # A schedule that cannot be finished must say so rather than encourage.
    steps = [{"days_before_deadline": 30, "task": "Draft", "hours": 20},
             {"days_before_deadline": 14, "task": "Trustee review", "hours": 6},
             {"days_before_deadline": 3, "task": "Submit", "hours": 2}]
    doomed = check_timeline((today + timedelta(days=2)).isoformat(), steps, done=set(), today=today)
    assert doomed.feasible is False, "an unfinishable timeline was called feasible"
    assert doomed.summary(), "empty timeline summary"
    roomy = check_timeline((today + timedelta(days=120)).isoformat(), steps, done=set(), today=today)
    assert roomy.feasible is True, "a comfortable timeline was called infeasible"
    assert drift_change(opp, doomed) is not None, "drift on a doomed timeline raised nothing"
    return (f"{len(first)} first-poll changes, repeat poll silent, "
            "infeasible/feasible both correct")


@step("drift detection: in-flight bids that are slipping")
def check_drift(tmp: Path) -> str:
    """The README's second surfacing trigger, and the one that makes this a
    background agent rather than a scorer.

    A bid is in flight once a workspace exists for it; the schedule the timeliner
    wrote lives in that workspace. This reads the plan back off disk and
    reconciles it with the calendar, which is the only part of the system whose
    state has to survive between runs.
    """
    from granted.run import ALERT_KINDS, in_flight_alerts
    from granted.schemas import Timeline, TimelineStep
    from granted.watcher import SURFACING, Opportunity, Register

    assert "new" not in ALERT_KINDS, "a new call is the decision, not an alert"
    assert ALERT_KINDS < SURFACING, "alerts must be a subset of surfacing changes"

    today = date(2026, 9, 9)
    root = tmp / "drift"

    def register_with(days: int, steps, done=None) -> Register:
        ws = root / f"ws{days}{'-done' if done else ''}"
        (ws / "05-timeline").mkdir(parents=True, exist_ok=True)
        tl = Timeline(deadline="x", total_hours_estimate=40.0,
                      steps=[TimelineStep(days_before_deadline=d, task=t,
                                          owner_role="coordinator") for d, t in steps])
        (ws / "05-timeline" / "schedule.json").write_text(tl.model_dump_json())
        if done:
            (ws / "05-timeline" / "done.txt").write_text("\n".join(done))
        opp = Opportunity(funder=f"Funder {days}", programme=None, url=f"u{days}",
                          closes=(today + timedelta(days=days)).isoformat(),
                          workspace=str(ws))
        reg = Register(root / f"r{days}{'-done' if done else ''}.json")
        reg.items = {opp.key: opp}
        return reg

    plan = [(60, "Draft the case for support"), (30, "Trustee review")]

    assert not in_flight_alerts(register_with(90, plan), today), \
        "a bid with plenty of time raised an alert"
    slipping = in_flight_alerts(register_with(10, plan), today)
    assert len(slipping) == 1, f"a slipping bid raised {len(slipping)} alerts"
    assert "Overdue" in slipping[0].detail, "the alert does not say what is overdue"
    assert slipping[0].workspace, "the alert does not point at the workspace"
    assert not in_flight_alerts(register_with(10, plan, done=[t for _, t in plan]), today), \
        "steps marked done in done.txt still counted as overdue"

    # Not in flight: no workspace means nothing has been committed to yet.
    reg = register_with(10, plan)
    next(iter(reg.items.values())).workspace = None
    assert not in_flight_alerts(reg, today), "a call with no workspace was treated as in flight"
    return "on-track silent, slipping surfaces, done.txt respected, 4 states correct"


@step("triage arithmetic, including the feasibility veto")
def check_triage() -> str:
    from granted.precedent import RevealedPreferences
    from granted.triage import Priority, rank, triage
    from granted.watcher import Opportunity

    rp = RevealedPreferences(
        funder_id="GB-CHC-1091210", funder_name="Quartet Community Foundation",
        n_grants=900, median_award=6500.0, award_p10=2000.0, award_p90=15000.0,
        median_duration_months=12.0, ask_to_award_ratio=0.82, ask_ratio_n=210,
        top_regions=[("Bristol", 540), ("Bath and North East Somerset", 190)],
        registers=[("GB-CHC", 700), ("GB-COH", 60)],
        top_programmes=[("Express Grants", 300)],
        top_classifications=[("food insecurity", 120), ("youth", 90)],
        repeat_funding_rate=0.4,
    )
    assert rp.brief(), "empty revealed-preferences brief"

    today = date(2026, 9, 9)
    opp = Opportunity(funder=rp.funder_name, programme="Express",
                      url="https://example.org/e",
                      closes=(today + timedelta(days=45)).isoformat())
    good = {"legal_form_register": "GB-CHC", "region": "Bristol",
            "themes": ["food insecurity", "youth"], "turnover": 148000}
    bad = {"legal_form_register": "GB-COH", "region": "Cornwall",
           "themes": ["heritage"], "turnover": 148000}

    strong = triage(opp, rp, good, ask=8000.0, today=today)
    weak = triage(opp, rp, bad, ask=95000.0, today=today)
    assert strong.composite > weak.composite, (
        f"a well-matched org did not outscore a mismatched one "
        f"({strong.composite:.0f} vs {weak.composite:.0f})")
    assert strong.explain(), "empty triage explanation"

    ordered = rank([weak, strong])
    assert ordered[0] is strong, "rank() did not put the stronger match first"

    # The veto only fired when a TimelineStatus already existed — which it never
    # does for a new call, because the timeliner runs after the gate. A perfect
    # match advertised two days before it closes has to stop here.
    from granted.triage import HOURS_PER_DAY, MIN_BID_HOURS, urgency

    rushed = Opportunity(funder=rp.funder_name, programme="Winter",
                         url="https://example.org/w",
                         closes=(today + timedelta(days=2)).isoformat())
    trap = triage(rushed, rp, good, ask=8000.0, today=today)
    assert trap.vetoed, "a well-matched call closing in 2 days was not vetoed"
    assert trap.priority is Priority.LOW, "a vetoed call must not outrank anything"
    assert "hours" in trap.vetoed, "the veto does not state the arithmetic"

    # The boundary is capacity, not a magic number of days.
    edge = int(MIN_BID_HOURS / HOURS_PER_DAY)          # 13 -> too tight, 14 -> fits
    assert urgency(edge, None)[1], f"{edge} days should not clear the capacity test"
    assert urgency(edge + 1, None)[1] is None, f"{edge + 1} days should clear it"
    assert urgency(None, None)[1] is None, "a rolling deadline is not a veto"
    assert urgency(-1, None)[1] == "closed", "a closed call must say so"

    return (f"aligned {strong.composite:.0f}/100 ({strong.priority.value}), "
            f"mismatched {weak.composite:.0f}/100, veto below {edge + 1} days")


@step("workspace builds the folder the gate opens")
def check_workspace(tmp: Path) -> str:
    from granted import workspace
    ws = workspace.create(tmp / "workspaces", "Quartet Community Foundation",
                          programme="Express Grants", deadline="2026-10-19", fit_score=78)
    assert ws.root.exists(), "workspace root not created"
    readmes = list(ws.root.rglob("README.md"))
    assert readmes, "no README written into the workspace"
    p = ws.write("drafts", "answer-1.md", "# Draft\n")
    assert p.exists(), "workspace.write did not produce a file"
    return f"{ws.root.name}, {len(readmes)} folders documented"


@step("graph assembles with all five agents wired")
def check_graph() -> str:
    from granted.graph import build_graph
    g = build_graph()
    nodes = sorted(g.nodes.keys()) if hasattr(g, "nodes") else []
    for expected in ("matcher", "director", "timeliner", "drafter", "auditor"):
        assert expected in nodes, f"node {expected!r} missing from the graph"
    return f"{len(nodes)} nodes: {', '.join(nodes)}"


@step("Find a grant listings parse from the page's embedded data")
def check_sources(tmp: Path) -> str:
    """The listing is read from the JSON the page embeds, not from its markup.

    A page with no data at all must raise. An empty list would read as a quiet
    day, and a broken parser passing for silence is the one failure this tool
    cannot afford to disguise.
    """
    from unittest import mock
    from granted import sources

    def page(rows, total=3):
        data = {"props": {"pageProps": {"searchResult": rows, "totalGrants": total}}}
        return ('<html><script id="__NEXT_DATA__" type="application/json">'
                + json.dumps(data) + "</script></html>")

    ok = {"grantName": "Community  Grants", "label": "community-grants-1",
          "grantFunder": "Department for Environment, Food and Rural Affairs",
          "grantApplicantType": ["Non-profit", "Public Sector"], "grantLocation": ["England"],
          "grantMinimumAward": 1500, "grantMaximumAward": 30500,
          "grantApplicationOpenDate": "2026-08-01T00:01",
          "grantApplicationCloseDate": "2026-10-23T23:59",
          "grantShortDescription": "Capital grants\n\nfor small charities."}
    private = dict(ok, grantName="Business only", label="business-only-1",
                   grantApplicantType=["Private Sector"])
    broken = {"grantFunder": "a record with no name and no link"}

    grants, failed, total = sources.parse_page(page([ok, private, broken]))
    assert (len(grants), failed, total) == (2, 1, 3), (
        f"parsed {len(grants)}, unreadable {failed}, total {total}")
    g = grants[0]
    assert g.title == "Community Grants", f"title not normalised: {g.title!r}"
    assert g.url == f"{sources.BASE}/grants/community-grants-1", g.url
    assert (g.amount_min, g.amount_max) == (1500.0, 30500.0), "award range lost"
    assert (g.opens, g.closes) == ("2026-08-01", "2026-10-23"), "dates not ISO"
    assert g.nonprofit_eligible and not grants[1].nonprofit_eligible, "non-profit filter wrong"
    try:
        sources.parse_page("<html>down for maintenance</html>")
    except sources.FindAGrantError:
        pass
    else:
        raise AssertionError("a page with no data parsed as an empty day")

    class Resp:
        def __init__(self, text):
            self.text = text

        def raise_for_status(self):
            pass

    class Session:
        def __init__(self, pages):
            self.headers, self.pages = {}, list(pages)

        def get(self, url, params=None, timeout=None):
            return Resp(self.pages.pop(0))

    ids_file = tmp / "funders.json"
    ids_file.write_text(json.dumps({
        "_comment": "funder names as Find a grant prints them",
        "Department for Environment Food and Rural Affairs": "GB-GOR-TEST"}))
    second = dict(ok, grantName="Second call", label="second-1")
    with mock.patch.object(sources, "MIN_INTERVAL", 0):
        client = sources.FindAGrant(session=Session(
            [page([ok, private], None), page([second], None), page([], None)]))
        opps = sources.opportunities(sources.load_funder_ids(ids_file), client)
    assert [o.programme for o in opps] == ["Community Grants", "Second call"], (
        f"expected the two non-profit calls across two pages, got {[o.programme for o in opps]}")
    assert client.listed == 3, f"read {client.listed} records, expected 3"
    assert all(o.funder_id == "GB-GOR-TEST" for o in opps), "funder id lost to punctuation drift"
    assert "England" in opps[0].summary, "location dropped from the call summary"
    return "embedded JSON, broken record skipped, no-data page raises, filter, 2 pages, id mapping"


@step("a slow 360Giving funder is survived, not fatal")
def check_slow_funder(tmp: Path) -> str:
    """A government department's 1,000-grant page takes over 30 seconds to serve,
    and one uncaught timeout once took down a whole live run. The client must
    ask only for what the run will read, retry once, and then raise the error a
    multi-funder run already knows how to survive."""
    import contextlib
    import io
    from unittest import mock

    import requests
    from granted import precedent
    from granted import run as run_mod
    from granted.watcher import Opportunity

    class Resp:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"results": [{"id": i} for i in range(3)], "next": None}

    class Session:
        def __init__(self, fail=0):
            self.fail, self.params = fail, []

        def get(self, url, params=None, headers=None, timeout=None):
            self.params.append(params)
            if self.fail:
                self.fail -= 1
                raise requests.ReadTimeout("slow funder")
            return Resp()

    with mock.patch.object(precedent, "MIN_INTERVAL", 0):
        s = Session()
        list(precedent.Client(s).grants_made("GB-GOR-D6", cap=300))
        assert s.params[0] == {"limit": 300}, f"asked for {s.params[0]} with a cap of 300"

        rows = list(precedent.Client(Session(fail=1)).grants_made("GB-GOR-D6", cap=300))
        assert len(rows) == 3, "one slow response was not retried"

        try:
            list(precedent.Client(Session(fail=5)).grants_made("GB-GOR-D6", cap=300))
        except precedent.ThreeSixtyGivingError:
            pass
        else:
            raise AssertionError("a persistent timeout did not become ThreeSixtyGivingError")

    # The run that crashed: live calls, one funder's history unavailable.
    org = Path(__file__).resolve().parents[1] / "demo" / "ORG.southwark.md"
    out = tmp / "slow-funder"
    live = Opportunity(funder="Department for Education (DfE)", programme="Holiday Activities",
                       url="https://example.org/dfe", funder_id="GB-GOR-D6",
                       closes=(date.today() + timedelta(days=45)).isoformat())
    with mock.patch.object(run_mod.sources, "opportunities", return_value=[live]), \
         mock.patch.object(run_mod, "profile_funder",
                           side_effect=precedent.ThreeSixtyGivingError("did not respond")), \
         mock.patch.object(run_mod, "build_graph") as bg, \
         contextlib.redirect_stderr(io.StringIO()):
        rc = run_mod.main(["--calls", "find-a-grant", "--org", str(org),
                           "--out", str(out), "--quiet"])
    assert rc == 0, f"one unavailable funder ended the run (exit {rc})"
    bg.assert_not_called()
    d = json.loads((out / "data" / "run.json").read_text(encoding="utf-8"))
    reason = d["not_pursued"][0]["reasons"][0]
    assert "360Giving could not supply" in reason, f"reason not reported: {reason}"
    assert d["not_pursued"][0]["basis"] == "stated criteria", "no fallback to the call's criteria"
    return "page size follows the cap, one retry, timeout -> reported, run completes"



@step("Word and PDF: past bids are read, bid documents are written")
def check_documents(tmp: Path) -> str:
    """A charity's archive is Word and PDF, not markdown. If those are not read,
    the style card and the track record learn nothing from the files that matter."""
    import zipfile
    from granted import documents
    from granted.archive import load_archive

    folder = tmp / "docs-archive"
    folder.mkdir(parents=True, exist_ok=True)
    body = ("We run a community kitchen in Peckham. Our members are local households "
            "facing hardship, and they tell us the meals matter. ") * 4
    docx = documents.write_docx(folder / "2024 Local Trust - successful.docx", [
        ("title", "Local Trust application"), ("p", body),
        ("table", [["Item", "Cost"], ["Freezer", "£4,000"]])])
    with zipfile.ZipFile(docx) as z:
        names = set(z.namelist())
    for part in ("[Content_Types].xml", "_rels/.rels", "word/document.xml", "word/styles.xml"):
        assert part in names, f"{part} missing from the .docx"
    text = documents.read_text(docx)
    assert "community kitchen in Peckham" in text and "£4,000" in text, "docx text not read back"

    # A real one-page PDF, built by hand so the test needs no other tool.
    line = "Unsuccessful: the panel preferred larger organisations."
    stream = f"BT /F1 12 Tf 72 720 Td ({line}) Tj ET".encode()
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
            b"/Resources << /Font << /F1 5 0 R >> >> >>",
            b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    pdf, offsets = bytearray(b"%PDF-1.4\n"), []
    for i, obj in enumerate(objs, 1):
        offsets.append(len(pdf))
        pdf += b"%d 0 obj\n" % i + obj + b"\nendobj\n"
    xref = len(pdf)
    pdf += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    pdf += b"".join(b"%010d 00000 n \n" % o for o in offsets)
    pdf += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    (folder / "2023 City Fund.pdf").write_bytes(bytes(pdf))
    assert line in documents.read_text(folder / "2023 City Fund.pdf"), "PDF text not read"

    # A broken file costs its text, never the scan.
    (folder / "broken.docx").write_bytes(b"not a zip")
    assert documents.read_text(folder / "broken.docx") == ""
    subs, _ = load_archive(folder)
    texts = [s.text for s in subs]
    assert any("community kitchen" in t for t in texts), "the archive did not read the Word bid"
    assert any(line in t for t in texts), "the archive did not read the PDF bid"
    return f"docx written and read back, PDF read, broken file tolerated, {len(subs)} bids loaded"


@step("a Granted folder sets up, runs from its settings, and survives a move")
def check_home(tmp: Path) -> str:
    """The installable shape: settings in granted.json, bids as folders of Word
    documents, credentials from outside the folder, and every path relative."""
    import contextlib
    import io
    import os
    import shutil
    from unittest import mock

    from granted import documents
    from granted import home as home_mod
    from granted import run as run_mod
    from granted.precedent import RevealedPreferences
    from granted.schemas import (Claim, Draft, GroundingAudit, Match, Timeline,
                                 TimelineStep)

    repo = Path(__file__).resolve().parents[1]
    base = tmp / "Rye Lane"
    root = base / "Granted"
    archive = base / "Funding" / "Past bids"
    shutil.copytree(repo / "fixtures" / "rye-lane-funding", archive)

    with contextlib.redirect_stdout(io.StringIO()):
        rc = home_mod.main(["--home", str(root), "--name", "Rye Lane Community Kitchen",
                            "--archive", str(archive)])
    assert rc == 0, f"setup exited {rc}"
    for name in ("granted.json", "ORG.md", "funders.json", "Dashboard.html",
                 "Bid folders.html", "Bids", "Daily notes", ".granted"):
        assert (root / name).exists(), f"setup did not make {name}"
    cfg = json.loads((root / "granted.json").read_text(encoding="utf-8"))
    assert cfg["archive"] == os.path.join("..", "Funding", "Past bids"), (
        f"archive not stored relative to the folder: {cfg['archive']}")
    drafted = (root / "ORG.md").read_text(encoding="utf-8")
    assert "TODO" in drafted, "ORG.md was not drafted"
    assert drafted.startswith("# Rye Lane Community Kitchen"), "the draft lost the organisation's name"

    # A charity with no past bids yet: a blank template with its name, its own
    # guide never read as a bid, and no legal form claimed from a question.
    from granted import org as org_mod
    fresh = tmp / "Fresh" / "Granted"
    with contextlib.redirect_stdout(io.StringIO()):
        assert home_mod.main(["--home", str(fresh), "--name", "St Mary's Youth Project"]) == 0
    blank = org_mod.load(fresh / "ORG.md")
    assert blank.name == "St Mary's Youth Project", f"blank ORG.md named {blank.name!r}"
    assert blank.legal_form_register is None, "a blank ORG.md claimed a legal form"
    assert "README" not in (fresh / "ORG.md").read_text(encoding="utf-8"), "the guide was read as a bid"
    asked = org_mod.parse("## Identity\n- TODO: Are you a charity, a CIO, a CIC, or something else?\n")
    assert asked.legal_form_register is None, "a TODO question was read as a legal form"

    # The demo's Southwark ORG.md carries a registered number; calls come from a file.
    shutil.copy(repo / "demo" / "ORG.southwark.md", root / "ORG.md")
    closes = (date.today() + timedelta(days=45)).isoformat()
    (root / "calls.json").write_text(json.dumps({"calls": [{
        "funder": "The Clothworkers Foundation", "programme": "Small Grants Programme",
        "url": "https://example.org/small", "closes": closes, "amount_max": 30000,
        "questions": ["What difference will it make?"]}]}), encoding="utf-8")
    cfg.update(calls="calls.json", funder="GB-CHC-274100")
    (root / "granted.json").write_text(json.dumps(cfg), encoding="utf-8")

    rp = RevealedPreferences(
        funder_id="GB-CHC-274100", funder_name="The Clothworkers Foundation",
        n_grants=300, median_award=12200.0, award_p10=4500.0, award_p90=40000.0,
        median_duration_months=12.0, ask_to_award_ratio=0.8, ask_ratio_n=120,
        top_regions=[("London", 58)], registers=[("GB-CHC", 240)],
        top_programmes=[("Small Grants Programme", 200)],
        top_classifications=[("food insecurity", 60)], repeat_funding_rate=0.0)

    class Obj:
        def __init__(self, **kw):
            self.__dict__.update(kw)

    results = {
        "matcher": Obj(structured_output=Match(
            fit_score=78, recommend_apply=True, suggested_ask_gbp=12200, reasoning="fits")),
        "director": Obj(result="Lead with the freezer.\n\nThen the van."),
        "timeliner": Obj(structured_output=Timeline(
            deadline=closes, total_hours_estimate=30, steps=[
                TimelineStep(days_before_deadline=30, task="Draft the narrative",
                             owner_role="Coordinator"),
                TimelineStep(days_before_deadline=10, task="Trustee review",
                             owner_role="Chair")])),
        "drafter": Obj(structured_output=Draft(
            question="What difference will it make?",
            answer="We served 7,400 meals across 148 sittings.",
            claims=[Claim(text="We served 7,400 meals across 148 sittings",
                          source_line="Volume 2025-26: 7,400 meals served across 148 sittings",
                          grounded=True)])),
        "auditor": Obj(structured_output=GroundingAudit(
            total_claims=1, grounded_claims=1, ungrounded=[], verdict="pass")),
    }
    graph = mock.MagicMock(return_value=Obj(results=results))
    creds = tmp / "home-credentials.env"
    # With a byte-order mark, the way Windows PowerShell writes files.
    creds.write_text("﻿GRANTED_TEST_TOKEN=from-the-file\n", encoding="utf-8")

    def go(folder: Path) -> dict:
        cwd = os.getcwd()
        try:
            with mock.patch.dict(os.environ, {"GRANTED_CREDENTIALS": str(creds)}), \
                 mock.patch.object(run_mod, "profile_funder", return_value=(rp, [])), \
                 mock.patch.object(run_mod, "build_graph", return_value=graph):
                os.environ.pop("GRANTED_TEST_TOKEN", None)
                assert run_mod.main(["--home", str(folder), "--quiet"]) == 0, "run failed"
                assert os.environ.get("GRANTED_TEST_TOKEN") == "from-the-file", (
                    "the credentials file was not loaded")
        finally:
            os.chdir(cwd)
        return json.loads((folder / ".granted" / "run.json").read_text(encoding="utf-8"))

    d = go(root)
    bids = [p for p in (root / "Bids").iterdir() if p.is_dir()]
    assert len(bids) == 1, f"expected one bid folder, found {[b.name for b in bids]}"
    bid = bids[0]
    expected = f"{date.today().isoformat()} The Clothworkers Foundation - Small Grants Programme"
    assert bid.name == expected, f"bid folder named {bid.name!r}"
    for rel in ("01-decision/Decision.docx", "02-direction/Direction.docx",
                "03-drafts/Draft answer v1.docx", "04-evidence/Evidence and sources.docx",
                "05-timeline/Timeline.docx"):
        assert documents.read_text(bid / rel), f"{rel} missing or empty"
    assert "Trustee review" in documents.read_text(bid / "05-timeline" / "Timeline.docx")
    assert list((root / "Daily notes").glob("*.md")), "no daily note written"
    assert d["org_id"] == "GB-CHC-1178432", f"org_id {d['org_id']!r}"
    assert d["surfaced"][0]["fit"] == 78
    register = json.loads((root / ".granted" / "register.json").read_text(encoding="utf-8"))
    paths = [v["workspace"] for v in register.values() if v.get("workspace")]
    assert paths and all(not Path(p).is_absolute() and Path(p).parts[0] == "Bids" for p in paths), (
        f"register holds paths that will not survive a move: {paths}")
    folders = (root / "Bid folders.html").read_text(encoding="utf-8")
    assert "Draft answer v1" in folders and "Bids/" in folders, "the bid folders page misses the Word files"
    page = (root / "Dashboard.html").read_text(encoding="utf-8")
    assert "window.__resources" in page and "<x-dc" in page, "the designed dashboard is not in the folder"
    js = (root / ".granted" / "run.js").read_text(encoding="utf-8")
    prefix = "window.__GRANTED_RUN__ = "
    assert js.startswith(prefix), "run.js is not the script the dashboard loads"
    assert json.loads(js[len(prefix):].rstrip().rstrip(";"))["org_id"] == "GB-CHC-1178432", "run.js lost the record"

    # Move the whole folder: the bid must still be found, and treated as in flight.
    moved = tmp / "Moved" / "Granted"
    shutil.copytree(root, moved)
    graph.reset_mock()
    d = go(moved)
    graph.assert_not_called()
    assert d["watcher_state"].get("in_flight"), "the moved folder lost track of its bid"
    return "setup, run from granted.json, 5 Word files, relative paths, credentials, moved folder"



@step("publishing withholds what a public page should not show")
def check_publish_redaction() -> str:
    """A run record is written for the organisation's own folder. Published, the
    names of the files it refused to read, and a path with a user name in it,
    would be readable by anyone with the link."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("publish", Path(__file__).with_name("publish.py"))
    publish = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(publish)

    record = {"scope": {"files_read": 20, "files_skipped": 1, "skipped_detail": [
                  {"name": "Safeguarding log - J Smith.docx",
                   "reason": "name matches sensitive-record pattern"}]},
              "workspace": "/home/jsmith/OneDrive/Granted/Bids/2026-09-10 Funder - Programme",
              "surfaced": []}
    out = publish.redact(record)
    text = json.dumps(out)
    assert "J Smith" not in text and "jsmith" not in text, "the published record still names a person"
    assert out["scope"]["files_skipped"] == 1 and out["scope"]["skipped_detail"][0]["reason"], (
        "redaction dropped the count or the reason")
    assert out["workspace"] == "2026-09-10 Funder - Programme", out["workspace"]
    assert record["scope"]["skipped_detail"][0]["name"].startswith("Safeguarding"), (
        "redact() changed the record it was given")
    return "skipped file names withheld, workspace path trimmed, counts kept, original untouched"



@step("location: a postcode or a place name becomes the labels calls use")
def check_location(tmp: Path) -> str:
    import os
    from unittest import mock
    from granted import location
    from granted import org as org_mod

    class Resp:
        def __init__(self, status, result):
            self.status_code, self._r = status, result

        def raise_for_status(self):
            pass

        def json(self):
            return {"result": self._r}

    eastons = [
        {"name_1": "Easton", "local_type": "Village", "country": "England", "region": "East of England",
         "county_unitary": "Norfolk", "district_borough": "South Norfolk"},
        # Same region as Bristol's Easton: only the county tells them apart.
        {"name_1": "Easton", "local_type": "Village", "country": "England", "region": "South West",
         "county_unitary": "Devon", "district_borough": "South Hams"},
        {"name_1": "Easton", "local_type": "Suburban Area", "country": "England", "region": "South West",
         "county_unitary": "City of Bristol", "district_borough": "Bristol"}]

    class Session:
        def __init__(self):
            self.calls = []

        def get(self, url, timeout=None):
            self.calls.append(url)
            if "/postcodes/BS5%206JY" in url:
                return Resp(200, {"country": "England", "region": "South West",
                                  "admin_district": "Bristol, City of", "admin_county": None})
            if "/places?q=Bristol" in url:
                return Resp(200, [{"name_1": "Bristol", "local_type": "City", "country": "England",
                                   "region": "South West", "county_unitary": "City of Bristol",
                                   "district_borough": None}])
            if "/places?q=Easton" in url:
                return Resp(200, eastons)
            return Resp(404, None)

    by_postcode = org_mod.parse("## Identity\n- Postcode: bs5 6jy\n- Area served: Easton, Bristol\n")
    place = location.resolve(by_postcode, cache=tmp / "loc.json", session=Session())
    assert (place.region, place.district) == ("South West", "Bristol"), place
    labels = place.funding_labels()
    assert {"england", "south west england", "bristol"} <= labels, labels
    again = Session()
    assert location.resolve(by_postcode, cache=tmp / "loc.json", session=again) == place
    assert not again.calls, "a cached place was looked up again"

    by_area = org_mod.parse("## Identity\n- Area served: Easton, Lawrence Hill and St Pauls wards, Bristol\n")
    place = location.resolve(by_area, session=Session())
    assert (place.region, place.district) == ("South West", "Bristol"), f"picked the wrong Easton: {place}"
    assert place.contains("Bristol") == "local" and place.contains("South West") == "regional"
    assert place.contains("Camden") is None
    with mock.patch.dict(os.environ, {"GRANTED_OFFLINE": "1"}):
        assert location.resolve(by_area) is None, "looked a place up while offline"
    return "postcode, place names read widest first (the Bristol Easton), cache, offline"


@step("stated criteria: calls with no award history are judged on what they say")
def check_criteria() -> str:
    from granted import criteria
    from granted import org as org_mod
    from granted.location import Place
    from granted.watcher import Opportunity

    charity = org_mod.parse(
        "# Kitchen\n## Identity\n- Legal form: Charitable Incorporated Organisation\n"
        "- Registered number: GB-CHC-1178432\n- Area served: Southwark, London\n"
        "## Scale\n- Annual turnover 2025-26: £136,000\n"
        "## Programmes\n- Community meals and a food pantry\n")
    cic = org_mod.parse("# Kitchen CIC\n## Identity\n- Legal form: Community Interest Company\n"
                        "- Registered number: GB-COH-01234567\n")
    london = Place("England", "London", "Greater London", "Southwark")
    today = date.today()
    closes = (today + timedelta(days=45)).isoformat()

    def call(**kw):
        base = dict(funder="Funder", programme="Fund", url="https://example.org", closes=closes)
        base.update(kw)
        return Opportunity(**base)

    assert criteria.gatekeep(call(locations=["Scotland"]), charity, london).startswith("this call is for Scotland")
    assert criteria.gatekeep(call(locations=["England", "Midlands"]), charity, london) is None
    assert criteria.gatekeep(call(locations=["National"]), charity, london) is None
    assert criteria.gatekeep(call(locations=["South West England"]), charity, london), (
        "a South West call was open to a London charity")
    business = "To lead a project your organisation must be a UK registered business of any size."
    assert criteria.gatekeep(call(eligibility=business), charity, london), "a charity could enter a business-only call"
    assert criteria.gatekeep(call(eligibility=business), cic, london) is None, "a company was barred from a business call"
    assert criteria.gatekeep(call(eligibility="UK registered organisations can apply, including charities."),
                             charity, london) is None

    fits = criteria.assess(call(summary="Grants for community meals and food pantry projects.",
                                amount_max=20000), charity, london, today=today)
    misses = criteria.assess(call(summary="Zero emission vessels and port infrastructure."),
                             charity, london, today=today)
    big = criteria.assess(call(summary="Community meals.", amount_min=500000, amount_max=2000000),
                          charity, london, today=today)
    assert fits.basis == criteria.STATED and fits.composite >= 40, f"a matching call scored {fits.composite:.0f}"
    assert misses.composite < 40, f"an unrelated call scored {misses.composite:.0f}"
    assert criteria.stop_reason(misses, 40).startswith("nothing in the call matches"), criteria.stop_reason(misses, 40)
    assert big.alignment.reasons[0].startswith("the smallest award"), big.alignment.reasons

    # A strong track record must not carry an unrelated call over the floor.
    import types
    winning = types.SimpleNamespace(n_decided=14, win_rate=0.64, confidence="indicative")
    unrelated = criteria.assess(call(summary="International distribution for UK films.", amount_max=50000),
                                charity, london, insight=winning, today=today)
    assert unrelated.composite <= criteria.OFF_TOPIC_CAP, (
        f"a 64% win rate lifted an unrelated call to {unrelated.composite:.0f}")
    research = call(summary="Applicants must be based at a UK research organisation eligible for MRC funding.")
    assert criteria.gatekeep(research, charity, london).startswith("only UK research organisations"), (
        "a research-only fellowship was open to a community charity")
    assert criteria.assess(call(summary="Community meals.", amount_max=2), charity, london,
                           today=today).alignment.reasons[0].find("£2") == -1, "a £2 placeholder was scored"

    # A sample of a large funder's awards says nothing about repeat funding.
    from granted.precedent import RevealedPreferences
    from granted.triage import score_plausibility
    lottery = RevealedPreferences(
        funder_id="GB-GOR-PB188", funder_name="The National Lottery Community Fund", n_grants=300,
        median_award=10000.0, award_p10=1000.0, award_p90=20000.0, median_duration_months=12.0,
        ask_to_award_ratio=None, ask_ratio_n=0, top_regions=[], registers=[], top_programmes=[],
        top_classifications=[], repeat_funding_rate=0.0, n_published=200000)
    assert lottery.sampled and "not measurable from a sample" in lottery.brief()
    assert not any("repeat" in r for r in score_plausibility(lottery, None, {}).reasons), (
        "a sampled repeat rate still reached the reasons")

    # A programme's stated range beats a sample of its funder's history: a sample
    # of 300 Awards for All grants must not rule out Reaching Communities.
    from dataclasses import replace
    from granted.triage import triage
    all_10k = replace(lottery, award_p10=10000.0, award_p90=10000.0)
    reaching = call(funder="The National Lottery Community Fund", programme="Reaching Communities England",
                    amount_min=20001, amount_max=500000, closes=None)
    verdict = triage(reaching, all_10k, {"legal_form_register": "GB-CHC"}, ask=20001.0, today=today)
    assert not any("wrong funder" in r for r in verdict.alignment.reasons), verdict.alignment.reasons
    assert any("stated range" in r for r in verdict.alignment.reasons), verdict.alignment.reasons

    # General programmes name no cause and must never be capped for it; an
    # off-topic call from a funder that rarely funds charities is stopped free.
    general = criteria.assess(call(summary="Capital grants to smaller UK charities for buildings and equipment."),
                              charity, london, today=today)
    assert general.composite > criteria.OFF_TOPIC_CAP, f"a general charity fund was capped at {general.composite:.0f}"
    assert not general.alignment.reasons[0].startswith("nothing"), general.alignment.reasons
    transport = replace(lottery, funder_name="Department for Transport",
                        registers=[("GB-COH", 280), ("GB-CHC", 12)])
    ev = call(funder="Department for Transport", programme="Electric Car Grant",
              summary="Discounts on new electric cars.")
    capped = criteria.cap_off_topic(triage(ev, transport, {"legal_form_register": "GB-CHC"}, today=today),
                                    ev, charity, transport)
    assert capped.composite <= criteria.OFF_TOPIC_CAP, f"an off-topic transport call scored {capped.composite:.0f}"
    assert "rarely funds charities" in capped.alignment.reasons[0], capped.alignment.reasons
    charitable = replace(lottery, registers=[("GB-CHC", 170), ("GB-COH", 130)])
    kept = criteria.cap_off_topic(triage(ev, charitable, {"legal_form_register": "GB-CHC"}, today=today),
                                  ev, charity, charitable)
    assert not kept.alignment.reasons[0].startswith("nothing"), "a mostly-charity funder's call was capped"

    # The model sees a programme's own range, not a sample of other programmes.
    from granted.precedent import for_call
    shown, view = for_call(all_10k, reaching)
    assert view == "no-amounts" and shown.median_award is None, (view, shown.median_award)
    assert "not shown" in shown.brief() and "Award size: median" not in shown.brief(), shown.brief()
    clothworkers = replace(lottery, award_p10=4500.0, award_p90=40000.0, median_award=12200.0)
    small = call(funder="The Clothworkers Foundation", programme="Small Grants Programme", amount_max=30000)
    assert for_call(clothworkers, small) == (clothworkers, "funder"), "an overlapping range lost its figures"
    return (f"location and business-only vetoes, companies allowed; matching call "
            f"{fits.composite:.0f}/100, unrelated {misses.composite:.0f}/100")


@step("National Lottery and Innovate UK parse, and a call listed twice is judged once")
def check_more_sources(tmp: Path) -> str:
    import argparse
    import contextlib
    import io
    from unittest import mock
    from granted import run as run_mod
    from granted import sources

    card = ('<div class="card mb-4"> <div class="card-body"> <h2><a href="/funding/funding-programmes/{slug}">'
            '{title}</a></h2> <p>{desc}</p> </div> <div class="card-footer"> <ul> '
            '<li><strong>Project location:</strong> {where}</li> '
            '<li> <strong>Amount: </strong> &#xA3;300 to &#xA3;20,000 </li> '
            '<li><strong>A decision in</strong>: 12 weeks</li> '
            '<li> <strong>Programme status:</strong> {status} </li> </ul> </div> </div>')
    page = "<html>" + "".join([
        card.format(slug="awards-for-all-england", title="National Lottery Awards for All England",
                    desc="Small grants for communities.", where="England", status="Open to applications"),
        card.format(slug="community-action", title="Community Action", desc="Scotland.",
                    where="Scotland", status="Open to applications"),
        card.format(slug="later", title="Later Fund", desc="Soon.", where="England", status="Coming soon"),
    ]) + "</html>"
    found, not_open, failed = sources.parse_lottery(page)
    assert (len(found), not_open, failed) == (2, 1, 0), (len(found), not_open, failed)
    a4a = found[0]
    assert a4a.funder_id == sources.LOTTERY_ID and a4a.locations == ["England"], a4a
    assert (a4a.amount_min, a4a.amount_max, a4a.closes) == (300.0, 20000.0, None), a4a
    try:
        sources.parse_lottery("<html>down for maintenance</html>")
    except sources.SourceError:
        pass
    else:
        raise AssertionError("an empty Lottery page parsed as no programmes")

    comp = ('<h2 class="govuk-heading-m govuk-!-margin-top-0"> <a class="govuk-link" '
            'href="/competition/2542/overview/abc">Engineering Biology R&amp;D</a> </h2> '
            '<div class="wysiwyg-styles govuk-!-margin-bottom-4">UK registered businesses can apply '
            'for a share of up to £8.5 million.</div> '
            '<h3 class="govuk-heading-s govuk-!-margin-bottom-0">Eligibility</h3> '
            '<div class="wysiwyg-styles"><div> Your organisation must be a UK registered business. </div></div> '
            '<h3 class="govuk-heading-s govuk-!-margin-bottom-0">Open now</h3> <dl> <dt>Opened:</dt> '
            '<dd >8 September 2026</dd> <dt>Closes:</dt> <dd>3 November 2026 11:00am</dd> </dl>')
    iuk = sources.parse_innovate(f"<ul>{comp}</ul>")
    assert len(iuk) == 1 and iuk[0].programme == "Engineering Biology R&D", iuk
    assert (iuk[0].opens, iuk[0].closes, iuk[0].amount_max) == ("2026-09-08", "2026-11-03", 8500000.0), iuk[0]
    assert "UK registered business" in iuk[0].eligibility
    assert sources.parse_innovate("<html>no results</html>") == []

    def listing(title, url, funder):
        return sources.GrantListing(title=title, url=url, description="", funder=funder).to_opportunity()

    fag = [listing("Engineering Biology R&D", "https://x/1", "Innovate UK, part of UKRI"),
           listing("Youth Jobs Grant", "https://x/2", "Department for Work and Pensions"),
           listing("Youth Jobs Grant", "https://x/3", "Department for Work and Pensions"),
           listing("Small Grants Programme", "https://x/4", "Funder A"),
           listing("Small Grants Programme", "https://x/5", "Funder B")]

    def fetch(name, ids=None, session=None):
        if name == "find-a-grant":
            return fag
        if name == "innovate-uk":
            return iuk
        raise sources.SourceError("down for maintenance")

    args = argparse.Namespace(calls="find-a-grant,innovate-uk,national-lottery",
                              funder=None, funder_ids=None)
    with mock.patch.object(run_mod.sources, "fetch_source", side_effect=fetch), \
         contextlib.redirect_stderr(io.StringIO()):
        calls, report = run_mod.read_calls(args)
    titles = sorted((o.programme, o.funder) for o in calls)
    assert [t for t, _ in titles] == ["Engineering Biology R&D", "Small Grants Programme",
                                      "Small Grants Programme", "Youth Jobs Grant"], titles
    assert report["find-a-grant"] == 5 and report["innovate-uk"] == 1, report
    assert str(report["national-lottery"]).startswith("unreadable"), report
    return "Lottery (open only), Innovate UK dates and eligibility, duplicates once, one source down"


@step("local funders: award histories say who funds near you")
def check_local(tmp: Path) -> str:
    from unittest import mock
    from granted import local
    from granted.location import Place
    from granted.precedent import Grant

    def grant(region, amount=5000, when="2026-03-01"):
        return Grant(grant_id="g", title="", description="", amount_awarded=amount,
                     amount_applied_for=None, award_date=when, duration_months=None,
                     recipient_id="GB-CHC-1", recipient_name="x", region=region, country=None,
                     postcode=None, programme=None)

    histories = {
        "GB-CHC-1091263": [grant("Southwark")] * 6 + [grant("Camden")] * 4,
        "GB-LAE-CMD": [grant("Camden")] * 10,
        "GB-GOR-PB188": [grant("London", 9000)] * 7 + [grant("Leeds")] * 3,
        "GB-CHC-999": [grant("Southwark")] * 9,
        "GB-CHC-1052061": [grant("London")] * 5 + [grant("Chelmsford")] * 95,
    }
    funders = [
        {"org_id": "GB-CHC-1091263", "name": "The London Community Foundation", "website": "https://londoncf.org.uk"},
        {"org_id": "GB-LAE-CMD", "name": "Camden Council", "website": None},
        {"org_id": "GB-GOR-PB188", "name": "The National Lottery Community Fund", "website": None},
        {"org_id": "GB-CHC-999", "name": "Wellcome Research Trust", "website": None},
        {"org_id": "GB-CHC-1052061", "name": "Essex Community Foundation", "website": None},
    ]
    here = Place("England", "London", "Greater London", "Southwark")
    found = local.near(here, funders, fetch=lambda oid: histories[oid])
    names = [f["name"] for f in found]
    assert names == ["The London Community Foundation", "The National Lottery Community Fund"], names
    assert found[0]["awards_near_you"] == 6 and found[1]["awards_in_region"] == 7, found

    asked = []

    def counting(oid):
        asked.append(oid)
        return histories[oid]

    cache = tmp / "local-funders.json"
    with mock.patch.object(local, "publishers", return_value=funders):
        first = local.refresh(here, cache, fetch=counting)
        n = len(asked)
        again = local.refresh(here, cache, fetch=counting)
    assert first == again and len(asked) == n, "a week-old list was rebuilt instead of read back"
    assert local.refresh(None, cache) == []
    return "a borough funder and a regional one; the other borough's council and a non-candidate left out; weekly cache"



@step("the register tracks a bid that surfaced late, and an outage is not a withdrawal")
def check_register_memory(tmp: Path) -> str:
    """Calls are now seen for days before they clear the gate, and three sources
    mean one can be down while the others are read."""
    from granted.watcher import Opportunity, Register

    path = tmp / "register-memory.json"
    day = date(2026, 9, 10)

    def a4a(**kw):
        base = dict(funder="The National Lottery Community Fund", programme="Awards for All England",
                    url="https://example.org/a4a", source="national-lottery")
        base.update(kw)
        return Opportunity(**base)

    reg = Register(path)
    reg.poll([a4a(fit_score=15)], today=day, min_fit=65)
    reg.save()
    reg = Register(path)
    reg.poll([a4a(fit_score=82, workspace="Bids/2026-09-11 Awards for All")],
             today=day + timedelta(days=1), min_fit=65)
    reg.save()
    known = Register(path).items[a4a().key]
    assert known.workspace == "Bids/2026-09-11 Awards for All", "a bid that surfaced late was never tracked"
    assert known.fit_score == 82, f"fit left at {known.fit_score}"

    reg = Register(path)
    other = Opportunity(funder="DfT", programme="Something", url="https://example.org/dft",
                        source="find-a-grant")
    changes = reg.poll([other], today=day + timedelta(days=2), min_fit=65, sources={"find-a-grant"})
    assert reg.items[a4a().key].status != "withdrawn", "an unread source's calls were marked withdrawn"
    assert not [c for c in changes if c.kind == "withdrawn"], "an outage raised a withdrawal alert"
    changes = reg.poll([other], today=day + timedelta(days=3), min_fit=65,
                       sources={"find-a-grant", "national-lottery"})
    assert reg.items[a4a().key].status == "withdrawn", "a call that really vanished was not withdrawn"
    return "late surfacing tracked, outage ignored, a real withdrawal still caught"


# Every top-level field the published dashboard reads (granted.dc.html).
DASHBOARD_KEYS = (
    "run_date", "run_time", "model", "provider", "duration_s", "model_calls",
    "threshold", "calls_considered", "schema_version", "bands", "surfaced",
    "not_pursued", "watcher_state", "changes", "archive", "revisit", "scope",
    "voice", "grounded", "total_claims", "org", "workspace", "direction", "draft",
    "timeline", "files_written", "outcome", "_produced_by",
)


@step("every run writes the dashboard's run.json, and it agrees with the digest")
def check_run_record(tmp: Path) -> str:
    """The published dashboard renders from data/run.json and nothing else.

    The real pipeline, with only the model and 360Giving stubbed: once where the
    gate fires, again on the same folder with that bid now in flight, once where
    triage vetoes the only call, and once on a live call whose funder has no
    known award history. Each run must write run.json, and the fit it shows must
    be the fit the digest shows.
    """
    import contextlib
    import io
    from unittest import mock

    from granted import run as run_mod
    from granted.precedent import RevealedPreferences
    from granted.schemas import (Claim, Draft, GroundingAudit, Match, Timeline,
                                    TimelineStep)
    from granted.watcher import Opportunity

    org = Path(__file__).resolve().parents[1] / "demo" / "ORG.southwark.md"
    archive = tmp / "archive"
    today = date.today()
    closes = (today + timedelta(days=45)).isoformat()

    rp = RevealedPreferences(
        funder_id="GB-CHC-274100", funder_name="The Clothworkers Foundation",
        n_grants=300, median_award=12200.0, award_p10=4500.0, award_p90=40000.0,
        median_duration_months=12.0, ask_to_award_ratio=0.8, ask_ratio_n=120,
        top_regions=[("London", 58), ("Southwark", 12)],
        registers=[("GB-CHC", 240), ("GB-COH", 20)],
        top_programmes=[("Small Grants Programme", 200)],
        top_classifications=[("food insecurity", 60), ("financial hardship", 40)],
        repeat_funding_rate=0.0,
    )

    class Obj:
        def __init__(self, **kw):
            self.__dict__.update(kw)

    unfounded = "A van will double our reach"
    results = {
        "matcher": Obj(structured_output=Match(
            fit_score=78, recommend_apply=True, suggested_ask_gbp=12200, reasoning="fits")),
        "director": Obj(result="Lead with the freezer.\n\nThen the van."),
        "timeliner": Obj(structured_output=Timeline(
            deadline=closes, total_hours_estimate=30, steps=[
                TimelineStep(days_before_deadline=30, task="Draft the narrative",
                             owner_role="Coordinator"),
                TimelineStep(days_before_deadline=10, task="Trustee review",
                             owner_role="Chair")])),
        "drafter": Obj(structured_output=Draft(
            question="What difference will it make?",
            answer="We served 7,400 meals across 148 sittings. A van will double our reach.",
            claims=[Claim(text="We served 7,400 meals across 148 sittings",
                          source_line="Volume 2025-26: 7,400 meals served across 148 sittings",
                          grounded=True),
                    Claim(text=unfounded, grounded=False)])),
        "auditor": Obj(structured_output=GroundingAudit(
            total_claims=2, grounded_claims=1, ungrounded=[unfounded], verdict="revise")),
    }
    graph = mock.MagicMock(return_value=Obj(results=results))

    def call(**kw):
        row = {"funder": "The Clothworkers Foundation", "programme": "Small Grants Programme",
               "url": "https://example.org/small", "closes": closes, "amount_max": 30000,
               "questions": ["What difference will it make?"]}
        row.update(kw)
        return row

    def go(out: Path, calls: list[dict]) -> dict:
        f = tmp / f"{out.name}-calls.json"
        f.write_text(json.dumps({"calls": calls}))
        argv = ["--funder", "GB-CHC-274100", "--org", str(org), "--calls", str(f),
                "--out", str(out), "--quiet"]
        if archive.exists():
            argv += ["--archive", str(archive)]
        with mock.patch.object(run_mod, "profile_funder", return_value=(rp, [])), \
             mock.patch.object(run_mod, "build_graph", return_value=graph):
            assert run_mod.main(argv) == 0, "run exited non-zero"
        record = out / "data" / "run.json"
        assert record.exists(), f"{record} was not written"
        return json.loads(record.read_text(encoding="utf-8"))

    # 1. The gate fires.
    out = tmp / "record-decision"
    d = go(out, [call()])
    missing = [k for k in DASHBOARD_KEYS if k not in d]
    assert not missing, f"run.json lacks fields the dashboard reads: {missing}"
    assert d["schema_version"] == 2
    assert len(d["surfaced"]) == 1 and not d["not_pursued"], (
        f"surfaced {len(d['surfaced'])}, not pursued {d['not_pursued']}")
    row = d["surfaced"][0]
    assert row["fit"] == 78 and row["stage"] == "surfaced", f"surfaced row wrong: {row}"
    md = next((out / "digests").glob("*.md")).read_text(encoding="utf-8")
    assert "78/100" in md, "the digest and run.json disagree on fit"
    assert d["direction"] == ["Lead with the freezer.", "Then the van."], d["direction"]
    assert [s["task"] for s in d["timeline"]["steps"]] == ["Draft the narrative", "Trustee review"]
    assert d["audit"]["verdict"] == "revise" and d["audit"]["ungrounded"] == [unfounded]
    assert d["total_claims"] == 3 and d["grounded"] < 3, (
        f"expected 2 drafted claims plus the ask's anchor, some ungrounded; "
        f"got {d['grounded']}/{d['total_claims']}")
    names = {f["name"] for f in d["files_written"]}
    for expected in ("04-evidence/claims.md", "05-timeline/schedule.json",
                     "06-submitted/outcome.meta.json"):
        assert expected in names, f"{expected} missing from files_written"
    assert d["outcome"]["status"] == "pending", d["outcome"]
    assert [c["kind"] for c in d["changes"]] == ["new"], d["changes"]

    # 2. Same folder, next day's run: the bid is in flight, not a new decision.
    graph.reset_mock()
    d = go(out, [call()])
    graph.assert_not_called()
    assert d["calls_considered"] == 0 and not d["surfaced"], "an in-flight bid was decided again"
    flight = d["watcher_state"].get("in_flight")
    assert flight and flight["summary"] and flight["deadline"] == closes, (
        f"in-flight bid missing from watcher_state: {d['watcher_state']}")

    # 3. Triage vetoes the only call: silent, free, and still a fresh run.json.
    d = go(tmp / "record-silent", [call(programme="Winter", url="https://example.org/w",
                                        closes=(today + timedelta(days=2)).isoformat())])
    graph.assert_not_called()
    assert not d["surfaced"] and len(d["not_pursued"]) == 1
    row = d["not_pursued"][0]
    assert row["vetoed"] and row["reasons"][0] == row["vetoed"], f"veto not leading: {row}"
    assert d["draft"] == {} and d["files_written"] == [] and d["workspace"] is None

    # 4. Live calls whose funders publish no award history: judged on what each
    #    call says, never on another funder's history. One is about food, one is
    #    not, and one is for Scotland only.
    from granted.location import Place
    out = tmp / "record-live"
    unrelated = Opportunity(funder="Ministry of Justice", programme="Victims Fund",
                            url="https://example.org/moj", closes=closes)
    food = Opportunity(funder="Department for Levelling Up", programme="Community Food Fund",
                       url="https://example.org/food", closes=closes,
                       summary="Grants for community meals and food pantry projects.",
                       locations=["England"], amount_max=20000)
    scottish = Opportunity(funder="Scottish Government", programme="Scottish Food Fund",
                           url="https://example.org/scot", closes=closes,
                           summary="Community meals in Scotland.", locations=["Scotland"])
    london = Place("England", "London", "Greater London", "Southwark", "test")
    graph.reset_mock()
    with mock.patch.object(run_mod.sources, "opportunities",
                           return_value=[unrelated, food, scottish]), \
         mock.patch.object(run_mod.location, "resolve", return_value=london), \
         mock.patch.object(run_mod, "profile_funder") as pf, \
         mock.patch.object(run_mod, "build_graph", return_value=graph):
        assert run_mod.main(["--calls", "find-a-grant", "--funder", "GB-CHC-274100",
                             "--org", str(org), "--out", str(out), "--quiet"]) == 0
    pf.assert_not_called()
    assert graph.call_count == 1, (
        f"only the food call should reach the matcher; {graph.call_count} did")
    d = json.loads((out / "data" / "run.json").read_text(encoding="utf-8"))
    rows = {r["programme"]: r for r in d["not_pursued"] + d["surfaced"]}
    victims, fund, scot = rows["Victims Fund"], rows["Community Food Fund"], rows["Scottish Food Fund"]
    assert victims["reasons"][0].startswith("nothing in the call matches"), victims
    assert victims["basis"] == "stated criteria" and victims["fit"] is not None, victims
    assert scot["reasons"][0].startswith("this call is for Scotland"), scot
    assert fund["stage"] == "surfaced" and fund["basis"] == "stated criteria", fund
    assert d["sources"] == {"find-a-grant": 3}, d["sources"]

    # 5. A calls file naming no funder, with no --funder (a local trust's call
    #    added by hand): judged on what it says, never on a guessed funder.
    f = tmp / "orphan-calls.json"
    f.write_text(json.dumps({"calls": [call(summary="Community meals for local families.")]}))
    graph.reset_mock()
    with mock.patch.object(run_mod, "profile_funder") as pf2, \
         mock.patch.object(run_mod, "build_graph", return_value=graph):
        rc = run_mod.main(["--calls", str(f), "--org", str(org),
                           "--out", str(tmp / "orphan"), "--quiet"])
    assert rc == 0, f"a hand-added call with no funder failed (exit {rc})"
    pf2.assert_not_called()
    d = json.loads((tmp / "orphan" / "data" / "run.json").read_text(encoding="utf-8"))
    rows = d["surfaced"] + d["held_at_gate"] + d["not_pursued"]
    assert rows and rows[0]["basis"] == "stated criteria", rows
    return "decision, in flight, veto, unmapped live call, orphan call; fit 78 in both records"


# The London Community Foundation: a community foundation of exactly the kind
# this project profiles, and one that publishes a full award history.
# Override with --funder GB-CHC-xxxxxxx to check a different one.
DEFAULT_FUNDER = "GB-CHC-1091263"


@step("360Giving API reachable and still the expected shape")
def check_360giving() -> str:
    from granted.precedent import profile_funder
    funder = DEFAULT_FUNDER
    if "--funder" in sys.argv:
        funder = sys.argv[sys.argv.index("--funder") + 1]

    rp = profile_funder(funder, cap=200)
    assert rp.n_grants > 0, f"no published grants for {funder}"
    assert rp.brief(), "empty brief"
    # The fields triage actually reads must survive the parse, or alignment
    # scoring silently degrades to a 50/100 shrug on live data.
    assert rp.registers, "no recipient legal forms parsed — Grant.org_register is broken"
    assert rp.top_regions, "no recipient regions parsed"
    detail = f"{rp.funder_name}: {rp.n_grants} grants"
    if rp.median_award:
        detail += f", median £{rp.median_award:,.0f}"
    return detail + f", {len(rp.registers)} legal forms, {len(rp.top_regions)} regions"


def main() -> int:
    import os
    # Offline means offline: no postcode or registry lookups from inside a run.
    os.environ["GRANTED_OFFLINE"] = "1"
    print("\nGranted offline preflight — no Bedrock calls\n")
    tmp = Path(tempfile.mkdtemp(prefix="granted-preflight-"))
    try:
        ok = check_schemas()
        ok = check_org(tmp) and ok
        ok = check_render(tmp) and ok
        ok = check_dashboard(tmp) and ok
        ok = check_region_picker() and ok
        ok = check_provider() and ok
        ok = check_structured_shapes() and ok
        ok = check_gate() and ok
        if check_archive(tmp):
            ok = check_voice() and ok
            ok = check_interview() and ok
            ok = check_wiring(tmp) and ok
        else:
            ok = False
        ok = check_cli() and ok
        ok = check_citations(tmp) and ok
        ok = check_watcher(tmp) and ok
        ok = check_drift(tmp) and ok
        ok = check_triage() and ok
        ok = check_workspace(tmp) and ok
        ok = check_graph() and ok
        ok = check_sources(tmp) and ok
        ok = check_run_record(tmp) and ok
        ok = check_slow_funder(tmp) and ok
        ok = check_documents(tmp) and ok
        ok = check_home(tmp) and ok
        ok = check_publish_redaction() and ok
        ok = check_location(tmp) and ok
        ok = check_criteria() and ok
        ok = check_more_sources(tmp) and ok
        ok = check_local(tmp) and ok
        ok = check_register_memory(tmp) and ok
        if "--network" in sys.argv:
            ok = check_360giving() and ok
        else:
            print("  [skip] 360Giving live call — pass --network to include it")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    failed = [r for r in results if r[0] == FAIL]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed.")
    if failed:
        return 1
    print("\nEverything that does not need Bedrock is working. The only untested\n"
          "path is a live model call: run scripts/preflight.py once access clears.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
