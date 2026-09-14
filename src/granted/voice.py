"""Write in the organisation's voice, because it is their application.

This is not about evading detection. It is about fidelity. A draft assembled from
an organisation's own prior sentences, in their measured register, is genuinely
their writing. That is both a better product and a defensible one.

The style card is measured from the archive, never assumed.
"""

from __future__ import annotations

import re
import statistics
from collections import Counter
from dataclasses import dataclass, field

from .archive import Submission

# Phrases that mark text as machine-written to anyone who reads a lot of it.
# Mechanical pass, not a model. Cheap and effective.
TELLS = [
    r"\bdelve\b", r"\btapestry\b", r"\bunderscore[sd]?\b", r"\bmoreover\b",
    r"\bfurthermore\b", r"\bit is worth noting\b", r"\bin today'?s\b",
    r"\bnavigat(e|ing) the\b", r"\blandscape of\b", r"\bplays a (vital|crucial|pivotal) role\b",
    r"\bstands as a\b", r"\btestament to\b", r"\bnot (just|merely|only) .{1,40}\bbut\b",
    r"\bserves as\b", r"\bfoster(ing)? a sense of\b", r"\bholistic approach\b",
    r"\bleverag(e|ing)\b", r"\brobust\b", r"\bseamless(ly)?\b", r"\bmultifaceted\b",
    r"\bever[- ]evolving\b", r"\bcommitment to excellence\b", r"\bat the heart of\b",
]
_TELLS = [(p, re.compile(p, re.I)) for p in TELLS]

# Same list, phrased for a prompt rather than a matcher.
BANNED_PLAIN = [
    "delve", "tapestry", "underscores", "moreover", "furthermore",
    "it is worth noting", "in today's", "navigating the", "landscape of",
    "plays a vital role", "stands as a", "testament to", "serves as",
    "fostering a sense of", "holistic approach", "leverage", "robust",
    "seamless", "multifaceted", "ever-evolving", "at the heart of",
    'the "not just X, but Y" construction',
    "a three-item list where two items would do",
]

_SENT = re.compile(r"(?<=[.!?])\s+")
_WORD = re.compile(r"[A-Za-z''-]+")

# Words an org uses for the people it serves. Never substitute across these.
BENEFICIARY_TERMS = [
    "members", "guests", "service users", "neighbours", "residents", "clients",
    "participants", "young people", "families", "households", "attendees",
    "beneficiaries", "customers", "visitors", "learners", "students",
]

STOPWORDS = set("""the a an and or but of to in for on with at by from as is are was were
be been being it its this that these those we our us they their he she you your i not
have has had do does did will would can could should may might must if then than so
""".split())


@dataclass
class StyleCard:
    """Measured, not assumed. Every field comes from their own text."""

    n_documents: int
    n_sentences: int
    median_sentence_words: float
    sentence_word_sd: float
    p10_sentence_words: float
    p90_sentence_words: float
    median_paragraph_sentences: float
    first_person_plural: bool
    contraction_rate: float
    beneficiary_term: str | None
    distinctive_lexicon: list[str] = field(default_factory=list)
    capitalised_terms: list[str] = field(default_factory=list)

    def prompt_block(self) -> str:
        """Injected into the drafter. Constraints, not vibes."""
        lines = [
            "VOICE CONSTRAINTS, measured from this organisation's own past applications.",
            f"Corpus: {self.n_documents} documents, {self.n_sentences} sentences.",
            "",
            f"Sentence length: median {self.median_sentence_words:.0f} words, "
            f"typical range {self.p10_sentence_words:.0f} to {self.p90_sentence_words:.0f}, "
            f"standard deviation {self.sentence_word_sd:.1f}.",
            "Match that variance. Do not write sentences of uniform length. Real writing "
            "puts a short sentence next to a long one. Even rhythm is the clearest tell "
            "that a machine wrote something.",
            "",
            f"Paragraphs run about {self.median_paragraph_sentences:.0f} sentences.",
            f"Person: {'first person plural (we, our)' if self.first_person_plural else 'third person'}. Do not switch.",
            f"Contractions: {'used' if self.contraction_rate > 0.02 else 'not used'}.",
        ]
        if self.beneficiary_term:
            lines.append(
                f'This organisation calls the people it serves "{self.beneficiary_term}". '
                "Use that exact word. Never substitute a synonym, however natural it feels."
            )
        if self.distinctive_lexicon:
            lines.append("Their vocabulary includes: " + ", ".join(self.distinctive_lexicon[:15]) + ".")
        if self.capitalised_terms:
            lines.append(
                "These terms appear capitalised in their writing: "
                + ", ".join(self.capitalised_terms)
                + ". Preserve that capitalisation, do not normalise it. Note that "
                "this list mixes house conventions with proper nouns; both should "
                "be left as written."
            )
        lines += [
            "",
            "Never use: " + ", ".join(BANNED_PLAIN) + ".",
            "",
            "Prefer reusing their own sentences verbatim over paraphrasing them. If a past "
            "application already states something well, lift it rather than rewriting it.",
        ]
        return "\n".join(lines)


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENT.split(text) if len(s.strip()) > 3]


