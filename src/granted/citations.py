"""Citations. Every number in a draft points at where it came from.

Four source kinds, each with a resolvable locator:

  org       ORG.md, by heading and verbatim line
  archive   a past submission, by file and line, with its outcome attached
  360giving a specific grant record, by grant_id, resolvable to a GrantNav URL
  external  a URL, with the accessed date and the quoted figure

An uncited figure never reaches the draft. It becomes a gap for a human to fill.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Literal

GRANTNAV_GRANT = "https://grantnav.threesixtygiving.org/grant/{grant_id}"
GRANTNAV_ORG = "https://grantnav.threesixtygiving.org/org/{org_id}"

SourceKind = Literal["org", "archive", "360giving", "external"]


@dataclass
class Source:
    kind: SourceKind
    label: str
    locator: str
    line: str | None = None
    url: str | None = None
    accessed: date | None = None
    note: str | None = None

    @classmethod
    def from_org(cls, heading: str, line: str, line_no: int, path: str = "ORG.md") -> "Source":
        return cls("org", f"{path}, {heading}", f"{path}#L{line_no}", line=line)

    @classmethod
    def from_archive(cls, path: Path, line: str, line_no: int, funder: str | None, outcome: str) -> "Source":
        label = f"{path.name}"
        if funder:
            label = f"{funder}, {path.name}"
        return cls("archive", label, f"{path}#L{line_no}", line=line,
                   note=f"that application was {outcome}")

    @classmethod
    def from_360giving(cls, grant_id: str, funder: str, amount: float | None, award_date: str | None) -> "Source":
        bits = [funder]
        if amount:
            bits.append(f"£{amount:,.0f}")
        if award_date:
            bits.append(award_date)
        return cls("360giving", ", ".join(bits), grant_id,
                   url=GRANTNAV_GRANT.format(grant_id=grant_id),
                   note="360Giving, CC-BY-SA")

    @classmethod
    def from_external(cls, label: str, url: str, quoted: str | None = None) -> "Source":
        return cls("external", label, url, line=quoted, url=url, accessed=date.today())

    def render(self) -> str:
        parts = [self.label]
        if self.url:
            parts.append(self.url)
        elif self.kind in ("org", "archive"):
            parts.append(self.locator)
        if self.line:
            snippet = self.line if len(self.line) <= 120 else self.line[:117] + "..."
            parts.append(f'"{snippet}"')
        if self.accessed:
            parts.append(f"accessed {self.accessed.isoformat()}")
        if self.note:
            parts.append(self.note)
        return " — ".join(parts)


@dataclass
class CitedClaim:
    text: str
    sources: list[Source] = field(default_factory=list)

    @property
    def grounded(self) -> bool:
        return bool(self.sources)


@dataclass
class CitedDraft:
    question: str
    body: str
    claims: list[CitedClaim] = field(default_factory=list)
    gaps: list[str] = field(default_factory=list)

    @property
    def grounding_rate(self) -> float:
        if not self.claims:
            return 1.0
        return sum(1 for c in self.claims if c.grounded) / len(self.claims)

    def render(self, footnotes: bool = True) -> str:
        """Body with inline markers, then a numbered source list.

        Markers are stripped for the version pasted into a funder's portal; the
        cited version stays in the workspace so a trustee can check any figure.
        """
        body = self.body
        out = [body.rstrip(), ""]
        if not footnotes:
            return body.rstrip()

        out.append("---")
        out.append("### Sources")
        n = 0
        for claim in self.claims:
            if not claim.sources:
                continue
            n += 1
            out.append(f"{n}. {claim.text}")
            for s in claim.sources:
                out.append(f"   - {s.render()}")
        if self.gaps:
            out += ["", "### Needs a human", ""]
            out += [f"- {g}" for g in self.gaps]
        out += [
            "",
            f"_Grounding: {sum(1 for c in self.claims if c.grounded)} of {len(self.claims)} "
            f"claims traced to a source ({self.grounding_rate:.0%})._",
        ]
        return "\n".join(out)


def index_org_file(path: Path) -> dict[str, list[tuple[int, str]]]:
    """Map each ORG.md heading to its lines, so a claim can cite a line number.

    This is what makes `from_org` a real locator rather than a vague attribution.
    """
    sections: dict[str, list[tuple[int, str]]] = {}
    heading = "(preamble)"
    for i, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if line.startswith("#"):
            heading = line.lstrip("#").strip()
            sections.setdefault(heading, [])
        elif line.startswith("-") and line.strip("- "):
            sections.setdefault(heading, []).append((i, line.lstrip("- ").strip()))
    return sections


def _figures(text: str) -> set[str]:
    """Numbers a claim rests on, normalised so £9,700 and 9700 compare equal."""
    return {m.replace(",", "").rstrip(".").lstrip("0") or "0"
            for m in re.findall(r"\d[\d,]*(?:\.\d+)?", text)}


def _tokens(text: str) -> set[str]:
    return {w.lower().strip(".,;:()£%\"'") for w in text.split() if len(w) > 3}


def find_support(fact: str, index: dict[str, list[tuple[int, str]]],
                 min_overlap: int = 2) -> Source | None:
    """Cheap lexical check that a claim is actually present in ORG.md.

    Deliberately conservative, and it has to be. A footnote pointing at an
    unrelated line is worse than no footnote: it survives exactly as long as it
    takes a trustee to click it, and it discredits every other citation on the
    page. Bare token overlap is not conservative enough — "Southwark received 15
    awards from the London Community Foundation" and "National Lottery Community
    Fund, Awards for All, £9,700, 2023" share enough common words to pass at two.

    So two extra conditions:

    - If the claim rests on a number, that number must appear in the line. This
      is what separates a real match from a shared vocabulary, and it is the rule
      that keeps claims about the *funder* from being grounded in ORG.md, where
      the answer was never going to be.
    - Longer claims need proportionally more overlap, so a wordy sentence cannot
      match on two incidental words.
    """
    figures = _figures(fact)
    tokens = _tokens(fact)
    if not tokens:
        return None
    needed = max(min_overlap, min(4, round(len(tokens) * 0.3)))

    best: tuple[int, Source] | None = None
    for heading, lines in index.items():
        for line_no, line in lines:
            if figures and not (figures & _figures(line)):
                continue
            overlap = len(tokens & _tokens(line))
            if overlap >= needed and (best is None or overlap > best[0]):
                best = (overlap, Source.from_org(heading, line, line_no))
    return best[1] if best else None
