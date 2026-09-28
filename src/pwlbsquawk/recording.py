"""`record`: append raw events + market snapshots to NDJSON. `replay`: re-run the writer over a recording."""

from __future__ import annotations

import datetime as dt
import json
import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import Settings
from .models import Event, MarketSnapshot, parse_ts, to_iso
from .sources.market import MockMarketSource, ThresholdDetector
from .store import Store
from .writer.backends import WriterBackend
from .writer.cost import Usage, estimate_cost
from .writer.writer import SquawkWriter, WriterPass

log = logging.getLogger(__name__)


class Recorder:
    """Append-only NDJSON: {"kind": "event", ...} and {"kind": "market", ...} lines."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.count = 0

    def _write(self, obj: dict[str, Any]) -> None:
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(obj, ensure_ascii=False) + "\n")
        self.count += 1

    def event(self, event: Event) -> None:
        self._write({"kind": "event", **event.model_dump(mode="json")})

    def market(self, snap: MarketSnapshot) -> None:
        self._write({"kind": "market", **snap.model_dump(mode="json")})


def load_recording(path: str | Path) -> tuple[list[Event], list[MarketSnapshot]]:
    events: list[Event] = []
    snaps: list[MarketSnapshot] = []
    with Path(path).open(encoding="utf-8") as fh:
        for n, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            kind = obj.pop("kind", "event")
            try:
                if kind == "market":
                    snaps.append(MarketSnapshot.model_validate(obj))
                else:
                    events.append(Event.model_validate(obj))
            except Exception as exc:  # noqa: BLE001 - skip a bad line, keep the rest
                log.warning("%s line %d skipped: %s", path, n, exc)
    events.sort(key=lambda e: e.ts)
    snaps.sort(key=lambda s: s.ts)
    return events, snaps


def export_recording(store: Store, path: str | Path, *, since: dt.datetime | None = None) -> int:
    """Dump the event log (and stored market snapshots) into a recording file."""
    rec = Recorder(path)
    for e in store.all_events(since=since):
        rec.event(e)
    row_iter = store._execute("SELECT ts FROM market_snapshots ORDER BY ts").fetchall()
    for r in row_iter:
        snap = store.latest_market_snapshot(at_or_before=parse_ts(r["ts"]))
        if snap and (since is None or snap.ts >= since):
            rec.market(snap)
    return rec.count


@dataclass
class ReplayReport:
    recording: str
    window: dt.timedelta
    windows: int = 0
    calls: int = 0
    events: int = 0
    squawks: int = 0
    dropped: int = 0
    errors: int = 0
    usage: Usage = field(default_factory=Usage)
    cost_usd: float = 0.0
    model: str = ""
    passes: list[dict[str, Any]] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"replay {self.recording}: windows={self.windows} calls={self.calls} events={self.events} "
            f"squawks={self.squawks} dropped={self.dropped} errors={self.errors} "
            f"tokens(in={self.usage.input_tokens} out={self.usage.output_tokens} "
            f"cache_read={self.usage.cache_read_tokens} cache_write={self.usage.cache_write_tokens}) "
            f"est_cost=${self.cost_usd:.4f} model={self.model}"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "recording": self.recording,
            "window_seconds": self.window.total_seconds(),
            "windows": self.windows,
            "calls": self.calls,
            "events": self.events,
            "squawks": self.squawks,
            "dropped": self.dropped,
            "errors": self.errors,
            "usage": self.usage.__dict__,
            "cost_usd": self.cost_usd,
            "model": self.model,
            "passes": self.passes,
        }


def replay(
    settings: Settings,
    recording: str | Path,
    backend: WriterBackend,
    *,
    out_dir: str | Path,
    window: dt.timedelta = dt.timedelta(minutes=5),
    start: dt.datetime | None = None,
    end: dt.datetime | None = None,
    limit_windows: int | None = None,
    system_prompt: str | None = None,
    fresh: bool = True,
) -> ReplayReport:
    """Feed a recording through a fresh store in simulated windows and run the writer at each tick."""
    out = Path(out_dir)
    if fresh and out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)
    events, snaps = load_recording(recording)
    if start:
        events = [e for e in events if e.ts >= start]
        snaps = [s for s in snaps if s.ts >= start]
    if end:
        events = [e for e in events if e.ts <= end]
        snaps = [s for s in snaps if s.ts <= end]
    report = ReplayReport(recording=str(recording), window=window, model=backend.model)
    if not events:
        log.warning("recording has no events in range")
        return report
    if not snaps:
        # a polled/backfilled recording has no market ticks: synthesise a mock series so the
        # writer always sees a snapshot (clearly labelled source="mock" in the prompt)
        log.info("recording has no market snapshots; generating a mock series at the window cadence")
        snaps = MockMarketSource(seed=7).history(
            events[0].ts, events[-1].ts, max(window, dt.timedelta(minutes=5))
        )
    local = settings.model_copy(update={"output_dir": out, "db_path": out / "replay.sqlite"})
    store = Store(local.db_path)
    detector = ThresholdDetector(
        local.market_thresholds,
        window=dt.timedelta(minutes=local.market_window_minutes),
        cooldown=dt.timedelta(minutes=local.market_cooldown_minutes),
    )
    writer = SquawkWriter(
        local, store, backend, mode="replay", system_prompt=system_prompt, stale_after=dt.timedelta(days=3650)
    )
    t = _floor(events[0].ts, window) + window
    last = max(events[-1].ts, snaps[-1].ts if snaps else events[-1].ts)
    ei = si = 0
    latest_snap: MarketSnapshot | None = None
    while t <= last + window:
        while si < len(snaps) and snaps[si].ts <= t:
            snap = snaps[si]
            store.insert_market_snapshot(snap)
            latest_snap = snap
            for alert in detector.observe(snap):
                store.insert_event(alert.to_event())
            si += 1
        while ei < len(events) and events[ei].ts <= t:
            store.insert_event(
                events[ei],
                dedupe_window_hours=local.dedupe_window_hours,
                dedupe_threshold=local.dedupe_threshold,
            )
            ei += 1
        result: WriterPass = writer.run_once(now=t, snapshot=latest_snap)
        report.windows += 1
        if not result.skipped:
            report.calls += 1
            report.events += len(result.events)
            report.squawks += len(result.squawks)
            report.dropped += result.dropped
            report.usage = report.usage + result.usage
            report.cost_usd += result.cost_usd
            if result.error:
                report.errors += 1
            report.passes.append(
                {
                    "at": to_iso(t),
                    "events": len(result.events),
                    "squawks": [s.headline for s in result.squawks],
                    "error": result.error,
                    "cost_usd": result.cost_usd,
                }
            )
            log.info(
                "replay %s: events=%d squawks=%d cost=$%.4f%s",
                to_iso(t),
                len(result.events),
                len(result.squawks),
                result.cost_usd,
                f" error={result.error}" if result.error else "",
            )
        if limit_windows and report.calls >= limit_windows:
            break
        t += window
    report.cost_usd = round(report.cost_usd, 6)
    (out / "report.json").write_text(json.dumps(report.to_dict(), indent=1), encoding="utf-8")
    store.close()
    return report


def _floor(ts: dt.datetime, window: dt.timedelta) -> dt.datetime:
    seconds = int(window.total_seconds())
    epoch = int(ts.timestamp())
    return dt.datetime.fromtimestamp(epoch - epoch % seconds, tz=dt.UTC)


__all__ = ["Recorder", "ReplayReport", "estimate_cost", "export_recording", "load_recording", "replay"]
