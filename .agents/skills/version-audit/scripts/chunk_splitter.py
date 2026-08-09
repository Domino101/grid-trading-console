#!/usr/bin/env python3
"""
chunk_splitter.py — Extract diff chunks for specific files/modules.

Reads a filtered_diff.txt produced by diff_preprocessor.py and extracts
only the diff sections relevant to the specified files or module group.

This keeps each audit chunk small and focused, preventing context overflow.

Usage:
    # Extract diff for specific files
    python chunk_splitter.py --diff scratch/version_audit_xxx/filtered_diff.txt \
        --module bot/execution/engine.py bot/risk_engine.py

    # Extract by module name (uses change_manifest.json)
    python chunk_splitter.py --diff scratch/version_audit_xxx/filtered_diff.txt \
        --by-module EXECUTION

    # Extract by directory prefix
    python chunk_splitter.py --diff scratch/version_audit_xxx/filtered_diff.txt \
        --prefix bot/strategies/

    # List available modules from manifest
    python chunk_splitter.py --manifest scratch/version_audit_xxx/change_manifest.json \
        --list-modules
"""

import argparse
import json
import re
import sys
from pathlib import Path


def split_diff_by_file(diff_text: str) -> dict[str, str]:
    """Split a unified diff into per-file sections.

    Returns a dict mapping filepath -> diff section text.
    """
    sections: dict[str, str] = {}
    current_file: str | None = None
    current_lines: list[str] = []

    for line in diff_text.split("\n"):
        if line.startswith("diff --git"):
            # Save previous section
            if current_file is not None:
                sections[current_file] = "\n".join(current_lines)

            # Extract b-path
            match = re.search(r"b/(.+)$", line)
            current_file = match.group(1) if match else None
            current_lines = [line]
        else:
            current_lines.append(line)

    # Save last section
    if current_file is not None:
        sections[current_file] = "\n".join(current_lines)

    return sections


def extract_files(
    sections: dict[str, str], file_paths: list[str]
) -> dict[str, str]:
    """Extract diff sections for specific file paths."""
    result: dict[str, str] = {}
    for fp in file_paths:
        if fp in sections:
            result[fp] = sections[fp]
        else:
            # Try partial match
            for key in sections:
                if key.endswith(fp) or fp in key:
                    result[key] = sections[key]
    return result


def extract_by_prefix(sections: dict[str, str], prefix: str) -> dict[str, str]:
    """Extract diff sections for files matching a directory prefix."""
    return {k: v for k, v in sections.items() if k.startswith(prefix)}


def extract_by_module(
    sections: dict[str, str], manifest: dict[str, dict], module_name: str
) -> dict[str, str]:
    """Extract diff sections for files belonging to a module group."""
    module_files = {
        fp for fp, info in manifest.items() if info.get("module") == module_name
    }
    return {k: v for k, v in sections.items() if k in module_files}


def estimate_tokens(text: str) -> int:
    """Rough token estimate: ~4 characters per token."""
    return len(text) // 4


def format_chunk_header(file_path: str, diff_text: str) -> str:
    """Format a chunk header with file info."""
    additions = sum(1 for l in diff_text.split("\n") if l.startswith("+") and not l.startswith("+++"))
    deletions = sum(1 for l in diff_text.split("\n") if l.startswith("-") and not l.startswith("---"))
    tokens = estimate_tokens(diff_text)
    return (
        f"\n{'='*70}\n"
        f"FILE: {file_path}\n"
        f"Changes: +{additions} -{deletions}  (~{tokens:,} tokens)\n"
        f"{'='*70}\n"
    )


def main() -> None:
    """Entry point: extract and print diff chunks for specified files."""
    parser = argparse.ArgumentParser(
        description="Extract diff chunks by file/module for token-efficient auditing."
    )
    parser.add_argument("--diff", required=True, help="Path to filtered_diff.txt")
    parser.add_argument(
        "--module",
        nargs="+",
        default=[],
        help="Specific file paths to extract (e.g. bot/execution/engine.py bot/risk_engine.py)",
    )
    parser.add_argument(
        "--prefix",
        default=None,
        help="Directory prefix to filter (e.g. bot/strategies/)",
    )
    parser.add_argument(
        "--by-module",
        default=None,
        help="Module group name from manifest (e.g. EXECUTION, STRATEGY)",
    )
    parser.add_argument(
        "--manifest",
        default=None,
        help="Path to change_manifest.json (needed for --by-module and --list-modules)",
    )
    parser.add_argument(
        "--list-modules",
        action="store_true",
        help="List available module groups from manifest",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Write chunk to file instead of stdout",
    )
    args = parser.parse_args()

    # Validate inputs
    diff_path = Path(args.diff)
    if not diff_path.exists():
        print(f"[ERROR] Diff file not found: {diff_path}", file=sys.stderr)
        sys.exit(1)

    diff_text = diff_path.read_text(encoding="utf-8")
    sections = split_diff_by_file(diff_text)

    # Handle --list-modules
    if args.list_modules:
        if not args.manifest:
            print("[ERROR] --manifest is required with --list-modules", file=sys.stderr)
            sys.exit(1)

        manifest_path = Path(args.manifest)
        if not manifest_path.exists():
            print(f"[ERROR] Manifest not found: {manifest_path}", file=sys.stderr)
            sys.exit(1)

        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        modules: dict[str, list[str]] = {}
        for fp, info in sorted(manifest.items()):
            mod = info.get("module", "OTHER")
            modules.setdefault(mod, []).append(fp)

        print("Available modules in manifest:\n")
        for mod in sorted(modules.keys()):
            files = modules[mod]
            print(f"  {mod:20s}  ({len(files)} files)")
            for f in files:
                print(f"    - {f}")
        return

    # Determine which files to extract
    selected: dict[str, str] = {}

    if args.module:
        selected = extract_files(sections, args.module)
    elif args.prefix:
        selected = extract_by_prefix(sections, args.prefix)
    elif args.by_module:
        if not args.manifest:
            # Try to find manifest next to diff file
            auto_manifest = diff_path.parent / "change_manifest.json"
            if auto_manifest.exists():
                args.manifest = str(auto_manifest)
            else:
                print("[ERROR] --manifest is required with --by-module", file=sys.stderr)
                sys.exit(1)

        manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
        selected = extract_by_module(sections, manifest, args.by_module)
    else:
        print("[ERROR] Specify --module, --prefix, or --by-module", file=sys.stderr)
        sys.exit(1)

    if not selected:
        print("[WARN] No matching files found in diff.")
        sys.exit(0)

    # Build output
    output_lines: list[str] = []
    total_chars = 0

    output_lines.append(
        f"DIFF CHUNK — {len(selected)} file(s)  |  "
        f"Total ~{estimate_tokens(''.join(selected.values())):,} tokens"
    )
    output_lines.append(f"Source: {diff_path}")
    output_lines.append("")

    for fp in sorted(selected.keys()):
        chunk = selected[fp]
        header = format_chunk_header(fp, chunk)
        output_lines.append(header)
        output_lines.append(chunk)
        total_chars += len(chunk)

    result_text = "\n".join(output_lines)

    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(result_text, encoding="utf-8")
        print(f"[INFO] Chunk written to {out_path} ({total_chars:,} chars, ~{estimate_tokens(result_text):,} tokens)")
    else:
        print(result_text)

    # Print size warning
    tokens = estimate_tokens(result_text)
    if tokens > 8000:
        print(
            f"\n[WARN] This chunk is ~{tokens:,} tokens. "
            f"Consider splitting further to stay under 8K tokens per chunk.",
            file=sys.stderr,
        )


if __name__ == "__main__":
    main()
