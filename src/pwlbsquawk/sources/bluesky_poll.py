"""Polling source: app.bsky.feed.getAuthorFeed per account, tracking the last seen post.

Lets the tool run as a one-shot job (cron, GitHub Actions, a Claude Code routine)
with no persistent connection. `since` walks further back for historical backfills.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass, field
from typing import Any

import httpx

from ..config import Account, Accounts
from ..models import parse_ts, utcnow
from ..store import Store
from .bluesky import USER_AGENT, feed_item_to_event

log = logging.getLogger(__name__)


@dataclass
class PollStats:
    accounts: int = 0
    fetched: int = 0
    inserted: int = 0
    near_duplicates: int = 0
    already_seen: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)
    per_account: dict[str, int] = field(default_factory=dict)

    def summary(self) -> str:
        return (
            f"accounts={self.accounts} fetched={self.fetched} new={self.inserted} "
            f"near_dupes={self.near_duplicates} seen={self.already_seen} errors={len(self.errors)}"
        )


def resolve_handle(client: httpx.Client, api_base: str, handle: str) -> str:
    r = client.get(f"{api_base}/xrpc/com.atproto.identity.resolveHandle", params={"handle": handle})
    r.raise_for_status()
    return r.json()["did"]


class BlueskyPoller:
    def __init__(
        self,
        api_base: str,
        accounts: Accounts,
        store: Store,
        *,
        backfill_hours: float = 6.0,
        page_size: int = 30,
        max_pages: int = 10,
        dedupe_window_hours: float = 6.0,
        dedupe_threshold: float = 0.6,
        client: httpx.Client | None = None,
    ):
        self.api_base = api_base.rstrip("/")
        self.accounts = accounts
        self.store = store
        self.backfill_hours = backfill_hours
        self.page_size = page_size
        self.max_pages = max_pages
        self.dedupe_window_hours = dedupe_window_hours
        self.dedupe_threshold = dedupe_threshold
        self.client = client or httpx.Client(timeout=30, headers={"User-Agent": USER_AGENT})

    def _fetch_page(self, did: str, cursor: str | None) -> dict[str, Any]:
        params: dict[str, Any] = {"actor": did, "limit": self.page_size}
        if cursor:
            params["cursor"] = cursor
        r = self.client.get(f"{self.api_base}/xrpc/app.bsky.feed.getAuthorFeed", params=params)
        r.raise_for_status()
        return r.json()

    def poll_account(self, account: Account, *, since: dt.datetime | None = None) -> PollStats:
        stats = PollStats(accounts=1)
        did = account.did or resolve_handle(self.client, self.api_base, account.handle)
        key = f"poll:{did}"
        last_seen = self.store.get_cursor(key)
        if since is not None:
            lower = since
            max_pages = max(self.max_pages, 400)
        elif last_seen:
            lower = parse_ts(last_seen)
            max_pages = self.max_pages
        else:
            lower = utcnow() - dt.timedelta(hours=self.backfill_hours)
            max_pages = self.max_pages
        newest_seen = parse_ts(last_seen) if last_seen else None
        cursor: str | None = None
        for _ in range(max_pages):
            page = self._fetch_page(did, cursor)
            items = page.get("feed") or []
            if not items:
                break
            reached_lower = False
            for item in items:
                post = item.get("post") or {}
                indexed = parse_ts(post.get("indexedAt") or post.get("record", {}).get("createdAt"))
                if indexed < lower:
                    reached_lower = True  # feed is newest-first: everything after this is older
                    continue
                if post.get("author", {}).get("did") != did or item.get("reason"):
                    stats.skipped += 1  # reposts and pinned posts by others
                    continue
                event = feed_item_to_event(item, tier=account.tier)
                if event is None:
                    stats.skipped += 1
                    continue
                stats.fetched += 1
                result = self.store.insert_event(
                    event,
                    dedupe_window_hours=self.dedupe_window_hours,
                    dedupe_threshold=self.dedupe_threshold,
                )
                if result.status == "inserted":
                    stats.inserted += 1
                elif result.status == "near_duplicate":
                    stats.near_duplicates += 1
                else:
                    stats.already_seen += 1
                if newest_seen is None or indexed > newest_seen:
                    newest_seen = indexed
            cursor = page.get("cursor")
            if reached_lower or not cursor:
                break
        if newest_seen is not None:
            # full precision so a post indexed in the same second is not re-fetched forever
            self.store.set_cursor(key, newest_seen.isoformat())
        stats.per_account[account.handle] = stats.inserted
        return stats

    def poll_once(self, *, since: dt.datetime | None = None) -> PollStats:
        total = PollStats()
        for account in self.accounts.active:
            try:
                s = self.poll_account(account, since=since)
            except Exception as exc:  # noqa: BLE001 - keep polling the other accounts
                log.warning("poll failed for %s: %s", account.handle, exc)
                total.errors.append(f"{account.handle}: {exc}")
                continue
            total.accounts += 1
            total.fetched += s.fetched
            total.inserted += s.inserted
            total.near_duplicates += s.near_duplicates
            total.already_seen += s.already_seen
            total.skipped += s.skipped
            total.per_account.update(s.per_account)
        log.info("poll: %s", total.summary())
        return total
