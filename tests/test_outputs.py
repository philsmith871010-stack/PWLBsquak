import datetime as dt
import json

from pwlbsquawk.config import Settings
from pwlbsquawk.models import Squawk
from pwlbsquawk.outputs import append_ndjson, read_ndjson, write_outputs, write_squawks_json


def _sq(i, base):
    return Squawk(
        id=f"s{i:03d}",
        ts=base + dt.timedelta(minutes=i),
        headline=f"headline {i}",
        la_angle="a",
        category="fiscal",
        importance=1,
        source_event_ids=["e"],
        model="m",
        run_id="r",
        created_at=base + dt.timedelta(minutes=i),
    )


def test_squawks_json_keeps_latest_200_newest_first(store, tmp_path):
    base = dt.datetime(2026, 9, 1, tzinfo=dt.UTC)
    squawks = [_sq(i, base) for i in range(250)]
    store.insert_squawks(squawks)
    settings = Settings(output_dir=tmp_path)
    write_outputs(store, settings, squawks[-5:], generated_at=base)
    doc = json.loads(settings.squawks_json_path.read_text())
    assert doc["count"] == 200 and len(doc["squawks"]) == 200
    assert doc["squawks"][0]["id"] == "s249" and doc["squawks"][-1]["id"] == "s050"
    assert doc["generated_at"] == "2026-09-01T00:00:00Z"
    assert len(read_ndjson(settings.squawks_ndjson_path)) == 5


def test_ndjson_is_append_only(tmp_path):
    base = dt.datetime(2026, 9, 1, tzinfo=dt.UTC)
    p = tmp_path / "h.ndjson"
    append_ndjson(p, [_sq(1, base)])
    append_ndjson(p, [_sq(2, base)])
    rows = read_ndjson(p)
    assert [r.id for r in rows] == ["s001", "s002"]


def test_json_write_is_atomic_no_tmp_left(tmp_path):
    p = tmp_path / "out" / "squawks.json"
    write_squawks_json(p, [])
    assert p.exists() and not p.with_suffix(".json.tmp").exists()
    assert json.loads(p.read_text())["squawks"] == []
