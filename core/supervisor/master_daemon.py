#!/usr/bin/env python3
"""
Master Supervisor — 多進程監督者守護程式 (P1-3)

負責管理所有策略 Worker 子進程的生命週期、存活健康檢查、異常退出捕獲、
指數退避自癒重啟 (Exponential Backoff Auto-healing)，以及接收即時 Redis 指令與檔案對齊。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

# 專案根目錄
project_root = Path(__file__).resolve().parent.parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

try:
    from dotenv import load_dotenv
    load_dotenv(project_root / ".env", override=False)
except ImportError:
    pass

from core.adapters.exchanges.utils import setup_optimized_logging
from core.logging import get_system_logger
from core.infrastructure.database.redis_support import RedisCommandQueue, redis_enabled
from web_ui.backend.control_plane import publish_lifecycle
from web_ui.backend.lifecycle import account_mode_from_yaml, live_trading_enabled, start_rejection

logger = get_system_logger()


@dataclass
class WorkerInfo:
    config_path: str
    bot_type: str
    process: Optional[subprocess.Popen] = None
    started_at: float = 0.0
    restarts: int = 0
    last_crash_at: float = 0.0
    manually_stopped: bool = False
    next_retry_at: float = 0.0

    @property
    def is_alive(self) -> bool:
        if self.process is None:
            return False
        return self.process.poll() is None

    @property
    def pid(self) -> Optional[int]:
        if self.process is None:
            return None
        return self.process.pid


class MasterSupervisor:
    """Master 進程監督者，管理獨立子進程 Worker 池。"""

    def __init__(self, debug: bool = False, python_bin: Optional[str] = None):
        self.debug = debug
        self.python_bin = python_bin or sys.executable
        self.workers: Dict[str, WorkerInfo] = {}
        self.running = True
        self.command_queue = RedisCommandQueue() if redis_enabled() else None
        self.consumer_id = f"master_{os.getpid()}"
        self.active_grids_file = project_root / "config" / "active_grids.json"
        self.active_dca_file = project_root / "config" / "active_dca.json"

    def _abs_path(self, rel_or_abs: str) -> Path:
        p = Path(rel_or_abs)
        if not p.is_absolute():
            p = project_root / p
        return p

    def start_worker(self, config_path: str, bot_type: str) -> bool:
        """啟動或重啟指定策略的 Worker 子進程。"""
        abs_p = self._abs_path(config_path)
        if not abs_p.exists():
            logger.error(f"❌ [Master] 找不到策略配置檔案: {config_path}")
            publish_lifecycle(config_path, "failed", detail=f"配置不存在: {config_path}")
            return False

        worker = self.workers.get(config_path)
        if worker and worker.is_alive:
            logger.info(f"ℹ️ [Master] Worker 已在運行中 (PID={worker.pid}): {config_path}")
            return True

        if worker is None:
            worker = WorkerInfo(config_path=config_path, bot_type=bot_type)
            self.workers[config_path] = worker

        worker.manually_stopped = False

        cmd = [
            self.python_bin,
            "-m",
            "core.supervisor.worker_runner",
            "--config",
            config_path,
            "--type",
            bot_type,
        ]
        if self.debug:
            cmd.append("--debug")

        try:
            # 啟動獨立子進程，不共用標準輸入，保留 stdout/stderr
            proc = subprocess.Popen(
                cmd,
                cwd=str(project_root),
                stdin=subprocess.DEVNULL,
                env=os.environ.copy(),
            )
            worker.process = proc
            worker.started_at = time.time()
            worker.next_retry_at = 0.0
            logger.info(f"🚀 [Master] 已啟動 Worker 子進程 (PID={proc.pid}) -> {config_path} [{bot_type}]")
            return True
        except Exception as e:
            logger.error(f"❌ [Master] 啟動 Worker 子進程失敗: {e}", exc_info=True)
            publish_lifecycle(config_path, "failed", detail=str(e))
            return False

    def stop_worker(self, config_path: str, timeout: float = 10.0) -> bool:
        """優雅停止指定 Worker 子進程 (SIGTERM -> SIGKILL)。"""
        worker = self.workers.get(config_path)
        if not worker or not worker.is_alive:
            logger.info(f"ℹ️ [Master] Worker 未在運行: {config_path}")
            if worker:
                worker.manually_stopped = True
            return True

        worker.manually_stopped = True
        proc = worker.process
        pid = proc.pid
        logger.info(f"🛑 [Master] 正在向 Worker (PID={pid}) 發送 SIGTERM: {config_path}")

        try:
            proc.terminate()
            t0 = time.time()
            while time.time() - t0 < timeout:
                if proc.poll() is not None:
                    logger.info(f"✅ [Master] Worker (PID={pid}) 已優雅退出: {config_path}")
                    worker.process = None
                    return True
                time.sleep(0.2)

            # 超過 timeout，發送 SIGKILL
            logger.warning(f"⚠️ [Master] Worker (PID={pid}) 未在 {timeout}s 內退出，強制發送 SIGKILL")
            proc.kill()
            proc.wait(timeout=3.0)
            worker.process = None
            return True
        except Exception as e:
            logger.error(f"⚠️ [Master] 停止 Worker (PID={pid}) 異常: {e}")
            worker.process = None
            return False

    def check_health(self):
        """健康探測：檢查所有子進程狀態，捕獲異常退出並進行帶指數退避的重試自癒。"""
        now = time.time()
        for config_path, worker in list(self.workers.items()):
            proc = worker.process
            if proc is not None:
                ret = proc.poll()
                if ret is not None:
                    # 子進程已結束
                    pid = proc.pid
                    worker.process = None
                    if worker.manually_stopped:
                        logger.info(f"ℹ️ [Master] Worker {config_path} 正常結束 (手動停止, exit={ret})")
                        publish_lifecycle(config_path, "stopped")
                    else:
                        # 異常崩潰！
                        logger.error(f"💥 [Master] Worker {config_path} 異常崩潰退出! (PID={pid}, exit={ret})")
                        publish_lifecycle(config_path, "failed", detail=f"進程異常崩潰 (exit={ret})")
                        worker.restarts += 1
                        worker.last_crash_at = now
                        # 指數退避: 2, 4, 8, 16, 32, max 60s
                        backoff = min(2.0 ** min(worker.restarts, 6), 60.0)
                        worker.next_retry_at = now + backoff
                        logger.warning(
                            f"⏳ [Master] 將在 {backoff:.1f} 秒後自動重啟 Worker: {config_path} "
                            f"(累計重試第 {worker.restarts} 次)"
                        )

            # 檢查是否到達自動重試時間
            if (
                not worker.manually_stopped
                and worker.process is None
                and worker.next_retry_at > 0.0
                and now >= worker.next_retry_at
            ):
                logger.info(f"🔄 [Master] 觸發自癒重啟: {config_path} [{worker.bot_type}]")
                self.start_worker(config_path, worker.bot_type)

    def reconcile_file_controls(self):
        """對齊 active_grids.json 與 active_dca.json 檔案狀態 (Reconciliation)。"""
        # 網格
        if self.active_grids_file.exists():
            try:
                with open(self.active_grids_file, "r", encoding="utf-8") as f:
                    grids = json.load(f)
                if isinstance(grids, dict):
                    for path, enabled in grids.items():
                        worker = self.workers.get(path)
                        if enabled:
                            if not worker or not worker.is_alive:
                                if not worker or not worker.manually_stopped:
                                    self.start_worker(path, "grid")
                        else:
                            if worker and worker.is_alive:
                                self.stop_worker(path)
            except Exception as e:
                logger.warning(f"⚠️ [Master] 讀取 active_grids.json 異常: {e}")

        # DCA
        if self.active_dca_file.exists():
            try:
                with open(self.active_dca_file, "r", encoding="utf-8") as f:
                    dcas = json.load(f)
                if isinstance(dcas, dict):
                    for path, enabled in dcas.items():
                        worker = self.workers.get(path)
                        if enabled:
                            if not worker or not worker.is_alive:
                                if not worker or not worker.manually_stopped:
                                    self.start_worker(path, "dca")
                        else:
                            if worker and worker.is_alive:
                                self.stop_worker(path)
            except Exception as e:
                logger.warning(f"⚠️ [Master] 讀取 active_dca.json 異常: {e}")

    def poll_redis_commands(self):
        """非阻塞讀取 Redis 即時指令隊列。"""
        if not self.command_queue:
            return

        try:
            cmds = self.command_queue.read_commands(
                consumer_id=self.consumer_id,
                count=10,
                block_ms=10,
            )
            for cmd in cmds:
                msg_id = cmd.get("_msg_id")
                action = cmd.get("action", "").upper()
                target_type = cmd.get("target_type", "").lower()
                path = cmd.get("config_path", "")
                cmd_id = cmd.get("command_id")
                logger.info(f"⚡ [Master 即時指令] 收到 {action} {target_type} {path} (command_id={cmd_id})")

                if action == "START":
                    self.start_worker(path, target_type)
                elif action == "STOP":
                    self.stop_worker(path)

                if msg_id:
                    self.command_queue.ack(msg_id)
        except Exception as e:
            logger.warning(f"⚠️ [Master] 讀取 Redis 指令隊列異常: {e}")

    def shutdown_all(self, timeout: float = 8.0):
        """停機清理所有 Worker 子進程 (並行優雅終止，防範串行累積超時 FRG-05)。"""
        logger.info("🛑 [Master] 收到關閉信號，正在終止所有 Worker 子進程...")
        self.running = False

        # 1. 向所有運作中的 Worker 同步發送 SIGTERM
        active_workers = []
        for path, worker in list(self.workers.items()):
            if worker and worker.is_alive and worker.process:
                worker.manually_stopped = True
                try:
                    worker.process.terminate()
                    active_workers.append((path, worker))
                except Exception as e:
                    logger.warning(f"⚠️ [Master] 向 Worker ({path}) 發送 SIGTERM 失敗: {e}")

        # 2. 並行等待所有 Worker 退出 (總上限 timeout 秒)
        t0 = time.time()
        while active_workers and (time.time() - t0 < timeout):
            remaining = []
            for path, worker in active_workers:
                if worker.process and worker.process.poll() is not None:
                    logger.info(f"✅ [Master] Worker 已優雅退出: {path}")
                    worker.process = None
                else:
                    remaining.append((path, worker))
            active_workers = remaining
            if active_workers:
                time.sleep(0.2)

        # 3. 超時者統一 SIGKILL 強制清理
        for path, worker in active_workers:
            if worker.process and worker.process.poll() is None:
                logger.warning(f"⚠️ [Master] Worker ({path}) 停機超時，發送 SIGKILL")
                try:
                    worker.process.kill()
                    worker.process.wait(timeout=2.0)
                except Exception:
                    pass
                worker.process = None

        logger.info("🏁 [Master] 所有 Worker 子進程已終止完畢")

    def run_loop(self):
        """主監督循環。"""
        logger.info(f"👑 MasterSupervisor 已啟動 (PID={os.getpid()})")
        if self.command_queue:
            self.command_queue.ensure_group()
            logger.info("⚡ 已啟用 Redis Streams 即時控制面")

        # 初次根據檔案對齊期望狀態
        self.reconcile_file_controls()

        last_file_reconcile = time.time()

        while self.running:
            try:
                # 1. 處理 Redis 即時指令 (< 10ms 響應)
                self.poll_redis_commands()

                # 2. 健康檢查與自癒
                self.check_health()

                # 3. 每 5 秒低頻檔案對齊 (Reconciliation fallback)
                now = time.time()
                if now - last_file_reconcile >= 5.0:
                    self.reconcile_file_controls()
                    last_file_reconcile = now

                time.sleep(0.1)
            except KeyboardInterrupt:
                break
            except Exception as e:
                logger.error(f"⚠️ [Master] 主循環未捕獲異常: {e}", exc_info=True)
                time.sleep(1.0)

        self.shutdown_all()


def run_master_daemon(args):
    """供 run_multiple_grids.py 或獨立 CLI 呼叫的入口。"""
    setup_optimized_logging(use_colored=True)
    master = MasterSupervisor(debug=getattr(args, "debug", False))

    def _sig_handler(signum, frame):
        logger.info(f"🛑 收到系統信號 {signal.Signals(signum).name}，啟動關閉流程...")
        master.shutdown_all()
        sys.exit(0)

    signal.signal(signal.SIGTERM, _sig_handler)
    signal.signal(signal.SIGINT, _sig_handler)

    # 若 CLI 有指定初始配置路徑，預先啟動
    initial_configs = getattr(args, "configs", []) or []
    for cp in initial_configs:
        bot_type = "dca" if "dca" in cp.lower() else "grid"
        master.start_worker(cp, bot_type)

    master.run_loop()


def main():
    parser = argparse.ArgumentParser(description="多策略 Master 監督進程 (P1-3)")
    parser.add_argument("configs", metavar="CONFIG_PATH", type=str, nargs="*", help="指定要立即啟動的策略配置路徑")
    parser.add_argument("--debug", action="store_true", help="啟用 DEBUG 級別日誌")
    args = parser.parse_args()

    run_master_daemon(args)


if __name__ == "__main__":
    main()
