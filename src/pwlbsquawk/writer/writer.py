"""One writer pass: select events, call the backend, validate, store, publish."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import uuid
from dataclasses import dataclass, field

from pydantic import ValidationError

from ..config import Settings
from ..models import (
    HEADLINE_MAX,
    Event,
    MarketSnapshot,
    RunRecord,
    Squawk,
    SquawkDraft,
    SquawkSource,
    parse_ts,
    utcnow,
)
from ..outputs import write_outputs
from ..store import Store
from .backends import WriterBackend, WriterError
from .cost import Usage, estimate_cost, format_usage
from .prompt import build_user_message, load_system_prompt

log = logging.getLogger(__name__)


@dataclass
class WriterPass:
    run_id: str
    events: list[Event] = field(default_factory=list)
    squawks: list[Squawk] = field(default_factory=list)
    material: bool = False
    usage: Usage = field(default_factory=Usage)
    cost_usd: float = 0.0
    skipped: bool = False
    error: str | None = None
    dropped: int = 0

    @property
    def called_model(self) -> bool:
        return not self.skipped and self.error is None


def repair_headline(text: str) -> str:
    """Trim an over-long headline at a word boundary; the model is told the limit, this is a net."""
    text = " ".join(text.split())
    if len(text) <= HEADLINE_MAX:
        return text
    cut = text[: HEADLINE_MAX - 1]
    if " " in cut:
        cut = cut[: cut.rfind(" ")]
    return cut.rstrip(" ,;:-") + "…"


def parse_writer_output(text: str) -> tuple[bool, list[dict]]:
    """Parse the model reply leniently: the strict object form, a bare list, or {"material": false}.

    Returns (material, raw_squawk_dicts). Each squawk is validated individually afterwards
    so one bad item never discards the whole batch.
    """
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.startswith("json"):
            cleaned = cleaned[4:]
    data = json.loads(cleaned)
    if isinstance(data, list):
        return bool(data), [d for d in data if isinstance(d, dict)]
    if not isinstance(data, dict):
        raise ValueError("writer output must be a JSON object or list")
    squawks = data.get("squawks") or []
    if not isinstance(squawks, list):
        raise ValueError("'squawks' must be a list")
    material = bool(data.get("material", bool(squawks)))
    return material, [d for d in squawks if isinstance(d, dict)]


class SquawkWriter:
    def __init__(
        self,
        settings: Settings,
        store: Store,
        backend: WriterBackend,
        *,
        mode: str = "write",
        system_prompt: str | None = None,
        write_files: bool = True,
        stale_after: dt.timedelta = dt.timedelta(hours=12),
        persist: bool = True,
    ):
        self.settings = settings
        self.store = store
        self.backend = backend
        self.mode = mode
        self.system_prompt = system_prompt or load_system_prompt(settings.prompt_file)
        self.write_files = write_files
        self.stale_after = stale_after
        self.persist = persist  # False = dry run: call the model, keep nothing

    # -- selection -------------------------------------------------------------

    def _select(self, now: dt.datetime) -> list[Event]:
        events = self.store.unprocessed_events(
            limit=self.settings.max_events_per_call, include_replies=self.settings.include_replies
        )
        fresh: list[Event] = []
        stale: list[str] = []
        for e in events:
            if now - e.ts > self.stale_after:
                stale.append(e.id)
            else:
                fresh.append(e)
        if stale:
            log.info("skipping %d stale events older than %s", len(stale), self.stale_after)
            if self.persist:
                self.store.mark_processed(stale, now)
        return fresh

    # -- validation ------------------------------------------------------------

    def _materialise(
        self, drafts: list[SquawkDraft], events: dict[str, Event], run_id: str, now: dt.datetime
    ) -> tuple[list[Squawk], int]:
        squawks: list[Squawk] = []
        dropped = 0
        seen_headlines: set[str] = set()
        for draft in drafts:
            ids = [i for i in draft.source_event_ids if i in events]
            if not ids:
                log.warning("dropping squawk with unknown source ids: %r", draft.headline[:80])
                dropped += 1
                continue
            key = draft.headline.lower()
            if key in seen_headlines:
                dropped += 1
                continue
            seen_headlines.add(key)
            try:
                ts = parse_ts(draft.ts) if draft.ts else max(events[i].ts for i in ids)
            except ValueError:
                ts = max(events[i].ts for i in ids)
            sid = hashlib.blake2b(f"{run_id}|{draft.headline}".encode(), digest_size=6).hexdigest()
            squawks.append(
                Squawk(
                    id=sid,
                    ts=ts,
                    headline=draft.headline,
                    la_angle=draft.la_angle,
                    category=draft.category,
                    importance=draft.importance,
                    source_event_ids=ids,
                    sources=[
                        SquawkSource(author=events[i].author, uri=events[i].uri, url=events[i].web_url)
                        for i in ids
                    ],
                    model=self.backend.model,
                    run_id=run_id,
                    created_at=now,
                )
            )
        return squawks, dropped

    def _validate_drafts(self, raw_squawks: list[dict]) -> tuple[list[SquawkDraft], int]:
        drafts: list[SquawkDraft] = []
        dropped = 0
        for data in raw_squawks:
            data = dict(data)
            if isinstance(data.get("headline"), str):
                data["headline"] = repair_headline(data["headline"])
            try:
                drafts.append(SquawkDraft.model_validate(data))
            except ValidationError as exc:
                log.warning("dropping invalid squawk: %s", exc.errors()[0].get("msg"))
                dropped += 1
        return drafts, dropped

    # -- the pass --------------------------------------------------------------

    def run_once(
        self, *, now: dt.datetime | None = None, snapshot: MarketSnapshot | None = None
    ) -> WriterPass:
        now = now or utcnow()
        run_id = uuid.uuid4().hex[:12]
        result = WriterPass(run_id=run_id)
        events = self._select(now)
        if not events:
            result.skipped = True
            log.debug("writer: nothing new, skipping")
            return result
        result.events = events
        snapshot = snapshot or self.store.latest_market_snapshot(at_or_before=now)
        user = build_user_message(
            now=now,
            events=events,
            snapshot=snapshot,
            recent_headlines=self.store.recent_headlines(self.settings.recent_squawks_in_prompt),
        )
        started = utcnow()
        by_id = {e.id: e for e in events}
        try:
            reply = self.backend.complete(self.system_prompt, user)
            result.usage = reply.usage
            result.cost_usd = estimate_cost(reply.model, reply.usage)
            material_flag, raw_squawks = parse_writer_output(reply.text)
        except (WriterError, ValueError) as exc:
            result.error = f"{type(exc).__name__}: {exc}"[:500]
            log.error("writer pass failed: %s", result.error)
            self._record(result, started, now)
            return result
        except Exception as exc:  # API/network errors: leave events unprocessed for the next pass
            result.error = f"{type(exc).__name__}: {exc}"[:500]
            log.error("writer pass failed: %s", result.error)
            self._record(result, started, now)
            return result

        drafts, dropped = self._validate_drafts(raw_squawks)
        squawks, dropped2 = self._materialise(drafts, by_id, run_id, now)
        result.dropped = dropped + dropped2
        result.material = bool(squawks)
        if material_flag and not squawks and raw_squawks:
            log.warning("model marked the batch material but no squawk survived validation")
        result.squawks = squawks
        self.store.insert_squawks(squawks)
        self.store.mark_processed(by_id.keys(), now)
        self._record(result, started, now)
        if self.write_files and squawks:
            write_outputs(self.store, self.settings, squawks, generated_at=now)
        log.info(
            "writer: events=%d material=%s squawks=%d dropped=%d %s",
            len(events),
            result.material,
            len(squawks),
            result.dropped,
            format_usage(reply.model, reply.usage),
        )
        return result

    def _record(self, result: WriterPass, started: dt.datetime, now: dt.datetime) -> None:
        self.store.insert_run(
            RunRecord(
                id=result.run_id,
                started_at=started,
                finished_at=utcnow(),
                mode=self.mode,
                backend=self.backend.name,
                model=self.backend.model,
                n_events=len(result.events),
                n_squawks=len(result.squawks),
                material=result.material,
                input_tokens=result.usage.input_tokens,
                output_tokens=result.usage.output_tokens,
                cache_read_tokens=result.usage.cache_read_tokens,
                cache_write_tokens=result.usage.cache_write_tokens,
                cost_usd=result.cost_usd,
                error=result.error,
            )
        )
