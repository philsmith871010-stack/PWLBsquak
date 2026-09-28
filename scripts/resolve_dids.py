#!/usr/bin/env python3
"""Resolve the handles in config/accounts.yaml to DIDs and flag dead/inactive accounts.

    uv run scripts/resolve_dids.py                      # report only
    uv run scripts/resolve_dids.py --write              # fill in DIDs/names in accounts.yaml
    uv run scripts/resolve_dids.py --write --disable-inactive
    uv run scripts/resolve_dids.py --check some.handle another.handle
"""

from __future__ import annotations

import sys

from pwlbsquawk.cli import main

if __name__ == "__main__":
    sys.exit(main(["resolve", *sys.argv[1:]]))
