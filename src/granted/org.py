"""Read ORG.md into the handful of fields triage needs to score arithmetically.

Two consumers, two needs. `triage.score_alignment` wants a small dict it can
compare against a funder's award history: legal form, region, turnover, themes.
The matcher agent wants the whole file, verbatim, because a model reading the
real thing beats a model reading someone's summary of it.

So this parses conservatively and keeps `raw` intact. A field it cannot read
stays None, and the corresponding term in score_alignment simply does not fire —
which is correct. Guessing a legal form from prose would silently distort every
score downstream, and a wrong blocker is worse than a missing one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

# Registers as 360Giving publishes them, so the two sides compare directly.
_FORM_PATTERNS: list[tuple[str, str]] = [
    (r"\bscottish charity\b|\bscio\b|\boscr\b", "GB-SC"),
    (r"\bnorthern irel(and|ish)\b.*\bcharity\b|\bnic\b", "GB-NIC"),
    (r"\bcharitable incorporated organisation\b|\bcio\b|\bregistered charity\b"
     r"|\bcharity\b", "GB-CHC"),
    (r"\bcommunity interest company\b|\bcic\b|\bcompany limited by guarantee\b"
     r"|\bltd\b|\blimited\b|\bcompany\b", "GB-COH"),
]

# Vocabulary that actually appears in funders' own classification labels. Kept
# short on purpose: a theme only helps if the funder uses the same word.
_THEME_VOCAB: dict[str, tuple[str, ...]] = {
    "food insecurity": ("food bank", "foodbank", "food parcel", "food poverty",
                        "food insecurity", "meals", "pantry", "hunger"),
    "youth": ("young people", "youth", "children", "under-18", "teenagers"),
    "mental health": ("mental health", "wellbeing", "counselling", "isolation"),
    "employment": ("employment", "training", "skills", "back to work"),
    "housing": ("housing", "homeless", "tenancy", "rough sleep"),
    "older people": ("older people", "elderly", "over-65", "dementia"),
    "disability": ("disability", "disabled", "send", "accessible"),
    "refugees": ("refugee", "asylum", "migrant", "resettlement"),
    "community": ("community centre", "community hub", "volunteering"),
    "health": ("health", "nutrition", "physical activity", "exercise"),
}

_MONEY = re.compile(r"£\s?([\d,]+(?:\.\d+)?)\s*(k|m)?", re.I)


@dataclass
class OrgProfile:
    """ORG.md, parsed for triage and kept whole for the model."""

    raw: str
    path: Path | None = None
    name: str | None = None
    legal_form_register: str | None = None
    legal_form_text: str | None = None
    region: str | None = None
    turnover: float | None = None
    postcode: str | None = None
    area_served: str | None = None          # as written, for the place lookup
    themes: list[str] = field(default_factory=list)
    sections: dict[str, list[str]] = field(default_factory=dict)

    @property
    def org_id(self) -> str | None:
        """The organisation's id in 360Giving's scheme, from ORG.md's Identity lines.

        It is the same key funders' award histories use for recipients, so a
        record written by one Granted folder can be matched to the same
        organisation anywhere else, without relying on how its name is spelled.
        """
        text = " ".join(self.sections.get("Identity", [])) or self.raw
        m = re.search(r"\bGB-(?:CHC|COH|SC|NIC)-[A-Z0-9]+\b", text, re.I)
        if m:
            return m.group(0).upper()
        for label, register in (("charity", "GB-CHC"), ("company", "GB-COH")):
            m = re.search(rf"{label}[^:\n]*?number\s*:?\s*((?:SC|NI)?\d{{5,8}})", text, re.I)
            if m:
                number = m.group(1).upper()
                if number.startswith("SC"):
                    register = "GB-SC"
                elif number.startswith("NI"):
                    register = "GB-NIC"
                return f"{register}-{number}"
        return None

    def as_triage_dict(self) -> dict:
        """The shape `triage.score_alignment` expects."""
        return {
            "legal_form_register": self.legal_form_register,
            "region": self.region,
            "turnover": self.turnover,
            "themes": self.themes,
        }

    @property
    def unreadable(self) -> list[str]:
        """Fields triage will score without. Shown to the user, never guessed at."""
        missing = []
        if not self.legal_form_register:
            missing.append("legal form")
        if not self.region:
            missing.append("area served")
        if self.turnover is None:
            missing.append("annual turnover")
        if not self.themes:
            missing.append("themes")
        return missing


def _money(text: str) -> float | None:
    m = _MONEY.search(text)
    if not m:
        return None
    value = float(m.group(1).replace(",", ""))
    suffix = (m.group(2) or "").lower()
    return value * {"k": 1_000, "m": 1_000_000}.get(suffix, 1)


def _register(text: str) -> str | None:
    """Prefer the registered number: it states the register outright."""
    m = re.search(r"\b(GB-(?:CHC|COH|SC|NIC|EDU|GOR|LAE|LAS|PLA|REV|UKPRN))\b", text, re.I)
    if m:
        return m.group(1).upper()
    for pattern, register in _FORM_PATTERNS:
        if re.search(pattern, text, re.I):
            return register
    return None


def _region(value: str) -> str | None:
    """Take the widest place named, which is conventionally the last one.

    "Easton, Lawrence Hill and St Pauls wards, Bristol" -> "Bristol", because
    that is the level a funder's award history is aggregated at.
    """
    cleaned = re.sub(r"\b(wards?|local authority|borough|council|area|district)\b",
                     "", value, flags=re.I)
    parts = [p.strip(" .,;") for p in cleaned.split(",") if p.strip(" .,;")]
    if not parts:
        return None
    last = parts[-1]
    # "St Pauls and Bristol" -> "Bristol"
    if " and " in last.lower():
        last = re.split(r"\band\b", last, flags=re.I)[-1].strip()
    return last or None


def _themes(text: str) -> list[str]:
    low = text.lower()
    return [theme for theme, words in _THEME_VOCAB.items()
            if any(w in low for w in words)]


def parse(text: str, path: Path | None = None) -> OrgProfile:
    """Parse ORG.md. Unreadable fields stay None rather than being inferred."""
    profile = OrgProfile(raw=text, path=path)

    heading = None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.startswith("#"):
            title = line.lstrip("#").strip()
            if line.startswith("## "):
                heading = title
                profile.sections.setdefault(heading, [])
            elif not profile.name and line.startswith("# ") and title.upper() != "ORG.MD":
                profile.name = title
            continue
        if not line.startswith("-"):
            continue
        item = line.lstrip("- ").strip()
        # A TODO is a question the draft asked, not a fact. Read as prose,
        # "Are you a charity, a CIO...?" would claim a legal form.
        if item.upper().startswith("TODO"):
            continue
        if heading:
            profile.sections.setdefault(heading, []).append(item)

        key, _, value = item.partition(":")
        key, value = key.strip().lower(), value.strip()
        if not value:
            continue
        if key == "legal name" and not profile.name:
            profile.name = value
        elif key == "legal form":
            profile.legal_form_text = value
        elif key == "area served" and not profile.region:
            profile.region = _region(value)
            profile.area_served = value
        elif key == "postcode" and not profile.postcode:
            profile.postcode = value.upper()
        elif ("turnover" in key or "income" in key) and profile.turnover is None:
            profile.turnover = _money(value)

    # Register: the number is authoritative, the prose is a fallback.
    facts = "\n".join(ln for ln in text.splitlines() if "TODO" not in ln.upper())
    identity = " ".join(profile.sections.get("Identity", [])) or facts
    profile.legal_form_register = _register(identity) or _register(facts)
    profile.themes = _themes(facts)
    return profile


def load(path: Path) -> OrgProfile:
    return parse(Path(path).read_text(encoding="utf-8"), path=Path(path))
