"""`squawk run`: ingest (Jetstream or polling) + market monitor + writer loop in one process."""

from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import logging
import signal
from dataclasses import dataclass, field

from .accounts import resolve_missing_dids
from .config import Accounts, Settings
from .eventmode import EventMode
from .models import Event, utcnow
from .recording import Recorder
from .sources.bluesky_poll import BlueskyPoller
from .sources.jetstream import JetstreamListener
from .sources.market import MarketMonitor, MarketSource, ThresholdDetector, load_market_source
from .store import Store
from .writer.backends import WriterBackend
from .writer.writer import SquawkWriter

log = logging.getLogger(__name__)

CURSOR_KEY = "jetstream:cursor_us"


@dataclass
class RunSummary:
    started_at: dt.datetime
    finished_at: dt.datetime | None = None
    new_events: int = 0
    near_duplicates: int = 0
    market_alerts: int = 0
    writer_passes: int = 0
    squawks: int = 0
    cost_usd: float = 0.0
    errors: list[str] = field(default_factory=list)

    def line(self) -> str:
        secs = ((self.finished_at or utcnow()) - self.started_at).total_seconds()
        return (
            f"run finished after {secs / 60:.1f} min: events={self.new_events} "
            f"near_dupes={self.near_duplicates} "
            f"market_alerts={self.market_alerts} writer_passes={self.writer_passes} squawks={self.squawks} "
            f"est_cost=${self.cost_usd:.4f} errors={len(self.errors)}"
        )


