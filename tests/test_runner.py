import asyncio
import datetime as dt
import json

import httpx

from pwlbsquawk.cli import build_parser, parse_duration, parse_when
from pwlbsquawk.config import Account, Accounts, Settings
from pwlbsquawk.eventmode import EventMode
from pwlbsquawk.recording import Recorder, load_recording, replay
from pwlbsquawk.runner import Runner
from pwlbsquawk.sources.bluesky_poll import BlueskyPoller
from pwlbsquawk.sources.market import MockMarketSource
from pwlbsquawk.writer.backends import MockBackend
from tests.conftest import make_event, make_snapshot
from tests.test_bluesky_sources import DID, _feed_transport, _item

NOW = dt.datetime(2026, 9, 28, 9, 30, tzinfo=dt.UTC)


def test_event_mode_triggers_and_expires():
    em = EventMode(dt.timedelta(minutes=30), ["bank rate", "mpc"])
    assert em.interval(300, 60, NOW) == 300
    assert em.consider(make_event("Weather is nice", rkey="1"), NOW) is False
    assert em.consider(make_event("Bank Rate held at 4%", rkey="2", tier=1), NOW) is True
    assert em.interval(300, 60, NOW) == 60
    assert em.interval(300, 60, NOW + dt.timedelta(minutes=31)) == 300
    # tier-3 wires do not trigger on keywords, market alerts always do
    assert (
        em.consider(make_event("Bank Rate chatter", rkey="3", tier=3), NOW + dt.timedelta(hours=1)) is False
    )
    market = make_event("MARKET MOVE: 10-year gilt yield up 6bp", rkey="m", source="market", author="market")
    assert em.consider(market, NOW + dt.timedelta(hours=1)) is True


def test_recorder_roundtrip(tmp_path):
    p = tmp_path / "rec.ndjson"
    rec = Recorder(p)
    rec.event(make_event("hello gilts", rkey="a", ts="2026-09-28T09:00:00Z"))
    rec.market(make_snapshot(NOW, 4.5))
    rec.event(make_event("earlier post", rkey="b", ts="2026-09-28T08:00:00Z"))
    events, snaps = load_recording(p)
    assert [e.text for e in events] == ["earlier post", "hello gilts"]  # sorted by ts
    assert snaps[0].gilt_10y == 4.5 and rec.count == 3


def test_replay_runs_writer_per_window_and_reports_cost(tmp_path):
    p = tmp_path / "day.ndjson"
    rec = Recorder(p)
    base = dt.datetime(2026, 9, 28, 9, 0, tzinfo=dt.UTC)
    rec.event(make_event("Bank of England holds Bank Rate at 4%", rkey="a", ts=base))
    rec.event(make_event("Nothing to see here, lunch", rkey="b", ts=base + dt.timedelta(minutes=7)))
    rec.event(make_event("UK CPI rises to 3.8%", rkey="c", ts=base + dt.timedelta(minutes=12)))
    for i in range(4):
        rec.market(make_snapshot(base + dt.timedelta(minutes=5 * i), 4.5 + (0.07 if i == 3 else 0)))
    settings = Settings(writer_backend="mock")
    out = tmp_path / "out"
    report = replay(
        settings, p, MockBackend(), out_dir=out, window=dt.timedelta(minutes=5), system_prompt="sys"
    )
    assert report.calls == 3 and report.windows >= 3
    assert report.squawks >= 2 and report.errors == 0
    assert report.usage.input_tokens > 0 and report.cost_usd >= 0
    doc = json.loads((out / "squawks.json").read_text())
    assert doc["count"] == report.squawks
    rep = json.loads((out / "report.json").read_text())
    assert rep["calls"] == 3 and len(rep["passes"]) == 3
    # the market jump on the last snapshot produced a threshold alert -> market squawk in the final window
    assert any("Gilt move" in h for p_ in rep["passes"] for h in p_["squawks"])


