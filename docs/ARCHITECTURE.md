# Architecture

`pwlbsquawk` is a small pipeline: **sources → event log → AI writer → outputs**.
Every stage is a plain Python module with one job, so a later port to a
Cloudflare Worker / Durable Object or a VPS can replace one stage at a time
(see `PORTING.md`).

```
                 ┌────────────────────────┐
  Bluesky        │ sources/jetstream.py   │  websocket, curated DIDs, cursor resume
  (Jetstream) ──▶│ sources/bluesky_poll.py│  getAuthorFeed polling, last-seen per account
                 └──────────┬─────────────┘
                            │ Event
  Market feed    ┌──────────▼─────────────┐
  (mock / yours)─▶ sources/market.py      │  MarketSource -> MarketSnapshot
                 │ ThresholdDetector      │  e.g. 10y moves >5bp in 15 min -> Event(source="market")
                 └──────────┬─────────────┘
                            │
                 ┌──────────▼─────────────┐
                 │ store.py (SQLite)      │  events, squawks, cursors, runs, market_snapshots
                 │ dedupe.py              │  exact (uri) + near-duplicate (normalised text, Jaccard)
                 └──────────┬─────────────┘
                            │ unprocessed events + latest snapshot
                 ┌──────────▼─────────────┐
                 │ writer/                │  prompt.py builds the request
                 │  backend: anthropic    │  Claude structured JSON output (schema.py)
                 │  backend: mock         │  deterministic, free, used in tests/replay dry runs
                 └──────────┬─────────────┘
                            │ Squawk[]
                 ┌──────────▼─────────────┐
                 │ outputs.py             │  data/squawks.json (latest 200), data/squawks.ndjson (append-only)
                 └──────────┬─────────────┘
                            │
                   web/ticker.html (demo) / PWLBtoday site (colleague's integration)
```

## File layout

```
pwlbsquawk/
├── README.md                     setup in <10 steps, commands, tuning loop
├── pyproject.toml / uv.lock      Python 3.12, uv; console script `squawk`
├── .env.example                  every env var with defaults
├── config/accounts.yaml          handles grouped by tier (+ resolved DIDs)
├── prompts/squawk_writer.md      the writer system prompt (edit this to tune)
├── src/pwlbsquawk/
│   ├── cli.py                    squawk listen | poll | write | run | record | replay (+ resolve, serve)
│   ├── config.py                 Settings from env/.env; accounts.yaml loader
│   ├── models.py                 pydantic: Event, MarketSnapshot, Squawk, WriterResult
│   ├── store.py                  SQLite schema + queries
│   ├── dedupe.py                 text normalisation + near-duplicate detection
│   ├── outputs.py                squawks.json / squawks.ndjson writers
│   ├── eventmode.py              event-mode timer (1-min cadence after a trigger)
│   ├── runner.py                 `run`: listener + market monitor + writer loop
│   ├── recording.py              `record` / `replay`
│   ├── accounts.py               handle→DID resolution, activity check
│   ├── sources/
│   │   ├── bluesky.py            post record -> Event (text + link-card title/description)
│   │   ├── jetstream.py          websocket listener with reconnect + cursor
│   │   ├── bluesky_poll.py       getAuthorFeed poller
│   │   └── market.py             MarketSource protocol, MockMarketSource, ThresholdDetector
│   └── writer/
│       ├── schema.py             JSON schema for the strict model output
│       ├── prompt.py             request builder (events + market snapshot + recent squawks)
│       ├── backends.py           AnthropicBackend, MockBackend
│       ├── cost.py               token usage -> estimated USD
│       └── writer.py             one writer pass: select, call, validate, store, output
├── scripts/resolve_dids.py       resolve handles, flag dead/inactive accounts
├── data/
│   ├── squawks.json              latest 200 squawks, newest first   (generated)
│   ├── squawks.ndjson            append-only history                 (generated)
│   ├── squawk.sqlite             event log                           (generated)
│   ├── recordings/               `squawk record` output              (generated)
│   └── samples/                  a committed captured sample for `squawk replay`
├── web/ticker.html               self-contained demo ticker
├── docs/
│   ├── ARCHITECTURE.md           this file
│   ├── INTEGRATION.md            the contract: JSON schema, cadence, service vs job, env vars
│   ├── PORTING.md                Cloudflare Worker / Durable Object / VPS notes
│   └── ACCOUNTS.md               seed verification + proposed additions (need approval)
└── tests/                        pytest: dedupe, schema, thresholds, store, sources, writer, outputs
```

