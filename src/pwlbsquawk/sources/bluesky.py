"""Convert Bluesky post records into Events. Shared by the Jetstream and polling sources."""

from __future__ import annotations

from typing import Any

from ..models import Event

POST_COLLECTION = "app.bsky.feed.post"
USER_AGENT = "pwlbsquawk/0.1 (prototype squawk feed; contact via repository)"


def post_uri(did: str, rkey: str) -> str:
    return f"at://{did}/{POST_COLLECTION}/{rkey}"


def split_post_uri(uri: str) -> tuple[str, str]:
    """at://did/app.bsky.feed.post/rkey -> (did, rkey)."""
    rest = uri.removeprefix("at://")
    did, _, tail = rest.partition("/")
    _, _, rkey = tail.rpartition("/")
    return did, rkey


def _external_embed(record: dict[str, Any]) -> dict[str, Any] | None:
    embed = record.get("embed") or {}
    kind = embed.get("$type", "")
    if kind == "app.bsky.embed.external":
        return embed.get("external") or None
    if kind == "app.bsky.embed.recordWithMedia":
        media = embed.get("media") or {}
        if media.get("$type") == "app.bsky.embed.external":
            return media.get("external") or None
    return None


def record_text(record: dict[str, Any]) -> str:
    """Post text plus the link-card title/description (wire accounts often post only a card)."""
    parts = [(record.get("text") or "").strip()]
    ext = _external_embed(record)
    if ext:
        title = (ext.get("title") or "").strip()
        desc = (ext.get("description") or "").strip()
        if title and title.lower() not in parts[0].lower():
            parts.append(title)
        if desc and desc.lower() not in parts[0].lower() and desc != title:
            parts.append(desc)
    return "\n".join(p for p in parts if p)


def record_to_event(
    *,
    did: str,
    rkey: str,
    record: dict[str, Any],
    handle: str | None = None,
    tier: int | None = None,
    extra_raw: dict[str, Any] | None = None,
) -> Event:
    raw = {"did": did, "rkey": rkey, "record": record}
    if extra_raw:
        raw.update(extra_raw)
    return Event.create(
        source="bluesky",
        author=handle or did,
        author_did=did,
        ts=record.get("createdAt") or "1970-01-01T00:00:00Z",
        text=record_text(record),
        uri=post_uri(did, rkey),
        raw=raw,
        is_reply="reply" in record,
        tier=tier,
    )


def jetstream_message_to_event(
    msg: dict[str, Any], *, handles: dict[str, str] | None = None, tiers: dict[str, int] | None = None
) -> Event | None:
    """Jetstream `commit` message -> Event, or None for anything that is not a new post."""
    if msg.get("kind") != "commit":
        return None
    commit = msg.get("commit") or {}
    if commit.get("operation") != "create" or commit.get("collection") != POST_COLLECTION:
        return None
    did = msg["did"]
    return record_to_event(
        did=did,
        rkey=commit["rkey"],
        record=commit.get("record") or {},
        handle=(handles or {}).get(did),
        tier=(tiers or {}).get(did),
        extra_raw={"time_us": msg.get("time_us"), "cid": commit.get("cid")},
    )


def feed_item_to_event(item: dict[str, Any], *, tier: int | None = None) -> Event | None:
    """app.bsky.feed.getAuthorFeed item -> Event; reposts (items with `reason`) are skipped."""
    if item.get("reason"):
        return None
    post = item.get("post") or {}
    author = post.get("author") or {}
    did, rkey = split_post_uri(post["uri"])
    return record_to_event(
        did=did,
        rkey=rkey,
        record=post.get("record") or {},
        handle=author.get("handle"),
        tier=tier,
        extra_raw={"cid": post.get("cid"), "indexedAt": post.get("indexedAt")},
    )
