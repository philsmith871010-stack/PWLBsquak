import textwrap

from pwlbsquawk.config import Settings, dump_accounts, load_accounts


def test_settings_from_env(monkeypatch, tmp_path):
    monkeypatch.setenv("SQUAWK_MODEL", "claude-sonnet-5-5")
    monkeypatch.setenv("SQUAWK_WRITE_INTERVAL", "120")
    monkeypatch.setenv("SQUAWK_INCLUDE_REPLIES", "true")
    monkeypatch.setenv("SQUAWK_MARKET_THRESHOLDS", "gilt_10y=7.5, gilt_2y=4")
    monkeypatch.setenv("SQUAWK_EVENT_MODE_KEYWORDS", "Bank Rate, MPC")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    s = Settings.from_env(dotenv=tmp_path / "nope.env")
    assert s.model == "claude-sonnet-5-5"
    assert s.write_interval == 120 and s.include_replies is True
    assert s.market_thresholds == {"gilt_10y": 7.5, "gilt_2y": 4.0}
    assert s.event_mode_keywords == ["bank rate", "mpc"]
    assert s.anthropic_api_key == "sk-test"


def test_accounts_yaml_roundtrip(tmp_path):
    text = textwrap.dedent(
        """
        tiers:
          1:
            label: Core UK economics
            accounts:
              - handle: bruceandy.bsky.social
                did: did:plc:zpgmsa7eo63jbc57nd5gbr2c
                name: Andy Bruce (Reuters)
              - tobyn.bsky.social
          3:
            label: Wires
            accounts:
              - handle: reuters.com
                did: did:plc:jbvnehrrdqoulco4rf5gxg5r
        disabled:
          - handle: bankofengland.bsky.social
            did: did:plc:5ucsszonytyeh55sfrycr72h
            reason: no posts
        """
    )
    p = tmp_path / "accounts.yaml"
    p.write_text(text)
    accs = load_accounts(p)
    assert [a.handle for a in accs.active] == ["bruceandy.bsky.social", "tobyn.bsky.social", "reuters.com"]
    assert accs.active[1].tier == 1 and accs.active[1].did is None
    assert accs.dids == ["did:plc:zpgmsa7eo63jbc57nd5gbr2c", "did:plc:jbvnehrrdqoulco4rf5gxg5r"]
    assert accs.disabled[0].reason == "no posts"
    assert accs.tier_labels == {1: "Core UK economics", 3: "Wires"}
    again = load_accounts(_write(tmp_path, dump_accounts(accs)))
    assert again == accs


def _write(tmp_path, text):
    p = tmp_path / "again.yaml"
    p.write_text(text)
    return p


def test_resolve_write_keeps_header_comments(tmp_path, monkeypatch):
    from pwlbsquawk import accounts as acc_mod
    from pwlbsquawk.accounts import AccountStatus, leading_comments

    text = "# curated accounts\n# second line\n\ntiers:\n  1:\n    label: Core\n    accounts:\n      - handle: a.bsky.social\n"
    p = tmp_path / "accounts.yaml"
    p.write_text(text)
    assert leading_comments(text) == "# curated accounts\n# second line\n"
    monkeypatch.setattr(
        acc_mod,
        "check_accounts",
        lambda accounts, api_base, inactive_days=14, client=None: [
            AccountStatus(handle="a.bsky.social", tier=1, did="did:plc:abc", name="A", status="active")
        ],
    )
    acc_mod.resolve_file(str(p), "https://api.bsky.app", write=True)
    out = p.read_text()
    assert out.startswith("# curated accounts\n# second line\n")
    again = load_accounts(p)
    assert again.active[0].did == "did:plc:abc" and again.active[0].name == "A"
