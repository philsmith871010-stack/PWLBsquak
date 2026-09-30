import datetime as dt
import json
import types

import pytest

from pwlbsquawk.config import Settings
from pwlbsquawk.models import Category
from pwlbsquawk.outputs import read_ndjson
from pwlbsquawk.writer.backends import (
    FALLBACK_BETA,
    AnthropicBackend,
    MockBackend,
    WriterError,
    make_backend,
)
from pwlbsquawk.writer.cost import Usage, estimate_cost
from pwlbsquawk.writer.prompt import build_user_message
from pwlbsquawk.writer.writer import SquawkWriter, parse_writer_output, repair_headline
from tests.conftest import make_event, make_snapshot

NOW = dt.datetime(2026, 9, 28, 9, 30, tzinfo=dt.UTC)


def _settings(tmp_path, **kw) -> Settings:
    return Settings(output_dir=tmp_path, writer_backend="mock", **kw)


def _seed(store):
    e1 = make_event(
        "Bank of England holds Bank Rate at 4%, MPC votes 7-2", rkey="a", ts="2026-09-28T09:00:00Z"
    )
    e2 = make_event(
        "Weekend football roundup and a nice walk",
        rkey="b",
        ts="2026-09-28T09:05:00Z",
        author="tobyn.bsky.social",
    )
    e3 = make_event(
        "UK CPI rises to 3.8% in August, above forecasts",
        rkey="c",
        ts="2026-09-28T09:10:00Z",
        author="reuters.com",
    )
    for e in (e1, e2, e3):
        store.insert_event(e)
    return e1, e2, e3


def test_mock_writer_pass_end_to_end(store, tmp_path):
    settings = _settings(tmp_path)
    e1, e2, e3 = _seed(store)
    store.insert_market_snapshot(make_snapshot(NOW - dt.timedelta(minutes=1), 4.55))
    writer = SquawkWriter(settings, store, MockBackend(), system_prompt="sys")
    result = writer.run_once(now=NOW)
    assert not result.skipped and result.error is None
    assert result.material and len(result.squawks) == 2
    cats = {s.category for s in result.squawks}
    assert cats == {Category.boe, Category.data}
    # every event is processed, squawks stored, outputs written
    assert store.count_unprocessed() == 0
    assert [s.id for s in store.latest_squawks()] == [s.id for s in result.squawks][::-1] or len(
        store.latest_squawks()
    ) == 2
    doc = json.loads(settings.squawks_json_path.read_text())
    assert doc["schema_version"] == 1 and doc["count"] == 2 and len(doc["squawks"]) == 2
    first = doc["squawks"][0]
    assert set(first) == {
        "id",
        "ts",
        "headline",
        "detail",
        "category",
        "importance",
        "source_event_ids",
        "sources",
        "model",
        "run_id",
        "created_at",
    }
    assert first["sources"][0]["url"].startswith("https://bsky.app/profile/")
    assert len(read_ndjson(settings.squawks_ndjson_path)) == 2
    totals = store.run_totals()
    assert totals["runs"] == 1 and totals["input_tokens"] > 0
    # a second pass with nothing new makes no call
    backend = MockBackend()
    writer2 = SquawkWriter(settings, store, backend, system_prompt="sys")
    assert writer2.run_once(now=NOW).skipped and backend.calls == 0


def test_not_material_batch_marks_events_processed(store, tmp_path):
    settings = _settings(tmp_path)
    store.insert_event(make_event("Lovely weather in Cornwall this weekend", rkey="w"))
    writer = SquawkWriter(settings, store, MockBackend(), system_prompt="sys")
    result = writer.run_once(now=NOW)
    assert result.material is False and result.squawks == []
    assert store.count_unprocessed() == 0
    assert not settings.squawks_json_path.exists()


class _FakeUsage:
    def __init__(self, i=1200, o=300, cr=0, cw=900):
        self.input_tokens, self.output_tokens = i, o
        self.cache_read_input_tokens, self.cache_creation_input_tokens = cr, cw


