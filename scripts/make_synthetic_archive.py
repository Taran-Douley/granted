"""Generate a synthetic bid archive for a fictional charity.

Deliberately messy. A clean fixture makes the extraction code look like it works
and teaches you nothing. This one mirrors what an actual charity folder contains
after five years: inconsistent naming, three applications where nobody recorded
the result, one bid saved as a PDF, sensitive files that should never be read,
a nested folder someone made once, and one funder who said yes and was never
approached again.

Two organisations are available, because the demo pairs an archive with an
ORG.md and they have to be the same charity. A style card measured from one
organisation's bids and applied to another's draft reports differences that
are real but meaningless -- the two simply write differently.

Each profile carries its own funders, places and hand-written corpus rather
than substituting names into shared sentences, because the corpus is what the
style card measures: sentence rhythm, house capitalisation, the word they use
for the people they serve. Those have to be authored to be worth measuring.

Run:  python scripts/make_synthetic_archive.py [--profile easton|rye-lane]
                                               [--out DIR]
"""

from __future__ import annotations

import argparse
import json
import random
from dataclasses import dataclass
from pathlib import Path

random.seed(1904)

EASTON_ORG = "Easton Community Kitchen"

# (filename, funder, submitted, asked, awarded, outcome, has_sidecar, feedback)
EASTON_BIDS = [
    ("2021 Quartet Community Foundation application.md", "Quartet Community Foundation",
     "2021-04-12", 8000, 8000, "awarded", True, None),
    ("Lottery Awards for All 2021.md", "National Lottery Community Fund",
     "2021-09-30", 9800, 9800, "awarded", True, None),
    ("2022-02 Tudor Trust main grant.md", "Tudor Trust",
     "2022-02-14", 60000, None, "rejected", True,
     "Strong local delivery but the request exceeded what we fund at this stage. "
     "We would encourage a more focused application."),
    ("Garfield Weston bid FINAL v3.md", "Garfield Weston Foundation",
     "2022-05-03", 25000, None, "rejected", True,
     "We receive many more applications than we can support."),
    ("2022 Bristol City Council community resilience.md", "Bristol City Council",
     "2022-07-19", 22000, 22000, "awarded", True, None),
    ("quartet 2022 round 2.md", "Quartet Community Foundation",
     "2022-11-02", 15000, 15000, "awarded", True, None),
    ("2023 Henry Smith Charity.md", "Henry Smith Charity",
     "2023-01-25", 48000, None, "rejected", True,
     "Assessment noted limited independent evidence of outcomes."),
    ("Screwfix Foundation application.md", "Screwfix Foundation",
     "2023-03-08", 4500, 4500, "awarded", True, None),
    # No sidecar. Outcome must be inferred from the filename.
    ("2023 Postcode Local Trust unsuccessful.md", "Postcode Local Trust",
     "2023-06-14", 18000, None, "rejected", False, None),
    ("2023 Awards for All second application.md", "National Lottery Community Fund",
     "2023-10-01", 9500, None, "rejected", True,
     "The application did not clearly show how this differs from previously funded work."),
    ("2024-01 Quartet express.md", "Quartet Community Foundation",
     "2024-01-30", 5000, 5000, "awarded", True, None),
    # Awarded once, never approached again. The 'money on the table' case.
    ("John James Bristol Foundation 2024.md", "John James Bristol Foundation",
     "2024-03-22", 12000, 12000, "awarded", True, None),
    ("2024 Trussell Trust partnership bid.md", "Trussell Trust",
     "2024-06-11", 30000, None, "rejected", True,
     "Not a fit for this round; we prioritised applications with existing partnership history."),
    ("bid to Souter Charitable Trust.md", "Souter Charitable Trust",
     "2024-09-05", 7000, 7000, "awarded", True, None),
    ("2025 Tudor Trust second attempt £48,000 unsuccessful.md", "Tudor Trust",
     "2025-02-17", 48000, None, "rejected", False, None),
    # Outcome never recorded. Becomes an interview question.
    ("2025 Bristol City Council AEP.md", None, "2025-05-09", 26000, None, "unknown", False, None),
    ("Comic Relief community fund 2025.md", "Comic Relief", "2025-08-20", 35000, None, "unknown", False, None),
    ("2026 Quartet Community Foundation express grants.md", "Quartet Community Foundation",
     "2026-02-03", 15000, 15000, "awarded", True, None),
    ("2026 Lloyds Bank Foundation.md", None, "2026-04-30", 50000, None, "unknown", False, None),
    ("older/2020 small grant application.md", "Quartet Community Foundation",
     "2020-06-01", 3000, 3000, "awarded", True, None),
]

# Never read, even sitting in the funding folder.
SENSITIVE = [
    "safeguarding log 2025.md",
    "beneficiary contact list.md",
    "referral form blank.md",
    "HR - staff contracts.md",
    "DBS check register.md",
]

