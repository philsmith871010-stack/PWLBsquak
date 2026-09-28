"""Token accounting and estimated cost per writer call."""

from __future__ import annotations

import logging
from dataclasses import dataclass

log = logging.getLogger(__name__)

# USD per million tokens: (input, output, cache write, cache read). Anthropic list prices
# as of 2026-09; update here if they change. Unknown models fall back to Opus 5.5 rates.
PRICES: dict[str, tuple[float, float, float, float]] = {
    "claude-opus-5-5": (4.0, 20.0, 5.0, 0.20),
    "claude-opus-5": (5.0, 25.0, 6.25, 0.50),
    "claude-opus-4-8": (5.0, 25.0, 6.25, 0.50),
    "claude-opus-4-7": (5.0, 25.0, 6.25, 0.50),
    "claude-opus-4-6": (5.0, 25.0, 6.25, 0.50),
    "claude-sonnet-5-5": (2.0, 10.0, 2.50, 0.20),
    "claude-sonnet-5": (2.0, 10.0, 2.50, 0.20),
    "claude-sonnet-4-6": (3.0, 15.0, 3.75, 0.30),
    "claude-haiku-4-5": (1.0, 5.0, 1.25, 0.10),
    "claude-fable-5-1": (10.0, 50.0, 12.50, 0.25),
    "claude-fable-5": (10.0, 50.0, 12.50, 1.00),
}
_FALLBACK = "claude-opus-5-5"
_warned: set[str] = set()


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            self.input_tokens + other.input_tokens,
            self.output_tokens + other.output_tokens,
            self.cache_read_tokens + other.cache_read_tokens,
            self.cache_write_tokens + other.cache_write_tokens,
        )

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens + self.cache_read_tokens + self.cache_write_tokens


def prices_for(model: str) -> tuple[float, float, float, float]:
    for key in sorted(PRICES, key=len, reverse=True):  # longest prefix wins
        if model.startswith(key):
            return PRICES[key]
    if model not in _warned:
        _warned.add(model)
        log.warning("no price table entry for %s; estimating with %s rates", model, _FALLBACK)
    return PRICES[_FALLBACK]


def estimate_cost(model: str, usage: Usage) -> float:
    inp, out, cw, cr = prices_for(model)
    usd = (
        usage.input_tokens * inp
        + usage.output_tokens * out
        + usage.cache_write_tokens * cw
        + usage.cache_read_tokens * cr
    ) / 1_000_000
    return round(usd, 6)


def format_usage(model: str, usage: Usage) -> str:
    return (
        f"model={model} in={usage.input_tokens} out={usage.output_tokens} "
        f"cache_read={usage.cache_read_tokens} cache_write={usage.cache_write_tokens} "
        f"est_cost=${estimate_cost(model, usage):.4f}"
    )
