"""Resolve handles to DIDs and flag dead or inactive accounts (no posts in N days)."""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass

import httpx

from .config import Account, Accounts, DisabledAccount, dump_accounts, load_accounts
from .models import parse_ts, to_iso, utcnow
from .sources.bluesky import USER_AGENT

log = logging.getLogger(__name__)


@dataclass
class AccountStatus:
    handle: str
    tier: int | None = None
    did: str | None = None
    name: str | None = None
    followers: int | None = None
    posts_total: int | None = None
    last_post: str | None = None
    days_since_post: float | None = None
    status: str = "unknown"  # active | inactive | dead | error
    note: str = ""

    @property
    def ok(self) -> bool:
        return self.status == "active"


def check_account(
    client: httpx.Client, api_base: str, handle: str, *, inactive_days: int = 14, tier: int | None = None
) -> AccountStatus:
    st = AccountStatus(handle=handle, tier=tier)
    base = api_base.rstrip("/")
    try:
        r = client.get(f"{base}/xrpc/com.atproto.identity.resolveHandle", params={"handle": handle})
        if r.status_code == 400:
            st.status, st.note = "dead", "handle does not resolve"
            return st
        r.raise_for_status()
        st.did = r.json()["did"]
        p = client.get(f"{base}/xrpc/app.bsky.actor.getProfile", params={"actor": st.did})
        if p.status_code == 200:
            pj = p.json()
            st.name = pj.get("displayName") or None
            st.followers = pj.get("followersCount")
            st.posts_total = pj.get("postsCount")
        f = client.get(
            f"{base}/xrpc/app.bsky.feed.getAuthorFeed",
            params={"actor": st.did, "limit": 30, "filter": "posts_with_replies"},
        )
        if f.status_code == 400 and "suspended" in f.text.lower():
            st.status, st.note = "dead", "account suspended"
            return st
        f.raise_for_status()
        own = [
            it["post"]["record"].get("createdAt")
            for it in f.json().get("feed", [])
            if it["post"]["author"]["did"] == st.did and not it.get("reason")
        ]
        if not own:
            st.status, st.note = "inactive", "no posts found"
            return st
        latest = max(own)
        st.last_post = latest
        st.days_since_post = round((utcnow() - parse_ts(latest)).total_seconds() / 86400, 1)
        if st.days_since_post > inactive_days:
            st.status, st.note = "inactive", f"last post {st.days_since_post:.0f} days ago"
        else:
            st.status = "active"
    except httpx.HTTPError as exc:
        st.status, st.note = "error", str(exc)[:200]
    return st


def check_accounts(
    accounts: Accounts, api_base: str, *, inactive_days: int = 14, client: httpx.Client | None = None
) -> list[AccountStatus]:
    client = client or httpx.Client(timeout=30, headers={"User-Agent": USER_AGENT})
    out: list[AccountStatus] = []
    for acc in accounts.active:
        st = check_account(client, api_base, acc.handle, inactive_days=inactive_days, tier=acc.tier)
        log.info("%-34s %-8s %s", acc.handle, st.status, st.note or (st.last_post or ""))
        out.append(st)
    return out


def apply_statuses(accounts: Accounts, statuses: list[AccountStatus], *, disable_inactive: bool) -> Accounts:
    """Fill in DIDs/names; optionally move dead/inactive accounts to the disabled list."""
    by_handle = {s.handle: s for s in statuses}
    active: list[Account] = []
    disabled = list(accounts.disabled)
    for acc in accounts.active:
        st = by_handle.get(acc.handle)
        if st is None:
            active.append(acc)
            continue
        if st.did:
            acc.did = st.did
        if st.name and not acc.name:
            acc.name = st.name
        if disable_inactive and st.status in ("dead", "inactive"):
            disabled.append(
                DisabledAccount(
                    handle=acc.handle,
                    did=acc.did,
                    reason=f"{st.status}: {st.note} (checked {to_iso(utcnow())[:10]})",
                )
            )
        else:
            active.append(acc)
    return Accounts(active=active, disabled=disabled, tier_labels=accounts.tier_labels)


def format_table(statuses: list[AccountStatus]) -> str:
    lines = [f"{'handle':34} {'tier':>4} {'status':8} {'last post':20} {'followers':>9}  note"]
    for s in statuses:
        lines.append(
            f"{s.handle:34} {str(s.tier or ''):>4} {s.status:8} {(s.last_post or '')[:19]:20} "
            f"{str(s.followers if s.followers is not None else ''):>9}  {s.note}"
        )
    return "\n".join(lines)


def statuses_to_dicts(statuses: list[AccountStatus]) -> list[dict]:
    return [asdict(s) for s in statuses]


def resolve_file(
    path: str, api_base: str, *, inactive_days: int = 14, write: bool = False, disable_inactive: bool = False
) -> list[AccountStatus]:
    accounts = load_accounts(path)
    statuses = check_accounts(accounts, api_base, inactive_days=inactive_days)
    if write:
        updated = apply_statuses(accounts, statuses, disable_inactive=disable_inactive)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(dump_accounts(updated))
        log.info("updated %s", path)
    return statuses


def check_handles(handles: list[str], api_base: str, *, inactive_days: int = 14) -> list[AccountStatus]:
    client = httpx.Client(timeout=30, headers={"User-Agent": USER_AGENT})
    return [check_account(client, api_base, h, inactive_days=inactive_days) for h in handles]


def resolve_missing_dids(
    accounts: Accounts, api_base: str, *, client: httpx.Client | None = None
) -> Accounts:
    """Fill DIDs for accounts.yaml entries that only have a handle (used at startup)."""
    missing = [a for a in accounts.active if not a.did]
    if not missing:
        return accounts
    client = client or httpx.Client(timeout=30, headers={"User-Agent": USER_AGENT})
    base = api_base.rstrip("/")
    for acc in missing:
        r = client.get(f"{base}/xrpc/com.atproto.identity.resolveHandle", params={"handle": acc.handle})
        if r.status_code == 200:
            acc.did = r.json()["did"]
        else:
            log.warning("could not resolve %s (%s); skipping this run", acc.handle, r.status_code)
    accounts.active = [a for a in accounts.active if a.did]
    return accounts