OTHER_NOISE = ["budget template.xlsx", "logo.png", "notes.docx"]

# Voice: first person plural, "members", lumpy sentence lengths, house
# capitalisation of Trustee and Pantry, no contractions. The style card should
# recover all of that.
EASTON_OPENERS = [
    "We have run community meals from St Mark's Hall since 2019.",
    "Easton Community Kitchen serves three sittings a week, every week, without referral.",
    "We are asking for support to keep the Pantry open through next winter.",
    "Our members are not statistics to us. We know their names, and we know when they stop coming.",
]

EASTON_BODIES = [
    "Last year we served 8,900 meals across 156 sittings. Forty-one volunteers did most of it.",
    "The Pantry costs members £4.50 a week and returns about £22 of food, which for a household "
    "on Universal Credit is the difference between a full week and a thin one.",
    "Our Trustee board reviewed this application in full before submission.",
    "We work in Easton, Lawrence Hill and St Pauls. We do not work outside Bristol, and we say so "
    "when funders ask us to.",
    "Of the 210 members we surveyed, 78 per cent told us they now eat at least one hot meal a day "
    "more often than they did before they found us.",
    "Demand rose. It rose again. We have not turned anyone away yet and we would like to keep it "
    "that way.",
    "An independent evaluation by Bristol Impact Partnership, published in July 2025, looked at "
    "the meals programme across two years and found retention well above what comparable projects "
    "report.",
    "We are small. Three paid staff, 2.2 full time equivalent, and a Trustee board that meets "
    "every six weeks.",
]

EASTON_CLOSERS = [
    "We would be glad to talk this through with you.",
    "This money would go on food, heat and the van. Nothing else.",
    "We have costed this carefully and we can evidence every figure in it.",
]


RYE_LANE_ORG = "Rye Lane Community Kitchen"

RYE_LANE_BIDS = [
    ("2021 Southwark Council neighbourhoods fund.md", "Southwark Council",
     "2021-05-10", 7500, 7500, "awarded", True, None),
    ("Awards for All 2021.md", "National Lottery Community Fund",
     "2021-10-04", 9700, 9700, "awarded", True, None),
    ("2022-03 City Bridge Trust bridging.md", "City Bridge Trust",
     "2022-03-14", 45000, None, "rejected", True,
     "Panel felt the request was large relative to current turnover."),
    ("Trust for London application FINAL.md", "Trust for London",
     "2022-06-20", 30000, None, "rejected", False, None),
    ("2022 London Community Foundation cornerstone.md", "The London Community Foundation",
     "2022-09-02", 9000, 9000, "awarded", True, None),
    ("southwark council 2023 round 2.md", "Southwark Council",
     "2023-02-16", 12000, 8500, "awarded", True, None),
    ("2023 Henry Smith Charity.md", "Henry Smith Charity",
     "2023-05-30", 60000, None, "rejected", True,
     "Assessment noted limited independent evidence of outcomes."),
    ("Peckham Regeneration Fund.md", "Southwark Council",
     "2023-08-11", 4000, 4000, "awarded", True, None),
    ("2023 Postcode Local Trust unsuccessful.md", None,
     "2023-10-01", 18000, None, "rejected", False, None),
    ("2024 Awards for All second application.md", "National Lottery Community Fund",
     "2024-01-22", 9900, 9900, "awarded", True, None),
    ("2024-04 London Community Foundation express.md", "The London Community Foundation",
     "2024-04-08", 5000, 5000, "awarded", True, None),
    ("United St Saviour's Charity 2024.md", "United St Saviour's Charity",
     "2024-07-19", 15000, 15000, "awarded", True, None),
    ("2024 Trussell Trust partnership bid.md", "Trussell Trust",
     "2024-11-05", 22000, None, "rejected", True, None),
    ("bid to Souter Charitable Trust.md", "Souter Charitable Trust",
     "2025-01-30", 6000, None, "rejected", False, None),
    ("2025 City Bridge second attempt £48,000 unsuccessful.md", None,
     "2025-03-17", 48000, None, "rejected", True, None),
    ("2025 Southwark Council AEP.md", None, "2025-05-09", 26000, None, "unknown", False, None),
    ("Comic Relief community fund 2025.md", "Comic Relief",
     "2025-08-20", 35000, None, "unknown", False, None),
    ("2025 London Community Foundation winter.md", "The London Community Foundation",
     "2025-09-15", 3000, None, "unknown", True, None),
    ("2026 Trust for London second approach.md", "Trust for London",
     "2026-01-12", 28000, None, "unknown", False, None),
    ("older/2020 small grant application.md", "Southwark Council",
     "2020-07-03", 2500, 2500, "awarded", True, None),
]

