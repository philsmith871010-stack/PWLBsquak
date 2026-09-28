"""SQLite event log, squawk store, cursors and run accounting."""

from __future__ import annotations

import datetime as dt
import json
import os
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import dedupe
from .models import Event, MarketSnapshot, RunRecord, Squawk, parse_ts, to_iso, utcnow

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id            TEXT PRIMARY KEY,
    source        TEXT NOT NULL,
    author        TEXT NOT NULL,
    author_did    TEXT,
    ts            TEXT NOT NULL,
    text          TEXT NOT NULL,
    uri           TEXT NOT NULL UNIQUE,
    raw_json      TEXT NOT NULL,
    is_reply      INTEGER NOT NULL DEFAULT 0,
    tier          INTEGER,
    norm_text     TEXT NOT NULL,
    fingerprint   TEXT NOT NULL,
    dedupe_of     TEXT,
    dedupe_score  REAL,
    ingested_at   TEXT NOT NULL,
    processed_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
CREATE INDEX IF NOT EXISTS idx_events_unprocessed ON events(processed_at) WHERE processed_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_events_fingerprint ON events(fingerprint);

CREATE TABLE IF NOT EXISTS squawks (
    id               TEXT PRIMARY KEY,
    ts               TEXT NOT NULL,
    headline         TEXT NOT NULL,
    la_angle         TEXT NOT NULL,
    category         TEXT NOT NULL,
    importance       INTEGER NOT NULL,
    source_event_ids TEXT NOT NULL,
    sources_json     TEXT NOT NULL,
    model            TEXT NOT NULL,
    run_id           TEXT NOT NULL,
    created_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_squawks_created ON squawks(created_at);

CREATE TABLE IF NOT EXISTS cursors (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    id                 TEXT PRIMARY KEY,
    started_at         TEXT NOT NULL,
    finished_at        TEXT NOT NULL,
    mode               TEXT NOT NULL,
    backend            TEXT NOT NULL,
    model              TEXT NOT NULL,
    n_events           INTEGER NOT NULL,
    n_squawks          INTEGER NOT NULL,
    material           INTEGER NOT NULL,
    input_tokens       INTEGER NOT NULL DEFAULT 0,
    output_tokens      INTEGER NOT NULL DEFAULT 0,
    cache_read_tokens  INTEGER NOT NULL DEFAULT 0,
    cache_write_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd           REAL NOT NULL DEFAULT 0,
    error              TEXT
);

CREATE TABLE IF NOT EXISTS market_snapshots (
    ts       TEXT PRIMARY KEY,
    source   TEXT NOT NULL,
    gilt_2y  REAL NOT NULL,
    gilt_5y  REAL NOT NULL,
    gilt_10y REAL NOT NULL,
    gilt_30y REAL NOT NULL,
    sonia    REAL NOT NULL,
    ois_json TEXT NOT NULL
);
"""


@dataclass(frozen=True)
class InsertResult:
    status: str  # inserted | duplicate_uri | near_duplicate
    event_id: str
    dedupe_of: str | None = None
    score: float | None = None

    @property
    def is_new(self) -> bool:
        return self.status == "inserted"


def _row_to_event(row: sqlite3.Row) -> Event:
    return Event(
        id=row["id"],
        source=row["source"],
        author=row["author"],
        author_did=row["author_did"],
        ts=row["ts"],
        text=row["text"],
        uri=row["uri"],
        raw=json.loads(row["raw_json"]),
        is_reply=bool(row["is_reply"]),
        tier=row["tier"],
    )


def _row_to_squawk(row: sqlite3.Row) -> Squawk:
    return Squawk(
        id=row["id"],
        ts=row["ts"],
        headline=row["headline"],
        la_angle=row["la_angle"],
        category=row["category"],
        importance=row["importance"],
        source_event_ids=json.loads(row["source_event_ids"]),
        sources=json.loads(row["sources_json"]),
        model=row["model"],
        run_id=row["run_id"],
        created_at=row["created_at"],
    )


class Store:
    """Thin wrapper over one SQLite file. Safe to use from a single process."""

    def __init__(self, path: str | os.PathLike[str] = ":memory:"):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, isolation_level=None, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        if self.path != ":memory:":
            self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(SCHEMA)

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> Store:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- events ------------------------------------------------------------

    def insert_event(
        self,
        event: Event,
        *,
        dedupe_window_hours: float = 6.0,
        dedupe_threshold: float = 0.6,
    ) -> InsertResult:
        """Store an event. Exact URI duplicates are ignored; near-duplicates are
        stored with `dedupe_of` pointing at the canonical event."""
        existing = self.conn.execute("SELECT id FROM events WHERE uri = ?", (event.uri,)).fetchone()
        if existing:
            return InsertResult("duplicate_uri", existing["id"])
        norm = dedupe.normalise(event.text)
        fp = dedupe.fingerprint(norm)
        dup: tuple[str, float] | None = None
        if event.source != "market" and norm:
            since = to_iso(event.ts - dt.timedelta(hours=dedupe_window_hours))
            until = to_iso(event.ts + dt.timedelta(hours=dedupe_window_hours))
            rows = self.conn.execute(
                "SELECT id, norm_text FROM events WHERE source != 'market' AND dedupe_of IS NULL "
                "AND ts BETWEEN ? AND ? ORDER BY ts DESC LIMIT 2000",
                (since, until),
            ).fetchall()
            dup = dedupe.find_duplicate(
                norm, ((r["id"], r["norm_text"]) for r in rows), threshold=dedupe_threshold
            )
        self.conn.execute(
            "INSERT INTO events (id, source, author, author_did, ts, text, uri, raw_json, is_reply, "
            "tier, norm_text, fingerprint, dedupe_of, dedupe_score, ingested_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                event.id,
                event.source,
                event.author,
                event.author_did,
                to_iso(event.ts),
                event.text,
                event.uri,
                json.dumps(event.raw, ensure_ascii=False, separators=(",", ":")),
                int(event.is_reply),
                event.tier,
                norm,
                fp,
                dup[0] if dup else None,
                dup[1] if dup else None,
                to_iso(utcnow()),
            ),
        )
        if dup:
            return InsertResult("near_duplicate", event.id, dup[0], dup[1])
        return InsertResult("inserted", event.id)

    def unprocessed_events(self, *, limit: int = 60, include_replies: bool = False) -> list[Event]:
        """Canonical (non-duplicate) events no writer pass has seen yet, oldest first."""
        sql = "SELECT * FROM events WHERE processed_at IS NULL AND dedupe_of IS NULL"
        if not include_replies:
            sql += " AND is_reply = 0"
        sql += " ORDER BY ts ASC LIMIT ?"
        return [_row_to_event(r) for r in self.conn.execute(sql, (limit,)).fetchall()]

    def count_unprocessed(self, *, include_replies: bool = False) -> int:
        sql = "SELECT COUNT(*) FROM events WHERE processed_at IS NULL AND dedupe_of IS NULL"
        if not include_replies:
            sql += " AND is_reply = 0"
        return int(self.conn.execute(sql).fetchone()[0])

    def mark_processed(self, ids: Iterable[str], when: dt.datetime | None = None) -> None:
        stamp = to_iso(when or utcnow())
        self.conn.executemany("UPDATE events SET processed_at = ? WHERE id = ?", [(stamp, i) for i in ids])
        # duplicates of processed events are processed too
        self.conn.execute(
            "UPDATE events SET processed_at = ? WHERE processed_at IS NULL AND dedupe_of IN "
            "(SELECT id FROM events WHERE processed_at IS NOT NULL)",
            (stamp,),
        )

    def events_by_ids(self, ids: Iterable[str]) -> dict[str, Event]:
        ids = list(ids)
        if not ids:
            return {}
        marks = ",".join("?" * len(ids))
        rows = self.conn.execute(f"SELECT * FROM events WHERE id IN ({marks})", ids).fetchall()
        return {r["id"]: _row_to_event(r) for r in rows}

    def all_events(self, *, since: dt.datetime | None = None) -> list[Event]:
        if since:
            rows = self.conn.execute(
                "SELECT * FROM events WHERE ts >= ? ORDER BY ts ASC", (to_iso(since),)
            ).fetchall()
        else:
            rows = self.conn.execute("SELECT * FROM events ORDER BY ts ASC").fetchall()
        return [_row_to_event(r) for r in rows]

    def count_events(self) -> int:
        return int(self.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0])

    def event_stats(self) -> dict[str, int]:
        row = self.conn.execute(
            "SELECT COUNT(*) AS total, SUM(dedupe_of IS NOT NULL) AS dupes, "
            "SUM(processed_at IS NULL AND dedupe_of IS NULL) AS pending, "
            "SUM(source = 'market') AS market FROM events"
        ).fetchone()
        return {k: int(row[k] or 0) for k in ("total", "dupes", "pending", "market")}

    # -- squawks ------------------------------------------------------------

    def insert_squawks(self, squawks: Iterable[Squawk]) -> int:
        n = 0
        for s in squawks:
            self.conn.execute(
                "INSERT OR IGNORE INTO squawks (id, ts, headline, la_angle, category, importance, "
                "source_event_ids, sources_json, model, run_id, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    s.id,
                    to_iso(s.ts),
                    s.headline,
                    s.la_angle,
                    s.category.value,
                    s.importance,
                    json.dumps(s.source_event_ids),
                    json.dumps([src.model_dump() for src in s.sources]),
                    s.model,
                    s.run_id,
                    to_iso(s.created_at),
                ),
            )
            n += 1
        return n

    def latest_squawks(self, limit: int = 200) -> list[Squawk]:
        """Newest first by publication time, then by squawk timestamp."""
        rows = self.conn.execute(
            "SELECT * FROM squawks ORDER BY created_at DESC, ts DESC LIMIT ?", (limit,)
        ).fetchall()
        return [_row_to_squawk(r) for r in rows]

    def recent_headlines(self, limit: int = 12) -> list[str]:
        rows = self.conn.execute(
            "SELECT headline FROM squawks ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [r["headline"] for r in rows]

    # -- cursors ------------------------------------------------------------

    def get_cursor(self, key: str) -> str | None:
        row = self.conn.execute("SELECT value FROM cursors WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def set_cursor(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO cursors (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )

    # -- runs ---------------------------------------------------------------

    def insert_run(self, run: RunRecord) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO runs (id, started_at, finished_at, mode, backend, model, n_events, "
            "n_squawks, material, input_tokens, output_tokens, cache_read_tokens, cache_write_tokens, "
            "cost_usd, error) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                run.id,
                to_iso(run.started_at),
                to_iso(run.finished_at),
                run.mode,
                run.backend,
                run.model,
                run.n_events,
                run.n_squawks,
                int(run.material),
                run.input_tokens,
                run.output_tokens,
                run.cache_read_tokens,
                run.cache_write_tokens,
                run.cost_usd,
                run.error,
            ),
        )

    def run_totals(self) -> dict[str, Any]:
        row = self.conn.execute(
            "SELECT COUNT(*) AS runs, COALESCE(SUM(input_tokens),0) AS input_tokens, "
            "COALESCE(SUM(output_tokens),0) AS output_tokens, "
            "COALESCE(SUM(cache_read_tokens),0) AS cache_read_tokens, "
            "COALESCE(SUM(cache_write_tokens),0) AS cache_write_tokens, "
            "COALESCE(SUM(cost_usd),0) AS cost_usd, COALESCE(SUM(n_squawks),0) AS n_squawks "
            "FROM runs"
        ).fetchone()
        return dict(row)

    # -- market -------------------------------------------------------------

    def insert_market_snapshot(self, snap: MarketSnapshot) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO market_snapshots (ts, source, gilt_2y, gilt_5y, gilt_10y, gilt_30y, "
            "sonia, ois_json) VALUES (?,?,?,?,?,?,?,?)",
            (
                to_iso(snap.ts),
                snap.source,
                snap.gilt_2y,
                snap.gilt_5y,
                snap.gilt_10y,
                snap.gilt_30y,
                snap.sonia,
                json.dumps(snap.ois),
            ),
        )

    def latest_market_snapshot(self, *, at_or_before: dt.datetime | None = None) -> MarketSnapshot | None:
        if at_or_before:
            row = self.conn.execute(
                "SELECT * FROM market_snapshots WHERE ts <= ? ORDER BY ts DESC LIMIT 1",
                (to_iso(at_or_before),),
            ).fetchone()
        else:
            row = self.conn.execute("SELECT * FROM market_snapshots ORDER BY ts DESC LIMIT 1").fetchone()
        if not row:
            return None
        return MarketSnapshot(
            ts=parse_ts(row["ts"]),
            source=row["source"],
            gilt_2y=row["gilt_2y"],
            gilt_5y=row["gilt_5y"],
            gilt_10y=row["gilt_10y"],
            gilt_30y=row["gilt_30y"],
            sonia=row["sonia"],
            ois=json.loads(row["ois_json"]),
        )
