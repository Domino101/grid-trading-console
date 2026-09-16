# -*- coding: utf-8 -*-
"""CoinFull Prime universe-79 panel builder.

Reads the raw Binance Vision CSVs produced by fetch_universe79.py and writes:

  panel/close.csv            wide daily close (all symbols ever listed; NaN pre-listing)
  panel/open.csv  high.csv  low.csv  volume.csv
  panel/quote_volume.csv     wide daily quote volume (USDT) — universe ranking input
  panel/taker_buy_quote.csv  wide daily taker-buy quote volume (optional flow factor)
  panel/fund_daily.csv       wide daily funding (sum of 8h settlements, ffill<=2)
  panel/universe_mask.csv    bool: top-79 by t-1 quote volume, stablecoins excluded
  panel/universe_size.csv    daily universe size
  panel/top40_mask.csv       bool: top-40 (production gate universe)
  panel/meta.json            symbols, span, counts, stablecoin list
  derivatives/funding_all.csv  (timestamp_ms, symbol, funding_rate) — 19-coin loader compat
  macro/ (optional --macro)    fear_greed_daily.csv, fred_daily.csv

Rules:
  * daily bar day = UTC date of the candle open time
  * funding day = UTC date of fundingTime (same convention as the 19-coin panel)
  * universe mask is point-in-time (t-1 quote volume) — see universe79_lib.build_universe_mask

Usage:  python build_panel79.py [--top-n 79] [--macro]
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import urllib.request
from typing import Dict, List

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import universe79_lib as L  # noqa: E402

KLINES_COLS = ["open_time", "open", "high", "low", "close", "volume",
               "close_time", "quote_volume", "trades",
               "taker_buy_base_asset_volume", "taker_buy_quote_asset_volume", "ignore"]


# --------------------------------------------------------------------------- raw -> long frames
def load_klines_symbol(csv_paths: List[str]) -> "pd.DataFrame | None":
    """Concatenate one symbol's monthly Vision 1d klines CSVs -> daily bars (UTC)."""
    frames = []
    for p in sorted(csv_paths):
        df = pd.read_csv(p)
        if not df.shape[1] or list(df.columns)[:6] != KLINES_COLS[:6]:
            # tolerate header variants; identify by name where possible
            df.columns = [c.lower().strip() for c in df.columns]
        if "open_time" not in df.columns or "close" not in df.columns:
            continue
        frames.append(df)
    if not frames:
        return None
    d = pd.concat(frames, ignore_index=True)
    d["dt"] = pd.to_datetime(d["open_time"], unit="ms", utc=True).dt.tz_localize(None).dt.normalize()
    keep = [c for c in ["dt", "open", "high", "low", "close", "volume",
                        "quote_volume", "taker_buy_quote_asset_volume"] if c in d.columns]
    d = d[keep].drop_duplicates("dt").sort_values("dt").set_index("dt")
    d = d[~d.index.duplicated(keep="last")]
    return d.astype(float)


def load_funding_symbol(csv_paths: List[str], symbol: str) -> "pd.DataFrame | None":
    frames = []
    for p in sorted(csv_paths):
        df = pd.read_csv(p)
        df.columns = [c.lower().strip() for c in df.columns]
        time_col = next((c for c in ["calc_time", "fundingtime", "funding_time", "time", "timestamp"] if c in df.columns), None)
        rate_col = next((c for c in ["last_funding_rate", "fundingrate", "funding_rate", "rate"] if c in df.columns), None)
        if not time_col or not rate_col:
            continue
        sub = df[[time_col, rate_col]].rename(columns={time_col: "timestamp_ms", rate_col: "funding_rate"})
        frames.append(sub)
    if not frames:
        return None
    d = pd.concat(frames, ignore_index=True)
    d["symbol"] = symbol
    d = d.drop_duplicates(["timestamp_ms"]).sort_values("timestamp_ms")
    return d


