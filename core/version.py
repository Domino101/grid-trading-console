"""Single source of truth for the application version.

The repository root VERSION file is authoritative. The FastAPI app reads it
through this module, and the frontend receives the same value at build time via
Vite's __APP_VERSION__ define. Do not hardcode a version in UI or routes.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

VERSION_PATTERN = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")
VERSION_FILE = Path(__file__).resolve().parents[1] / "VERSION"


def validate_version(value: str) -> str:
    """Return a normalized version or raise when it is not semver-like."""
    normalized = value.strip()
    if not VERSION_PATTERN.fullmatch(normalized):
        raise ValueError(f"Invalid VERSION value: {value!r} (expected e.g. 0.3.14)")
    return normalized


@lru_cache(maxsize=1)
def read_version() -> str:
    """Read the repository VERSION file, failing closed when it is missing."""
    try:
        raw = VERSION_FILE.read_text(encoding="utf-8")
    except OSError as exc:
        raise RuntimeError(f"Cannot read version file: {VERSION_FILE}") from exc
    return validate_version(raw)


__version__ = read_version()
