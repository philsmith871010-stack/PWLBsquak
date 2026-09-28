"""Event mode: shorten the writer cadence after a market alert or a headline trigger."""

from __future__ import annotations

import datetime as dt
import logging

from .models import Event

log = logging.getLogger(__name__)


class EventMode:
    def __init__(
        self, duration: dt.timedelta, keywords: list[str] | None = None, *, keyword_max_tier: int = 1
    ):
        self.duration = duration
        self.keywords = [k.lower() for k in (keywords or []) if k]
        self.keyword_max_tier = keyword_max_tier
        self.until: dt.datetime | None = None
        self.reason: str | None = None

    def active(self, now: dt.datetime) -> bool:
        return self.until is not None and now < self.until

    def trigger(self, reason: str, now: dt.datetime) -> None:
        was_active = self.active(now)
        self.until = now + self.duration
        self.reason = reason
        if not was_active:
            log.info("event mode ON for %s (%s)", self.duration, reason)

    def keyword_hit(self, event: Event) -> str | None:
        if event.tier is not None and event.tier > self.keyword_max_tier:
            return None
        text = event.text.lower()
        for kw in self.keywords:
            if kw in text:
                return kw
        return None

    def consider(self, event: Event, now: dt.datetime) -> bool:
        """Trigger on market alerts, or on keyword hits from top-tier accounts."""
        if event.source == "market":
            self.trigger(f"market alert {event.uri}", now)
            return True
        hit = self.keyword_hit(event)
        if hit:
            self.trigger(f"keyword '{hit}' from {event.author}", now)
            return True
        return False

    def interval(self, normal: int, event: int, now: dt.datetime) -> int:
        return event if self.active(now) else normal
