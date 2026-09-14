#!/usr/bin/env python3
"""
Minimal Bedrock proof-of-life THROUGH graph.py.

Not the pipeline. One model call: build the graph's matcher agent exactly as
graph.py constructs it (same model id, same MATCHER_PROMPT, same
structured_output_model=Match), feed a stub prompt, and print the returned
`Match` pydantic object. This proves three things together:
  1. Strands Agent construction + invocation works,
  2. structured_output_model returns a typed Match (not prose),
  3. Bedrock model access resolves and responds.

    python scripts/smoke_bedrock.py         # uses graph.py's default model id
    python scripts/smoke_bedrock.py --model us.anthropic.claude-sonnet-4-5-20250929-v1:0
"""
from __future__ import annotations
import argparse, os, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

STUB = """FUNDER (revealed preferences, from award history):
  Bristol Community Foundation. 900 awards. Recipients almost all GB-CHC
  (registered charities) in Bristol and Bath. Award p10-p90 GBP 2,000-15,000,
  median 6,500. Repeat-funding rate 40%. Themes: youth, food insecurity, mental health.

ORG (from ORG.md):
  Southmead Foodbank. Legal form GB-CHC. Based in Bristol. Two paid staff.
  Annual income GBP 90,000. Runs a weekly food parcel service and a cooking club.

QUESTION: Should this organisation apply, and if so for how much? Decide."""

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=None, help="Bedrock model id (default: graph.py's)")
    ap.add_argument("--region", default=os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION"))
    args = ap.parse_args()

    from granted import graph
    from granted.schemas import Match
    from strands import Agent

    model_id = args.model or graph.build_graph.__defaults__[0]
    print(f"# model     : {model_id}")
    print(f"# region    : {args.region or '(none set — Bedrock will fail without one)'}")
    print(f"# schema    : Match (structured_output_model)")
    print(f"# prompt    : stub matcher input ({len(STUB)} chars)\n")

    # Construct the matcher EXACTLY as graph.py does.
    matcher = Agent(
        name="matcher",
        model=model_id,
        system_prompt=graph.MATCHER_PROMPT,
        structured_output_model=Match,
    )

    result = matcher(STUB)                       # one Bedrock call
    match = graph._structured(result, Match) or getattr(result, "structured_output", None)
    if not isinstance(match, Match):
        # some strands versions return the pydantic object directly
        match = result if isinstance(result, Match) else match
    print("=== structured Match returned by Bedrock ===")
    if isinstance(match, Match):
        print(match.model_dump_json(indent=2))
        print("\nOK: Strands + structured output + Bedrock all working.")
    else:
        print("Model responded but no typed Match was extracted; raw result:")
        print(repr(result)[:800])
        sys.exit(2)

if __name__ == "__main__":
    main()
