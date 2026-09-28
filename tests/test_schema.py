import json

import pytest
from pydantic import ValidationError

from pwlbsquawk.models import Category, Squawk, SquawkDraft, WriterOutput


def test_not_material_minimal_payload():
    out = WriterOutput.model_validate_json('{"material": false}')
    assert out.material is False and out.squawks == []


def test_material_with_valid_squawk():
    payload = {
        "material": True,
        "squawks": [
            {
                "ts": "2026-09-28T09:00:00Z",
                "headline": "Bank Rate held at 4%; MPC signals gradual cuts",
                "la_angle": "PWLB certainty rates track gilts, so a steady Bank Rate keeps borrowing costs range-bound for now.",
                "category": "boe",
                "importance": 3,
                "source_event_ids": ["abc123"],
            }
        ],
    }
    out = WriterOutput.model_validate(payload)
    assert out.material and out.squawks[0].category is Category.boe


@pytest.mark.parametrize(
    "bad",
    [
        {
            "headline": "x" * 141,
            "la_angle": "y",
            "category": "boe",
            "importance": 2,
            "source_event_ids": ["a"],
        },
        {
            "headline": "ok",
            "la_angle": "y",
            "category": "equities",
            "importance": 2,
            "source_event_ids": ["a"],
        },
        {"headline": "ok", "la_angle": "y", "category": "boe", "importance": 4, "source_event_ids": ["a"]},
        {"headline": "ok", "la_angle": "y", "category": "boe", "importance": 0, "source_event_ids": ["a"]},
        {"headline": "ok", "la_angle": "y", "category": "boe", "importance": 2, "source_event_ids": []},
        {"headline": "", "la_angle": "y", "category": "boe", "importance": 2, "source_event_ids": ["a"]},
        {
            "headline": "ok",
            "la_angle": "y",
            "category": "boe",
            "importance": 2,
            "source_event_ids": ["a"],
            "extra": 1,
        },
    ],
    ids=[
        "headline>140",
        "bad-category",
        "importance>3",
        "importance<1",
        "no-sources",
        "empty-headline",
        "extra-field",
    ],
)
def test_invalid_squawk_rejected(bad):
    with pytest.raises(ValidationError):
        SquawkDraft.model_validate(bad)


def test_headline_exactly_140_ok():
    d = SquawkDraft(headline="h" * 140, la_angle="a", category="data", importance=1, source_event_ids=["a"])
    assert len(d.headline) == 140


def test_public_dict_is_json_serialisable_with_z_timestamps():
    s = Squawk(
        id="sq1",
        ts="2026-09-28T09:00:00+00:00",
        headline="h",
        la_angle="a",
        category="fiscal",
        importance=2,
        source_event_ids=["e1"],
        sources=[{"author": "x", "uri": "at://did/app.bsky.feed.post/1", "url": None}],
        model="m",
        run_id="r",
        created_at="2026-09-28T09:05:00Z",
    )
    d = s.to_public_dict()
    assert d["ts"] == "2026-09-28T09:00:00Z" and d["created_at"] == "2026-09-28T09:05:00Z"
    assert d["category"] == "fiscal"
    json.dumps(d)
