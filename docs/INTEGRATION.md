# Integration contract

This is the interface between `pwlbsquawk` and the PWLBtoday site. Everything the site
needs is in two files under `data/` (or wherever `SQUAWK_OUTPUT_DIR` points).

## Files

| file | contents | written |
|---|---|---|
| `data/squawks.json` | the latest 200 squawks, newest first, wrapped in a small envelope | rewritten atomically after every writer pass that produced squawks |
| `data/squawks.ndjson` | append-only history, one squawk per line (same object as below) | appended in the same pass |

Squawks are only ever added; an existing squawk is never edited or removed (the writer
is told about recent headlines to avoid repeats instead).

## `squawks.json`

```json
{
  "schema_version": 1,
  "generated_at": "2026-09-28T10:35:02Z",
  "count": 2,
  "squawks": [
    {
      "id": "a1b2c3d4e5f6",
      "ts": "2026-09-28T10:31:00Z",
      "headline": "MPC holds Bank Rate at 4%; two members vote for a cut",
      "detail": "The MPC voted 7-2 to keep Bank Rate at 4%, with two members preferring a 25bp cut; gilt yields were reported little changed after the decision.",
      "category": "boe",
      "importance": 3,
      "source_event_ids": ["1f2e3d4c5b6a7980"],
      "sources": [
        {"author": "bruceandy.bsky.social", "uri": "at://did:plc:.../app.bsky.feed.post/3m...", "url": "https://bsky.app/profile/bruceandy.bsky.social/post/3m..."}
      ],
      "model": "claude-opus-5-5",
      "run_id": "0f1e2d3c4b5a",
      "created_at": "2026-09-28T10:35:02Z"
    }
  ]
}
```

### Squawk object

| field | type | notes |
|---|---|---|
| `id` | string | stable, unique (12 hex chars) |
| `ts` | ISO 8601 UTC (`Z`) | time of the underlying event (post or market move) |
| `headline` | string, 1-140 chars | UK English, present tense, no hashtags/emojis |
| `detail` | string | 1-2 factual sentences: source, figures, timing, reported reaction; no interpretation |
| `category` | enum | `boe`, `data`, `fiscal`, `supply`, `geopolitics`, `energy`, `global_rates` |
| `importance` | integer 1-3 | 3 = moves PWLB rates now / rate decision / key data print; 2 = notable; 1 = colour |
| `source_event_ids` | string[] | ids in the `events` table; at least one |
| `sources` | object[] | `{author, uri, url}` for each source event; `url` is a bsky.app link or `null` for market alerts. Link to these; never republish post text |
| `model` | string | model id that wrote it (`mock-writer` for the mock backend) |
| `run_id` | string | writer pass id; joins to the `runs` table for token usage |
| `created_at` | ISO 8601 UTC | when the squawk was published |

JSON schema (draft 2020-12) for the squawk object, if you want to validate:

```json
{
  "type": "object",
  "required": ["id", "ts", "headline", "detail", "category", "importance", "source_event_ids", "sources", "model", "run_id", "created_at"],
  "additionalProperties": false,
  "properties": {
    "id": {"type": "string"},
    "ts": {"type": "string", "format": "date-time"},
    "headline": {"type": "string", "minLength": 1, "maxLength": 140},
    "detail": {"type": "string", "minLength": 1},
    "category": {"enum": ["boe", "data", "fiscal", "supply", "geopolitics", "energy", "global_rates"]},
    "importance": {"type": "integer", "minimum": 1, "maximum": 3},
    "source_event_ids": {"type": "array", "items": {"type": "string"}, "minItems": 1},
    "sources": {"type": "array", "items": {"type": "object", "required": ["author", "uri"], "properties": {"author": {"type": "string"}, "uri": {"type": "string"}, "url": {"type": ["string", "null"]}}}},
    "model": {"type": "string"},
    "run_id": {"type": "string"},
    "created_at": {"type": "string", "format": "date-time"}
  }
}
```

The same schema is what `pwlbsquawk.models.Squawk` enforces before anything is written.

## Update cadence

- Writer passes run every `SQUAWK_WRITE_INTERVAL` seconds (default 300). In **event mode**
  (after a market threshold alert, or a keyword such as "Bank Rate"/"MPC" in a tier-1 post)
  the interval drops to `SQUAWK_EVENT_MODE_INTERVAL` (default 60) for
  `SQUAWK_EVENT_MODE_DURATION` seconds (default 1800).
