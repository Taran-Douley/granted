"""The Granted folder: one per organisation, sitting among its own files.

    Granted/
      START HERE.txt         how to use it
      granted.json           settings: who you are, where your past bids live
      ORG.md                 the one file you maintain
      funders.json           funders whose award history can be scored
      Dashboard.html         rebuilt after every run; double-click to open
      Bid folders.html       every bid folder and its files, at a glance
      Bids/                  one folder per tender worth your time
      Daily notes/           what each run looked at, and why it stayed quiet
      Past applications/     your past bids, unless granted.json points elsewhere
      .granted/              the agent's memory; leave it alone

Every path in it is relative to the folder, so it survives being moved, synced
through OneDrive or SharePoint, or opened on another machine. Credentials are the
one thing that never lives here: a shared folder is the wrong place for a key.

    granted setup --name "Rye Lane Community Kitchen" [--archive "../Funding/Past bids"]
    granted run                      # inside the folder, or with --home <folder>
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

CONFIG = "granted.json"
BIDS = "Bids"
NOTES = "Daily notes"
ARCHIVE = "Past applications"
DASHBOARD = "Dashboard.html"
BID_FOLDERS = "Bid folders.html"
INTERNAL = ".granted"
RECORD = f"{INTERNAL}/run.json"
RUN_JS = f"{INTERNAL}/run.js"

CONFIG_COMMENT = (
    "Settings for this Granted folder; edit in any text editor. 'archive' is the folder "
    "of past applications, relative to this one or a full path. 'calls' lists where "
    "calls come from, separated by commas: find-a-grant, national-lottery, "
    "innovate-uk, or the name of a calls file. 'funder' is "
    "a 360Giving id for calls that name no funder of their own. 'cap' is how many of "
    "a funder's past awards to read.")

_NOTES = {
    BIDS: "One folder per tender Granted thinks is worth your time, opened the day it\n"
          "decided. Inside: the decision, the angle, the draft, the evidence and the\n"
          "timeline, each as a Word document. Everything in here is yours to edit.\n",
    NOTES: "One note per run: what Granted looked at, and why each call stopped.\n"
           "Most days it stays quiet. These notes are how you know it looked.\n",
    ARCHIVE: "Put copies of your past funding applications here: Word, PDF or text.\n"
             "Put the result in the file name where you know it, for example\n"
             "'2024 Awards for All - successful.docx'. Granted learns your track\n"
             "record and your writing style from these. It never changes them.\n\n"
             "Already keep them somewhere else? Set \"archive\" in granted.json to that\n"
             "folder instead, and leave this one empty.\n",
}

# Hints sit on their own lines rather than after the colon: ORG.md is parsed,
# and a hint where a value belongs would be read as the value.
SKELETON = """# {name}

## Identity
Your registered number decides which funders will consider you; your postcode
places you for regional and local funding.
- Legal name: {name}
- Legal form:
- Registered number:
- Area served:
- Postcode:

## Scale
- Annual turnover:
- Paid staff:
- Regular volunteers:
- Beneficiaries reached last year:

## Programmes
One heading per programme. A number you would stand behind beats a description.
### Programme name
- Delivery:
- Volume:
- Outcome:

## Evidence and evaluation
-

## Capability
- Safeguarding policy last reviewed:
- Finance:

## Funding history
One line per award: funder, programme, amount, year.
-