def test_runner_poll_mode_end_to_end(store, tmp_path):
    now = dt.datetime.now(dt.UTC)
    t = lambda m: (now - dt.timedelta(minutes=m)).isoformat().replace("+00:00", "Z")  # noqa: E731
    pages = {
        None: {
            "feed": [
                _item("p1", "Bank of England holds Bank Rate at 4%", t(1)),
                _item("p2", "Nice walk today", t(2)),
            ]
        }
    }
    client = httpx.Client(transport=_feed_transport(pages))
    accounts = Accounts(active=[Account(handle="bruceandy.bsky.social", did=DID, tier=1)])
    settings = Settings(
        output_dir=tmp_path,
        writer_backend="mock",
        write_interval=1,
        event_mode_interval=1,
        market_poll_interval=1,
    )
    runner = Runner(
        settings,
        store,
        accounts,
        backend=MockBackend(),
        use_poll=True,
        duration=dt.timedelta(seconds=3),
        recorder=Recorder(tmp_path / "rec.ndjson"),
        market_source=MockMarketSource(jump_bp=9.0, jump_at_tick=2),
        poll_interval=1,
        demo_market=True,
    )
    # inject the fake HTTP client into the poller the runner builds
    orig_init = BlueskyPoller.__init__

    def patched(self, *a, **kw):
        kw["client"] = client
        orig_init(self, *a, **kw)

    BlueskyPoller.__init__ = patched
    try:
        summary = asyncio.run(runner.run())
    finally:
        BlueskyPoller.__init__ = orig_init
    assert summary.new_events == 2 and summary.errors == []
    assert summary.market_alerts >= 1
    assert summary.squawks >= 1 and summary.writer_passes >= 1
    assert store.count_unprocessed() == 0
    doc = json.loads((tmp_path / "squawks.json").read_text())
    assert doc["count"] >= 1
    events, snaps = load_recording(tmp_path / "rec.ndjson")
    assert len(events) >= 3 and len(snaps) >= 2  # 2 posts + market alert(s), 2+ ticks


def test_duration_and_when_parsing():
    assert parse_duration("10m") == dt.timedelta(minutes=10)
    assert parse_duration("1.5h") == dt.timedelta(hours=1.5)
    assert parse_duration("2d") == dt.timedelta(days=2)
    assert parse_duration("90") == dt.timedelta(seconds=90)
    assert parse_when("2026-09-01") == dt.datetime(2026, 9, 1, tzinfo=dt.UTC)
    assert parse_when("2026-09-01T10:00:00Z").hour == 10
    assert (dt.datetime.now(dt.UTC) - parse_when("30d")).days in (29, 30)


def test_cli_global_options_before_or_after_subcommand():
    p = build_parser()
    a = p.parse_args(["--db", "x.sqlite", "stats"])
    b = p.parse_args(["stats", "--db", "x.sqlite"])
    assert a.db == b.db == "x.sqlite"
    c = p.parse_args(["run", "--duration", "10m", "--backend", "mock", "--market-jump", "8"])
    assert c.duration == dt.timedelta(minutes=10) and c.backend == "mock" and c.market_jump == 8.0
    d = p.parse_args(["replay", "day.ndjson", "--window", "1w"])
    assert d.window == dt.timedelta(weeks=1)


def test_writer_wait_shortens_when_event_mode_starts(store, tmp_path):
    """A market alert 6s into a 300s wait must bring the next pass forward to the 60s cadence."""
    settings = Settings(output_dir=tmp_path, writer_backend="mock", write_interval=300, event_mode_interval=1)
    passes = []

    class Spy(MockBackend):
        def complete(self, system, user):
            passes.append(dt.datetime.now(dt.UTC))
            return super().complete(system, user)

    runner = Runner(
        settings,
        store,
        Accounts(active=[Account(handle="x", did=DID, tier=1)]),
        backend=Spy(),
        duration=dt.timedelta(seconds=4),
        ingest=False,
        market=False,
    )
    runner.writer_slice_s = 0.2
    store.insert_event(make_event("Bank Rate held", rkey="k", ts=dt.datetime.now(dt.UTC)))

    async def go():
        task = asyncio.create_task(runner.run())
        await asyncio.sleep(0.5)
        runner.event_mode.trigger("test", dt.datetime.now(dt.UTC))
        return await task

    summary = asyncio.run(go())
    assert summary.writer_passes >= 1 and len(passes) >= 1
    assert passes[0] - summary.started_at < dt.timedelta(seconds=3)