# --------------------------------------------------------------------------- macro (optional)
def fetch_macro(macro_dir: str) -> None:
    os.makedirs(macro_dir, exist_ok=True)
    # Fear & Greed (alternative.me) — full history, one request
    fg_p = os.path.join(macro_dir, "fear_greed_daily.csv")
    if not os.path.isfile(fg_p):
        url = "https://api.alternative.me/fng/?limit=max"
        req = urllib.request.Request(url, headers={"User-Agent": "coinfull-research/1.0"})
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read().decode())
        fg = pd.DataFrame([{"date": int(x["value"] + x["timestamp"]), "value": x["value"]} for x in data["data"]])
        fg["date"] = pd.to_datetime(fg["date"], unit="s").dt.strftime("%Y-%m-%d")
        fg.to_csv(fg_p, index=False)
        print(f"macro: fear&greed {len(fg)} rows -> {fg_p}")
    # FRED: VIX, DGS10, DTWEXBGS, SP500
    fr_p = os.path.join(macro_dir, "fred_daily.csv")
    if not os.path.isfile(fr_p):
        series = {"vix": "VIX", "us_10y_yield_pct": "DGS10",
                  "trade_weighted_usd_index": "DTWEXBGS", "sp500_index": "SP500"}
        cols = {}
        for name, fid in series.items():
            url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={fid}&cosd=2020-06-01"
            req = urllib.request.Request(url, headers={"User-Agent": "coinfull-research/1.0"})
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    df = pd.read_csv(r)
                df.columns = [c.strip().lower() for c in df.columns]
                df["date"] = pd.to_datetime(df["date"])
                df = df.set_index("date")[[df.columns[1]]].rename(columns={df.columns[1]: name})
                df = df[df.index >= "2021-01-01"]
                cols[name] = df[name]
            except Exception as e:
                print(f"macro: FRED {fid} failed: {e}")
        if cols:
            fr = pd.concat(cols, axis=1).sort_index()
            fr.index = fr.index.strftime("%Y-%m-%d")
            fr.to_csv(fr_p)
            print(f"macro: fred {fr.shape} -> {fr_p}")