## Constraints
- Match funding available:
- Cannot deliver:
"""


@dataclass
class Home:
    root: Path
    organisation: str
    archive: str = ARCHIVE
    calls: str = "find-a-grant,national-lottery,innovate-uk"
    funder: str | None = None
    funder_ids: str | None = "funders.json"
    cap: int = 300

    @property
    def archive_path(self) -> Path:
        p = Path(self.archive).expanduser()
        return p if p.is_absolute() else self.root / p


def find(start: Path | None = None) -> Path | None:
    """The Granted folder containing `start`, searching upwards."""
    here = Path(start or Path.cwd()).resolve()
    for folder in (here, *here.parents):
        if (folder / CONFIG).is_file():
            return folder
    return None


def load(root: Path) -> Home:
    root = Path(root).resolve()
    raw = json.loads((root / CONFIG).read_text(encoding="utf-8"))
    known = set(Home.__dataclass_fields__) - {"root"}
    return Home(root=root, **{k: v for k, v in raw.items() if k in known})


def save(home: Home) -> Path:
    data = {"_comment": CONFIG_COMMENT}
    data.update({k: getattr(home, k) for k in Home.__dataclass_fields__ if k != "root"})
    path = home.root / CONFIG
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def init(root: Path, organisation: str, archive: str | None = None,
         calls: str = "find-a-grant,national-lottery,innovate-uk") -> Home:
    """Lay out a Granted folder. Never overwrites anything already there."""
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    folders = [BIDS, NOTES] + ([ARCHIVE] if archive is None else [])
    for folder in folders:
        (root / folder).mkdir(exist_ok=True)
        readme = root / folder / "README.txt"
        if not readme.exists():
            readme.write_text(_NOTES[folder], encoding="utf-8")
    (root / INTERNAL).mkdir(exist_ok=True)

    funders = root / "funders.json"
    if not funders.exists():
        shipped = resources.files("granted") / "data" / "funders.json"
        funders.write_bytes(shipped.read_bytes())

    if (root / CONFIG).exists():
        return load(root)
    home = Home(root=root, organisation=organisation, archive=archive or ARCHIVE, calls=calls)
    save(home)
    return home


def write_dashboard(root: Path) -> Path | None:
    """The designed dashboard, in the folder, opening with a double-click.

    Its runtime re-reads its own page over the network unless told the page is
    self-contained, and a page opened from disk may not do that, so nothing
    would render. The switch is set here rather than in the page itself, which
    stays exactly the copy the web version publishes.
    """
    shipped = resources.files("granted") / "data" / "dashboard.html"
    if not shipped.is_file():
        return None
    page = shipped.read_text(encoding="utf-8").replace(
        "<head>", "<head>\n<script>window.__resources = window.__resources || {};</script>", 1)
    out = Path(root) / DASHBOARD
    out.write_text(page, encoding="utf-8")
    return out


def refresh_run_js(root: Path) -> Path | None:
    """Rewrite .granted/run.js from .granted/run.json, for the dashboard."""
    record = Path(root) / RECORD
    if not record.is_file():
        return None
    from .digest import run_js
    out = Path(root) / RUN_JS
    out.write_text(run_js(json.loads(record.read_text(encoding="utf-8"))), encoding="utf-8")
    return out


def credentials_file() -> Path:
    """Where this machine keeps the model credentials: outside any synced folder.

    `GRANTED_CREDENTIALS` overrides it, which is how tests keep their hands off
    the real one.
    """
    override = os.environ.get("GRANTED_CREDENTIALS")
    if override:
        return Path(override)
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "Granted"
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / "Granted"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "granted"
    return base / "credentials.env"


def load_credentials(path: Path | None = None) -> list[str]:
    """Put KEY=VALUE lines into the environment, never overriding what is set.

    Returns the names loaded, never the values.
    """
    path = path or credentials_file()
    if not path.is_file():
        return []
    loaded = []
    # utf-8-sig: Windows PowerShell puts a byte-order mark on files it writes.
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and value and key not in os.environ:
            os.environ[key] = value
            loaded.append(key)
    return loaded


def main(argv: list[str] | None = None, prog: str | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog=prog or "granted setup",
        description="Set up a Granted folder for one organisation.")
    ap.add_argument("--home", default=".", help="the folder to set up (default: this one)")
    ap.add_argument("--name", required=True, help="the organisation's name")
    ap.add_argument("--archive", default=None,
                    help=f"folder of past applications (default: '{ARCHIVE}' inside the folder)")
    ap.add_argument("--calls", default="find-a-grant,national-lottery,innovate-uk",
                    help="where calls come from, comma-separated: find-a-grant, "
                         "national-lottery, innovate-uk, or a calls file")
    args = ap.parse_args(argv)

    root = Path(args.home).resolve()
    archive = args.archive
    if archive:
        # Stored relative where it can be, so the folder still finds its past
        # bids after a move, as long as the two move together.
        full = Path(archive).expanduser().resolve()
        try:
            archive = os.path.relpath(full, root)
        except ValueError:          # on another drive, on Windows
            archive = str(full)
    home = init(root, args.name, archive, args.calls)
    print(f"\n  Granted folder ready: {home.root}")

    org = home.root / "ORG.md"
    archive = home.archive_path
    if org.exists():
        print("  ORG.md already exists, so it was left exactly as it is.")
    else:
        from .archive import load_archive
        subs = load_archive(archive)[0] if archive.is_dir() else []
        if subs:
            from .onboard import main as onboard_main
            onboard_main(["--archive", str(archive), "--out", str(org), "--name", args.name])
        else:
            org.write_text(SKELETON.format(name=args.name), encoding="utf-8")
            print(f"  No past applications in {archive} yet, so ORG.md is a blank template.\n"
                  "  Fill it in, or add past bids, delete ORG.md, and run setup again\n"
                  "  to have it drafted for you.")

    from . import dashboard
    dashboard.build(home.root, bids=BIDS, digests=NOTES, filename=BID_FOLDERS)
    write_dashboard(home.root)
    print(f"\n  Next: check ORG.md and put your postcode on its Postcode line, then run\n"
          f"  Granted. Open {DASHBOARD} to see what it found.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
