"""Jetstream websocket listener: curated DIDs, app.bsky.feed.post only, cursor resume."""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlencode

import websockets

from ..models import Event
from .bluesky import POST_COLLECTION, jetstream_message_to_event

log = logging.getLogger(__name__)

EventCallback = Callable[[Event], Awaitable[None] | None]
CursorCallback = Callable[[int], None]

REWIND_US = 5_000_000  # overlap 5s on reconnect; the store drops exact duplicates


def build_url(base: str, dids: list[str], cursor_us: int | None = None) -> str:
    params: list[tuple[str, Any]] = [("wantedCollections", POST_COLLECTION)]
    params += [("wantedDids", d) for d in dids]
    if cursor_us:
        params.append(("cursor", int(cursor_us)))
    sep = "&" if "?" in base else "?"
    return f"{base}{sep}{urlencode(params)}"


class JetstreamListener:
    """Streams new posts from the curated accounts and hands each Event to `on_event`.

    Reconnects with exponential backoff and resumes from the last seen `time_us`.
    """

    def __init__(
        self,
        url: str,
        dids: list[str],
        on_event: EventCallback,
        *,
        handles: dict[str, str] | None = None,
        tiers: dict[str, int] | None = None,
        cursor_us: int | None = None,
        on_cursor: CursorCallback | None = None,
        cursor_every: int = 25,
        max_backoff: float = 30.0,
    ):
        if not dids:
            raise ValueError("JetstreamListener needs at least one DID")
        if len(dids) > 10_000:
            raise ValueError("Jetstream accepts at most 10,000 wantedDids")
        self.url = url
        self.dids = dids
        self.on_event = on_event
        self.handles = handles or {}
        self.tiers = tiers or {}
        self.cursor_us = cursor_us
        self.on_cursor = on_cursor
        self.cursor_every = cursor_every
        self.max_backoff = max_backoff
        self.messages_seen = 0
        self.events_emitted = 0
        self.connected = False

    async def _emit(self, event: Event) -> None:
        result = self.on_event(event)
        if inspect.isawaitable(result):
            await result

    async def handle_message(self, raw: str | bytes) -> None:
        msg = json.loads(raw)
        self.messages_seen += 1
        if "time_us" in msg:
            self.cursor_us = int(msg["time_us"])
            if self.on_cursor and self.messages_seen % self.cursor_every == 0:
                self.on_cursor(self.cursor_us)
        event = jetstream_message_to_event(msg, handles=self.handles, tiers=self.tiers)
        if event is None:
            return
        self.events_emitted += 1
        await self._emit(event)

    async def run(self, *, stop: asyncio.Event | None = None, duration: float | None = None) -> None:
        """Listen until `stop` is set or `duration` seconds elapse."""
        deadline = time.monotonic() + duration if duration else None
        backoff = 1.0

        def should_stop() -> bool:
            if stop is not None and stop.is_set():
                return True
            return deadline is not None and time.monotonic() >= deadline

        while not should_stop():
            cursor = self.cursor_us - REWIND_US if self.cursor_us else None
            url = build_url(self.url, self.dids, cursor)
            try:
                async with websockets.connect(url, max_size=2**22, open_timeout=20) as ws:
                    self.connected = True
                    backoff = 1.0
                    log.info("jetstream connected (%d accounts, cursor=%s)", len(self.dids), cursor)
                    while not should_stop():
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
                        except TimeoutError:
                            continue
                        await self.handle_message(raw)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - any network failure -> reconnect
                self.connected = False
                if should_stop():
                    break
                log.warning("jetstream disconnected (%s); reconnecting in %.0fs", exc, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, self.max_backoff)
            finally:
                self.connected = False
                if self.on_cursor and self.cursor_us:
                    self.on_cursor(self.cursor_us)
        log.info(
            "jetstream listener stopped (%d messages, %d posts)", self.messages_seen, self.events_emitted
        )
