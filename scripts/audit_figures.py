#!/usr/bin/env python3
"""Check every published squawk's figures against the Bluesky posts it cites.

    uv run scripts/audit_figures.py [data/squawks.ndjson]

Prints squawks containing a number that is not in their cited posts. Some hits are
legitimate carry-overs (a figure from an earlier squawk on the same story); anything
else is a candidate for data/corrections.json.
"""

from __future__ import annotations

import json
import sys
import time

import httpx

from pwlbsquawk.sources.bluesky import USER_AGENT, record_text
from pwlbsquawk.writer.figures import unsupported


def main(path: str = "data/squawks.ndjson") -> int:
    rows = [json.loads(line) for line in open(path, encoding="utf-8") if line.strip()]
    uris = sorted({s["uri"] for r in rows for s in r.get("sources", []) if s["uri"].startswith("at://")})
    texts: dict[str, str] = {}
    with httpx.Client(timeout=30, headers={"User-Agent": USER_AGENT}) as c:
        for i in range(0, len(uris), 25):
            r = c.get(
                "https://api.bsky.app/xrpc/app.bsky.feed.getPosts",
                params=[("uris", u) for u in uris[i : i + 25]],
            )
            r.raise_for_status()
            for p in r.json()["posts"]:
                texts[p["uri"]] = record_text(p["record"])
            time.sleep(0.2)
    flagged = 0
    for r in rows:
        src = "\n".join(texts.get(s["uri"], "") for s in r.get("sources", []))
        allowed = {r["ts"][:4]}
        bad = unsupported(r["headline"], src, allowed=allowed) | unsupported(
            r["detail"], src, allowed=allowed
        )
        if bad:
            flagged += 1
            print(f"{r['id']}  {r['ts'][:16]}  not in cited posts: {sorted(bad)}")
            print(f"    {r['headline']}\n    {r['detail']}\n")
    print(f"{len(rows)} squawks checked, {flagged} with figures not in their cited posts")
    return 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:]))
