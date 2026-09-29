"""`squawk` command line: listen | poll | write | run | record | replay | resolve | serve | stats."""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import functools
import http.server
import json
import logging
import re
import sys
from pathlib import Path

from . import __version__
from .accounts import check_handles, format_table, resolve_file, statuses_to_dicts
from .config import Settings, load_accounts
from .models import parse_ts, to_iso, utcnow
from .recording import Recorder, export_recording, replay
from .runner import Runner
from .sources.bluesky_poll import BlueskyPoller
from .sources.market import MockMarketSource
from .store import Store
from .writer.backends import make_backend
from .writer.prompt import load_system_prompt
from .writer.writer import SquawkWriter

log = logging.getLogger("squawk")

_DURATION_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([smhdw]?)\s*$", re.I)
_UNITS = {"": 1, "s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}


def parse_duration(text: str) -> dt.timedelta:
    m = _DURATION_RE.match(text)
    if not m:
        raise argparse.ArgumentTypeError(f"bad duration {text!r}; use e.g. 90s, 10m, 2h, 1d, 1w")
    return dt.timedelta(seconds=float(m.group(1)) * _UNITS[m.group(2).lower()])


def parse_when(text: str) -> dt.datetime:
    """ISO timestamp, a date, or a relative duration like '2d' (ago)."""
    if _DURATION_RE.match(text):
        return utcnow() - parse_duration(text)
    try:
        return parse_ts(text if "T" in text else text + "T00:00:00Z")
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"bad timestamp {text!r}") from exc


