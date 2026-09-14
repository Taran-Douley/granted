"""Preflight. Run this before anything else.

Checks, in order of how likely they are to be the thing that's broken:

  1. credentials resolve
  2. the configured model id exists as an inference profile in this region
  3. a plain Bedrock call returns text
  4. a Strands Agent with structured_output_model returns a typed object
  5. the graph assembles and the silence gate fires correctly

Step 4 is the one that matters. Everything in Granted depends on structured
output working, and it is the piece most likely to behave differently from
expectation. Find that out now, not on the 12th.

Run:  python scripts/preflight.py
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from granted.config import model_id, region  # noqa: E402

PASS, FAIL, SKIP = "PASS", "FAIL", "SKIP"
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


@step("credentials resolve")
def check_credentials() -> str:
    import boto3
    sts = boto3.client("sts", region_name=region())
    ident = sts.get_caller_identity()
    arn = ident["Arn"]
    return f"account {ident['Account']}, {arn.split('/')[-1]}"


@step("model id exists in this region")
def check_model() -> str:
    import boto3
    br = boto3.client("bedrock", region_name=region())
    target = model_id()
    profiles = {p["inferenceProfileId"] for p in
                br.list_inference_profiles()["inferenceProfileSummaries"]}
    if target not in profiles:
        claude = sorted(p for p in profiles if "claude" in p)
        raise RuntimeError(
            f"{target} not found in {region()}.\n"
            f"         Available Claude profiles:\n"
            + "\n".join(f"           {p}" for p in claude[:12])
        )
    return f"{target} in {region()}"


@step("plain Bedrock invocation")
def check_invoke() -> str:
    import json
    import boto3
    rt = boto3.client("bedrock-runtime", region_name=region())
    resp = rt.invoke_model(
        modelId=model_id(),
        body=json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 32,
            "messages": [{"role": "user", "content": "Reply with the single word: ready"}],
        }),
    )
    text = json.loads(resp["body"].read())["content"][0]["text"].strip()
    return f"model replied {text!r}"


@step("Strands agent with structured output")
def check_structured() -> str:
    from strands import Agent
    from granted.schemas import Match

    agent = Agent(
        name="preflight",
        model=model_id(),
        system_prompt=(
            "You assess whether a small charity should apply for funding. "
            "Be conservative."
        ),
        structured_output_model=Match,
    )
    result = agent(
        "Funder awards a median of £15,000 to registered charities in Bristol. "
        "The organisation is a Bristol CIO with £148,000 turnover running food "
        "programmes. They are considering asking for £15,000. Should they apply?"
    )
    match = getattr(result, "structured_output", None) or getattr(result, "result", None)
    if not isinstance(match, Match):
        raise RuntimeError(
            f"expected a Match, got {type(match).__name__}. "
            "Inspect the result object shape and update graph._structured()."
        )
    return (f"fit {match.fit_score}/100, recommend_apply={match.recommend_apply}, "
            f"{len(match.eligibility_gaps)} gaps")


@step("graph assembles and gate logic holds")
def check_graph() -> str:
    from granted.graph import build_graph, gate_passes, FIT_THRESHOLD
    from granted.schemas import Match

    g = build_graph()
    nodes = sorted(g.nodes.keys()) if hasattr(g, "nodes") else []

    class _R: pass
    class _S: pass
    s = _S(); r = _R(); s.results = {"matcher": r}

    r.structured_output = Match(fit_score=90, recommend_apply=True, reasoning="x")
    assert gate_passes(s) is True, "gate should fire on a strong match"
    r.structured_output = Match(fit_score=90, recommend_apply=True,
                                blocking=["wrong region"], reasoning="x")
    assert gate_passes(s) is False, "gate must not fire when blocked"
    r.structured_output = Match(fit_score=FIT_THRESHOLD - 1, recommend_apply=True, reasoning="x")
    assert gate_passes(s) is False, "gate must not fire below threshold"
    s.results = {}
    assert gate_passes(s) is False, "gate must not fire with no result"
    return f"{len(nodes)} nodes, 4 gate cases correct"


def main() -> int:
    print(f"\nGranted preflight — region {region()}, model {model_id()}\n")
    ok = check_credentials()
    ok = check_model() and ok
    if ok:
        ok = check_invoke() and ok
        ok = check_structured() and ok
    else:
        print("  [SKIP] Bedrock calls — fix the above first")
    ok = check_graph() and ok

    failed = [r for r in results if r[0] == FAIL]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed.")
    if failed:
        print("\nCommon causes:")
        print("  403 on invoke      -> bare model id used instead of the us.* profile id,")
        print("                        or Marketplace auto-enable blocked for this identity")
        print("  ThrottlingException -> account quota at zero; check Service Quotas > Bedrock")
        print("  no credentials      -> run `aws configure`, or set AWS_PROFILE")
        return 1
    print("\nEverything is wired. Proceed to run.py.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
