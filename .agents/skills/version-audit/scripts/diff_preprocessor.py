#!/usr/bin/env python3
"""
diff_preprocessor.py — Token-efficient diff preprocessor for version audits.

Generates a filtered git diff (excluding noise files) and a structured
change manifest (JSON) so the AI agent can audit module-by-module
without loading entire source files into context.

Usage:
    python diff_preprocessor.py --base origin/main --head HEAD
    python diff_preprocessor.py --base v1.0.0 --head v2.0.0 --outdir /tmp/audit
"""

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Noise patterns to exclude from the diff
# ---------------------------------------------------------------------------
EXCLUDE_PATTERNS: list[str] = [
    # Dependency / build artefacts
    "*.lock",
    "package-lock.json",
    "poetry.lock",
    "Pipfile.lock",
    # Virtual environments & caches
    "venv/",
    ".venv/",
    "node_modules/",
    "__pycache__/",
    ".pytest_cache/",
    ".mypy_cache/",
    # Database files
    "*.db",
    "*.db-journal",
    "*.db-shm",
    "*.db-wal",
    # Binary / media
    "*.png",
    "*.jpg",
    "*.jpeg",
    "*.gif",
    "*.ico",
    "*.svg",
    "*.woff",
    "*.woff2",
    "*.ttf",
    "*.eot",
    "*.pdf",
    "*.zip",
    "*.tar.gz",
    # IDE / OS
    ".idea/",
    ".vscode/",
    "*.swp",
    "*.swo",
    ".DS_Store",
    # Misc generated
    "scratch/*.json",
    "scratch/*.md",
    "logs/",
    "docs/historical-reports/",
    "download/",
    # Large generated HTML backups
    "*.bak_*",
    # Skills lock (agent metadata, not code)
    "skills-lock.json",
]

# ---------------------------------------------------------------------------
# Module classification (maps file paths to logical module groups)
# ---------------------------------------------------------------------------
MODULE_MAP: list[tuple[str, str]] = [
    (r"^bot/execution/", "EXECUTION"),
    (r"^bot/services/", "SERVICES"),
    (r"^bot/risk_engine\.py$", "RISK_ENGINE"),
    (r"^bot/reconciler\.py$", "RECONCILER"),
    (r"^bot/strategies/", "STRATEGY"),
    (r"^bot/state\.py$", "STATE"),
    (r"^bot/config\.py$", "CONFIG"),
    (r"^bot/config_watcher\.py$", "CONFIG"),
    (r"^bot/orderbook\.py$", "ORDERBOOK"),
    (r"^bot/scanner\.py$", "SCANNER"),
    (r"^bot/monitoring\.py$", "MONITORING"),
    (r"^bot/logging\.py$", "LOGGING"),
    (r"^bot/security\.py$", "SECURITY"),
    (r"^bot/resilience\.py$", "RESILIENCE"),
    (r"^bot/redeemer\.py$", "REDEEMER"),
    (r"^bot/", "BOT_CORE"),
    (r"^dashboard/api\.py$", "DASHBOARD_API"),
    (r"^dashboard/static/", "DASHBOARD_UI"),
    (r"^dashboard/", "DASHBOARD"),
    (r"^main\.py$", "ENTRYPOINT"),
    (r"^config\.yaml$", "CONFIG_YAML"),
    (r"^docker-compose\.yml$", "INFRA"),
    (r"^deploy\.sh$", "INFRA"),
    (r"^requirements\.txt$", "DEPENDENCIES"),
    (r"^\.env", "ENV_CONFIG"),
    (r"^tests/", "TESTS"),
    (r"^scripts/", "SCRIPTS"),
    (r"^scratch/", "SCRATCH"),
    (r"^\.agents/", "AGENT_CONFIG"),
]


def classify_module(filepath: str) -> str:
    """Classify a file path into a logical module group."""
    for pattern, module_name in MODULE_MAP:
        if re.search(pattern, filepath):
            return module_name
    return "OTHER"


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------

def run_git(*args: str, cwd: str | None = None) -> str:
    """Run a git command and return stdout."""
    result = subprocess.run(
        ["git"] + list(args),
        capture_output=True,
        text=True,
        cwd=cwd or os.getcwd(),
    )
    if result.returncode != 0:
        print(f"[ERROR] git {' '.join(args)} failed:\n{result.stderr}", file=sys.stderr)
        sys.exit(1)
    return result.stdout.strip()


def resolve_ref(ref: str, cwd: str | None = None) -> str:
    """Resolve a git ref to its full SHA."""
    return run_git("rev-parse", ref, cwd=cwd)


def build_exclude_args() -> list[str]:
    """Build git diff pathspec exclude arguments."""
    args = ["--", "."]
    for pattern in EXCLUDE_PATTERNS:
        args.append(f":(exclude){pattern}")
    return args


# ---------------------------------------------------------------------------
# Diff parsing
# ---------------------------------------------------------------------------

def parse_diff_stats(diff_text: str) -> dict[str, dict]:
    """Parse unified diff to extract per-file addition/deletion counts."""
    files: dict[str, dict] = {}
    current_file: str | None = None

    for line in diff_text.split("\n"):
        if line.startswith("diff --git"):
            # Extract b-path
            match = re.search(r"b/(.+)$", line)
            if match:
                current_file = match.group(1)
                files[current_file] = {"additions": 0, "deletions": 0}
        elif current_file and line.startswith("+") and not line.startswith("+++"):
            files[current_file]["additions"] += 1
        elif current_file and line.startswith("-") and not line.startswith("---"):
            files[current_file]["deletions"] += 1

    return files