# Same design goals as the Easton corpus and none of the same sentences:
# first person plural, "members", markedly uneven sentence lengths, house
# capitalisation of Trustee and Pantry, no contractions.
RYE_LANE_OPENERS = [
    "We have cooked out of Rye Lane Baptist Hall since 2018.",
    "Rye Lane Community Kitchen runs three sittings a week and turns nobody away at the door.",
    "We are asking for help to hold the Pantry open through the winter.",
    "Our members know us by name, and we notice the week somebody stops coming.",
]

RYE_LANE_BODIES = [
    "We served 7,400 meals over 148 sittings last year. Thirty-eight volunteers carried most of that.",
    "The Pantry costs members £4.50 a week and sends them home with roughly £22 of food, which "
    "on a fixed income is the difference between managing and not.",
    "Our Trustee board read this application end to end before it was sent.",
    "We work in Peckham and Nunhead. We do not deliver outside the Southwark boundary, and we "
    "tell funders so rather than pretend otherwise.",
    "Of the 180 members who answered our survey, 74 per cent said they now eat a hot meal daily "
    "more often than they managed before they came to us.",
    "Referrals climbed. Then they climbed again. We have not closed the list yet.",
    "Southwark Impact Partnership evaluated the meals programme independently and published in "
    "June 2025, finding retention stronger than comparable projects across the borough report.",
    "We are a small operation. Three paid staff, two full time equivalent, and a Trustee board "
    "that sits every six weeks without fail.",
]

RYE_LANE_CLOSERS = [
    "We would welcome the chance to walk you through this in person.",
    "This grant would pay for food, fuel and the fridge. That is all.",
    "Every figure here is costed and we can show you the working.",
]


@dataclass
class Profile:
    """One fictional charity: its bids and the voice they are written in."""

    key: str
    org: str
    out: str
    bids: list
    openers: list[str]
    bodies: list[str]
    closers: list[str]


PROFILES = {
    "easton": Profile("easton", EASTON_ORG, "fixtures/easton-funding", EASTON_BIDS,
                      EASTON_OPENERS, EASTON_BODIES, EASTON_CLOSERS),
    "rye-lane": Profile("rye-lane", RYE_LANE_ORG, "fixtures/rye-lane-funding",
                        RYE_LANE_BIDS, RYE_LANE_OPENERS, RYE_LANE_BODIES,
                        RYE_LANE_CLOSERS),
}


def body_text(rng: random.Random, funder: str | None, profile: Profile) -> str:
    paras = []
    paras.append(rng.choice(profile.openers))
    picks = rng.sample(profile.bodies, k=rng.randint(3, 5))
    paras.append(" ".join(picks[:2]))
    paras.append(" ".join(picks[2:]))
    if funder:
        paras.append(
            f"We are applying to {funder} because our work sits squarely within what you fund, "
            "and because we would rather ask once, properly, than send the same letter everywhere."
        )
    paras.append(rng.choice(profile.closers))
    return "\n\n".join(p for p in paras if p.strip())


def build(out: Path, profile: Profile) -> dict:
    rng = random.Random(1904)
    out.mkdir(parents=True, exist_ok=True)
    (out / "older").mkdir(exist_ok=True)

    written = 0
    for name, funder, submitted, asked, awarded, outcome, sidecar, feedback in profile.bids:
        p = out / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body_text(rng, funder, profile))
        written += 1
        if sidecar:
            meta = {
                "funder": funder,
                "submitted": submitted,
                "amount_asked": asked,
                "amount_awarded": awarded,
                "outcome": outcome,
            }
            if feedback:
                meta["feedback"] = feedback
            (out / f"{name}.meta.json").write_text(json.dumps(meta, indent=2))

    for name in SENSITIVE:
        (out / name).write_text("This file must never be read by the agent.\n")
    for name in OTHER_NOISE:
        (out / name).write_bytes(b"binary-ish placeholder")

    bids = profile.bids
    decided = [b for b in bids if b[5] in ("awarded", "rejected")]
    return {
        "bids": written,
        "with_sidecar": sum(1 for b in bids if b[6]),
        "decided": len(decided),
        "awarded": sum(1 for b in decided if b[5] == "awarded"),
        "unknown_outcome": sum(1 for b in bids if b[5] == "unknown"),
        "no_funder_recorded": sum(1 for b in bids if b[1] is None),
        "sensitive_planted": len(SENSITIVE),
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default="easton", choices=sorted(PROFILES),
                    help="which fictional charity to generate")
    ap.add_argument("--out", default=None, type=Path,
                    help="output folder (defaults to the profile's own)")
    args = ap.parse_args()
    profile = PROFILES[args.profile]
    out = args.out or Path(profile.out)
    stats = build(out, profile)
    print(f"{profile.org} synthetic archive -> {out}")
    for k, v in stats.items():
        print(f"  {k}: {v}")