class _FakeResponse:
    def __init__(self, text, stop_reason="end_turn", model="claude-opus-5-5"):
        self.content = [types.SimpleNamespace(type="text", text=text)]
        self.stop_reason = stop_reason
        self.stop_details = None
        self.usage = _FakeUsage()
        self.model = model
        self._request_id = "req_test"


class _FakeClient:
    """Records the request the backend would send and returns a canned response."""

    def __init__(self, response, beta_error=None):
        self.requests = []
        self.response = response
        self.beta_error = beta_error
        fake = self

        class _Messages:
            def create(self, **kw):
                fake.requests.append(("messages", kw))
                return fake.response

        class _BetaMessages:
            def create(self, **kw):
                fake.requests.append(("beta", kw))
                if fake.beta_error:
                    raise fake.beta_error
                return fake.response

        self.messages = _Messages()
        self.beta = types.SimpleNamespace(messages=_BetaMessages())


def test_anthropic_backend_request_shape_and_usage():
    payload = {
        "material": True,
        "squawks": [
            {
                "ts": "2026-09-28T09:00:00Z",
                "headline": "h",
                "detail": "a",
                "category": "boe",
                "importance": 3,
                "source_event_ids": ["x"],
            }
        ],
    }
    client = _FakeClient(_FakeResponse(json.dumps(payload)))
    backend = AnthropicBackend(model="claude-opus-5-5", effort="low", max_tokens=4000, client=client)
    res = backend.complete("SYSTEM", "USER")
    kind, req = client.requests[0]
    assert kind == "beta" and req["betas"] == [FALLBACK_BETA] and req["fallbacks"] == "default"
    assert req["model"] == "claude-opus-5-5" and req["max_tokens"] == 4000
    assert req["system"][0]["cache_control"] == {"type": "ephemeral"} and req["system"][0]["text"] == "SYSTEM"
    assert req["output_config"]["effort"] == "low"
    assert req["output_config"]["format"]["type"] == "json_schema"
    schema = req["output_config"]["format"]["schema"]
    assert schema["required"] == ["material", "squawks"] and schema["additionalProperties"] is False
    assert res.usage == Usage(1200, 300, 0, 900) and res.request_id == "req_test"
    assert json.loads(res.text) == payload


def test_anthropic_backend_disables_fallbacks_when_rejected():
    import anthropic
    import httpx2

    resp = httpx2.Response(400, request=httpx2.Request("POST", "https://api.anthropic.com/v1/messages"))
    err = anthropic.BadRequestError(
        "Unexpected value(s) for the anthropic-beta header: fallbacks", response=resp, body=None
    )
    client = _FakeClient(_FakeResponse('{"material": false, "squawks": []}'), beta_error=err)
    backend = AnthropicBackend(model="claude-opus-5-5", client=client)
    backend.complete("s", "u")
    assert [k for k, _ in client.requests] == ["beta", "messages"]
    assert backend.use_fallbacks is False
    backend.complete("s", "u")
    assert [k for k, _ in client.requests] == ["beta", "messages", "messages"]


def test_anthropic_backend_refusal_and_truncation():
    client = _FakeClient(_FakeResponse("", stop_reason="refusal"))
    res = AnthropicBackend(model="m", fallbacks="off", client=client).complete("s", "u")
    assert res.stop_reason == "refusal" and json.loads(res.text) == {"material": False, "squawks": []}
    client = _FakeClient(_FakeResponse('{"material": true', stop_reason="max_tokens"))
    with pytest.raises(WriterError):
        AnthropicBackend(model="m", fallbacks="off", client=client).complete("s", "u")


