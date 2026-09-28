# pwlbsquawk

Prototype live squawk feed for [PWLBtoday](https://pwlbtoday.com): curated Bluesky posts
plus market data go in, short original squawks on what each event means for UK rates and
PWLB borrowing come out. Outputs are two files (`data/squawks.json`, `data/squawks.ndjson`)
and a demo ticker; the contract for integrating them into the site is in
[`docs/INTEGRATION.md`](docs/INTEGRATION.md).

```
Bluesky (Jetstream websocket, or polling)  ─┐
                                            ├─▶ SQLite event log (dedupe) ─▶ Claude writer (strict JSON) ─▶ squawks.json / .ndjson ─▶ ticker
Market feed (mock now; your feed later)    ─┘        ▲ threshold alerts (e.g. 10y ±5bp in 15 min) switch on 1-minute "event mode"
```

Architecture and file layout: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).
Porting to a Cloudflare Worker/Durable Object or a VPS: [`docs/PORTING.md`](docs/PORTING.md).
Account verification and proposed additions: [`docs/ACCOUNTS.md`](docs/ACCOUNTS.md).

## Setup (8 steps)

1. Install [uv](https://docs.astral.sh/uv/) and clone this repo.
2. `uv sync` (creates `.venv` with Python 3.12 and the `squawk` command).
3. `cp .env.example .env` and set `ANTHROPIC_API_KEY`. Everything else has a default.
4. `uv run scripts/resolve_dids.py` to confirm the accounts in `config/accounts.yaml`
   still resolve and are active (`--write` refreshes DIDs).
5. `uv run pytest` (68 tests, no network, no key).
6. Try it without spending anything: `uv run squawk run --duration 3m --backend mock --market-jump 8`
   streams real posts from Jetstream, fires a demo market alert, and writes `data/squawks.json`
   with placeholder squawks from the mock writer.
7. Real squawks: `uv run squawk run` (Ctrl-C to stop). Watch the log for `SQUAWK [...]` lines
   and per-call token usage / estimated cost.
8. See it: `uv run squawk serve`, then open <http://127.0.0.1:8765/web/ticker.html>.

## Commands

| command | use |
|---|---|
| `squawk run [--duration 10m] [--poll] [--backend mock] [--market-jump BP] [--record FILE]` | local service: ingest + market monitor + writer loop |
| `squawk listen [--duration] [--record FILE]` | Jetstream → SQLite only (no writer) |
| `squawk poll [--since 2026-09-01\|30d]` | one polling pass (cron-friendly); `--since` backfills history |
| `squawk write [--dry-run] [--backend mock]` | one writer pass over unprocessed events |
| `squawk record [--duration 24h] [--poll --since DATE] [--out FILE] [--from-db]` | capture raw events for replay |
| `squawk replay FILE [--window 5m\|1h\|1d\|1w] [--backend mock] [--limit N] [--prompt FILE]` | re-run the writer over a capture; prints tokens and cost |
| `squawk resolve [--write] [--disable-inactive] [--check HANDLE...]` | resolve handles, flag dead/inactive accounts |
| `squawk serve [--port 8765]` | static server for the demo ticker |
| `squawk stats` | event-log counts and token/cost totals |

Global options (`--db`, `--accounts`, `--output-dir`, `--env`, `-v`) go before or after the
subcommand. Scheduled-job deployment is `squawk poll && squawk write`; see
`.github/workflows/squawk-poll.yml` for a GitHub Actions example.

## Tuning the writer without waiting for news

The prompt is `prompts/squawk_writer.md`; the output schema is `src/pwlbsquawk/writer/schema.py`.

```
# 1. capture (live for a day, or backfill the last two days through polling in ~1 minute)
uv run squawk record --duration 24h
uv run squawk record --poll --since 2d --out data/recordings/backfill.ndjson

# 2. replay: mock backend first (free) to check plumbing and volume, then the real model
uv run squawk replay data/samples/2026-09-27_28-seed-accounts.ndjson --backend mock --window 1h
uv run squawk replay data/samples/2026-09-27_28-seed-accounts.ndjson --window 5m --limit 20
uv run squawk replay data/samples/2026-09-27_28-seed-accounts.ndjson --window 5m --prompt prompts/variant.md --out data/replay/variant
```

Each replay writes `squawks.json`, `squawks.ndjson` and `report.json` (per-window headlines,
tokens, estimated cost) into `data/replay/<name>/`. The committed sample holds 1,241 real
events from the seed accounts over 27-28 September 2026; it has no market ticks, so replay
synthesises a mock gilt series (labelled `mock` in the prompt).

Weekly commentary and historical annotation work the same way: `squawk poll --since 30d`
(or `record --poll --since 30d`), then `replay --window 1w`. Bluesky's `getAuthorFeed`
pages back through an account's full history; only the Jetstream websocket is limited to a
short replay window.

## Cost

Every writer call logs `in/out/cache_read/cache_write` tokens and an estimated USD figure
(`writer/cost.py` has the price table; `squawk stats` sums the `runs` table). A pass with
no new events makes no call. With the current 12 accounts (about 600 events a day, two
thirds of them from the FinTwitter aggregator) and a 5-minute cadence, expect roughly
150 calls and ~$1-3 a day on `claude-opus-5-5`; hourly windows over the two-day sample
came to an estimated $0.65.

## Notes and known limits

- **Model settings**: `claude-opus-5-5` with effort `medium`, adaptive thinking (the API
  default), a strict JSON schema via `output_config.format`, a cached system prompt, and
  server-side refusal fallbacks enabled (`SQUAWK_FALLBACKS=off` disables them). A refusal
  is treated as "not material" and logged.
- The mock writer (`--backend mock`) is keyword-based and only proves the plumbing; its
  headlines are obviously placeholders.
- Replies are stored but not sent to the writer (`SQUAWK_INCLUDE_REPLIES=true` changes that).
- Near-duplicate detection is bigram Jaccard over normalised text within 6 hours; heavy
  rewordings are left for the writer to merge into one squawk.
- The Bank of England account (`bankofengland.bsky.social`) resolves but has never posted,
  so it is listed under `disabled` in `config/accounts.yaml`.
- One process, one SQLite file. Fine for a prototype; `docs/PORTING.md` covers the next step.
