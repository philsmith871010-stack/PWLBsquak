"""Market data: the MarketSource slot, a mock implementation, and threshold detection.

Plugging in the real PWLBtoday feed
-----------------------------------
Implement a class with a `latest()` method returning a `MarketSnapshot` (yields in
percent) and point `SQUAWK_MARKET_SOURCE` at it as `package.module:ClassName`.
The class is constructed with no arguments. Optionally add
`history(start, end)` returning snapshots for historical annotation/replays.

    class PwlbTodayMarketSource:
        name = "pwlbtoday"
        def latest(self) -> MarketSnapshot: ...
"""

from __future__ import annotations

import datetime as dt
import importlib
import logging
import random
from collections import deque
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from typing import Protocol, runtime_checkable

from ..models import TENORS, Event, MarketSnapshot, to_iso, utcnow
from ..store import Store

log = logging.getLogger(__name__)

TENOR_LABELS = {
    "gilt_2y": "2-year gilt yield",
    "gilt_5y": "5-year gilt yield",
    "gilt_10y": "10-year gilt yield",
    "gilt_30y": "30-year gilt yield",
    "sonia": "SONIA",
}


@runtime_checkable
class MarketSource(Protocol):
    name: str

    def latest(self) -> MarketSnapshot: ...


class MockMarketSource:
    """Seeded random walk from realistic late-2026 levels. For demos, `jump_bp` adds a
    one-off move to `jump_tenor` on tick number `jump_at_tick`."""

    name = "mock"

    def __init__(
        self,
        *,
        seed: int = 7,
        step_bp: float = 0.4,
        jump_bp: float = 0.0,
        jump_tenor: str = "gilt_10y",
        jump_at_tick: int = 3,
        start: dict[str, float] | None = None,
        clock: callable | None = None,
    ):
        self.rng = random.Random(seed)
        self.step_bp = step_bp
        self.jump_bp = jump_bp
        self.jump_tenor = jump_tenor
        self.jump_at_tick = jump_at_tick
        self.clock = clock or utcnow
        self.tick = 0
        self.levels: dict[str, float] = {
            "gilt_2y": 3.92,
            "gilt_5y": 4.12,
            "gilt_10y": 4.58,
            "gilt_30y": 5.31,
            "sonia": 3.95,
            "ois_3m": 3.88,
            "ois_6m": 3.80,
            "ois_1y": 3.66,
            "ois_2y": 3.58,
        }
        if start:
            self.levels.update(start)

    def latest(self) -> MarketSnapshot:
        self.tick += 1
        # common factor + idiosyncratic noise so the curve moves together, in bp
        common = self.rng.gauss(0, self.step_bp)
        for key in self.levels:
            move = common + self.rng.gauss(0, self.step_bp / 2)
            if self.jump_bp and self.tick == self.jump_at_tick and key == self.jump_tenor:
                move += self.jump_bp
            self.levels[key] = round(self.levels[key] + move / 100.0, 4)
        return MarketSnapshot(
            ts=self.clock(),
            gilt_2y=self.levels["gilt_2y"],
            gilt_5y=self.levels["gilt_5y"],
            gilt_10y=self.levels["gilt_10y"],
            gilt_30y=self.levels["gilt_30y"],
            sonia=self.levels["sonia"],
            ois={k[4:]: v for k, v in self.levels.items() if k.startswith("ois_")},
            source=self.name,
        )

    def history(self, start: dt.datetime, end: dt.datetime, step: dt.timedelta) -> list[MarketSnapshot]:
        """Deterministic synthetic series (useful for replay/annotation demos)."""
        out: list[MarketSnapshot] = []
        t = start
        while t <= end:
            snap = self.latest().model_copy(update={"ts": t})
            out.append(snap)
            t += step
        return out


def load_market_source(spec: str, **kwargs) -> MarketSource:
    """'mock' -> MockMarketSource(**kwargs); 'pkg.mod:Class' -> Class()."""
    if spec in ("", "mock"):
        return MockMarketSource(**kwargs)
    module_name, _, class_name = spec.partition(":")
    if not class_name:
        raise ValueError("SQUAWK_MARKET_SOURCE must be 'mock' or 'package.module:ClassName'")
    module = importlib.import_module(module_name)
    source = getattr(module, class_name)()
    if not isinstance(source, MarketSource):
        raise TypeError(f"{spec} does not implement MarketSource.latest()")
    return source


