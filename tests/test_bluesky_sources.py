import asyncio
import datetime as dt
import json

import httpx

from pwlbsquawk.config import Account, Accounts
from pwlbsquawk.models import parse_ts
from pwlbsquawk.sources import bluesky, jetstream
from pwlbsquawk.sources.bluesky_poll import BlueskyPoller

DID = "did:plc:zpgmsa7eo63jbc57nd5gbr2c"


def _record(text, *, reply=False, external=None, created="2026-09-28T09:00:00.000Z"):
    rec = {"$type": "app.bsky.feed.post", "text": text, "createdAt": created, "langs": ["en"]}
    if reply:
        rec["reply"] = {
            "parent": {"uri": "at://x/app.bsky.feed.post/1", "cid": "c"},
            "root": {"uri": "at://x/app.bsky.feed.post/1", "cid": "c"},
        }
    if external:
        rec["embed"] = {"$type": "app.bsky.embed.external", "external": external}
    return rec


def test_record_text_appends_link_card_when_text_is_empty():
    rec = _record(
        "",
        external={
            "uri": "https://cityam.com/x",
            "title": "Gilt yields jump",
            "description": "Yields rose sharply on Tuesday.",
        },
    )
    assert bluesky.record_text(rec) == "Gilt yields jump\nYields rose sharply on Tuesday."


def test_record_text_does_not_repeat_title_already_in_text():
    rec = _record(
        "Gilt yields jump - my take", external={"uri": "u", "title": "Gilt yields jump", "description": ""}
    )
    assert bluesky.record_text(rec) == "Gilt yields jump - my take"


def test_jetstream_message_to_event():
    msg = {
        "did": DID,
        "time_us": 1790626148996282,
        "kind": "commit",
        "commit": {
            "rev": "r",
            "operation": "create",
            "collection": "app.bsky.feed.post",
            "rkey": "3mwm4cxsgkc2y",
            "record": _record("CPI 3.8%"),
            "cid": "bafy",
        },
    }
    ev = bluesky.jetstream_message_to_event(msg, handles={DID: "bruceandy.bsky.social"}, tiers={DID: 1})
    assert ev is not None
    assert ev.uri == f"at://{DID}/app.bsky.feed.post/3mwm4cxsgkc2y"
    assert ev.author == "bruceandy.bsky.social" and ev.tier == 1 and ev.is_reply is False
    assert ev.ts == dt.datetime(2026, 9, 28, 9, 0, tzinfo=dt.UTC)
    assert ev.web_url == "https://bsky.app/profile/bruceandy.bsky.social/post/3mwm4cxsgkc2y"
    assert ev.raw["time_us"] == 1790626148996282


def test_jetstream_ignores_deletes_likes_and_identity_events():
    base = {"did": DID, "time_us": 1, "kind": "commit"}
    assert (
        bluesky.jetstream_message_to_event(
            {**base, "commit": {"operation": "delete", "collection": "app.bsky.feed.post", "rkey": "x"}}
        )
        is None
    )
    assert (
        bluesky.jetstream_message_to_event(
            {
                **base,
                "commit": {
                    "operation": "create",
                    "collection": "app.bsky.feed.like",
                    "rkey": "x",
                    "record": {},
                },
            }
        )
        is None
    )
    assert (
        bluesky.jetstream_message_to_event({"did": DID, "time_us": 1, "kind": "identity", "identity": {}})
        is None
    )


def test_reply_flag_from_record():
    ev = bluesky.record_to_event(did=DID, rkey="k", record=_record("a reply", reply=True))
    assert ev.is_reply is True


def test_build_url_repeats_wanted_dids_and_cursor():
    url = jetstream.build_url("wss://js.example/subscribe", ["did:a", "did:b"], 123)
    assert (
        url
        == "wss://js.example/subscribe?wantedCollections=app.bsky.feed.post&wantedDids=did%3Aa&wantedDids=did%3Ab&cursor=123"
    )


def test_listener_handle_message_emits_and_tracks_cursor():
    got = []
    cursors = []
    listener = jetstream.JetstreamListener(
        "wss://x", [DID], got.append, on_cursor=cursors.append, cursor_every=1
    )
    msg = {
        "did": DID,
        "time_us": 42,
        "kind": "commit",
        "commit": {
            "operation": "create",
            "collection": "app.bsky.feed.post",
            "rkey": "k",
            "record": _record("hi there"),
        },
    }
    asyncio.run(listener.handle_message(json.dumps(msg)))
    assert len(got) == 1 and got[0].text == "hi there"
    assert listener.cursor_us == 42 and cursors == [42]


def _feed_transport(pages: dict[str | None, dict]):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/xrpc/app.bsky.feed.getAuthorFeed"
        cursor = request.url.params.get("cursor")
        return httpx.Response(200, json=pages[cursor])

    return httpx.MockTransport(handler)


def _item(rkey, text, indexed, reason=None, did=DID, handle="bruceandy.bsky.social"):
    item = {
        "post": {
            "uri": f"at://{did}/app.bsky.feed.post/{rkey}",
            "cid": "c" + rkey,
            "author": {"did": did, "handle": handle},
            "record": _record(text, created=indexed),
            "indexedAt": indexed,
        }
    }
    if reason:
        item["reason"] = reason
    return item


def test_poller_tracks_last_seen_and_skips_reposts(store):
    accounts = Accounts(active=[Account(handle="bruceandy.bsky.social", did=DID, tier=1)])
    now = dt.datetime.now(dt.UTC)
    t = lambda m: (now - dt.timedelta(minutes=m)).isoformat().replace("+00:00", "Z")  # noqa: E731
    pages = {
        None: {
            "feed": [
                _item("p3", "newest post about gilts", t(1)),
                _item("rp", "a repost", t(2), reason={"$type": "app.bsky.feed.defs#reasonRepost"}),
                _item("p2", "second post on Bank Rate", t(3)),
            ],
            "cursor": "c1",
        },
        "c1": {"feed": [_item("p1", "old post from yesterday", t(60 * 30))]},
    }
    client = httpx.Client(transport=_feed_transport(pages))
    poller = BlueskyPoller("https://api.bsky.app", accounts, store, backfill_hours=6, client=client)
    stats = poller.poll_once()
    assert stats.inserted == 2 and stats.skipped == 1
    assert store.count_events() == 2
    assert parse_ts(store.get_cursor(f"poll:{DID}")) == parse_ts(t(1))
    # second poll: nothing new
    stats2 = poller.poll_once()
    assert stats2.inserted == 0 and stats2.already_seen >= 1
    assert store.count_events() == 2


def test_poller_backfill_since_walks_further_back(store):
    accounts = Accounts(active=[Account(handle="bruceandy.bsky.social", did=DID, tier=1)])
    now = dt.datetime.now(dt.UTC)
    t = lambda d: (now - dt.timedelta(days=d)).isoformat().replace("+00:00", "Z")  # noqa: E731
    pages = {
        None: {"feed": [_item("a", "post today about the OBR forecast", t(0))], "cursor": "c1"},
        "c1": {"feed": [_item("b", "post from ten days ago on gilt supply", t(10))], "cursor": "c2"},
        "c2": {"feed": [_item("c", "post from forty days ago about CPI", t(40))]},
    }
    client = httpx.Client(transport=_feed_transport(pages))
    poller = BlueskyPoller("https://api.bsky.app", accounts, store, client=client)
    stats = poller.poll_once(since=now - dt.timedelta(days=30))
    assert stats.inserted == 2
    assert store.count_events() == 2
