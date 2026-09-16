# -*- coding: utf-8 -*-
"""CoinFull Prime universe-79 data fetcher (Binance Vision, USDT-M futures).

Downloads, per symbol and month (2021-01 .. 2026-07 by default):
  * daily 1d klines   https://data.binance.vision/data/futures/um/daily/klines/{SYM}/1d/{SYM}-1d-{YYYY-MM}.zip
  * monthly funding   https://data.binance.vision/data/futures/um/monthly/fundingRate/{SYM}/{SYM}-fundingRate-{YYYY-MM}.zip

Features: symbol discovery incl. delisted symbols (directory listing), concurrent
downloads, 3 retries with backoff, 404 = not-listed-that-month (recorded, not an error),
resume (existing complete files are skipped), atomic manifest, progress + ETA.

Usage:
  python fetch_universe79.py                     # full run
  python fetch_universe79.py --limit 20          # smoke test
  python fetch_universe79.py --workers 16        # faster on good links
  python fetch_universe79.py --funding-only      # refetch funding only
Data lands in $COINFULL_U79_DATA_DIR (default ~/.datacache/coinfull_universe79):
  raw/klines/{SYM}/{YYYY-MM}.zip|.csv
  raw/funding/{SYM}/{YYYY-MM}.zip|.csv
  raw/_symbols.json    raw/manifest.json
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import csv
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
import zipfile
from typing import Dict, List

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import universe79_lib as L  # noqa: E402

UA = "Mozilla/5.0 (compatible; coinfull-research/1.0; +local-backtest)"
MANIFEST_INTERVAL = 500


# --------------------------------------------------------------------------- http
from urllib.parse import quote, urlsplit, urlunsplit


def http_get(url: str, timeout: float = 30.0, retries: int = 3) -> "tuple[int, bytes | None, str]":
    """GET with retries. Returns (status, body, note). status 404 = not found (no retry)."""
    parts = urlsplit(url)
    safe_path = quote(parts.path, safe="/:")
    safe_url = urlunsplit((parts.scheme, parts.netloc, safe_path, parts.query, parts.fragment))
    last = ""
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(safe_url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, r.read(), ""
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return 404, None, "404"
            last = f"HTTP {e.code}"
        except Exception as e:  # URLError, timeout, TLS EOF...
            last = f"{type(e).__name__}: {e}"
        if attempt < retries:
            time.sleep(1.5 * (2 ** attempt))
    return 0, None, last


# --------------------------------------------------------------------------- tasks
def discover_symbols(timeout: float = 30.0) -> List[str]:
    """Every USDT-M perp that ever had Vision daily klines data (incl. delisted)."""
    status, body, note = http_get(L.symbols_listing_url(), timeout=timeout)
    if status != 200 or not body:
        raise RuntimeError(f"symbol discovery failed: {note}")
    syms = [s for s in json.loads(body.decode("utf-8")) if isinstance(s, str)]
    return sorted(syms)


def _csv_complete(path: str) -> bool:
    if not os.path.isfile(path):
        return False
    try:
        with open(path, "r", encoding="utf-8") as f:
            r = csv.reader(f)
            header = next(r, None)
            if header is None:
                return False
            rows = sum(1 for _ in r)
        return rows >= 1
    except Exception:
        return False


def _extract(zip_path: str, csv_path: str) -> int:
    with zipfile.ZipFile(zip_path) as z:
        names = [n for n in z.namelist() if n.lower().endswith(".csv")]
        if not names:
            raise ValueError(f"no csv inside {zip_path}")
        with z.open(names[0]) as src, open(csv_path, "wb") as dst:
            dst.write(src.read())
    with open(csv_path, "r", encoding="utf-8") as f:
        return sum(1 for _ in f) - 1


def _fetch_one(task: Dict[str, str], timeout: float, retries: int) -> Dict[str, str]:
    if task.get("cached_status") == "404":
        return {**task, "status": "404"}
    zip_p, csv_p = task["zip"], task["csv"]
    if _csv_complete(csv_p):
        return {**task, "status": "skip"}
    os.makedirs(os.path.dirname(zip_p), exist_ok=True)
    status, body, note = http_get(task["url"], timeout=timeout, retries=retries)
    if status == 404:
        if os.path.exists(zip_p):
            os.remove(zip_p)
        return {**task, "status": "404"}
    if status != 200 or body is None:
        return {**task, "status": f"error:{note[:80]}"}
    tmp = zip_p + ".part"
    with open(tmp, "wb") as f:
        f.write(body)
    os.replace(tmp, zip_p)
    try:
        rows = _extract(zip_p, csv_p)
    except Exception as e:
        return {**task, "status": f"error:extract:{e}"}
    if rows < 1:
        return {**task, "status": "error:empty"}
    return {**task, "status": "ok"}


# --------------------------------------------------------------------------- main
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--start", default=L.START_MONTH)
    ap.add_argument("--end", default=L.END_MONTH)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--timeout", type=float, default=30.0)
    ap.add_argument("--retries", type=int, default=3)
    ap.add_argument("--limit", type=int, default=0, help="stop after N tasks (smoke test)")
    ap.add_argument("--funding-only", action="store_true")
    ap.add_argument("--klines-only", action="store_true")
    ap.add_argument("--symbols-file", default="", help="one SYMBOL per line (skip discovery)")
    args = ap.parse_args()

    root = L.data_root()
    raw = os.path.join(root, "raw")
    os.makedirs(raw, exist_ok=True)
    kinds = ("funding",) if args.funding_only else ("klines",) if args.klines_only else ("klines", "funding")
    months = L.month_range(args.start, args.end)

    sym_file = os.path.join(raw, "_symbols.json")
    if args.symbols_file:
        with open(args.symbols_file) as f:
            symbols = [ln.strip().upper() for ln in f if ln.strip()]
    elif os.path.isfile(sym_file):
        symbols = json.loads(open(sym_file).read())
        print(f"symbols: {len(symbols)} (cached {sym_file})")
    else:
        print("discovering symbols (incl. delisted) ...")
        symbols = discover_symbols(timeout=args.timeout)
        json.dump(symbols, open(sym_file, "w"))
        print(f"symbols: {len(symbols)} -> {sym_file}")

    tasks = L.plan_tasks(symbols, months, root, kinds=kinds)
    if args.limit:
        tasks = tasks[: args.limit]
    print(f"months: {months[0]} .. {months[-1]} ({len(months)})   kinds: {kinds}   tasks: {len(tasks)}")
    print(f"root: {root}")

    # resume manifest (previous run state)
    mani_p = os.path.join(raw, "manifest.json")
    manifest: Dict[str, str] = {}
    if os.path.isfile(mani_p):
        try:
            manifest = json.loads(open(mani_p).read())
            manifest = {k: v for k, v in manifest.items() if not str(v).startswith("error")}
        except Exception:
            manifest = {}
    for t in tasks:
        key = f"{t['kind']}:{t['symbol']}:{t['month']}"
        if manifest.get(key) == "404":
            t["cached_status"] = "404"
    lock = threading.Lock()
    stats = {"ok": 0, "skip": 0, "404": 0, "error": 0}
    t0 = time.time()
    done = 0

    with cf.ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(_fetch_one, t, args.timeout, args.retries): t for t in tasks}
        for fut in cf.as_completed(futs):
            res = fut.result()
            done += 1
            with lock:
                key = f"{res['kind']}:{res['symbol']}:{res['month']}"
                manifest[key] = res["status"]
                if res["status"] == "ok":
                    stats["ok"] += 1
                elif res["status"] == "skip":
                    stats["skip"] += 1
                elif res["status"] == "404":
                    stats["404"] += 1
                else:
                    stats["error"] += 1
                if stats["error"] and "first_error" not in manifest:
                    manifest["first_error"] = res["status"]
                if done % 200 == 0:
                    el = time.time() - t0
                    eta = el / done * (len(tasks) - done)
                    print(f"  {done}/{len(tasks)}  ok={stats['ok']} skip={stats['skip']} "
                          f"404={stats['404']} err={stats['error']}  eta={eta/60:.1f}min", flush=True)
                if done % MANIFEST_INTERVAL == 0:
                    tmp = mani_p + ".tmp"
                    json.dump(manifest, open(tmp, "w"))
                    os.replace(tmp, mani_p)

    tmp = mani_p + ".tmp"
    json.dump(manifest, open(tmp, "w"))
    os.replace(tmp, mani_p)
    el = time.time() - t0
    print(f"\ndone in {el/60:.1f}min  ok={stats['ok']} skip={stats['skip']} 404={stats['404']} error={stats['error']}")
    if stats["error"]:
        errs = [k for k, v in manifest.items() if v.startswith("error")]
        print(f"WARNING: {len(errs)} errored tasks (rerun to retry). sample: {errs[:5]}")
        return 1
    print(f"manifest: {mani_p}")
    print("next: python build_panel79.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
