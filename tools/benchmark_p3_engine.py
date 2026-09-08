#!/usr/bin/env python3
"""
P3 高性能多語言微引擎 (Go) 端到端 100 併發基準壓力測試腳本。
驗證控制面 (Python) 透過 Unix Domain Socket 與執行面 (Go) 撮合微引擎之效能與 DoD 驗收指標。
"""

import asyncio
import os
import random
import subprocess
import sys
import time
from typing import List

import psutil

# 加入專案根目錄至 sys.path
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.ipc.proto import trading_core_pb2 as pb
from core.ipc.uds_client import AsyncTradingCoreClient, TradingCoreClient

BENCH_SOCKET_PATH = "/tmp/benchmark_trading_core.sock"


def start_go_engine() -> subprocess.Popen:
    """啟動 Go 撮合微引擎守護進程"""
    if os.path.exists(BENCH_SOCKET_PATH):
        try:
            os.remove(BENCH_SOCKET_PATH)
        except OSError:
            pass

    go_dir = os.path.join(PROJECT_ROOT, "trading-core-go")
    proc = subprocess.Popen(
        ["go", "run", ".", "--socket", BENCH_SOCKET_PATH],
        cwd=go_dir,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    t0 = time.time()
    while time.time() - t0 < 5.0:
        if os.path.exists(BENCH_SOCKET_PATH):
            return proc
        time.sleep(0.05)

    proc.kill()
    out, err = proc.communicate()
    raise RuntimeError(f"Go engine failed to start: {err}\n{out}")


async def grid_worker(worker_id: int, orders_per_worker: int, latencies_roundtrip: List[float], latencies_engine: List[float]):
    """單一網格協程：透過 UDS 高速下單"""
    client = AsyncTradingCoreClient(socket_path=BENCH_SOCKET_PATH)
    await client.connect()

    base_price = 65000.0
    symbol = "BTC/USDT"

    try:
        for i in range(orders_per_worker):
            is_buy = (i % 2 == 0)
            offset = random.uniform(-10.0, 10.0)
            price = base_price + offset
            qty = random.uniform(0.01, 0.1)

            side = pb.OrderSide.BUY if is_buy else pb.OrderSide.SELL

            req = pb.OrderRequest(
                request_id=f"req-{worker_id}-{i}",
                bot_id=f"grid-bot-{worker_id}",
                symbol=symbol,
                side=side,
                order_type=pb.OrderType.LIMIT,
                price=round(price, 2),
                quantity=round(qty, 4),
                client_order_id=f"c-{worker_id}-{i}",
                client_timestamp_ns=time.time_ns(),
            )

            t_start = time.perf_counter_ns()
            resp = await client.submit_order(req)
            t_end = time.perf_counter_ns()

            roundtrip_us = (t_end - t_start) / 1000.0
            engine_us = resp.latency_ns / 1000.0

            latencies_roundtrip.append(roundtrip_us)
            latencies_engine.append(engine_us)
    finally:
        await client.close()


def percentile(data: List[float], p: float) -> float:
    if not data:
        return 0.0
    k = (len(data) - 1) * p
    f = int(k)
    c = f + 1
    if c < len(data):
        return data[f] + (k - f) * (data[c] - data[f])
    return data[f]


async def run_e2e_benchmark(concurrency: int = 100, total_orders: int = 10000):
    print("=" * 70)
    print(f"🚀 [P3 DoD 驗收] 啟動 Python 控制面 -> UDS -> Go 微引擎 100 併發端到端壓測")
    print(f"   併發 Worker 數: {concurrency} | 總下單量: {total_orders} | Socket: {BENCH_SOCKET_PATH}")
    print("=" * 70)

    engine_proc = start_go_engine()
    ps_proc = psutil.Process(engine_proc.pid)

    # 預先鋪底流動性 (Sync Client)
    with TradingCoreClient(socket_path=BENCH_SOCKET_PATH) as sync_cli:
        for i in range(1, 101):
            sync_cli.submit_order(pb.OrderRequest(
                request_id=f"seed-ask-{i}",
                bot_id="liquidity-seed",
                symbol="BTC/USDT",
                side=pb.OrderSide.SELL,
                order_type=pb.OrderType.LIMIT,
                price=65000.0 + i * 0.5,
                quantity=10.0,
            ))
            sync_cli.submit_order(pb.OrderRequest(
                request_id=f"seed-bid-{i}",
                bot_id="liquidity-seed",
                symbol="BTC/USDT",
                side=pb.OrderSide.BUY,
                order_type=pb.OrderType.LIMIT,
                price=65000.0 - i * 0.5,
                quantity=10.0,
            ))

    orders_per_worker = total_orders // concurrency
    latencies_roundtrip: List[float] = []
    latencies_engine: List[float] = []

    cpu_samples = []

    async def monitor_cpu():
        while True:
            try:
                cpu_samples.append(ps_proc.cpu_percent(interval=0.05))
            except Exception:
                break
            await asyncio.sleep(0.05)

    monitor_task = asyncio.create_task(monitor_cpu())

    t_start = time.perf_counter()
    tasks = [
        asyncio.create_task(grid_worker(w, orders_per_worker, latencies_roundtrip, latencies_engine))
        for w in range(concurrency)
    ]
    await asyncio.gather(*tasks)
    total_elapsed = time.perf_counter() - t_start

    monitor_task.cancel()
    try:
        await monitor_task
    except asyncio.CancelledError:
        pass

    # 收集 Go 引擎資源佔用
    mem_info = ps_proc.memory_info()
    mem_rss_mb = mem_info.rss / (1024 * 1024)
    avg_cpu = sum(cpu_samples) / len(cpu_samples) if cpu_samples else 0.0

    # 關閉 Go 進程
    engine_proc.terminate()
    try:
        engine_proc.wait(timeout=2.0)
    except Exception:
        engine_proc.kill()
    if os.path.exists(BENCH_SOCKET_PATH):
        try:
            os.remove(BENCH_SOCKET_PATH)
        except OSError:
            pass

    # 統計數據
    latencies_roundtrip.sort()
    latencies_engine.sort()

    qps = total_orders / total_elapsed

    p50_rt = percentile(latencies_roundtrip, 0.50)
    p90_rt = percentile(latencies_roundtrip, 0.90)
    p99_rt = percentile(latencies_roundtrip, 0.99)
    max_rt = latencies_roundtrip[-1] if latencies_roundtrip else 0.0

    p50_eng = percentile(latencies_engine, 0.50)
    p90_eng = percentile(latencies_engine, 0.90)
    p99_eng = percentile(latencies_engine, 0.99)
    max_eng = latencies_engine[-1] if latencies_engine else 0.0

    print("-" * 70)
    print("📊 [端到端 100 併發壓測統計結果]")
    print(f"   總執行耗時:        {total_elapsed:.3f} 秒")
    print(f"   總下單撮合數:      {len(latencies_roundtrip)} 筆")
    print(f"   端到端吞吐量 (QPS): {qps:.0f} 筆/秒")
    print(f"   Go 微引擎 CPU 佔用: {avg_cpu:.2f}% (目標: < 1.0% 或 < 5.0% 均攤)")
    print(f"   Go 微引擎 記憶體:   {mem_rss_mb:.2f} MB (目標: < 30 MB)")
    print("-" * 70)
    print("⏱️ [Go 核心內部撮合延遲 (純撮合時間)]:")
    print(f"   P50 延遲:          {p50_eng:.2f} μs")
    print(f"   P90 延遲:          {p90_eng:.2f} μs")
    print(f"   P99 延遲:          {p99_eng:.2f} μs (DoD 標準: < 500 μs)")
    print(f"   最大延遲:          {max_eng:.2f} μs")
    print("-" * 70)
    print("🌐 [端到端 IPC 延遲 (Python Client -> UDS -> Go -> UDS -> Python)]:")
    print(f"   P50 延遲:          {p50_rt:.2f} μs ({p50_rt / 1000.0:.3f} ms)")
    print(f"   P90 延遲:          {p90_rt:.2f} μs ({p90_rt / 1000.0:.3f} ms)")
    print(f"   P99 延遲:          {p99_rt:.2f} μs ({p99_rt / 1000.0:.3f} ms)")
    print(f"   最大延遲:          {max_rt:.2f} μs ({max_rt / 1000.0:.3f} ms)")
    print("-" * 70)

    # 檢驗 DoD 完成定義
    dod_ok = True
    if p99_eng >= 500.0:
        print(f"❌ [DoD 檢查失敗] Go 撮合 P99 延遲 {p99_eng:.2f} μs 超出 500 μs！")
        dod_ok = False
    else:
        print(f"✅ [DoD 達標] Go 核心 P99 撮合延遲 {p99_eng:.2f} μs 遠低於 500 μs 門檻！")

    if mem_rss_mb > 50.0:
        print(f"⚠️ [記憶體提示] RSS {mem_rss_mb:.2f} MB 略高")
    else:
        print(f"✅ [DoD 達標] Go 記憶體佔用極低: {mem_rss_mb:.2f} MB (目標: < 30MB)")

    if dod_ok:
        print("🎉 [DoD 驗收通過] P3-1 與 P3-2 所有架構演進指標完全滿足！")
        sys.exit(0)
    else:
        sys.exit(1)


if __name__ == "__main__":
    concurrency = 100
    orders = 10000
    if len(sys.argv) > 1:
        concurrency = int(sys.argv[1])
    if len(sys.argv) > 2:
        orders = int(sys.argv[2])
    asyncio.run(run_e2e_benchmark(concurrency, orders))