def _global_options(p: argparse.ArgumentParser, *, top: bool) -> None:
    """Global options work before or after the subcommand (`squawk --db x stats`, `squawk stats --db x`)."""
    sup = argparse.SUPPRESS
    p.add_argument("--env", default=".env" if top else sup, help="dotenv file to load (default .env)")
    p.add_argument(
        "-v", "--verbose", action="store_true", default=False if top else sup, help="debug logging"
    )
    p.add_argument("--db", default=None if top else sup, help="SQLite path (SQUAWK_DB_PATH)")
    p.add_argument(
        "--accounts", default=None if top else sup, help="accounts.yaml path (SQUAWK_ACCOUNTS_FILE)"
    )
    p.add_argument("--output-dir", default=None if top else sup, help="output dir for squawks.json/ndjson")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="squawk", description="PWLBtoday live squawk feed prototype")
    p.add_argument("--version", action="version", version=f"squawk {__version__}")
    _global_options(p, top=True)
    sub = p.add_subparsers(dest="command", required=True)
    _add = sub.add_parser

    def add_parser(name: str, **kw: object) -> argparse.ArgumentParser:
        sp = _add(name, **kw)
        _global_options(sp, top=False)
        return sp

    sub.add_parser = add_parser  # type: ignore[method-assign]

    def backend_args(sp: argparse.ArgumentParser) -> None:
        sp.add_argument(
            "--backend", choices=["anthropic", "mock"], help="writer backend (SQUAWK_WRITER_BACKEND)"
        )
        sp.add_argument("--model", help="model id (SQUAWK_MODEL)")
        sp.add_argument("--prompt", help="system prompt file (SQUAWK_PROMPT_FILE)")

    sp = sub.add_parser("listen", help="stream posts from Jetstream into SQLite")
    sp.add_argument("--duration", type=parse_duration, help="stop after e.g. 10m (default: until Ctrl-C)")
    sp.add_argument("--record", metavar="FILE", help="also append raw events to this NDJSON file")
    sp.add_argument("--no-market", action="store_true", help="do not poll the market source")

    sp = sub.add_parser("poll", help="one polling pass over the curated accounts (cron-friendly)")
    sp.add_argument("--since", type=parse_when, help="backfill from this time/date or e.g. 30d ago")
    sp.add_argument("--backfill-hours", type=float, help="first-run lookback (SQUAWK_POLL_BACKFILL_HOURS)")

    sp = sub.add_parser("write", help="one writer pass over unprocessed events")
    backend_args(sp)
    sp.add_argument("--dry-run", action="store_true", help="call the model but store nothing; print the JSON")
    sp.add_argument("--market-tick", action="store_true", help="take one market snapshot first")

    sp = sub.add_parser("run", help="listener + market monitor + writer loop (local service)")
    backend_args(sp)
    sp.add_argument("--duration", type=parse_duration, help="stop after e.g. 10m")
    sp.add_argument("--poll", action="store_true", help="poll instead of the Jetstream websocket")
    sp.add_argument("--poll-interval", type=int, help="seconds between polls (default: write interval)")
    sp.add_argument(
        "--write-interval", type=int, help="seconds between writer passes (SQUAWK_WRITE_INTERVAL)"
    )
    sp.add_argument("--record", metavar="FILE", help="also append raw events to this NDJSON file")
    sp.add_argument(
        "--market-jump",
        type=float,
        metavar="BP",
        help="demo: make the mock market jump by BP on its 3rd tick",
    )

    sp = sub.add_parser("record", help="capture raw events (no writer) for later replay")
    sp.add_argument("--out", metavar="FILE", help="NDJSON path (default data/recordings/<UTC date>.ndjson)")
    sp.add_argument("--duration", type=parse_duration, help="capture for e.g. 24h (default: until Ctrl-C)")
    sp.add_argument("--poll", action="store_true", help="poll instead of Jetstream")
    sp.add_argument(
        "--since", type=parse_when, help="with --poll: backfill history from this time, then exit"
    )
    sp.add_argument("--from-db", action="store_true", help="export the existing event log to --out and exit")

    sp = sub.add_parser("replay", help="re-run the writer over a recording in simulated windows")
    sp.add_argument("recording", help="NDJSON file from `squawk record`")
    backend_args(sp)
    sp.add_argument(
        "--window",
        type=parse_duration,
        default=dt.timedelta(minutes=5),
        help="writer cadence to simulate (5m, 1h, 1d, 1w)",
    )
    sp.add_argument("--out", help="output directory (default data/replay/<recording name>)")
    sp.add_argument("--limit", type=int, help="stop after N writer calls")
    sp.add_argument("--from", dest="start", type=parse_when, help="ignore events before this time")
    sp.add_argument("--to", dest="end", type=parse_when, help="ignore events after this time")

    sp = sub.add_parser("resolve", help="resolve handles to DIDs, flag dead/inactive accounts")
    sp.add_argument("--write", action="store_true", help="write DIDs/names back to accounts.yaml")
    sp.add_argument(
        "--disable-inactive",
        action="store_true",
        help="with --write: move dead/inactive accounts to disabled",
    )
    sp.add_argument("--inactive-days", type=int, default=14)
    sp.add_argument("--check", nargs="+", metavar="HANDLE", help="check ad-hoc handles instead of the file")
    sp.add_argument("--json", action="store_true", help="JSON output")

    sp = sub.add_parser(
        "serve", help="serve the repo over HTTP so web/ticker.html can load data/squawks.json"
    )
    sp.add_argument("--port", type=int, default=8765)
    sp.add_argument("--dir", default=".", help="directory to serve (default: current)")

    add_parser("stats", help="event log and token/cost totals")
    return p


def _settings(args: argparse.Namespace) -> Settings:
    s = Settings.from_env(dotenv=args.env)
    overrides = {}
    if args.db:
        overrides["db_path"] = Path(args.db)
    if args.accounts:
        overrides["accounts_file"] = Path(args.accounts)
    if args.output_dir:
        overrides["output_dir"] = Path(args.output_dir)
    if getattr(args, "backend", None):
        overrides["writer_backend"] = args.backend
    if getattr(args, "model", None):
        overrides["model"] = args.model
    if getattr(args, "prompt", None):
        overrides["prompt_file"] = Path(args.prompt)
    if getattr(args, "write_interval", None):
        overrides["write_interval"] = args.write_interval
    if getattr(args, "backfill_hours", None):
        overrides["poll_backfill_hours"] = args.backfill_hours
    if args.verbose:
        overrides["log_level"] = "DEBUG"
    return s.model_copy(update=overrides)


def _setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("websockets").setLevel(logging.WARNING)


# -- commands ----------------------------------------------------------------------


