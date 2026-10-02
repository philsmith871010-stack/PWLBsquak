"""Corrections to published squawks, kept in data/corrections.json and applied on every run.

    {"corrections": {"<squawk id>": {"headline": "...", "detail": "...", "reason": "..."}}}

Applying is idempotent: the store row, the ndjson history line and squawks.json are only
rewritten when they differ from the correction. Keeping the file in the repo means a lost
database or a history restore cannot bring an uncorrected squawk back.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from .config import Settings
from .models import Squawk
from .outputs import write_squawks_json
from .store import Store

log = logging.getLogger(__name__)

FIELDS = ("headline", "detail")


def load_corrections(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("corrections", {})


def _patch(sq: dict, fix: dict) -> bool:
    changed = False
    for f in FIELDS:
        if f in fix and sq.get(f) != fix[f]:
            sq[f] = fix[f]
            changed = True
    return changed


def apply_corrections(store: Store, settings: Settings, path: Path | None = None) -> int:
    path = path or settings.output_dir / "corrections.json"
    fixes = load_corrections(path)
    if not fixes:
        return 0
    changed = 0
    for sid, fix in fixes.items():
        changed += store.update_squawk_text(sid, **{f: fix[f] for f in FIELDS if f in fix})
    nd = settings.squawks_ndjson_path
    nd_changed = 0
    if nd.exists():
        lines = nd.read_text(encoding="utf-8").splitlines()
        out = []
        for line in lines:
            if line.strip():
                sq = json.loads(line)
                if sq.get("id") in fixes and _patch(sq, fixes[sq["id"]]):
                    nd_changed += 1
                    line = json.dumps(Squawk.model_validate(sq).to_public_dict(), ensure_ascii=False)
            out.append(line)
        if nd_changed:
            tmp = nd.with_suffix(nd.suffix + ".tmp")
            tmp.write_text("\n".join(out) + "\n", encoding="utf-8")
            os.replace(tmp, nd)
    if changed or nd_changed:
        write_squawks_json(settings.squawks_json_path, store.latest_squawks(settings.squawks_json_limit))
        log.info("applied corrections: %d in the store, %d in the history file", changed, nd_changed)
    return changed + nd_changed
