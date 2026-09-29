"""Builds the writer request: a stable system prompt plus one JSON user message."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

from ..models import Event, MarketSnapshot, to_iso

DEFAULT_PROMPT_PATH = Path(__file__).resolve().parents[3] / "prompts" / "squawk_writer.md"


def load_system_prompt(path: str | Path | None = None) -> str:
    p = Path(path) if path else DEFAULT_PROMPT_PATH
    if not p.exists() and path:
        raise FileNotFoundError(f"prompt file not found: {p}")
    return p.read_text(encoding="utf-8").strip()


def event_to_prompt_dict(event: Event) -> dict[str, Any]:
    return {
        "id": event.id,
        "ts": to_iso(event.ts),
        "author": event.author,
        "tier": event.tier,
        "text": event.text,
    }


def build_user_message(
    *,
    now: dt.datetime,
    events: list[Event],
    snapshot: MarketSnapshot | None,
    recent_headlines: list[str],
) -> str:
    market_alerts = [e for e in events if e.source == "market"]
    posts = [e for e in events if e.source != "market"]
    payload = {
        "now": to_iso(now),
        "market_snapshot": snapshot.to_prompt_dict() if snapshot else None,
        "market_alerts": [{"id": e.id, "ts": to_iso(e.ts), "text": e.text} for e in market_alerts],
        "recent_squawk_headlines": recent_headlines,
        "events": [event_to_prompt_dict(e) for e in posts],
    }
    body = json.dumps(payload, ensure_ascii=False, indent=1)
    return (
        "Here is the latest batch. Decide whether anything is a material fact for UK rates and "
        "the gilt market and reply with JSON only.\n\n" + body
    )
