"""Build the folder an organisation downloads.

    python scripts/make_release.py      ->  release/Granted/  and  release/Granted.zip

What goes in is only what an organisation needs on day one: the installers, the
guide, and the program itself tucked into `.granted/app` where nobody has to
look at it. Everything else in the folder (ORG.md, Bids, the dashboard) is made
on their machine, from their files, by the installer.

Windows files get CRLF line endings and are checked to be plain ASCII, because
Windows PowerShell 5.1 misreads anything else in a script without a BOM.
"""

from __future__ import annotations

import shutil
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACK = ROOT / "packaging"
OUT = ROOT / "release"
NAME = "Granted"


def _crlf(text: str) -> bytes:
    return text.replace("\r\n", "\n").replace("\n", "\r\n").encode("ascii")


def build() -> Path:
    target = OUT / NAME
    if target.exists():
        shutil.rmtree(target)
    app = target / ".granted" / "app"
    app.mkdir(parents=True)

    for name in ("pyproject.toml", "README.md", "LICENSE"):
        shutil.copy2(ROOT / name, app / name)
    shutil.copytree(ROOT / "src" / "granted", app / "src" / "granted",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))

    (target / "START HERE.txt").write_bytes(_crlf((PACK / "START HERE.txt").read_text(encoding="utf-8")))
    (target / "Install Granted (Windows).bat").write_bytes(
        _crlf((PACK / "Install Granted (Windows).bat").read_text(encoding="utf-8")))
    (target / ".granted" / "install.ps1").write_bytes(
        _crlf((PACK / "install.ps1").read_text(encoding="utf-8")))
    shutil.copy2(PACK / "install.sh", target / "install.sh")
    return target


def zip_folder(folder: Path) -> Path:
    """Zip with the folder at the top, and install.sh marked executable."""
    archive = OUT / f"{NAME}.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as z:
        for path in sorted(folder.rglob("*")):
            if path.is_dir():
                continue
            info = zipfile.ZipInfo.from_file(path, (Path(NAME) / path.relative_to(folder)).as_posix())
            info.compress_type = zipfile.ZIP_DEFLATED
            mode = 0o755 if path.name == "install.sh" else 0o644
            info.external_attr = (0o100000 | mode) << 16
            z.writestr(info, path.read_bytes())
    return archive


def main() -> int:
    folder = build()
    archive = zip_folder(folder)
    files = [p for p in folder.rglob("*") if p.is_file()]
    print(f"{folder}  ({len(files)} files)")
    for p in sorted(folder.iterdir()):
        print(f"  {p.name}{'/' if p.is_dir() else ''}")
    print(f"{archive}  ({archive.stat().st_size / 1024:.0f} kB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