def build_style_card(subs: list[Submission]) -> StyleCard | None:
    """Measure the org's voice. Returns None if the corpus is too thin to trust."""
    docs = [s.text for s in subs if s.text and len(s.text.split()) > 40]
    if not docs:
        return None

    sents = [x for d in docs for x in _sentences(d)]
    lengths = [len(_WORD.findall(s)) for s in sents]
    if len(lengths) < 5:
        return None

    words = [w for d in docs for w in _WORD.findall(d)]
    lower = [w.lower() for w in words]

    paras = [p for d in docs for p in d.split("\n\n") if p.strip()]
    para_lens = [max(1, len(_sentences(p))) for p in paras]

    fpp = sum(1 for w in lower if w in ("we", "our", "us")) > sum(1 for w in lower if w in ("they", "their"))
    contractions = sum(1 for w in words if "'" in w) / max(1, len(words))

    term_counts = Counter()
    blob = " ".join(docs).lower()
    for t in BENEFICIARY_TERMS:
        n = blob.count(t)
        if n:
            term_counts[t] = n
    beneficiary = term_counts.most_common(1)[0][0] if term_counts else None

    lexicon = [w for w, n in Counter(lower).most_common(200)
               if w not in STOPWORDS and len(w) > 5 and n >= 3][:20]

    # A word capitalised mid-sentence is a house convention. Sentence-initial
    # capitals and proper nouns are not, so only count occurrences that are not
    # the first word of a sentence.
    mid_sentence: Counter = Counter()
    for d in docs:
        for s in _sentences(d):
            for w in _WORD.findall(s)[1:]:
                if w[0].isupper():
                    mid_sentence[w] += 1
    quirks = [
        w for w, n in mid_sentence.most_common(40)
        if n >= 2 and len(w) > 4 and w.lower() not in STOPWORDS
        and not w.isupper()          # acronyms are not style
    ]
    return StyleCard(
        n_documents=len(docs),
        n_sentences=len(sents),
        median_sentence_words=statistics.median(lengths),
        sentence_word_sd=statistics.pstdev(lengths) if len(lengths) > 1 else 0.0,
        p10_sentence_words=min(lengths),
        p90_sentence_words=max(lengths),
        median_paragraph_sentences=statistics.median(para_lens) if para_lens else 1,
        first_person_plural=fpp,
        contraction_rate=contractions,
        beneficiary_term=beneficiary,
        distinctive_lexicon=lexicon,
        capitalised_terms=quirks[:8],
    )


@dataclass
class VoiceReport:
    tells_found: list[tuple[str, str]]
    uniformity_warning: str | None
    term_violations: list[str]

    @property
    def clean(self) -> bool:
        return not self.tells_found and not self.uniformity_warning and not self.term_violations


def check(draft: str, card: StyleCard | None) -> VoiceReport:
    """Mechanical post-pass. Runs after generation, before the draft is filed."""
    tells = []
    for raw, rx in _TELLS:
        for m in rx.finditer(draft):
            tells.append((m.group(0), raw))

    warning = None
    sents = _sentences(draft)
    if len(sents) > 3:
        lengths = [len(_WORD.findall(s)) for s in sents]
        sd = statistics.pstdev(lengths)
        if card and card.sentence_word_sd and sd < card.sentence_word_sd * 0.6:
            warning = (
                f"Sentence lengths too uniform: sd {sd:.1f} against their measured "
                f"{card.sentence_word_sd:.1f}. Break up the rhythm."
            )

    violations = []
    if card and card.beneficiary_term:
        for t in BENEFICIARY_TERMS:
            if t != card.beneficiary_term and re.search(rf"\b{t}\b", draft, re.I):
                violations.append(f'used "{t}" where they say "{card.beneficiary_term}"')

    return VoiceReport(tells, warning, violations)
