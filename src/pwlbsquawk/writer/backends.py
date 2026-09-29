"""Writer backends: the Claude API (Anthropic Python SDK) and a deterministic mock."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Protocol

from ..models import Category
from .cost import Usage
from .schema import WRITER_OUTPUT_SCHEMA

log = logging.getLogger(__name__)

NOT_MATERIAL = '{"material": false, "squawks": []}'
FALLBACK_BETA = "server-side-fallback-2026-07-01"


class WriterError(RuntimeError):
    pass


@dataclass(frozen=True)
class BackendResult:
    text: str
    usage: Usage
    model: str
    stop_reason: str
    request_id: str | None = None
    cost_usd: float | None = None  # set when the backend reports its own figure


class WriterBackend(Protocol):
    name: str
    model: str
    cost_note: str  # appended to the cost in log lines, e.g. " (API-equivalent, not billed)"

    def complete(self, system: str, user: str) -> BackendResult: ...


class AnthropicBackend:
    """One structured-output call per writer pass.

    - system prompt carries a cache_control breakpoint (stable prefix)
    - output_config.format pins the JSON schema; effort is configurable
    - server-side refusal fallbacks are on by default (SQUAWK_FALLBACKS=off disables)
    """

    name = "anthropic"
    cost_note = " (estimated)"

    def __init__(
        self,
        *,
        model: str,
        api_key: str | None = None,
        effort: str = "medium",
        max_tokens: int = 8000,
        fallbacks: str = "default",
        timeout: float = 180.0,
        client: Any | None = None,
    ):
        self.model = model
        self.effort = effort
        self.max_tokens = max_tokens
        self.use_fallbacks = fallbacks != "off"
        if client is None:
            import anthropic

            client = anthropic.Anthropic(api_key=api_key, timeout=timeout, max_retries=3)
        self.client = client

    def _request(self, system: str, user: str) -> dict[str, Any]:
        return {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            "messages": [{"role": "user", "content": user}],
            "output_config": {
                "effort": self.effort,
                "format": {"type": "json_schema", "schema": WRITER_OUTPUT_SCHEMA},
            },
        }

    def _call(self, request: dict[str, Any]) -> Any:
        import anthropic

        if self.use_fallbacks:
            try:
                return self.client.beta.messages.create(betas=[FALLBACK_BETA], fallbacks="default", **request)
            except anthropic.BadRequestError as exc:
                if "fallback" in str(exc).lower() or "beta" in str(exc).lower():
                    log.warning("fallbacks rejected by the API (%s); continuing without them", exc)
                    self.use_fallbacks = False
                else:
                    raise
        return self.client.messages.create(**request)

    def complete(self, system: str, user: str) -> BackendResult:
        response = self._call(self._request(system, user))
        u = response.usage
        usage = Usage(
            input_tokens=int(u.input_tokens or 0),
            output_tokens=int(u.output_tokens or 0),
            cache_read_tokens=int(getattr(u, "cache_read_input_tokens", 0) or 0),
            cache_write_tokens=int(getattr(u, "cache_creation_input_tokens", 0) or 0),
        )
        request_id = getattr(response, "_request_id", None)
        stop = response.stop_reason or "end_turn"
        if stop == "refusal":
            details = getattr(response, "stop_details", None)
            log.warning("writer call refused (%s); treating batch as not material", details)
            return BackendResult(NOT_MATERIAL, usage, response.model, stop, request_id)
        if stop == "max_tokens":
            raise WriterError("writer output truncated at max_tokens; raise SQUAWK_MAX_TOKENS")
        text = "".join(block.text for block in response.content if block.type == "text")
        if not text.strip():
            raise WriterError(f"writer returned no text (stop_reason={stop})")
        return BackendResult(text, usage, response.model, stop, request_id)


# --- claude command line (Claude Code subscription, no API key) --------------


class ClaudeCliBackend:
    """Runs `claude -p` (Claude Code) with a JSON schema, so a Claude subscription can
    write squawks without an API key. Tools are disabled and one turn is allowed.
    `model` is a Claude Code model name (`opus`, `sonnet`), not an API model id."""

    name = "claude-cli"
    cost_note = " (API-equivalent; billed to your subscription, not per call)"

    def __init__(self, *, model: str, command: str = "claude", timeout: float = 300.0, retries: int = 1):
        self.model = model
        self.command = command
        self.timeout = timeout
        self.retries = retries

    def _args(self, system: str) -> list[str]:
        return [
            self.command,
            "-p",
            "--output-format",
            "json",
            "--model",
            self.model,
            "--system-prompt",
            system,
            "--json-schema",
            json.dumps(WRITER_OUTPUT_SCHEMA),
            "--tools",
            "",
            "--max-turns",
            "1",
        ]

    def complete(self, system: str, user: str) -> BackendResult:
        last: WriterError | None = None
        for attempt in range(self.retries + 1):
            try:
                return self._complete_once(system, user)
            except WriterError as exc:
                last = exc
                if attempt < self.retries and "exited" in str(exc):
                    log.warning("%s; retrying once", exc)
                    continue
                raise
        raise last  # pragma: no cover

    def _complete_once(self, system: str, user: str) -> BackendResult:
        import os
        import subprocess

        # an API key in .env would override the claude.ai login inside the subprocess
        env = {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")}
        try:
            proc = subprocess.run(
                self._args(system),
                input=user,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                env=env,
            )
        except FileNotFoundError as exc:
            raise WriterError(
                f"{self.command!r} not found; install Claude Code or set SQUAWK_CLAUDE_CLI"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise WriterError(f"{self.command} timed out after {self.timeout:.0f}s") from exc
        if proc.returncode != 0:
            detail = (proc.stderr.strip() or proc.stdout.strip())[:300]
            raise WriterError(f"{self.command} exited {proc.returncode}: {detail or 'no output'}")
        try:
            payload = json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            raise WriterError(f"{self.command} returned non-JSON output: {proc.stdout[:200]!r}") from exc
        if payload.get("is_error"):
            raise WriterError(f"{self.command} reported an error: {str(payload.get('result'))[:300]}")
        structured = payload.get("structured_output")
        text = json.dumps(structured) if structured is not None else str(payload.get("result") or "")
        if not text.strip():
            raise WriterError(f"{self.command} returned no result")
        u = payload.get("usage") or {}
        usage = Usage(
            input_tokens=int(u.get("input_tokens") or 0),
            output_tokens=int(u.get("output_tokens") or 0),
            cache_read_tokens=int(u.get("cache_read_input_tokens") or 0),
            cache_write_tokens=int(u.get("cache_creation_input_tokens") or 0),
        )
        cost = payload.get("total_cost_usd")
        return BackendResult(
            text,
            usage,
            self.model,
            "end_turn",
            payload.get("session_id"),
            cost_usd=float(cost) if cost is not None else None,
        )


# --- mock -------------------------------------------------------------------

_KEYWORDS: list[tuple[re.Pattern[str], Category, int, str]] = [
    (re.compile(r"\bmarket move\b", re.I), Category.global_rates, 3, "Gilt move"),
    (
        re.compile(r"\b(bank rate|mpc|monetary policy committee|bailey|bank of england|boe)\b", re.I),
        Category.boe,
        3,
        "Bank of England",
    ),
    (
        re.compile(
            r"\b(cpi|inflation|gdp|payrolls|unemployment|wage|labour market|retail sales|pmi|psnb)\b", re.I
        ),
        Category.data,
        2,
        "UK data",
    ),
    (
        re.compile(
            r"\b(budget|obr|fiscal|chancellor|reeves|treasury|spending review|borrowing|headroom)\b", re.I
        ),
        Category.fiscal,
        2,
        "Fiscal",
    ),
    (
        re.compile(r"\b(dmo|gilt auction|syndication|issuance|gilt sale|remit)\b", re.I),
        Category.supply,
        2,
        "Gilt supply",
    ),
    (re.compile(r"\b(oil|gas|energy|opec|hormuz)\b", re.I), Category.energy, 1, "Energy"),
    (
        re.compile(r"\b(fed|fomc|ecb|treasuries|treasury yields|bund)\b", re.I),
        Category.global_rates,
        1,
        "Global rates",
    ),
    (
        re.compile(r"\b(tariff|war|sanction|iran|ukraine|geopolitic)\b", re.I),
        Category.geopolitics,
        1,
        "Geopolitics",
    ),
    (re.compile(r"\b(gilt|gilts|yield|yields|sonia|swap)\b", re.I), Category.global_rates, 2, "Gilt market"),
]


class MockBackend:
    """Deterministic stand-in for tests, replays without a key, and demos.

    It classifies each event by keyword and emits one squawk per matching event
    without echoing the post text. Token counts are rough character-based estimates.
    """

    name = "mock"
    cost_note = " (mock estimate at Opus rates)"

    def __init__(self, model: str = "mock-writer", max_squawks: int = 6):
        self.model = model
        self.max_squawks = max_squawks
        self.calls = 0

    def complete(self, system: str, user: str) -> BackendResult:
        self.calls += 1
        usage = Usage(input_tokens=(len(system) + len(user)) // 4)
        try:
            payload = json.loads(user[user.index("{") :])
        except (ValueError, json.JSONDecodeError) as exc:
            raise WriterError(f"mock backend could not parse the user message: {exc}") from exc
        recent = set(payload.get("recent_squawk_headlines") or [])
        squawks: list[dict[str, Any]] = []
        items = list(payload.get("market_alerts") or []) + list(payload.get("events") or [])
        for item in items:
            text = item.get("text") or ""
            for pattern, category, importance, label in _KEYWORDS:
                m = pattern.search(text)
                if not m:
                    continue
                headline = f"{label}: {m.group(0).lower()} in focus for gilt investors"
                if headline in recent or any(s["headline"] == headline for s in squawks):
                    break
                squawks.append(
                    {
                        "ts": item.get("ts"),
                        "headline": headline[:140],
                        "la_angle": (
                            f"Mock analysis: a {label.lower()} item was flagged; PWLB certainty rates "
                            "move one-for-one with gilt yields, so watch the 10-year before fixing."
                        ),
                        "category": category.value,
                        "importance": importance,
                        "source_event_ids": [item["id"]],
                    }
                )
                break
            if len(squawks) >= self.max_squawks:
                break
        out = {"material": bool(squawks), "squawks": squawks}
        text = json.dumps(out)
        usage = Usage(usage.input_tokens, len(text) // 4, 0, 0)
        return BackendResult(text, usage, self.model, "end_turn")


def make_backend(settings: Any, *, client: Any | None = None) -> WriterBackend:
    """Build the backend named by settings.writer_backend."""
    if settings.writer_backend == "mock":
        return MockBackend()
    if settings.writer_backend == "claude-cli":
        return ClaudeCliBackend(model=settings.claude_cli_model, command=settings.claude_cli)
    if settings.writer_backend != "anthropic":
        raise ValueError("SQUAWK_WRITER_BACKEND must be 'anthropic', 'claude-cli' or 'mock'")
    if not settings.anthropic_api_key and client is None:
        raise WriterError("ANTHROPIC_API_KEY is not set (or use SQUAWK_WRITER_BACKEND=mock)")
    return AnthropicBackend(
        model=settings.model,
        api_key=settings.anthropic_api_key,
        effort=settings.effort,
        max_tokens=settings.max_tokens,
        fallbacks=settings.fallbacks,
        client=client,
    )
