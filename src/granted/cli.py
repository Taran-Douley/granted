"""The `granted` command.

Two things a person does with this tool, and they happen at different times.
`onboard` is run once, before there is an ORG.md. `run` is run repeatedly after
that, most often by cron, and usually prints nothing.

Subcommands are dispatched by hand rather than through argparse subparsers so
that each one keeps its own parser, its own --help, and its own module entry
point. `python -m granted.run` and `granted run` stay the same program.

Imports are deferred into the branch on purpose: `onboard` reads a folder and
writes a markdown file, and there is no reason for it to pay for boto3 and
strands loading first.
"""

from __future__ import annotations

import sys

from . import __version__

USAGE = """granted — tells small UK organisations when not to apply for funding

  granted setup     make a folder an organisation's Granted folder: settings,
                       ORG.md drafted from past bids, Bids/, Daily notes/
  granted run       decide against funders' award histories, and usually
                       say nothing; inside a Granted folder it needs no options
  granted onboard   draft ORG.md from a folder of past bids, then ask for
                       what is missing
  granted dashboard rebuild dashboard.html from what is already in the
                       folder (run does this for you)

  granted <command> --help   options for a command
  granted --version

Silence is the expected result of `run`. On most days there is no decision to
make, and the absence of output is the tool working."""


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    if not argv or argv[0] in ("-h", "--help", "help"):
        print(USAGE)
        return 0
    if argv[0] in ("-V", "--version", "version"):
        print(f"granted {__version__}")
        return 0

    command, rest = argv[0], argv[1:]

    if command == "setup":
        from .home import main as setup_main
        return setup_main(rest, prog="granted setup")
    if command == "run":
        from .run import main as run_main
        return run_main(rest, prog="granted run")
    if command == "onboard":
        from .onboard import main as onboard_main
        return onboard_main(rest, prog="granted onboard")
    if command == "dashboard":
        from .dashboard import main as dashboard_main
        return dashboard_main(rest, prog="granted dashboard")

    print(f"granted: unknown command {command!r}\n", file=sys.stderr)
    print(USAGE, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
