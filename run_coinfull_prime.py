#!/usr/bin/env python3
"""
CoinFull Prime（市場中性截面多空 Alpha 策略）獨立執行腳本

用法：
    # 1. 執行單次即時計算與預覽 (Dry-Run 模式，不發單)
    python3 run_coinfull_prime.py --dry-run --once

    # 2. 以常駐守護進程模式運行 (每週四 UTC 00:05 自動喚醒換倉，含風險閘門判定)
    python3 run_coinfull_prime.py --daemon

    # 3. 指定自定義設定檔
    python3 run_coinfull_prime.py --config config/coinfull_prime/strategy_config.yaml
"""

import argparse
import asyncio
import logging
import sys
import os

# 確保專案根目錄在 sys.path
_project_root = os.path.abspath(os.path.dirname(__file__))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from core.services.coinfull_prime.service import CoinFullPrimeService

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("CoinFullPrime.CLI")


def print_rankings_table(rank_result):
    """終端美化輸出 Top 8 多與 Bottom 8 空"""
    print("\n" + "=" * 80)
    print(" 🚀 CoinFull Prime 每日截面打分排名結果")
    print(f" 時間戳: {rank_result.timestamp} | 母體幣種數: {rank_result.universe_size}")
    print("=" * 80)

    print("\n🟢 【Top 8 多頭籃子 (Long Basket - 等權各佔 12.5% 名義本金)】")
    print("-" * 80)
    print(f"{'排名':<6} {'幣種':<12} {'綜合得分':<10} {'動能 Z':<8} {'資金流 Z':<10} {'費率 Z':<8} {'大戶 Z':<8} {'目標金額':<10}")
    print("-" * 80)
    for item in rank_result.long_list:
        sig = item.signals
        print(
            f"{item.rank:<6} {item.symbol:<12} {item.score:<10.4f} "
            f"{sig.mom_z:<8.2f} {sig.flow_z:<10.2f} {sig.fund_z:<8.2f} {sig.liq_z:<8.2f} "
            f"${item.target_notional:<10.1f}"
        )

    print("\n🔴 【Bottom 8 空頭籃子 (Short Basket - 等權各佔 12.5% 名義本金)】")
    print("-" * 80)
    print(f"{'排名':<6} {'幣種':<12} {'綜合得分':<10} {'動能 Z':<8} {'資金流 Z':<10} {'費率 Z':<8} {'大戶 Z':<8} {'目標金額':<10}")
    print("-" * 80)
    for item in rank_result.short_list:
        sig = item.signals
        print(
            f"{item.rank:<6} {item.symbol:<12} {item.score:<10.4f} "
            f"{sig.mom_z:<8.2f} {sig.flow_z:<10.2f} {sig.fund_z:<8.2f} {sig.liq_z:<8.2f} "
            f"${item.target_notional:<10.1f}"
        )
    print("=" * 80 + "\n")


async def main():
    parser = argparse.ArgumentParser(description="CoinFull Prime 市場中性策略執行器")
    parser.add_argument("--config", type=str, default=None, help="YAML 設定檔路徑")
    parser.add_argument("--dry-run", action="store_true", default=False, help="強制以 Dry-Run 模式運行（不發真實訂單）")
    parser.add_argument("--once", action="store_true", default=False, help="僅執行一次即退出")
    parser.add_argument("--daemon", action="store_true", default=False, help="作為常駐服務運行（每日 UTC 00:00 自動換倉）")

    args = parser.parse_args()

    service = CoinFullPrimeService(config_path=args.config)
    await service.initialize()

    # 如果指定 --dry-run，強制設定
    dry_run = True if args.dry_run else None

    if args.once or not args.daemon:
        logger.info("執行單次 CoinFull Prime 量化計算...")
        rank_result, rebalance_report = await service.trigger_once(dry_run=dry_run)
        print_rankings_table(rank_result)

        print("📋 【換倉訂單明細】")
        for o in rebalance_report.orders:
            flag = "🛑 平倉" if o.reduce_only else "🟢 開倉/調倉"
            print(f" - {flag} {o.symbol:<10} {o.side.upper():<4} {o.amount:.4f} 顆 (${o.notional_usdt:.1f} USDT) [{o.reason}] -> 狀態: {o.status}")
        print("\n完成！")
    else:
        logger.info("以常駐守護進程模式啟動 CoinFull Prime...")
        service.start_scheduler()
        try:
            while True:
                await asyncio.sleep(3600)
        except (KeyboardInterrupt, asyncio.CancelledError):
            logger.info("收到退出訊號，正在關閉排程...")
            service.stop_scheduler()


if __name__ == "__main__":
    asyncio.run(main())