def build_manifest(
    name_status: str, diff_stats: dict[str, dict]
) -> dict[str, dict]:
    """Merge git name-status with diff stats into a structured manifest."""
    manifest: dict[str, dict] = {}

    for line in name_status.strip().split("\n"):
        if not line.strip():
            continue
        parts = line.split("\t", 1)
        if len(parts) < 2:
            continue
        status_code = parts[0].strip()
        filepath = parts[1].strip()

        # Handle renames: R095\told.py\tnew.py
        if status_code.startswith("R"):
            file_parts = filepath.split("\t")
            filepath = file_parts[-1]  # use new name
            status = "RENAMED"
        else:
            status_map = {"A": "ADDED", "M": "MODIFIED", "D": "DELETED", "C": "COPIED"}
            status = status_map.get(status_code[0], status_code)

        stats = diff_stats.get(filepath, {"additions": 0, "deletions": 0})
        manifest[filepath] = {
            "status": status,
            "additions": stats["additions"],
            "deletions": stats["deletions"],
            "net": stats["additions"] - stats["deletions"],
            "module": classify_module(filepath),
        }

    return manifest


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    """Entry point: generate filtered diff and change manifest."""
    parser = argparse.ArgumentParser(
        description="Token-efficient diff preprocessor for version audits."
    )
    parser.add_argument("--base", required=True, help="Base git ref (e.g. origin/main, v1.0.0)")
    parser.add_argument("--head", default="HEAD", help="Head git ref (default: HEAD)")
    parser.add_argument(
        "--outdir",
        default=None,
        help="Output directory (default: scratch/version_audit_<timestamp>)",
    )
    parser.add_argument(
        "--repo",
        default=None,
        help="Repository path (default: current working directory)",
    )
    args = parser.parse_args()

    cwd = args.repo or os.getcwd()
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

    # Resolve refs
    base_sha = resolve_ref(args.base, cwd)
    head_sha = resolve_ref(args.head, cwd)
    print(f"[INFO] Base: {args.base} → {base_sha[:12]}")
    print(f"[INFO] Head: {args.head} → {head_sha[:12]}")

    if base_sha == head_sha:
        print("[WARN] Base and head resolve to the same commit. Nothing to diff.")
        sys.exit(0)

    # Create output directory
    if args.outdir:
        outdir = Path(args.outdir)
    else:
        outdir = Path(cwd) / "scratch" / f"version_audit_{timestamp}"
    outdir.mkdir(parents=True, exist_ok=True)

    # Generate filtered diff
    exclude_args = build_exclude_args()
    filtered_diff = run_git("diff", base_sha, head_sha, *exclude_args, cwd=cwd)

    diff_path = outdir / "filtered_diff.txt"
    diff_path.write_text(filtered_diff, encoding="utf-8")
    print(f"[INFO] Filtered diff written to {diff_path} ({len(filtered_diff):,} bytes)")

    # Generate name-status (also filtered)
    name_status = run_git(
        "diff", "--name-status", base_sha, head_sha, *exclude_args, cwd=cwd
    )

    # Parse and build manifest
    diff_stats = parse_diff_stats(filtered_diff)
    manifest = build_manifest(name_status, diff_stats)

    manifest_path = outdir / "change_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"[INFO] Change manifest written to {manifest_path}")

    # Group by module
    module_groups: dict[str, list[str]] = {}
    for filepath, info in sorted(manifest.items()):
        mod = info["module"]
        module_groups.setdefault(mod, []).append(filepath)

    # Build summary
    total_add = sum(v["additions"] for v in manifest.values())
    total_del = sum(v["deletions"] for v in manifest.values())
    total_files = len(manifest)

    summary_lines = [
        f"VERSION AUDIT SUMMARY — {timestamp}",
        f"Base: {args.base} ({base_sha[:12]})",
        f"Head: {args.head} ({head_sha[:12]})",
        "",
        f"Total files changed: {total_files}",
        f"Total additions: +{total_add}  deletions: -{total_del}",
        f"Estimated tokens: ~{(len(filtered_diff) // 4):,}",
        "",
        "By module:",
    ]

    for mod in sorted(module_groups.keys()):
        files = module_groups[mod]
        mod_add = sum(manifest[f]["additions"] for f in files)
        mod_del = sum(manifest[f]["deletions"] for f in files)
        summary_lines.append(f"  {mod:20s}  {len(files):3d} files  +{mod_add:5d} -{mod_del:5d}")
        for f in files:
            info = manifest[f]
            flag = {"ADDED": "+", "DELETED": "-", "MODIFIED": "M", "RENAMED": "R", "COPIED": "C"}.get(
                info["status"], "?"
            )
            summary_lines.append(
                f"    [{flag}] {f:50s}  +{info['additions']:4d} -{info['deletions']:4d}"
            )

    summary_text = "\n".join(summary_lines)
    summary_path = outdir / "summary.txt"
    summary_path.write_text(summary_text, encoding="utf-8")

    print(f"\n{summary_text}")
    print(f"\n[INFO] Summary written to {summary_path}")
    print(f"[DONE] All outputs in {outdir}")


if __name__ == "__main__":
    main()