def cmd_listen(args: argparse.Namespace, settings: Settings) -> int:
    store = Store(settings.db_path)
    accounts = load_accounts(settings.accounts_file)
    recorder = Recorder(args.record) if args.record else None
    runner = Runner(
        settings, store, accounts, duration=args.duration, recorder=recorder, market=not args.no_market
    )
    asyncio.run(runner.run())
    return 0


def cmd_poll(args: argparse.Namespace, settings: Settings) -> int:
    store = Store(settings.db_path)
    accounts = load_accounts(settings.accounts_file)
    from .accounts import resolve_missing_dids

    accounts = resolve_missing_dids(accounts, settings.bsky_api)
    poller = BlueskyPoller(
        settings.bsky_api,
        accounts,
        store,
        backfill_hours=settings.poll_backfill_hours,
        page_size=settings.poll_page_size,
        dedupe_window_hours=settings.dedupe_window_hours,
        dedupe_threshold=settings.dedupe_threshold,
    )
    stats = poller.poll_once(since=args.since)
    print(stats.summary())
    for handle, n in sorted(stats.per_account.items(), key=lambda kv: -kv[1]):
        if n:
            print(f"  {handle:34} +{n}")
    for err in stats.errors:
        print(f"  error: {err}")
    return 0 if not stats.errors or stats.inserted else 1


def _restore_history(store: Store, settings: Settings) -> None:
    """A fresh database (e.g. a CI runner whose cache expired) gets the squawk history back
    from the committed ndjson so squawks.json keeps its 'latest 200' and repeats are avoided."""
    if store.count_squawks() == 0 and settings.squawks_ndjson_path.exists():
        n = store.import_squawks_ndjson(settings.squawks_ndjson_path)
        if n:
            log.info("restored %d squawks from %s", n, settings.squawks_ndjson_path)


def cmd_write(args: argparse.Namespace, settings: Settings) -> int:
    store = Store(settings.db_path)
    _restore_history(store, settings)
    backend = make_backend(settings)
    if args.market_tick:
        from .sources.market import MarketMonitor, ThresholdDetector, load_market_source

        MarketMonitor(
            load_market_source(settings.market_source), ThresholdDetector(settings.market_thresholds), store
        ).tick()
    writer = SquawkWriter(settings, store, backend, mode="write", persist=not args.dry_run)
    result = writer.run_once()
    if result.skipped:
        print("nothing to write (no new events)")
        return 0
    if result.error:
        print(f"writer error: {result.error}", file=sys.stderr)
        return 1
    if args.dry_run:
        print(json.dumps([s.to_public_dict() for s in result.squawks], indent=1, ensure_ascii=False))
    print(
        f"events={len(result.events)} material={result.material} squawks={len(result.squawks)} "
        f"dropped={result.dropped} tokens(in={result.usage.input_tokens} out={result.usage.output_tokens} "
        f"cache_read={result.usage.cache_read_tokens} cache_write={result.usage.cache_write_tokens}) "
        f"est_cost=${result.cost_usd:.4f}"
    )
    for s in result.squawks:
        print(f"  [{s.category.value}/{s.importance}] {s.headline}")
    return 0


def cmd_run(args: argparse.Namespace, settings: Settings) -> int:
    store = Store(settings.db_path)
    _restore_history(store, settings)
    accounts = load_accounts(settings.accounts_file)
    backend = make_backend(settings)
    recorder = Recorder(args.record) if args.record else None
    market_source = None
    if args.market_jump:
        market_source = MockMarketSource(jump_bp=args.market_jump)
    runner = Runner(
        settings,
        store,
        accounts,
        backend=backend,
        use_poll=args.poll,
        duration=args.duration,
        recorder=recorder,
        market_source=market_source,
        poll_interval=args.poll_interval,
    )
    summary = asyncio.run(runner.run())
    print(summary.line())
    return 0 if not summary.errors else 1


