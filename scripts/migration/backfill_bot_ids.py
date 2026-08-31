#!/usr/bin/env python3
"""P1-1 backfill: 為既有 grid / dca YAML 補上 ``metadata.bot_id``。

背景：穩定身份（bot_id）已與檔名解耦；新建立的 YAML 會寫入 ``metadata.bot_id``。
既有 YAML 尚未含此欄位，目前靠「檔名 stem」fallback 解析身份（與 legacy 帳本一致）。
本腳本為其補上 ``metadata.bot_id = 檔名 stem``，使身份顯式化且不改變既有帳本對接。

特性：
- 冪等：已存在 ``metadata.bot_id`` 的檔案跳過不動。
- 最小侵入：僅在 strategy 區塊前插入 ``metadata:`` 文字，保留既有註解與格式。
- 原子寫入：先寫暫存檔再 ``os.replace``，避免中斷損毀。
- 預設只掃 top-level ``config/grid/`` 與 ``config/dca/``（archived 不納入）。
- ``--dry-run`` 只列將變更的檔案，不寫入。

用法：
    python scripts/migration/backfill_bot_ids.py            # 直接執行
    python scripts/migration/backfill_bot_ids.py --dry-run  # 預覽
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import tempfile
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]


# 非機器人設定檔，不應補 bot_id（全域預設、讀我範本等）
SKIP_FILENAMES = frozenset({"defaults.yaml"})


def _target_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for directory in (root / "config" / "grid", root / "config" / "dca"):
        if not directory.is_dir():
            continue
        for entry in sorted(directory.iterdir()):
            if entry.is_file() and entry.suffix == ".yaml" and entry.name not in SKIP_FILENAMES:
                files.append(entry)
    return files


def _needs_backfill(path: Path) -> bool:
    """True when the YAML has no metadata.bot_id and parsing succeeds."""
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return False
    if not isinstance(data, dict):
        return False
    meta = data.get("metadata")
    if isinstance(meta, dict) and meta.get("bot_id"):
        return False
    return True


def _apply_backfill(path: Path) -> None:
    """Atomically insert ``metadata.bot_id = path.stem``, preserving the file body.

    Legacy configs are heavily commented; a full YAML load/dump round-trip would
    destroy comments and reformat the document.  Instead we do a targeted text
    insertion: a ``metadata:`` block is added right above the first top-level key
    (``grid_system:`` / ``dca:``), leaving every other line untouched.
    """
    text = path.read_text(encoding="utf-8")

    # 冪等：已含 metadata.bot_id 則不動
    data = yaml.safe_load(text or "{}") or {}
    if isinstance(data, dict):
        meta = data.get("metadata")
        if isinstance(meta, dict) and meta.get("bot_id"):
            return

    lines = text.splitlines(keepends=True)
    insert_at = len(lines)
    for i, line in enumerate(lines):
        stripped = line.lstrip()
        if stripped == "" or stripped.startswith("#"):
            continue
        # 第一個頂層 key（column 0）＝ strategy 區塊起點
        if re.match(r"^[A-Za-z_][A-Za-z0-9_.-]*:", line):
            insert_at = i
            break

    block = f"metadata:\n  bot_id: {path.stem}\n"
    if insert_at == len(lines):
        # 無頂層 key（空檔/異常）→ 直接附在檔尾
        new_text = text.rstrip("\n") + "\n" + block if text.strip() else block
    else:
        new_text = "".join(lines[:insert_at]) + block + "".join(lines[insert_at:])

    fd, tmp_path = tempfile.mkstemp(prefix=".backfill.", suffix=".yaml.tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(new_text)
            handle.flush()
            os.fsync(handle.fileno())
        # 保留原檔權限（部分既有 config 為 executable）
        os.chmod(tmp_path, os.stat(path).st_mode)
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill metadata.bot_id into grid/dca YAML")
    parser.add_argument("--dry-run", action="store_true", help="只預覽，不寫入")
    parser.add_argument("--root", type=Path, default=PROJECT_ROOT, help="專案根目錄（預設：倉庫根）")
    args = parser.parse_args()

    root: Path = args.root.resolve()
    changed: list[str] = []
    skipped: list[str] = []
    for path in _target_files(root):
        rel = str(path.relative_to(root))
        if _needs_backfill(path):
            changed.append(rel)
            if not args.dry_run:
                _apply_backfill(path)
        else:
            skipped.append(rel)

    mode = "DRY-RUN" if args.dry_run else "DONE"
    print(f"[backfill_bot_ids] {mode} | changed={len(changed)} skipped={len(skipped)}")
    for rel in changed:
        print(f"  + {rel}")
    if args.dry_run:
        print("（--dry-run 模式，未寫入任何檔案；移除 --dry-run 以實際執行）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