# --------------------------------------------------------------------------- main
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--top-n", type=int, default=L.TOP_N)
    ap.add_argument("--macro", action="store_true", help="fetch fear&greed + FRED (needs network)")
    ap.add_argument("--max-symbols", type=int, default=0, help="diagnostic: use first N symbols")
    args = ap.parse_args()

    root = L.data_root()
    kdir = os.path.join(root, "raw", "klines")
    fdir = os.path.join(root, "raw", "funding")
    if not os.path.isdir(kdir):
        print(f"no raw klines at {kdir} — run fetch_universe79.py first")
        return 1

    symbols = sorted(d for d in os.listdir(kdir) if os.path.isdir(os.path.join(kdir, d)))
    if args.max_symbols:
        symbols = symbols[: args.max_symbols]
    print(f"symbols: {len(symbols)}")

    # ---- klines
    wide: Dict[str, Dict[str, pd.Series]] = {k: {} for k in
                                              ["open", "high", "low", "close", "volume",
                                               "quote_volume", "taker_buy_quote"]}
    listing: Dict[str, "tuple[pd.Timestamp, pd.Timestamp]"] = {}
    n_kl_files = 0
    for sym in symbols:
        paths = glob.glob(os.path.join(kdir, sym, "*.csv"))
        paths = [p for p in paths if not p.endswith(".part")]
        d = load_klines_symbol(paths)
        if d is None or d.empty:
            continue
        n_kl_files += len(paths)
        listing[sym] = (d.index.min(), d.index.max())
        for k in wide:
            src = "taker_buy_quote_asset_volume" if k == "taker_buy_quote" else k
            if src in d.columns:
                wide[k][sym] = d[src].astype(float)
        print(f"  {sym}: {d.index.min().date()} .. {d.index.max().date()} ({len(d)}d, {len(paths)} files)")

    if not wide["close"]:
        print("no klines parsed — abort")
        return 1
    all_dates = sorted(set().union(*[set(s.dropna().index) for s in wide["close"].values()]))
    idx = pd.DatetimeIndex(all_dates)
    panels = {}
    for k, dd in wide.items():
        df = pd.DataFrame(dd, index=idx).sort_index()
        panels[k] = df

    close = panels["close"]
    qv = panels.get("quote_volume", close)
    print(f"\npanel: {close.shape}  {idx.min().date()} .. {idx.max().date()}")

    # ---- funding
    fund_rows = []
    n_fund_files = 0
    for sym in symbols:
        paths = [p for p in glob.glob(os.path.join(fdir, sym, "*.csv")) if not p.endswith(".part")]
        d = load_funding_symbol(paths, sym)
        if d is None or d.empty:
            continue
        n_fund_files += len(paths)
        fund_rows.append(d)
    if fund_rows:
        fund_all = pd.concat(fund_rows, ignore_index=True).sort_values(["timestamp_ms", "symbol"])
        fund_all = fund_all[["timestamp_ms", "symbol", "funding_rate"]]
        os.makedirs(os.path.join(root, "derivatives"), exist_ok=True)
        fund_all.to_csv(os.path.join(root, "derivatives", "funding_all.csv"), index=False)
        fd = fund_all.copy()
        fd["dt"] = pd.to_datetime(fd["timestamp_ms"], unit="ms", utc=True).dt.tz_localize(None).dt.normalize()
        fund_daily = fd.groupby(["dt", "symbol"])["funding_rate"].sum().unstack().reindex(idx).ffill(limit=2)
    else:
        fund_daily = pd.DataFrame(np.nan, index=idx, columns=close.columns)
        print("WARNING: no funding files parsed")
    fund_daily = fund_daily.reindex(columns=close.columns)

    # ---- universe mask (point-in-time top-N by t-1 quote volume, stablecoins out)
    mask, size = L.build_universe_mask(qv, top_n=args.top_n, exclude=L.STABLECOINS)
    m40, _ = L.build_universe_mask(qv, top_n=L.GATE_TOP_N, exclude=L.STABLECOINS)

    # ---- write
    pdir = os.path.join(root, "panel")
    os.makedirs(pdir, exist_ok=True)

    def _w(name, df):
        df.to_csv(os.path.join(pdir, name))

    _w("close.csv", close)
    for k in ["open", "high", "low", "volume", "quote_volume", "taker_buy_quote"]:
        if k in panels:
            _w(f"{k}.csv", panels[k])
    _w("fund_daily.csv", fund_daily)
    _w("universe_mask.csv", mask.astype(int))
    _w("universe_size.csv", size.to_frame("size"))
    _w("top40_mask.csv", m40.astype(int))

    # ---- diagnostics
    size_by_year = size.groupby(size.index.year).agg(["min", "median", "max", "count"])
    delisted = {s: (lo, hi) for s, (lo, hi) in listing.items() if hi < idx.max() - pd.Timedelta(days=30)}
    new_2021 = {s: (lo, hi) for s, (lo, hi) in listing.items() if lo >= pd.Timestamp("2021-01-01") - pd.Timedelta(days=10)}
    meta = {
        "start": str(idx.min().date()), "end": str(idx.max().date()),
        "n_symbols": len(listing), "n_days": len(idx),
        "n_klines_files": n_kl_files, "n_funding_files": n_fund_files,
        "top_n": args.top_n, "gate_top_n": L.GATE_TOP_N,
        "stablecoins_excluded": sorted(L.STABLECOINS),
        "universe_size_by_year": {int(y): {k: (float(v) if pd.notna(v) else None) for k, v in row.items()}
                                   for y, row in size_by_year.iterrows()},
        "delisted_symbols": {s: [str(lo.date()), str(hi.date())] for s, (lo, hi) in sorted(delisted.items())},
        "listed_from_2021": {s: [str(lo.date()), str(hi.date())] for s, (lo, hi) in sorted(new_2021.items())},
    }
    json.dump(meta, open(os.path.join(pdir, "meta.json"), "w"), indent=1)

    print("\n=== universe size by year ===")
    print(size_by_year.round(1).to_string())
    print(f"\ndelisted ({len(delisted)}): {', '.join(f'{s} ({lo.date()}..{hi.date()})' for s, (lo, hi) in sorted(delisted.items(), key=lambda x: x[1][1]))[:2000]}")
    print(f"first-listed in 2021+ ({len(new_2021)}): {len(new_2021)} symbols")
    cov = close.notna().sum(axis=0)
    print(f"coverage: median {cov.median():.0f}/{len(idx)} days per symbol; "
          f"funding coverage {fund_daily.notna().mean().mean()*100:.1f}% (universe cells)")

    if args.macro:
        fetch_macro(os.path.join(root, "macro"))

    print(f"\npanel written to {pdir}")
    print("next: python exp15_universe79.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
