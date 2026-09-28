# Porting notes: Cloudflare Worker / Durable Object or a small VPS

The prototype is a single Python process. Nothing in it assumes Python beyond the code
itself; the boundaries below are where a port would cut.

## What the process holds

| concern | today | notes |
|---|---|---|
| Bluesky ingest | Jetstream websocket (`sources/jetstream.py`) or polling (`sources/bluesky_poll.py`) | the only long-lived connection; polling needs none |
| market data | `MarketSource.latest()` every 60s + `ThresholdDetector` | detector state is a 15-minute deque |
| state | one SQLite file: events, squawks, cursors, runs, market_snapshots | all writes are single statements or two-statement updates |
| writer | one `POST /v1/messages` per pass with a JSON schema | stateless apart from reading the DB |
| outputs | `squawks.json` (latest 200) + `squawks.ndjson` (append) | files; could be an object store or a table |

## Option A: small VPS (least work)

1. `uv sync`, copy `.env`, run `squawk run` under systemd (`Restart=always`). Jetstream
   reconnects and resumes from the stored cursor on its own.
2. Serve `data/` read-only from nginx (or rsync `squawks.json` to the site host after each
   pass; the file is replaced atomically).
3. Back up `data/squawk.sqlite`.

That is the whole port. `squawk stats` gives cost totals; the `runs` table has per-call
detail.

## Option B: Cloudflare Workers + Durable Object

Map each concern onto a Cloudflare primitive:

1. **Ingest**: a Durable Object holds the Jetstream websocket (DOs can keep an outbound
   websocket open with the hibernation API) and writes events to its own SQLite-backed
   storage (DO storage is SQLite, so `store.py`'s schema ports almost verbatim). If a
   persistent socket is not wanted, replace it with a Cron Trigger that runs the polling
   logic (`bluesky_poll.py` is ~120 lines of HTTP + cursor bookkeeping).
2. **Market**: a Cron Trigger (every minute) fetches the PWLBtoday feed, stores a
   snapshot, and runs the threshold check (`ThresholdDetector.observe` is pure logic over
   the last 15 minutes of snapshots).
3. **Writer**: a Cron Trigger (every 5 minutes, or every minute while an `event_mode_until`
   timestamp is in the future) selects unprocessed events, builds the same JSON user
   message (`writer/prompt.py`), and calls the Messages API with `output_config.format`
   (`writer/schema.py` is the schema; the Anthropic TypeScript SDK exposes the same
   parameters as the Python one used here). Validate with the same rules as
   `models.SquawkDraft` (headline length, enum, importance range, known event ids).
4. **Outputs**: write `squawks.json` to R2 or KV and append history to R2; or serve it from
   the DO directly. Keep the envelope in `docs/INTEGRATION.md` so the site does not change.
5. **Dedupe**: `dedupe.py` is 60 lines of normalisation and bigram Jaccard; port as is.
6. **Prompt**: keep `prompts/squawk_writer.md` as the single source of truth and bundle it
   as a text asset.

Things that do not port and do not need to: the `record`/`replay` tooling (keep running it
locally against a recording exported from the DO), and `web/ticker.html` (a static page).

## Keeping the interfaces stable while porting

- The **event id** is `blake2b("source|uri")[:16]`; keep it so `source_event_ids` stay
  comparable across implementations.
- The **squawk envelope** and object are versioned (`schema_version`); bump it if fields
  change.
- The **writer prompt and schema** are the tuning surface; ship them unchanged and A/B them
  with `squawk replay` before deploying.