## Data model

**Event** (one row per raw item, `events` table)

| column | notes |
|---|---|
| `id` | deterministic: blake2b of `source|uri`, 16 hex chars |
| `source` | `bluesky` or `market` |
| `author` | Bluesky handle, or `market` |
| `author_did` | DID (Bluesky only) |
| `ts` | ISO 8601 UTC, the post's `createdAt` / the snapshot time |
| `text` | post text plus link-card title/description when present |
| `uri` | `at://did/app.bsky.feed.post/rkey` (unique) or `market://gilt_10y/<ts>` |
| `raw_json` | the untouched record |
| `is_reply` | replies are stored but skipped by the writer by default |
| `norm_text`, `fingerprint` | for dedupe |
| `dedupe_of` | id of the canonical event when this one is a near-duplicate |
| `processed_at` | set when a writer pass has seen the event |

**Squawk** (`squawks` table, and the JSON outputs)

`id, ts, headline (<=140), la_angle, category, importance (1-3), source_event_ids[], sources[] (handle + web link), model, run_id, created_at`

**Run** (`runs` table): one row per writer call with token counts and estimated cost.

## Dedupe

1. Exact: `uri` is unique (re-delivered Jetstream messages and overlapping polls are dropped).
2. Near-duplicate across accounts: text is normalised (lower-case, URLs/handles/
   punctuation stripped, whitespace collapsed). Identical normalised text is a
   duplicate. Otherwise word bigram shingles are compared with Jaccard similarity
   against events from the last 6 hours; >= 0.6 marks a duplicate. Duplicates are
   still stored (the log is complete) but carry `dedupe_of`, and the writer only
   sees canonical events.

## Writer

Every `SQUAWK_WRITE_INTERVAL` seconds (300; 60 in event mode) the writer:

1. selects unprocessed, canonical, non-reply events (oldest first, capped per call),
2. builds one request: system prompt from `prompts/squawk_writer.md` (cached), a
   user message with the market snapshot, any market threshold events, the
   headlines of the last few squawks (to avoid repeats), and the events,
3. calls Claude with a strict JSON schema (`output_config.format`) so the reply is
   always `{"material": false}` or `{"material": true, "squawks": [...]}`,
4. validates with pydantic (headline length, category enum, importance range,
   `source_event_ids` must reference supplied events), drops anything invalid,
5. stores squawks, marks events processed, records token usage + cost, rewrites
   `data/squawks.json` and appends to `data/squawks.ndjson`.

No new events → no API call → no cost.

**Event mode** starts when a market threshold event fires (or a configured
keyword such as "Bank Rate" appears in a tier-1 post) and lasts
`SQUAWK_EVENT_MODE_DURATION` seconds.

## Modes

| command | what it does |
|---|---|
| `squawk listen` | Jetstream websocket → SQLite (long-running) |
| `squawk poll` | one getAuthorFeed pass per account → SQLite (one-shot, cron-friendly) |
| `squawk write` | one writer pass over unprocessed events → outputs (one-shot) |
| `squawk run` | listener (or `--poll`) + market monitor + writer loop; `--duration 10m` |
| `squawk record` | like `listen` + market ticks, also appends raw events to `data/recordings/<date>.ndjson` |
| `squawk replay` | re-run the writer over a recording in simulated 5-minute windows; prints cost |
| `squawk resolve` | same as `scripts/resolve_dids.py` |
| `squawk serve` | tiny static server so `web/ticker.html` can fetch `data/squawks.json` |

Scheduled-job deployment = `squawk poll && squawk write` on a cron.
Service deployment = `squawk run`.

## Market adapter slot

`MarketSource` is a protocol with one method, `latest() -> MarketSnapshot`
(gilt 2y/5y/10y/30y yields in %, SONIA and an OIS curve). `MockMarketSource`
random-walks from realistic levels and can inject a jump for demos. Point
`SQUAWK_MARKET_SOURCE` at `your_module:YourClass` to plug in the real feed; no
other code changes.

## Portability notes

State lives in one SQLite file and two output files; the only long-lived
connection is the Jetstream websocket, and polling mode removes even that. See
`PORTING.md` for the Durable Object / VPS mapping.
