"""Settings (from environment / .env) and the curated accounts list."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, field_validator

ENV_PREFIX = "SQUAWK_"


def _env(name: str, default: str | None = None) -> str | None:
    return os.environ.get(ENV_PREFIX + name, default)


class Settings(BaseModel):
    """All tunables. Every field maps to SQUAWK_<UPPER_NAME> (see .env.example)."""

    model_config = ConfigDict(extra="forbid")

    # Claude
    anthropic_api_key: str | None = None  # from ANTHROPIC_API_KEY (no prefix)
    model: str = "claude-opus-5-5"
    effort: str = "medium"
    writer_backend: str = "anthropic"  # anthropic | claude-cli | mock
    claude_cli: str = "claude"  # command used by the claude-cli backend
    claude_cli_model: str = "opus"  # Claude Code model name for that backend (opus | sonnet | haiku)
    fallbacks: str = "default"  # default | off
    max_tokens: int = 8000
    max_events_per_call: int = 60
    include_replies: bool = False
    recent_squawks_in_prompt: int = 60  # headlines shown to the writer to stop repeats
    max_squawks_per_batch: int = 5
    category_caps: dict[str, int] = Field(default_factory=lambda: {"geopolitics": 2, "energy": 2})
    repeat_threshold: float = (
        0.75  # near-identical headline to a recent squawk; rewordings are left to the prompt
    )
    writer_stale_after_hours: float = 12.0  # events older than this are dropped unwritten; raise to backfill

    # cadence (seconds)
    write_interval: int = 300
    event_mode_interval: int = 60
    event_mode_duration: int = 1800
    event_mode_keywords: list[str] = Field(
        default_factory=lambda: ["bank rate", "mpc", "gilt", "emergency budget", "breaking"]
    )

    # sources
    jetstream_url: str = "wss://jetstream2.us-east.bsky.network/subscribe"
    bsky_api: str = "https://api.bsky.app"
    accounts_file: Path = Path("config/accounts.yaml")
    poll_backfill_hours: float = 6.0
    poll_page_size: int = 30

    # market
    market_source: str = "mock"
    market_poll_interval: int = 60
    market_thresholds: dict[str, float] = Field(
        default_factory=lambda: {"gilt_10y": 5.0, "gilt_2y": 5.0, "gilt_30y": 5.0}
    )
    market_window_minutes: int = 15
    market_cooldown_minutes: int = 15

    # dedupe
    dedupe_window_hours: float = 6.0
    dedupe_threshold: float = 0.6

    # storage / outputs
    db_path: Path = Path("data/squawk.sqlite")
    output_dir: Path = Path("data")
    prompt_file: Path = Path("prompts/squawk_writer.md")
    squawks_json_limit: int = 200
    log_level: str = "INFO"

    @field_validator("market_thresholds", mode="before")
    @classmethod
    def _parse_thresholds(cls, v: Any) -> Any:
        if isinstance(v, str):
            out: dict[str, float] = {}
            for part in v.split(","):
                part = part.strip()
                if not part:
                    continue
                key, _, val = part.partition("=")
                out[key.strip()] = float(val)
            return out
        return v

    @field_validator("category_caps", mode="before")
    @classmethod
    def _parse_caps(cls, v: Any) -> Any:
        if isinstance(v, str):
            out: dict[str, int] = {}
            for part in v.split(","):
                key, _, val = part.strip().partition("=")
                if key:
                    out[key.strip()] = int(val)
            return out
        return v

    @field_validator("event_mode_keywords", mode="before")
    @classmethod
    def _parse_keywords(cls, v: Any) -> Any:
        if isinstance(v, str):
            return [k.strip().lower() for k in v.split(",") if k.strip()]
        return v

    @field_validator("effort")
    @classmethod
    def _check_effort(cls, v: str) -> str:
        allowed = {"low", "medium", "high", "xhigh", "max"}
        if v not in allowed:
            raise ValueError(f"effort must be one of {sorted(allowed)}")
        return v

    @classmethod
    def from_env(cls, dotenv: str | os.PathLike[str] | None = ".env") -> Settings:
        """Build settings from the process environment (after loading .env if present)."""
        if dotenv and Path(dotenv).exists():
            load_dotenv(dotenv, override=False)
        values: dict[str, Any] = {}
        for name, field in cls.model_fields.items():
            if name == "anthropic_api_key":
                continue
            raw = _env(name.upper())
            if raw is None:
                continue
            ann = field.annotation
            if ann is bool:
                values[name] = raw.strip().lower() in {"1", "true", "yes", "on"}
            else:
                values[name] = raw
        values["anthropic_api_key"] = os.environ.get("ANTHROPIC_API_KEY") or None
        return cls(**values)

    @property
    def squawks_json_path(self) -> Path:
        return self.output_dir / "squawks.json"

    @property
    def squawks_ndjson_path(self) -> Path:
        return self.output_dir / "squawks.ndjson"


class Account(BaseModel):
    model_config = ConfigDict(extra="allow")

    handle: str
    did: str | None = None
    tier: int = 2
    name: str | None = None
    note: str | None = None


class DisabledAccount(BaseModel):
    model_config = ConfigDict(extra="allow")

    handle: str
    did: str | None = None
    reason: str | None = None


class Accounts(BaseModel):
    """Parsed config/accounts.yaml."""

    model_config = ConfigDict(extra="forbid")

    active: list[Account] = Field(default_factory=list)
    disabled: list[DisabledAccount] = Field(default_factory=list)
    tier_labels: dict[int, str] = Field(default_factory=dict)

    @property
    def dids(self) -> list[str]:
        return [a.did for a in self.active if a.did]

    def by_did(self) -> dict[str, Account]:
        return {a.did: a for a in self.active if a.did}

    def by_handle(self) -> dict[str, Account]:
        return {a.handle: a for a in self.active}


def load_accounts(path: str | os.PathLike[str]) -> Accounts:
    """Read accounts.yaml. Layout:

    tiers:
      1:
        label: ...
        accounts:
          - handle: x.bsky.social
            did: did:plc:...
            name: ...
    disabled:
      - handle: ...
        reason: ...
    """
    with open(path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    active: list[Account] = []
    labels: dict[int, str] = {}
    for tier_key, tier in (data.get("tiers") or {}).items():
        tier_no = int(tier_key)
        tier = tier or {}
        if tier.get("label"):
            labels[tier_no] = str(tier["label"])
        for entry in tier.get("accounts") or []:
            if isinstance(entry, str):
                entry = {"handle": entry}
            active.append(Account(**{**entry, "tier": tier_no}))
    disabled = [DisabledAccount(**d) for d in (data.get("disabled") or [])]
    return Accounts(active=active, disabled=disabled, tier_labels=labels)


def dump_accounts(accounts: Accounts) -> str:
    """Serialise back to the YAML layout used by load_accounts (used by resolve_dids)."""
    tiers: dict[int, dict[str, Any]] = {}
    for acc in sorted(accounts.active, key=lambda a: (a.tier, a.handle)):
        block = tiers.setdefault(acc.tier, {"label": accounts.tier_labels.get(acc.tier, ""), "accounts": []})
        entry: dict[str, Any] = {"handle": acc.handle}
        if acc.did:
            entry["did"] = acc.did
        if acc.name:
            entry["name"] = acc.name
        if acc.note:
            entry["note"] = acc.note
        block["accounts"].append(entry)
    out: dict[str, Any] = {"tiers": dict(sorted(tiers.items()))}
    if accounts.disabled:
        out["disabled"] = [d.model_dump(exclude_none=True) for d in accounts.disabled]
    return yaml.safe_dump(out, sort_keys=False, allow_unicode=True, width=100)
