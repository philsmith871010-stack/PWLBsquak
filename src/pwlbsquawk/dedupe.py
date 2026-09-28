"""Near-duplicate detection for short news-like texts.

Wire services, journalists and aggregators often post the same headline within
minutes of each other. We normalise the text, then compare word 3-gram shingles
with Jaccard similarity against recently ingested events.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable

_URL_RE = re.compile(r"https?://\S+|\b[\w.-]+\.(?:com|co\.uk|org|net|uk|io)\S*", re.IGNORECASE)
_HANDLE_RE = re.compile(r"@[\w.-]+")
_NONWORD_RE = re.compile(r"[^\w\s%]+", re.UNICODE)
_WS_RE = re.compile(r"\s+")

SHINGLE_SIZE = 2
MIN_CHARS_FOR_FUZZY = 24  # below this only exact normalised matches count


def normalise(text: str) -> str:
    """Lower-case, drop URLs/handles/punctuation, collapse whitespace."""
    t = text.lower()
    t = _URL_RE.sub(" ", t)
    t = _HANDLE_RE.sub(" ", t)
    t = t.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    t = _NONWORD_RE.sub(" ", t)
    t = _WS_RE.sub(" ", t).strip()
    return t


def fingerprint(norm_text: str) -> str:
    return hashlib.sha1(norm_text.encode()).hexdigest()[:16]


def shingles(norm_text: str, n: int = SHINGLE_SIZE) -> set[str]:
    words = norm_text.split()
    if len(words) < n:
        return set(words)
    return {" ".join(words[i : i + n]) for i in range(len(words) - n + 1)}


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    if inter == 0:
        return 0.0
    return inter / len(a | b)


def find_duplicate(
    norm_text: str,
    candidates: Iterable[tuple[str, str]],
    threshold: float = 0.6,
) -> tuple[str, float] | None:
    """Return (candidate_id, score) for the best near-duplicate above threshold.

    `candidates` yields (id, norm_text) for recent events. Exact normalised
    matches always win (score 1.0). Short texts are only matched exactly.
    """
    if not norm_text:
        return None
    mine = shingles(norm_text)
    best: tuple[str, float] | None = None
    fuzzy_ok = len(norm_text) >= MIN_CHARS_FOR_FUZZY
    for cand_id, cand_text in candidates:
        if cand_text == norm_text:
            return cand_id, 1.0
        if not fuzzy_ok or len(cand_text) < MIN_CHARS_FOR_FUZZY:
            continue
        score = jaccard(mine, shingles(cand_text))
        if score >= threshold and (best is None or score > best[1]):
            best = (cand_id, score)
    return best