- A pass with no new events makes no API call and writes nothing.
- Most passes return `{"material": false}`; expect a handful of squawks per busy day, not
  one per pass. `generated_at` moves only when squawks were added.
- Consumers should poll `squawks.json` no more often than once a minute and treat
  `generated_at` as the change marker. The file is replaced atomically (`os.replace`), so
  readers never see a partial file.

## Running it: service vs scheduled job

**Service (local machine or a small VPS):**

```
squawk run                # Jetstream websocket + market monitor + writer loop
squawk run --poll         # same, but polls getAuthorFeed instead of holding a websocket
```

**Scheduled job (cron, GitHub Actions, a Claude Code routine):**

```
squawk poll && squawk write
```

`poll` tracks the last seen post per account in the SQLite `cursors` table, so each run
only ingests new posts; `write` handles whatever is unprocessed. The SQLite file
(`SQUAWK_DB_PATH`) should persist between runs; if it is lost, `write` rebuilds the squawk
history from `data/squawks.ndjson` so the outputs stay consistent (only the poll cursors are
lost, and the next poll backfills `SQUAWK_POLL_BACKFILL_HOURS`).

### GitHub Actions + GitHub Pages (the hosted setup)

`.github/workflows/squawk.yml` runs poll + write every 5 minutes on weekdays 06:00-20:59
UTC, keeps the SQLite file in the Actions cache, and commits `data/squawks.json` and
`data/squawks.ndjson` back to the branch. With GitHub Pages set to serve that branch's root,
the ticker is at `https://<owner>.github.io/<repo>/` (the root `index.html` redirects to
`web/ticker.html`) and the feed at `https://<owner>.github.io/<repo>/data/squawks.json`.

Setup, once:

1. Repository → Settings → Secrets and variables → Actions: add **either**
   `ANTHROPIC_API_KEY` (Claude API) **or** `CLAUDE_CODE_OAUTH_TOKEN` (run `claude setup-token`
   on a machine where Claude Code is logged in and paste the token). The workflow picks the
   API when the key is present, otherwise the subscription via Claude Code.
2. Settings → Pages → Source: "Deploy from a branch", branch = the one the workflow commits
   to, folder = `/ (root)`.
3. Actions tab → "squawk" → Run workflow, to seed the first outputs.

**Private code, public page.** GitHub Pages on a free plan needs a public repository. Keep
this repo private (code, prompt, accounts, token) and publish only the page and the outputs
to a second, public repo:

1. Create an empty public repo, e.g. `pwlb-squawk-feed`, and turn on Pages for it
   (Settings → Pages → Deploy from a branch → `main`, `/ (root)`).
2. Create a fine-grained personal access token (GitHub → Settings → Developer settings →
   Personal access tokens → Fine-grained) scoped to that one repo with **Contents:
   read and write**. Add it to the private repo as the secret `PAGES_REPO_TOKEN`.
3. In the private repo, Settings → Secrets and variables → Actions → **Variables**: add
   `PAGES_REPO` = `owner/pwlb-squawk-feed`.

Every run then overwrites the public repo with `index.html`, `web/ticker.html`,
`data/squawks.json` and `data/squawks.ndjson` (history is kept in the private repo; the
public repo is a single-commit snapshot). The ticker is at
`https://<owner>.github.io/pwlb-squawk-feed/`.

Notes: GitHub delays scheduled runs by a few minutes under load; a 5-minute cron on a
private repository uses about 6 hours of Actions minutes a day (the free allowance is 2,000
minutes a month), so use `*/15` there or make the repo public. There is no event mode or
Jetstream in this mode; that needs the `squawk run` service (a small VPS, see PORTING.md).

Both modes share one database, so you can start with the scheduled job and switch to the
service later without migrating anything.

## Environment variables

Copy `.env.example` to `.env`. Everything has a default except the API key.

