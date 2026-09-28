import datetime as dt

import pytest

from pwlbsquawk.sources.market import MarketMonitor, MockMarketSource, ThresholdDetector, load_market_source
from tests.conftest import make_snapshot

T0 = dt.datetime(2026, 9, 28, 9, 0, tzinfo=dt.UTC)


def _det(**kw):
    return ThresholdDetector(
        {"gilt_10y": 5.0}, window=dt.timedelta(minutes=15), cooldown=dt.timedelta(minutes=15), **kw
    )


def test_no_alert_below_threshold():
    det = _det()
    assert det.observe(make_snapshot(T0, 4.500)) == []
    assert det.observe(make_snapshot(T0 + dt.timedelta(minutes=5), 4.540)) == []  # +4bp


def test_alert_when_move_exceeds_threshold_within_window():
    det = _det()
    det.observe(make_snapshot(T0, 4.500))
    det.observe(make_snapshot(T0 + dt.timedelta(minutes=5), 4.530))
    alerts = det.observe(make_snapshot(T0 + dt.timedelta(minutes=10), 4.560))  # +6bp vs T0
    assert len(alerts) == 1
    a = alerts[0]
    assert (
        a.tenor == "gilt_10y" and a.move_bp == pytest.approx(6.0) and a.from_ts == T0 and a.direction == "up"
    )
    ev = a.to_event()
    assert ev.source == "market" and "10-year gilt yield up 6.0bp in 10 minutes" in ev.text
    assert ev.uri == "market://gilt_10y/2026-09-28T09:10:00Z"


def test_downward_move_and_cooldown():
    det = _det()
    det.observe(make_snapshot(T0, 4.500))
    assert len(det.observe(make_snapshot(T0 + dt.timedelta(minutes=3), 4.440))) == 1  # -6bp
    # still >5bp vs T0 two minutes later, but inside the cooldown
    assert det.observe(make_snapshot(T0 + dt.timedelta(minutes=5), 4.430)) == []
    # after the cooldown a fresh move within the new window alerts again
    det.observe(make_snapshot(T0 + dt.timedelta(minutes=20), 4.430))
    assert len(det.observe(make_snapshot(T0 + dt.timedelta(minutes=25), 4.370))) == 1


def test_slow_drift_outside_window_does_not_alert():
    det = _det()
    for i in range(6):  # +2bp every 10 minutes: 10bp over an hour, but never >5bp within 15 minutes
        det.observe(make_snapshot(T0 + dt.timedelta(minutes=10 * i), 4.500 + 0.02 * i))
    assert det.last_alert == {}


def test_monitor_stores_snapshots_and_emits_events(store):
    clock_t = [T0]

    def clock():
        clock_t[0] += dt.timedelta(minutes=1)
        return clock_t[0]

    src = MockMarketSource(
        seed=1, step_bp=0.0, jump_bp=8.0, jump_tenor="gilt_10y", jump_at_tick=3, clock=clock
    )
    monitor = MarketMonitor(src, _det(), store)
    events = []
    for _ in range(4):
        events += monitor.tick()
    assert len(events) == 1 and events[0].source == "market"
    assert store.latest_market_snapshot() is not None
    assert store.count_events() == 1 and store.unprocessed_events()[0].id == events[0].id


def test_load_market_source_mock_and_dotted_path():
    assert isinstance(load_market_source("mock"), MockMarketSource)
    src = load_market_source("pwlbsquawk.sources.market:MockMarketSource")
    assert src.latest().gilt_10y > 0
    with pytest.raises(ValueError):
        load_market_source("nonsense")