def test_writer_drops_invalid_and_unknown_source_ids(store, tmp_path):
    settings = _settings(tmp_path)
    e1, _, _ = _seed(store)

    class Canned:
        name, model = "canned", "m"

        def complete(self, system, user):
            from pwlbsquawk.writer.backends import BackendResult

            out = {
                "material": True,
                "squawks": [
                    {
                        "ts": "2026-09-28T09:00:00Z",
                        "headline": "Valid squawk on Bank Rate",
                        "detail": "x",
                        "category": "boe",
                        "importance": 3,
                        "source_event_ids": [e1.id, "not-an-id"],
                    },
                    {
                        "ts": "2026-09-28T09:00:00Z",
                        "headline": "Unknown ids only",
                        "detail": "x",
                        "category": "boe",
                        "importance": 3,
                        "source_event_ids": ["nope"],
                    },
                    {
                        "ts": "bad-ts",
                        "headline": "Long " * 40,
                        "detail": "x",
                        "category": "data",
                        "importance": 2,
                        "source_event_ids": [e1.id],
                    },
                ],
            }
            return BackendResult(json.dumps(out), Usage(10, 5), "m", "end_turn")

    result = SquawkWriter(settings, store, Canned(), system_prompt="sys").run_once(now=NOW)
    assert result.error is None
    headlines = [s.headline for s in result.squawks]
    assert headlines[0] == "Valid squawk on Bank Rate"
    assert result.squawks[0].source_event_ids == [e1.id]
    assert len(headlines[1]) <= 140 and headlines[1].endswith("…")
    assert result.squawks[1].ts == e1.ts  # bad ts falls back to the event time
    assert result.dropped == 1


def test_backend_error_leaves_events_for_next_pass(store, tmp_path):
    settings = _settings(tmp_path)
    _seed(store)

    class Boom:
        name, model = "boom", "m"

        def complete(self, system, user):
            raise ConnectionError("network down")

    result = SquawkWriter(settings, store, Boom(), system_prompt="sys").run_once(now=NOW)
    assert result.error and "network down" in result.error
    assert store.count_unprocessed() == 3
    assert store.run_totals()["runs"] == 1


def test_stale_events_are_skipped_without_a_call(store, tmp_path):
    settings = _settings(tmp_path)
    store.insert_event(make_event("Bank Rate decision from last week", rkey="old", ts="2026-09-20T09:00:00Z"))
    backend = MockBackend()
    result = SquawkWriter(settings, store, backend, system_prompt="sys").run_once(now=NOW)
    assert result.skipped and backend.calls == 0 and store.count_unprocessed() == 0


@pytest.mark.parametrize(
    "text, material, n",
    [
        ('{"material": false}', False, 0),
        ('{"material": false, "squawks": []}', False, 0),
        (
            '[{"ts": null, "headline": "h", "detail": "a", "category": "data", "importance": 1, "source_event_ids": ["e"]}]',
            True,
            1,
        ),
        (
            '```json\n{"material": true, "squawks": [{"ts": null, "headline": "h", "detail": "a", "category": "data", "importance": 1, "source_event_ids": ["e"]}]}\n```',
            True,
            1,
        ),
    ],
)
def test_parse_writer_output_forms(text, material, n):
    got_material, squawks = parse_writer_output(text)
    assert got_material is material and len(squawks) == n


def test_repair_headline():
    assert repair_headline("  short   headline ") == "short headline"
    long = "word " * 40
    fixed = repair_headline(long)
    assert len(fixed) <= 140 and fixed.endswith("…") and not fixed[:-1].endswith(" ")


def test_prompt_contains_events_snapshot_and_recent_headlines():
    ev = make_event("CPI at 3.8%", rkey="p")
    msg = build_user_message(
        now=NOW, events=[ev], snapshot=make_snapshot(NOW, 4.5), recent_headlines=["old one"]
    )
    body = json.loads(msg[msg.index("{") :])
    assert body["events"][0]["id"] == ev.id and body["events"][0]["text"] == "CPI at 3.8%"
    assert body["market_snapshot"]["gilt_yields_pct"]["10y"] == 4.5
    assert body["recent_squawk_headlines"] == ["old one"]
    assert body["now"] == "2026-09-28T09:30:00Z"


