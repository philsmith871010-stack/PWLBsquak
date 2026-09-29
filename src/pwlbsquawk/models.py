"""Pydantic models shared across the pipeline."""

from __future__ import annotations

import datetime as dt
import hashlib
from enum import StrEnum
from typing import Any

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator

HEADLINE_MAX = 140


class Category(StrEnum):
    boe = "boe"
    data = "data"
    fiscal = "fiscal"
    supply = "supply"
    geopolitics = "geopolitics"
    energy = "energy"
    global_rates = "global_rates"


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def to_iso(ts: dt.datetime) -> str:
    """ISO 8601 with a trailing Z, second precision."""
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=dt.UTC)
    return ts.astimezone(dt.UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_ts(value: str | dt.datetime) -> dt.datetime:
    if isinstance(value, dt.datetime):
        return value if value.tzinfo else value.replace(tzinfo=dt.UTC)
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = dt.datetime.fromisoformat(text)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.UTC)


def event_id(source: str, uri: str) -> str:
    """Deterministic id so re-ingesting the same item is a no-op."""
    return hashlib.blake2b(f"{source}|{uri}".encode(), digest_size=8).hexdigest()


class Event(BaseModel):
    """One raw item from any source. Stored verbatim in the events table."""

    model_config = ConfigDict(extra="forbid")

    id: str
    source: str  # "bluesky" | "market"
    author: str  # Bluesky handle, or "market"
    author_did: str | None = None
    ts: dt.datetime
    text: str
    uri: str
    raw: dict[str, Any] = Field(default_factory=dict)
    is_reply: bool = False
    tier: int | None = None

    @field_validator("ts", mode="before")
    @classmethod
    def _parse_ts(cls, v: Any) -> dt.datetime:
        return parse_ts(v)

    @classmethod
    def create(
        cls,
        *,
        source: str,
        author: str,
        ts: dt.datetime | str,
        text: str,
        uri: str,
        raw: dict[str, Any] | None = None,
        **extra: Any,
    ) -> Event:
        return cls(
            id=event_id(source, uri),
            source=source,
            author=author,
            ts=parse_ts(ts),
            text=text,
            uri=uri,
            raw=raw or {},
            **extra,
        )

    @property
    def web_url(self) -> str | None:
        """bsky.app link for a post URI, None for other sources."""
        if self.uri.startswith("at://") and "/app.bsky.feed.post/" in self.uri:
            did, rkey = self.uri[len("at://") :].split("/app.bsky.feed.post/")
            return f"https://bsky.app/profile/{self.author or did}/post/{rkey}"
        return None


TENORS = ("gilt_2y", "gilt_5y", "gilt_10y", "gilt_30y")


class MarketSnapshot(BaseModel):
    """Latest UK rates picture. Yields and rates are in percent (e.g. 4.52)."""

    model_config = ConfigDict(extra="forbid")

    ts: dt.datetime
    gilt_2y: float
    gilt_5y: float
    gilt_10y: float
    gilt_30y: float
    sonia: float  # overnight SONIA fixing
    ois: dict[str, float] = Field(default_factory=dict)  # SONIA OIS curve, e.g. {"3m": 4.1}
    source: str = "mock"

    @field_validator("ts", mode="before")
    @classmethod
    def _parse_ts(cls, v: Any) -> dt.datetime:
        return parse_ts(v)

    def value(self, tenor: str) -> float:
        if tenor in TENORS or tenor == "sonia":
            return float(getattr(self, tenor))
        if tenor.startswith("ois_"):
            return float(self.ois[tenor[4:]])
        raise KeyError(tenor)

    def to_prompt_dict(self) -> dict[str, Any]:
        return {
            "as_of": to_iso(self.ts),
            "gilt_yields_pct": {t.split("_")[1]: round(getattr(self, t), 3) for t in TENORS},
            "sonia_pct": round(self.sonia, 3),
            "sonia_ois_pct": {k: round(v, 3) for k, v in self.ois.items()},
            "source": self.source,
        }


class SquawkDraft(BaseModel):
    """What the model returns for one squawk, before we attach ids and metadata."""

    model_config = ConfigDict(extra="forbid")

    ts: str | None = None
    headline: str = Field(min_length=1, max_length=HEADLINE_MAX)
    detail: str = Field(min_length=1)
    category: Category
    importance: int = Field(ge=1, le=3)
    source_event_ids: list[str] = Field(min_length=1)

    @field_validator("headline", "detail", mode="before")
    @classmethod
    def _strip(cls, v: Any) -> Any:
        return v.strip() if isinstance(v, str) else v


class WriterOutput(BaseModel):
    """The strict JSON contract with the model: {"material": false} or a list of squawks."""

    model_config = ConfigDict(extra="forbid")

    material: bool
    squawks: list[SquawkDraft] = Field(default_factory=list)


class SquawkSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    author: str
    uri: str
    url: str | None = None


class Squawk(BaseModel):
    """A published squawk: the integration contract (see docs/INTEGRATION.md)."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: str
    ts: dt.datetime
    headline: str = Field(min_length=1, max_length=HEADLINE_MAX)
    detail: str = Field(min_length=1, validation_alias=AliasChoices("detail", "la_angle"))
    category: Category
    importance: int = Field(ge=1, le=3)
    source_event_ids: list[str]
    sources: list[SquawkSource] = Field(default_factory=list)
    model: str = ""
    run_id: str = ""
    created_at: dt.datetime

    @field_validator("ts", "created_at", mode="before")
    @classmethod
    def _parse_ts(cls, v: Any) -> dt.datetime:
        return parse_ts(v)

    def to_public_dict(self) -> dict[str, Any]:
        d = self.model_dump(mode="json")
        d["ts"] = to_iso(self.ts)
        d["created_at"] = to_iso(self.created_at)
        d["category"] = self.category.value
        return d


class RunRecord(BaseModel):
    """Token usage and cost for one writer call."""

    model_config = ConfigDict(extra="forbid")

    id: str
    started_at: dt.datetime
    finished_at: dt.datetime
    mode: str  # write | run | replay
    backend: str
    model: str
    n_events: int
    n_squawks: int
    material: bool
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_usd: float = 0.0
    error: str | None = None