@dataclass(frozen=True)
class ThresholdAlert:
    tenor: str
    from_ts: dt.datetime
    to_ts: dt.datetime
    from_value: float
    to_value: float
    move_bp: float
    window_minutes: float
    threshold_bp: float

    @property
    def direction(self) -> str:
        return "up" if self.move_bp > 0 else "down"

    def to_event(self) -> Event:
        label = TENOR_LABELS.get(self.tenor, self.tenor)
        minutes = round((self.to_ts - self.from_ts).total_seconds() / 60)
        text = (
            f"MARKET MOVE: {label} {self.direction} {abs(self.move_bp):.1f}bp in {minutes} minutes, "
            f"from {self.from_value:.2f}% to {self.to_value:.2f}% (threshold {self.threshold_bp:g}bp "
            f"within {self.window_minutes:g} minutes)."
        )
        return Event.create(
            source="market",
            author="market",
            ts=self.to_ts,
            text=text,
            uri=f"market://{self.tenor}/{to_iso(self.to_ts)}",
            raw={k: (to_iso(v) if isinstance(v, dt.datetime) else v) for k, v in asdict(self).items()},
        )


class ThresholdDetector:
    """Emits an alert when a tenor moves by at least `thresholds[tenor]` bp against any
    snapshot within the trailing window. One alert per tenor per cooldown period."""

    def __init__(
        self,
        thresholds: dict[str, float],
        *,
        window: dt.timedelta = dt.timedelta(minutes=15),
        cooldown: dt.timedelta = dt.timedelta(minutes=15),
    ):
        self.thresholds = {k: float(v) for k, v in thresholds.items()}
        self.window = window
        self.cooldown = cooldown
        self.history: deque[MarketSnapshot] = deque()
        self.last_alert: dict[str, dt.datetime] = {}

    def observe(self, snap: MarketSnapshot) -> list[ThresholdAlert]:
        while self.history and snap.ts - self.history[0].ts > self.window:
            self.history.popleft()
        alerts: list[ThresholdAlert] = []
        for tenor, threshold in self.thresholds.items():
            try:
                now_v = snap.value(tenor)
            except (KeyError, AttributeError):
                continue
            best: MarketSnapshot | None = None
            best_move = 0.0
            for past in self.history:
                try:
                    move = (now_v - past.value(tenor)) * 100.0
                except (KeyError, AttributeError):
                    continue
                if abs(move) > abs(best_move):
                    best, best_move = past, move
            if best is None or abs(best_move) < threshold:
                continue
            last = self.last_alert.get(tenor)
            if last is not None and snap.ts - last < self.cooldown:
                continue
            self.last_alert[tenor] = snap.ts
            alerts.append(
                ThresholdAlert(
                    tenor=tenor,
                    from_ts=best.ts,
                    to_ts=snap.ts,
                    from_value=round(best.value(tenor), 4),
                    to_value=round(now_v, 4),
                    move_bp=round(best_move, 2),
                    window_minutes=self.window.total_seconds() / 60,
                    threshold_bp=threshold,
                )
            )
        self.history.append(snap)
        return alerts


class MarketMonitor:
    """Ties a MarketSource, the detector and the store together: one `tick()` per poll."""

    def __init__(self, source: MarketSource, detector: ThresholdDetector, store: Store):
        self.source = source
        self.detector = detector
        self.store = store
        self.latest: MarketSnapshot | None = None

    def tick(self) -> list[Event]:
        snap = self.source.latest()
        self.latest = snap
        self.store.insert_market_snapshot(snap)
        events: list[Event] = []
        for alert in self.detector.observe(snap):
            event = alert.to_event()
            result = self.store.insert_event(event)
            if result.is_new:
                events.append(event)
                log.info("market threshold: %s", event.text)
        return events

    def seed(self, snapshots: Iterable[MarketSnapshot]) -> None:
        """Pre-load detector history (e.g. from stored snapshots after a restart)."""
        for snap in snapshots:
            self.detector.observe(snap)


__all__ = [
    "TENORS",
    "MarketMonitor",
    "MarketSource",
    "MockMarketSource",
    "ThresholdAlert",
    "ThresholdDetector",
    "load_market_source",
]