def cmd_record(args: argparse.Namespace, settings: Settings) -> int:
    out = Path(args.out) if args.out else Path("data/recordings") / f"{utcnow():%Y-%m-%d}.ndjson"
    store = Store(settings.db_path)
    if args.from_db:
        n = export_recording(store, out)
        print(f"exported {n} lines to {out}")
        return 0
    accounts = load_accounts(settings.accounts_file)
    recorder = Recorder(out)
    if args.poll and args.since and not args.duration:
        from .accounts import resolve_missing_dids

        accounts = resolve_missing_dids(accounts, settings.bsky_api)
        poller = BlueskyPoller(settings.bsky_api, accounts, store, on_new=recorder.event)
        stats = poller.poll_once(since=args.since)
        print(f"{stats.summary()} -> {out} ({recorder.count} lines)")
        return 0
    runner = Runner(
        settings,
        store,
        accounts,
        use_poll=args.poll,
        duration=args.duration,
        recorder=recorder,
        poll_since=args.since,
    )
    asyncio.run(runner.run())
    print(f"recorded {recorder.count} lines to {out}")
    return 0


def cmd_replay(args: argparse.Namespace, settings: Settings) -> int:
    rec = Path(args.recording)
    out = Path(args.out) if args.out else Path("data/replay") / rec.stem
    backend = make_backend(settings)
    prompt = load_system_prompt(settings.prompt_file)
    report = replay(
        settings,
        rec,
        backend,
        out_dir=out,
        window=args.window,
        start=args.start,
        end=args.end,
        limit_windows=args.limit,
        system_prompt=prompt,
    )
    print(report.summary())
    for p in report.passes:
        for h in p["squawks"]:
            print(f"  {p['at']}  {h}")
    print(f"outputs: {out}/squawks.json, {out}/squawks.ndjson, {out}/report.json")
    return 0 if report.errors == 0 else 1


def cmd_resolve(args: argparse.Namespace, settings: Settings) -> int:
    if args.check:
        statuses = check_handles(args.check, settings.bsky_api, inactive_days=args.inactive_days)
    else:
        statuses = resolve_file(
            str(settings.accounts_file),
            settings.bsky_api,
            inactive_days=args.inactive_days,
            write=args.write,
            disable_inactive=args.disable_inactive,
        )
    if args.json:
        print(json.dumps(statuses_to_dicts(statuses), indent=1))
    else:
        print(format_table(statuses))
    bad = [s for s in statuses if not s.ok]
    if bad:
        print(f"\n{len(bad)} account(s) not active: " + ", ".join(f"{s.handle} ({s.status})" for s in bad))
    return 0


def cmd_serve(args: argparse.Namespace, settings: Settings) -> int:
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=args.dir)
    http.server.SimpleHTTPRequestHandler.extensions_map[".json"] = "application/json"
    with http.server.ThreadingHTTPServer(("127.0.0.1", args.port), handler) as httpd:
        print(f"serving {Path(args.dir).resolve()} on http://127.0.0.1:{args.port}")
        print(f"open http://127.0.0.1:{args.port}/web/ticker.html  (Ctrl-C to stop)")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass
    return 0


def cmd_stats(args: argparse.Namespace, settings: Settings) -> int:
    store = Store(settings.db_path)
    ev = store.event_stats()
    runs = store.run_totals()
    print(f"db: {settings.db_path}")
    print(
        f"events: total={ev['total']} near_dupes={ev['dupes']} pending={ev['pending']} market={ev['market']}"
    )
    print(
        f"runs: {runs['runs']} squawks={runs['n_squawks']} "
        f"tokens(in={runs['input_tokens']} out={runs['output_tokens']} "
        f"cache_read={runs['cache_read_tokens']} cache_write={runs['cache_write_tokens']}) "
        f"est_cost=${runs['cost_usd']:.4f}"
    )
    latest = store.latest_squawks(5)
    for s in latest:
        print(f"  {to_iso(s.created_at)} [{s.category.value}/{s.importance}] {s.headline}")
    return 0


COMMANDS = {
    "listen": cmd_listen,
    "poll": cmd_poll,
    "write": cmd_write,
    "run": cmd_run,
    "record": cmd_record,
    "replay": cmd_replay,
    "resolve": cmd_resolve,
    "serve": cmd_serve,
    "stats": cmd_stats,
}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = _settings(args)
    _setup_logging(settings.log_level)
    try:
        return COMMANDS[args.command](args, settings)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
