"""Migrate a legacy callrecall-ai store into the voice-memory v2 format.

    python -m voice_memory.migrate /path/to/callrecall-memory.json [dst]

Without ``dst`` the source file is converted in place. The full field
mapping is documented in MIGRATION.md.
"""
from __future__ import annotations

import json
import os
import sys

from .store import upgrade_legacy_store


def migrate_store(src_path: str, dst_path: str | None = None) -> dict:
    """Read a legacy store, return + write the v2 store. Returns the data."""
    with open(os.path.expanduser(src_path), encoding="utf-8") as f:
        parsed = json.load(f)
    data = upgrade_legacy_store(parsed)
    out_path = dst_path or src_path
    directory = os.path.dirname(os.path.abspath(os.path.expanduser(out_path))) or "."
    os.makedirs(directory, exist_ok=True)
    with open(os.path.expanduser(out_path), "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")
    return data


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        return 0 if args else 1
    src = args[0]
    dst = args[1] if len(args) > 1 else None
    try:
        data = migrate_store(src, dst)
    except (OSError, ValueError) as e:
        print(f"migrate: {e}", file=sys.stderr)
        return 1
    print(f"migrate: {len(data['callers'])} caller(s) -> {dst or src}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
