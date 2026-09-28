"""The integration outputs: data/squawks.json (latest N) and data/squawks.ndjson (history)."""

from __future__ import annotations

import datetime as dt
import json
import os
from collections.abc import Iterable
from pathlib import Path

from .config import Settings
from .models import Squawk, to_iso, utcnow
from .store import Store

SCHEMA_VERSION = 1


def squawks_document(squawks: list[Squawk], *, generated_at: dt.datetime | None = None) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": to_iso(generated_at or utcnow()),
        "count": len(squawks),
        "squawks": [s.to_public_dict() for s in squawks],
    }


def write_squawks_json(path: Path, squawks: list[Squawk], *, generated_at: dt.datetime | None = None) -> None:
    """Atomic write: the site never reads a half-written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(squawks_document(squawks, generated_at=generated_at), ensure_ascii=False, indent=1) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp, path)


def append_ndjson(path: Path, squawks: Iterable[Squawk]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with path.open("a", encoding="utf-8") as fh:
        for s in squawks:
            fh.write(json.dumps(s.to_public_dict(), ensure_ascii=False) + "\n")
            n += 1
    return n


def write_outputs(
    store: Store,
    settings: Settings,
    new_squawks: Iterable[Squawk],
    *,
    generated_at: dt.datetime | None = None,
) -> None:
    latest = store.latest_squawks(settings.squawks_json_limit)
    write_squawks_json(settings.squawks_json_path, latest, generated_at=generated_at)
    append_ndjson(settings.squawks_ndjson_path, new_squawks)


def read_ndjson(path: Path) -> list[Squawk]:
    if not path.exists():
        return []
    out: list[Squawk] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(Squawk.model_validate_json(line))
    return out
