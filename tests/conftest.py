import datetime as dt

import pytest

from pwlbsquawk.models import Event, MarketSnapshot
from pwlbsquawk.store import Store


@pytest.fixture
def store():
    with Store(":memory:") as s:
        yield s


def make_event(
    text: str,
    *,
    author="bruceandy.bsky.social",
    rkey="abc",
    did="did:plc:x",
    ts="2026-09-28T09:00:00Z",
    is_reply=False,
    tier=1,
    source="bluesky",
) -> Event:
    return Event.create(
        source=source,
        author=author,
        author_did=did,
        ts=ts,
        text=text,
        uri=f"at://{did}/app.bsky.feed.post/{rkey}",
        raw={"text": text},
        is_reply=is_reply,
        tier=tier,
    )


def make_snapshot(ts: dt.datetime, gilt_10y: float = 4.50, **kw) -> MarketSnapshot:
    return MarketSnapshot(
        ts=ts,
        gilt_2y=kw.get("gilt_2y", 3.90),
        gilt_5y=kw.get("gilt_5y", 4.10),
        gilt_10y=gilt_10y,
        gilt_30y=kw.get("gilt_30y", 5.20),
        sonia=kw.get("sonia", 3.95),
        ois={"3m": 3.90, "1y": 3.70},
        source="test",
    )