class Runner:
    def __init__(
        self,
        settings: Settings,
        store: Store,
        accounts: Accounts,
        *,
        backend: WriterBackend | None = None,
        use_poll: bool = False,
        duration: dt.timedelta | None = None,
        recorder: Recorder | None = None,
        market_source: MarketSource | None = None,
        poll_since: dt.datetime | None = None,
        poll_interval: int | None = None,
        ingest: bool = True,
        market: bool = True,
    ):
        self.settings = settings
        self.store = store
        self.accounts = accounts
        self.backend = backend
        self.use_poll = use_poll
        self.duration = duration
        self.recorder = recorder
        self.poll_since = poll_since
        self.poll_interval = poll_interval or settings.write_interval
        self.ingest = ingest
        self.market_enabled = market
        self.stop = asyncio.Event()
        self.summary = RunSummary(started_at=utcnow())
        self.event_mode = EventMode(
            dt.timedelta(seconds=settings.event_mode_duration), settings.event_mode_keywords
        )
        source = market_source or load_market_source(settings.market_source)
        detector = ThresholdDetector(
            settings.market_thresholds,
            window=dt.timedelta(minutes=settings.market_window_minutes),
            cooldown=dt.timedelta(minutes=settings.market_cooldown_minutes),
        )
        self.monitor = MarketMonitor(source, detector, store)
        self.writer = SquawkWriter(settings, store, backend, mode="run") if backend is not None else None
        self.listener: JetstreamListener | None = None

    # -- event plumbing ----------------------------------------------------------

    def on_event(self, event: Event) -> None:
        result = self.store.insert_event(
            event,
            dedupe_window_hours=self.settings.dedupe_window_hours,
            dedupe_threshold=self.settings.dedupe_threshold,
        )
        if result.status == "inserted":
            self.summary.new_events += 1
            self._after_new(event)
        elif result.status == "near_duplicate":
            self.summary.near_duplicates += 1
            log.debug("near-duplicate from %s (of %s)", event.author, result.dedupe_of)

    def _after_new(self, event: Event) -> None:
        log.info("new post  %-30s %s", event.author, event.text[:90].replace("\n", " | "))
        if self.recorder:
            self.recorder.event(event)
        self.event_mode.consider(event, utcnow())

    async def _sleep(self, seconds: float) -> None:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self.stop.wait(), timeout=seconds)

    # -- tasks -------------------------------------------------------------------

    async def _jetstream_task(self) -> None:
        dids = self.accounts.dids
        by_did = self.accounts.by_did()
        cursor = self.store.get_cursor(CURSOR_KEY)
        self.listener = JetstreamListener(
            self.settings.jetstream_url,
            dids,
            self.on_event,
            handles={d: a.handle for d, a in by_did.items()},
            tiers={d: a.tier for d, a in by_did.items()},
            cursor_us=int(cursor) if cursor else None,
            on_cursor=lambda c: self.store.set_cursor(CURSOR_KEY, str(c)),
        )
        await self.listener.run(stop=self.stop)

    async def _poll_task(self) -> None:
        poller = BlueskyPoller(
            self.settings.bsky_api,
            self.accounts,
            self.store,
            backfill_hours=self.settings.poll_backfill_hours,
            page_size=self.settings.poll_page_size,
            dedupe_window_hours=self.settings.dedupe_window_hours,
            dedupe_threshold=self.settings.dedupe_threshold,
            on_new=self._after_new,
        )
        since = self.poll_since
        while not self.stop.is_set():
            try:
                stats = await asyncio.to_thread(poller.poll_once, since=since)
                self.summary.new_events += stats.inserted
                self.summary.near_duplicates += stats.near_duplicates
                self.summary.errors.extend(stats.errors)
            except Exception as exc:  # noqa: BLE001
                log.error("poll failed: %s", exc)
                self.summary.errors.append(f"poll: {exc}")
            since = None
            await self._sleep(self.poll_interval)

    async def _market_task(self) -> None:
        window = dt.timedelta(minutes=self.settings.market_window_minutes)
        latest = self.store.latest_market_snapshot()
        if latest and utcnow() - latest.ts <= window:
            self.monitor.seed([latest])
        while not self.stop.is_set():
            try:
                alerts = await asyncio.to_thread(self.monitor.tick)
            except Exception as exc:  # noqa: BLE001
                log.error("market source failed: %s", exc)
                self.summary.errors.append(f"market: {exc}")
                alerts = []
            if self.recorder and self.monitor.latest:
                self.recorder.market(self.monitor.latest)
            for alert in alerts:
                self.summary.market_alerts += 1
                if self.recorder:
                    self.recorder.event(alert)
                self.event_mode.consider(alert, utcnow())
            await self._sleep(self.settings.market_poll_interval)

    async def _writer_task(self) -> None:
        assert self.writer is not None
        while not self.stop.is_set():
            interval = self.event_mode.interval(
                self.settings.write_interval, self.settings.event_mode_interval, utcnow()
            )
            await self._sleep(interval)
            if self.stop.is_set():
                break
            await self._writer_pass()

    async def _writer_pass(self) -> None:
        assert self.writer is not None
        result = await asyncio.to_thread(self.writer.run_once, snapshot=self.monitor.latest)
        if result.skipped:
            return
        self.summary.writer_passes += 1
        self.summary.squawks += len(result.squawks)
        self.summary.cost_usd += result.cost_usd
        if result.error:
            self.summary.errors.append(f"writer: {result.error}")
        for s in result.squawks:
            log.info("SQUAWK [%s/%d] %s", s.category.value, s.importance, s.headline)

    async def _timer_task(self) -> None:
        assert self.duration is not None
        await self._sleep(self.duration.total_seconds())
        if not self.stop.is_set():
            log.info("duration %s elapsed; stopping", self.duration)
            self.stop.set()

    # -- entry point -------------------------------------------------------------

    async def run(self) -> RunSummary:
        if self.ingest:
            self.accounts = resolve_missing_dids(self.accounts, self.settings.bsky_api)
            if not self.accounts.active:
                raise RuntimeError("no active accounts with DIDs; check config/accounts.yaml")
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            with contextlib.suppress(NotImplementedError, RuntimeError):
                loop.add_signal_handler(sig, self.stop.set)
        tasks: list[asyncio.Task] = []
        if self.ingest:
            tasks.append(asyncio.create_task(self._poll_task() if self.use_poll else self._jetstream_task()))
        if self.market_enabled:
            tasks.append(asyncio.create_task(self._market_task()))
        if self.writer is not None:
            tasks.append(asyncio.create_task(self._writer_task()))
        if self.duration is not None:
            tasks.append(asyncio.create_task(self._timer_task()))
        log.info(
            "running: ingest=%s market=%s writer=%s accounts=%d duration=%s",
            "poll" if self.use_poll else "jetstream" if self.ingest else "off",
            self.monitor.source.name if self.market_enabled else "off",
            self.backend.name if self.backend else "off",
            len(self.accounts.active),
            self.duration or "until stopped",
        )
        try:
            await self.stop.wait()
        finally:
            self.stop.set()
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            if self.listener and self.listener.cursor_us:
                self.store.set_cursor(CURSOR_KEY, str(self.listener.cursor_us))
            if self.writer is not None and self.store.count_unprocessed(
                include_replies=self.settings.include_replies
            ):
                log.info("final writer pass over pending events")
                await self._writer_pass()
            self.summary.finished_at = utcnow()
            log.info(self.summary.line())
        return self.summary