| variable | default | meaning |
|---|---|---|
| `ANTHROPIC_API_KEY` | (none) | required for the real writer |
| `SQUAWK_MODEL` | `claude-opus-5-5` | any current Claude model id |
| `SQUAWK_EFFORT` | `medium` | `low`/`medium`/`high`/`xhigh`/`max` |
| `SQUAWK_WRITER_BACKEND` | `anthropic` | `claude-cli` uses the `claude` command on a Claude Code subscription (no key); `mock` runs free (tests, demos, dry replays) |
| `SQUAWK_CLAUDE_CLI` | `claude` | command for the `claude-cli` backend, e.g. `/opt/homebrew/bin/claude` |
| `SQUAWK_CLAUDE_CLI_MODEL` | `opus` | Claude Code model name for that backend (`opus`, `sonnet`, `haiku`) |
| `SQUAWK_FALLBACKS` | `default` | server-side refusal fallbacks; `off` to disable |
| `SQUAWK_MAX_TOKENS` | `8000` | output cap per call |
| `SQUAWK_MAX_EVENTS_PER_CALL` | `60` | batch size; older events wait for the next pass |
| `SQUAWK_INCLUDE_REPLIES` | `false` | replies are stored but not sent to the writer |
| `SQUAWK_WRITE_INTERVAL` | `300` | seconds between passes |
| `SQUAWK_EVENT_MODE_INTERVAL` | `60` | seconds between passes in event mode |
| `SQUAWK_EVENT_MODE_DURATION` | `1800` | how long event mode lasts |
| `SQUAWK_EVENT_MODE_KEYWORDS` | `bank rate,mpc,gilt,emergency budget,breaking` | tier-1 post keywords that trigger event mode |
| `SQUAWK_JETSTREAM_URL` | `wss://jetstream2.us-east.bsky.network/subscribe` | |
| `SQUAWK_BSKY_API` | `https://api.bsky.app` | AppView used for polling and resolution |
| `SQUAWK_ACCOUNTS_FILE` | `config/accounts.yaml` | |
| `SQUAWK_POLL_BACKFILL_HOURS` | `6` | first-run lookback for `poll` |
| `SQUAWK_MARKET_SOURCE` | `mock` | or `package.module:ClassName` for the real feed |
| `SQUAWK_MARKET_POLL_INTERVAL` | `60` | seconds between market snapshots |
| `SQUAWK_MARKET_THRESHOLDS` | `gilt_10y=5,gilt_2y=5,gilt_30y=5` | bp moves that raise an alert |
| `SQUAWK_MARKET_WINDOW_MINUTES` | `15` | window for the threshold |
| `SQUAWK_MARKET_COOLDOWN_MINUTES` | `15` | one alert per tenor per cooldown |
| `SQUAWK_DEDUPE_WINDOW_HOURS` | `6` | near-duplicate lookback |
| `SQUAWK_DEDUPE_THRESHOLD` | `0.6` | Jaccard similarity that marks a duplicate |
| `SQUAWK_DB_PATH` | `data/squawk.sqlite` | |
| `SQUAWK_OUTPUT_DIR` | `data` | where `squawks.json`/`.ndjson` go |
| `SQUAWK_PROMPT_FILE` | `prompts/squawk_writer.md` | the writer system prompt |
| `SQUAWK_SQUAWKS_JSON_LIMIT` | `200` | how many squawks `squawks.json` keeps |
| `SQUAWK_LOG_LEVEL` | `INFO` | |

## Plugging in the real market feed

Implement one class and point `SQUAWK_MARKET_SOURCE` at it:

```python
# pwlbtoday_feed.py (anywhere on PYTHONPATH)
from pwlbsquawk.models import MarketSnapshot

class PwlbTodayMarketSource:
    name = "pwlbtoday"

    def latest(self) -> MarketSnapshot:
        return MarketSnapshot(
            ts=..., gilt_2y=3.92, gilt_5y=4.12, gilt_10y=4.58, gilt_30y=5.31,
            sonia=3.95, ois={"3m": 3.88, "6m": 3.80, "1y": 3.66, "2y": 3.58},
            source=self.name,
        )
```

```
SQUAWK_MARKET_SOURCE=pwlbtoday_feed:PwlbTodayMarketSource
```

Yields are in percent. `ThresholdDetector` compares each new snapshot with every snapshot
in the trailing window and raises one alert per tenor per cooldown; alerts become
`source="market"` events that the writer sees alongside posts and that switch on event
mode. An optional `history(start, end)` method lets replays and historical annotation use
real levels instead of the synthetic series.

## The event log

`events` (SQLite) keeps every raw item: `id, source, author, author_did, ts, text, uri,
raw_json, is_reply, tier, norm_text, fingerprint, dedupe_of, dedupe_score, ingested_at,
processed_at`. `dedupe_of` points at the canonical event for near-duplicates. `runs`
records token usage and estimated cost per writer call; `squawk stats` prints totals.
