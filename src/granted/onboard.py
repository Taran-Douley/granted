"""Draft ORG.md from the archive, then ask only about what is missing.

    python -m granted.onboard --archive fixtures/easton-funding --out ORG.md

The premise of the interview is that a funding folder already contains most of
what an application needs, so asking forty questions is mostly asking people to
retype their own filing cabinet. This reads the archive, fills in what it can
find, and turns the remainder into a short ordered list — eligibility blockers
first, because getting those wrong wastes the whole application.

Nothing is invented. A field the archive does not evidence is written as a TODO
against a plain question, not guessed at and quietly presented as fact.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .archive import load_archive
from .interview import bootstrap, prioritise
from .render import ink_for
from .voice import build_style_card


def main(argv: list[str] | None = None, prog: str | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog=prog or "granted.onboard",
        description="Draft ORG.md from a folder of past bids, then ask for the rest.")
    ap.add_argument("--archive", required=True, help="folder of past applications")
    ap.add_argument("--out", default="ORG.md", help="where to write the draft")
    ap.add_argument("--name", default=None, help="the organisation's name")
    ap.add_argument("--force", action="store_true",
                    help="overwrite an existing ORG.md")
    args = ap.parse_args(argv)

    ink = ink_for()
    folder = Path(args.archive)
    if not folder.exists():
        print(f"no such folder: {folder}", file=sys.stderr)
        return 2

    out = Path(args.out)
    # ORG.md is hand-maintained and is the single interface to this tool.
    # Overwriting someone's own file without asking would be unforgivable.
    if out.exists() and not args.force:
        print(f"{out} already exists. Pass --force to overwrite it.", file=sys.stderr)
        return 2

    subs, insight = load_archive(folder)
    if not subs:
        print(f"no readable applications in {folder}", file=sys.stderr)
        return 2

    result = bootstrap(subs, insight)
    out.write_text(result.to_markdown(args.name or "Your organisation"), encoding="utf-8")

    filled = sum(len(v) for v in result.filled.values())
    asked = prioritise(result.gaps, limit=6)

    print()
    print(ink.bold("Granted") + ink.dim("  ·  ORG.md drafted from your archive"))
    print(ink.dim(f"{len(subs)} applications read  ·  {insight.confidence} confidence"))
    print(ink.dim("─" * 76))
    print()
    print(f"  Wrote {ink.bold(str(out))} — {filled} facts found, "
          f"{len(result.gaps)} gaps left.")
    print()

    if asked:
        print(f"  {ink.bold('Answer these six and it is usable')}")
        print(ink.dim(f"  Ordered so the ones that decide eligibility come first. "
                      f"The other {max(0, len(result.gaps) - len(asked))} can wait."))
        print()
        for i, gap in enumerate(asked, 1):
            print(f"  {ink.accent(str(i))}. {gap.question}")
            print(ink.dim(f"     {gap.section} · {gap.why}"))
        print()

    card = build_style_card(subs)
    if card:
        term = card.beneficiary_term or "not consistent enough to call"
        print(f"  {ink.bold('Your voice, measured')}")
        print(ink.dim(f"     {card.n_documents} documents, {card.n_sentences} sentences. "
                      f"Median sentence {card.median_sentence_words:.0f} words."))
        print(ink.dim(f"     You call the people you serve: {term}."))
        print()

    print(ink.dim("─" * 76))
    print(ink.dim("  Fill the gaps in ORG.md, then run Granted."))
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