def test_cost_estimate_uses_price_table():
    usd = estimate_cost("claude-opus-5-5", Usage(1_000_000, 100_000, 500_000, 200_000))
    assert usd == pytest.approx(4 + 2 + 0.1 + 1.0)
    assert estimate_cost("claude-sonnet-5-5", Usage(1_000_000, 0, 0, 0)) == 2.0
    assert estimate_cost("some-unknown-model", Usage(1_000_000, 0, 0, 0)) == 4.0


def test_make_backend_requires_key_for_anthropic(tmp_path):
    with pytest.raises(WriterError):
        make_backend(Settings(writer_backend="anthropic", anthropic_api_key=None))
    assert isinstance(make_backend(Settings(writer_backend="mock")), MockBackend)


def test_claude_cli_backend_parses_json_output(tmp_path):
    from pwlbsquawk.writer.backends import ClaudeCliBackend

    fake = tmp_path / "claude"
    fake.write_text(
        "#!/bin/sh\n"
        "# echo the args and stdin to a file, then print a Claude Code json result\n"
        'printf "%s\\n" "$@" > "$0.args"\n'
        'cat > "$0.stdin"\n'
        "cat <<EOT\n"
        '{"type":"result","is_error":false,"result":"{\\"material\\": false, \\"squawks\\": []}",'
        '"session_id":"sess1","total_cost_usd":0.01,'
        '"usage":{"input_tokens":1200,"output_tokens":20,"cache_read_input_tokens":900,"cache_creation_input_tokens":0}}\n'
        "EOT\n"
    )
    fake.chmod(0o755)
    backend = ClaudeCliBackend(model="claude-opus-5-5", command=str(fake))
    res = backend.complete("SYSTEM PROMPT", "USER MESSAGE")
    assert json.loads(res.text) == {"material": False, "squawks": []}
    assert res.usage == Usage(1200, 20, 900, 0) and res.request_id == "sess1"
    args = (tmp_path / "claude.args").read_text().splitlines()
    assert args[:4] == ["-p", "--output-format", "json", "--model"]
    assert "--json-schema" in args and "--system-prompt" in args and args[args.index("--tools") + 1] == ""
    assert (tmp_path / "claude.stdin").read_text() == "USER MESSAGE"


def test_claude_cli_backend_prefers_structured_output_and_reports_errors(tmp_path):
    from pwlbsquawk.writer.backends import ClaudeCliBackend

    fake = tmp_path / "claude"
    fake.write_text(
        '#!/bin/sh\ncat >/dev/null\necho \'{"is_error":false,"result":"ignored","structured_output":{"material":true,"squawks":[]},"usage":{}}\'\n'
    )
    fake.chmod(0o755)
    res = ClaudeCliBackend(model="m", command=str(fake)).complete("s", "u")
    assert json.loads(res.text) == {"material": True, "squawks": []}
    bad = tmp_path / "bad"
    bad.write_text("#!/bin/sh\ncat >/dev/null\necho boom >&2\nexit 1\n")
    bad.chmod(0o755)
    with pytest.raises(WriterError):
        ClaudeCliBackend(model="m", command=str(bad)).complete("s", "u")
    with pytest.raises(WriterError):
        ClaudeCliBackend(model="m", command=str(tmp_path / "missing")).complete("s", "u")


def test_make_backend_claude_cli():
    from pwlbsquawk.writer.backends import ClaudeCliBackend

    b = make_backend(Settings(writer_backend="claude-cli", claude_cli="/opt/homebrew/bin/claude"))
    assert isinstance(b, ClaudeCliBackend) and b.command == "/opt/homebrew/bin/claude" and b.model == "opus"


def test_claude_cli_backend_strips_api_key_from_subprocess(tmp_path, monkeypatch):
    from pwlbsquawk.writer.backends import ClaudeCliBackend

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-placeholder")
    fake = tmp_path / "claude"
    # the fake echoes whatever key it sees into the result text
    fake.write_text(
        "#!/bin/sh\ncat >/dev/null\n"
        'printf \'{"is_error":false,"result":"%s","usage":{}}\' "${ANTHROPIC_API_KEY:-unset}"\n'
    )
    fake.chmod(0o755)
    res = ClaudeCliBackend(model="opus", command=str(fake)).complete("s", "u")
    assert res.text == "unset"


