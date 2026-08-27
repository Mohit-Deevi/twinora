#!/usr/bin/env python3
"""Deep-merge hermes/config.additions.yaml into the live Hermes config.yaml.

Run with the Hermes venv interpreter (it has PyYAML):
  %LOCALAPPDATA%\\hermes\\hermes-agent\\venv\\Scripts\\python.exe setup\\merge-config.py [--dry-run]

Behaviour
- Writes config.yaml.bak.<timestamp> before touching anything.
- Dicts merge recursively; scalars and lists from the additions file replace the existing value.
- Prints every key it changed. --dry-run prints and exits without writing.
- Comments in the existing config.yaml are not preserved (PyYAML limitation) — the backup keeps them.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover
    sys.exit("PyYAML not found — run this with the Hermes venv python (see docstring).")

ROOT = Path(__file__).resolve().parent.parent
ADDITIONS = ROOT / "hermes" / "config.additions.yaml"


def hermes_home() -> Path:
    if os.environ.get("HERMES_HOME"):
        return Path(os.environ["HERMES_HOME"])
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "hermes"
    return Path.home() / ".hermes"


def merge(base: dict, add: dict, path: str = "", changes: list[str] | None = None) -> dict:
    changes = changes if changes is not None else []
    for key, value in add.items():
        full = f"{path}.{key}" if path else key
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            merge(base[key], value, full, changes)
        else:
            if base.get(key) != value:
                changes.append(f"{full}: {base.get(key)!r} -> {value!r}")
            base[key] = value
    return base


def main() -> int:
    dry = "--dry-run" in sys.argv
    cfg_path = hermes_home() / "config.yaml"
    if not cfg_path.exists():
        sys.exit(f"config.yaml not found at {cfg_path}")
    if not ADDITIONS.exists():
        sys.exit(f"additions file not found at {ADDITIONS}")

    base = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    add = yaml.safe_load(ADDITIONS.read_text(encoding="utf-8")) or {}

    changes: list[str] = []
    merged = merge(base, add, changes=changes)

    if not changes:
        print("config.yaml already contains every addition — nothing to do.")
        return 0

    print(f"{len(changes)} change(s):")
    for line in changes:
        print("  " + line)

    if dry:
        print("--dry-run: not writing.")
        return 0

    backup = cfg_path.with_name(f"config.yaml.bak.{time.strftime('%Y%m%d_%H%M%S')}")
    backup.write_text(cfg_path.read_text(encoding="utf-8"), encoding="utf-8")
    cfg_path.write_text(yaml.safe_dump(merged, sort_keys=False, allow_unicode=True, width=120), encoding="utf-8")
    # Re-read to prove the file is valid YAML before declaring success.
    yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    print(f"written: {cfg_path}\nbackup:  {backup}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
