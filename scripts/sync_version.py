#!/usr/bin/env python3
"""Keep all package metadata aligned with the repository VERSION file.

Usage:
  python3 scripts/sync_version.py                 # apply VERSION to package files
  python3 scripts/sync_version.py --check         # fail on drift (CI)
  python3 scripts/sync_version.py --set 0.3.15    # bump VERSION and sync

The frontend display and FastAPI app version are derived at build/start time,
so this script only owns the files npm requires a literal version in.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.version import VERSION_FILE, validate_version

PACKAGE_JSON = ROOT / "web_ui" / "frontend" / "package.json"
PACKAGE_LOCK = ROOT / "web_ui" / "frontend" / "package-lock.json"


def _load_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise RuntimeError(f"Cannot read {path}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid JSON in {path}: {exc}") from exc


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _check_field(
    label: str,
    path: Path,
    getter: Callable[[dict[str, Any]], Any],
    expected: str,
    issues: list[str],
) -> None:
    data = _load_json(path)
    actual = getter(data)
    if actual == expected:
        print(f"{label}: {actual} OK")
    else:
        issues.append(f"{label}: {actual!r} != {expected!r} ({path})")
        print(f"{label}: {actual!r} != {expected!r}")


def _apply_package(path: Path, version: str) -> bool:
    data = _load_json(path)
    if data.get("version") == version:
        return False
    data["version"] = version
    _write_json(path, data)
    return True


def _apply_lock(path: Path, version: str) -> bool:
    data = _load_json(path)
    changed = False
    if data.get("version") != version:
        data["version"] = version
        changed = True
    packages = data.setdefault("packages", {})
    root_package = packages.setdefault("", {})
    if root_package.get("version") != version:
        root_package["version"] = version
        changed = True
    if changed:
        _write_json(path, data)
    return changed


def _read_version() -> str:
    try:
        return validate_version(VERSION_FILE.read_text(encoding="utf-8"))
    except OSError as exc:
        raise RuntimeError(f"Cannot read {VERSION_FILE}") from exc


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync package versions with VERSION")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--check", action="store_true", help="report drift and exit 1 without writing")
    group.add_argument("--set", metavar="X.Y.Z", help="write a new VERSION before syncing")
    args = parser.parse_args()

    if args.set:
        version = validate_version(args.set)
        VERSION_FILE.write_text(version + "\n", encoding="utf-8")
    else:
        version = _read_version()

    print(f"Canonical VERSION: {version}")

    if args.check:
        issues: list[str] = []
        _check_field("web_ui/frontend/package.json", PACKAGE_JSON, lambda d: d.get("version"), version, issues)
        _check_field("web_ui/frontend/package-lock.json", PACKAGE_LOCK, lambda d: d.get("version"), version, issues)
        _check_field(
            'web_ui/frontend/package-lock.json packages[""].version',
            PACKAGE_LOCK,
            lambda d: d.get("packages", {}).get("", {}).get("version"),
            version,
            issues,
        )
        if issues:
            print("\nVersion drift detected. Run: python3 scripts/sync_version.py", file=sys.stderr)
            return 1
        print("All package versions are aligned.")
        return 0

    changed = []
    if _apply_package(PACKAGE_JSON, version):
        changed.append(str(PACKAGE_JSON.relative_to(ROOT)))
    if _apply_lock(PACKAGE_LOCK, version):
        changed.append(str(PACKAGE_LOCK.relative_to(ROOT)))

    if changed:
        print("Updated: " + ", ".join(changed))
    else:
        print("Already aligned; no files changed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
