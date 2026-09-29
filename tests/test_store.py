import datetime as dt

from pwlbsquawk.models import RunRecord, Squawk, utcnow
from tests.conftest import make_event, make_snapshot


def test_events_roundtrip_and_processing(store):
    e = make_event("UK GDP grew 0.3% in Q2", rkey="g1")
    store.insert_event(e)
    got = store.events_by_ids([e.id])[e.id]
    assert got.text == e.text and got.uri == e.uri and got.raw == {"text": e.text}
    assert store.count_unprocessed() == 1
    store.mark_processed([e.id])
    assert store.count_unprocessed() == 0
    assert store.unprocessed_events() == []


def test_replies_hidden_by_default(store):
    store.insert_event(make_event("a reply here about gilts and more", rkey="r", is_reply=True))
    assert store.unprocessed_events() == []
    assert len(store.unprocessed_events(include_replies=True)) == 1


def test_squawks_newest_first_and_limit(store):
    base = dt.datetime(2026, 9, 28, 9, 0, tzinfo=dt.UTC)
    for i in range(5):
        store.insert_squawks(
            [
                Squawk(
                    id=f"s{i}",
                    ts=base + dt.timedelta(minutes=i),
                    headline=f"h{i}",
                    detail="a",
                    category="data",
                    importance=1,
                    source_event_ids=["e"],
                    model="m",
                    run_id="r",
                    created_at=base + dt.timedelta(minutes=i),
                )
            ]
        )
    latest = store.latest_squawks(limit=3)
    assert [s.id for s in latest] == ["s4", "s3", "s2"]
    assert store.recent_headlines(2) == ["h4", "h3"]


def test_cursors_and_runs(store):
    assert store.get_cursor("jetstream") is None
    store.set_cursor("jetstream", "123")
    store.set_cursor("jetstream", "456")
    assert store.get_cursor("jetstream") == "456"
    now = utcnow()
    store.insert_run(
        RunRecord(
            id="run1",
            started_at=now,
            finished_at=now,
            mode="write",
            backend="mock",
            model="m",
            n_events=3,
            n_squawks=1,
            material=True,
            input_tokens=100,
            output_tokens=50,
            cost_usd=0.001,
        )
    )
    totals = store.run_totals()
    assert totals["runs"] == 1 and totals["input_tokens"] == 100 and totals["n_squawks"] == 1


def test_market_snapshots(store):
    t0 = dt.datetime(2026, 9, 28, 9, 0, tzinfo=dt.UTC)
    store.insert_market_snapshot(make_snapshot(t0, 4.50))
    store.insert_market_snapshot(make_snapshot(t0 + dt.timedelta(minutes=5), 4.56))
    latest = store.latest_market_snapshot()
    assert latest and latest.gilt_10y == 4.56 and latest.ois == {"3m": 3.90, "1y": 3.70}
    earlier = store.latest_market_snapshot(at_or_before=t0 + dt.timedelta(minutes=2))
    assert earlier and earlier.gilt_10y == 4.50


def test_import_squawks_from_ndjson(store, tmp_path):
    from pwlbsquawk.outputs import append_ndjson

    base = dt.datetime(2026, 9, 28, 9, 0, tzinfo=dt.UTC)
    p = tmp_path / "h.ndjson"
    sq = [
        Squawk(
            id=f"s{i}",
            ts=base,
            headline=f"h{i}",
            detail="a",
            category="boe",
            importance=1,
            source_event_ids=["e"],
            model="m",
            run_id="r",
            created_at=base,
        )
        for i in range(3)
    ]
    append_ndjson(p, sq)
    assert store.count_squawks() == 0
    assert store.import_squawks_ndjson(p) == 3
    assert store.count_squawks() == 3
    assert store.import_squawks_ndjson(p) == 3 and store.count_squawks() == 3  # idempotent