def test_claude_cli_backend_retries_once_and_reports_cli_cost(tmp_path):
    from pwlbsquawk.writer.backends import ClaudeCliBackend

    fake = tmp_path / "claude"
    marker = tmp_path / "first"
    fake.write_text(
        "#!/bin/sh\ncat >/dev/null\n"
        f'if [ ! -f "{marker}" ]; then touch "{marker}"; exit 1; fi\n'
        'echo \'{"is_error":false,"result":"{\\"material\\": false}","usage":{"input_tokens":5},"total_cost_usd":0.0123}\'\n'
    )
    fake.chmod(0o755)
    res = ClaudeCliBackend(model="opus", command=str(fake)).complete("s", "u")
    assert res.cost_usd == 0.0123 and marker.exists()


def _canned(squawks):
    from pwlbsquawk.writer.backends import BackendResult

    class Canned:
        name, model, cost_note = "canned", "m", ""

        def complete(self, system, user):
            return BackendResult(
                json.dumps({"material": True, "squawks": squawks}), Usage(1, 1), "m", "end_turn"
            )

    return Canned()


def test_writer_enforces_category_caps_keeping_highest_importance(store, tmp_path):
    e1, _, _ = _seed(store)
    geo = [
        {
            "ts": None,
            "headline": f"Geopolitical item number {i} about a distinct place {i}",
            "detail": "d",
            "category": "geopolitics",
            "importance": imp,
            "source_event_ids": [e1.id],
        }
        for i, imp in enumerate([1, 2, 1, 2])
    ]
    result = SquawkWriter(_settings(tmp_path), store, _canned(geo), system_prompt="s").run_once(now=NOW)
    assert [s.importance for s in result.squawks] == [2, 2]
    assert result.dropped == 2


def test_writer_drops_repeat_of_recent_squawk(store, tmp_path):
    e1, _, _ = _seed(store)
    first = [
        {
            "ts": None,
            "headline": "US offers up to 40 million barrels of oil from the Strategic Petroleum Reserve",
            "detail": "d",
            "category": "energy",
            "importance": 2,
            "source_event_ids": [e1.id],
        }
    ]
    SquawkWriter(_settings(tmp_path), store, _canned(first), system_prompt="s").run_once(now=NOW)
    store.insert_event(make_event("SPR again", rkey="z", ts="2026-09-28T09:20:00Z"))
    again = [
        {
            "ts": None,
            "headline": "US offers to lend up to 40 million barrels of oil from the Strategic Petroleum Reserve",
            "detail": "d",
            "category": "energy",
            "importance": 2,
            "source_event_ids": [store.unprocessed_events()[0].id],
        }
    ]
    result = SquawkWriter(_settings(tmp_path), store, _canned(again), system_prompt="s").run_once(now=NOW)
    assert result.squawks == [] and result.dropped == 1


def test_writer_total_cap(store, tmp_path):
    e1, _, _ = _seed(store)
    many = [
        {
            "ts": None,
            "headline": f"Distinct data release number {i} on topic {i * 7}",
            "detail": "d",
            "category": "data",
            "importance": 2,
            "source_event_ids": [e1.id],
        }
        for i in range(7)
    ]
    result = SquawkWriter(_settings(tmp_path), store, _canned(many), system_prompt="s").run_once(now=NOW)
    assert len(result.squawks) == 5 and result.dropped == 2


def test_category_caps_from_env(monkeypatch, tmp_path):
    monkeypatch.setenv("SQUAWK_CATEGORY_CAPS", "geopolitics=1, energy=3")
    s = Settings.from_env(dotenv=tmp_path / "none.env")
    assert s.category_caps == {"geopolitics": 1, "energy": 3}
