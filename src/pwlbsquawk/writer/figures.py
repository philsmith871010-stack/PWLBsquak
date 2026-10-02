"""Guard against invented numbers: every figure in a squawk must appear in its inputs.

A "figure" is any number in the squawk text except tenors (2-year, 10y), clock times
(08:15), days of the month written with a month name (7 October) and the current year.
Source text is normalised so that 54.918K matches 54,918 and 1.701M matches 1,701,000.
"""

from __future__ import annotations

import re

_MONTHS = "January|February|March|April|May|June|July|August|September|October|November|December"
_SKIP_RE = re.compile(
    r"\b\d{1,2}:\d{2}\b"  # clock times
    r"|\b\d+(?=-(?:year|month|week|day|yr)\b)"  # 2-year, 3-month
    r"|\b\d+(?=\s?(?:y|Y|yr)\b)"  # 10y, 30Y
    rf"|\b\d{{1,2}}(?=(?:st|nd|rd|th)?\s+(?:{_MONTHS})\b)"  # 7 October
    rf"|\b(?:{_MONTHS})\s\d{{1,2}}\b(?!\d)",  # October 7 (month name dropped too)
)
_NUM_RE = re.compile(r"(?<![A-Za-z])\d+(?:[.,]\d+)*")
_SCALED_RE = re.compile(r"(?<![A-Za-z])(\d+(?:[.,]\d+)*)\s?(k|K|m|M|mn|bn|B)\b")
_SENT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z])")
_MULT = {"k": 1e3, "K": 1e3, "m": 1e6, "M": 1e6, "mn": 1e6, "bn": 1e9, "B": 1e9}


def _norm(tok: str) -> str:
    tok = tok.replace(",", "")
    if "." in tok:
        tok = tok.rstrip("0").rstrip(".") or "0"
    return tok


def _fmt(x: float) -> str:
    return _norm(f"{x:.6f}")


def figures(text: str) -> set[str]:
    """Numbers a reader would take as data."""
    cleaned = _SKIP_RE.sub(" ", text)
    return {_norm(m.group(0)) for m in _NUM_RE.finditer(cleaned)}


def source_figures(text: str) -> set[str]:
    """Every number in the sources, plus scaled forms of 54.9K, 1.7M, 3bn."""
    out = {_norm(m.group(0)) for m in _NUM_RE.finditer(text)}
    for m in _SCALED_RE.finditer(text):
        v = float(m.group(1).replace(",", ""))
        mult = _MULT[m.group(2)]
        full = v * mult
        out |= {_fmt(full), _fmt(full / 1e3), _fmt(full / 1e6), _fmt(full / 1e9)}
    return out


def unsupported(text: str, sources: str, *, allowed: set[str] | None = None) -> set[str]:
    """Figures in `text` that do not appear in `sources` (or in `allowed`)."""
    have = source_figures(sources) | (allowed or set())
    return {f for f in figures(text) if f not in have}


def sentences(text: str) -> list[str]:
    return [s for s in _SENT_RE.split(text.strip()) if s]


def strip_unsupported(detail: str, sources: str, *, allowed: set[str] | None = None) -> tuple[str, list[str]]:
    """Drop whole sentences that carry an unsupported figure. Returns (new_detail, removed)."""
    kept, removed = [], []
    for s in sentences(detail):
        (removed if unsupported(s, sources, allowed=allowed) else kept).append(s)
    return " ".join(kept), removed
