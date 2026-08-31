"""Stable bot identity helpers (P1-1).

Decouples the durable ledger key (``bot_id``) from the mutable config
filename.  The filename may embed volatile parameters (grid price range,
DCA timestamp), which historically caused a config edit to "rename" the bot
and orphan its accumulated trade ledger (reset-to-zero bug).

Contract:
- ``generate_bot_id()``      -> fresh stable id (uuid4 hex).
- ``resolve_bot_id(path)``   -> ``metadata.bot_id`` from the YAML if present,
                                else the filename stem (legacy fallback).
- ``read_bot_id(path)``      -> ``metadata.bot_id`` or ``None``.
- ``with_metadata_bot_id(d)``-> attach ``metadata.bot_id`` to a YAML dict.
- ``find_config_path(id, dir)`` -> reverse lookup (id -> config path).

All disk reads are memoized by ``(absolute path, mtime)`` so that the
per-heartbeat fallback path does not re-read YAML files on every tick.
"""

from __future__ import annotations

import os
import threading
import uuid
from pathlib import Path
from typing import Dict, Optional, Tuple

import yaml

# Repo root = <repo>/core/infrastructure/identity.py -> ../..
_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

# (absolute_path, mtime) -> metadata dict (may be None)
_meta_cache: Dict[Tuple[str, Optional[float]], Optional[dict]] = {}
_cache_lock = threading.Lock()
_CACHE_MAX = 512


def generate_bot_id() -> str:
    """Return a fresh stable bot id (32 hex chars, filename/order-id safe)."""
    return uuid.uuid4().hex


def _abs_path(config_path: str, project_root: Optional[str] = None) -> str:
    if os.path.isabs(config_path):
        return config_path
    return os.path.join(project_root or _PROJECT_ROOT, config_path)


def _read_metadata(config_path: str, project_root: Optional[str] = None) -> Optional[dict]:
    """Read and return the top-level ``metadata`` mapping of a YAML, or None."""
    path = _abs_path(config_path, project_root)
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        mtime = None

    with _cache_lock:
        if (path, mtime) in _meta_cache:
            return _meta_cache[(path, mtime)]

    metadata: Optional[dict] = None
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
        if isinstance(data, dict):
            meta = data.get("metadata")
            if isinstance(meta, dict):
                metadata = meta
    except (OSError, yaml.YAMLError):
        metadata = None

    with _cache_lock:
        if len(_meta_cache) >= _CACHE_MAX:
            _meta_cache.clear()
        _meta_cache[(path, mtime)] = metadata
    return metadata


def read_bot_id(config_path: str, project_root: Optional[str] = None) -> Optional[str]:
    """Return ``metadata.bot_id`` if present and non-empty, else None."""
    metadata = _read_metadata(config_path, project_root)
    if not metadata:
        return None
    bot_id = metadata.get("bot_id")
    if isinstance(bot_id, str) and bot_id:
        return bot_id
    return None


def resolve_bot_id(config_path: str, project_root: Optional[str] = None) -> str:
    """Return the stable bot id for a config, falling back to filename stem."""
    bot_id = read_bot_id(config_path, project_root)
    if bot_id:
        return bot_id
    return Path(config_path).stem


def with_metadata_bot_id(data: dict, bot_id: str) -> dict:
    """Attach ``metadata.bot_id`` to a YAML dict (grid_system / dca sibling)."""
    out = dict(data)
    existing = out.get("metadata")
    merged = dict(existing) if isinstance(existing, dict) else {}
    merged["bot_id"] = bot_id
    out["metadata"] = merged
    return out


def find_config_path(bot_id: str, directory: str, project_root: Optional[str] = None) -> Optional[str]:
    """Reverse lookup: return ``<directory>/<name>.yaml`` whose metadata.bot_id matches."""
    root = project_root or _PROJECT_ROOT
    abs_dir = os.path.join(root, directory)
    if not os.path.isdir(abs_dir):
        return None
    for name in sorted(os.listdir(abs_dir)):
        if not name.endswith(".yaml"):
            continue
        rel = f"{directory}/{name}"
        if read_bot_id(rel, root) == bot_id:
            return rel
    return None
