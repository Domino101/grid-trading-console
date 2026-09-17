"""
CoinFull Prime 整合協調服務 (排程調度、生命週期管理與 API 接口)
"""

import asyncio
import logging
import os
import json
import time
from datetime import date, datetime, timezone, timedelta
from pathlib import Path
from decimal import Decimal
from typing import Optional, Dict, Any, Tuple, List, Set
import yaml
import aiohttp

from core.services.interfaces.base import BaseService
from .models import UniverseRankResult, RebalanceReport, RegimeDecision
from .quant_engine import CoinFullPrimeQuant
from .rebalancer import PortfolioRebalancer
from .regime_filter import RegimeFilter
from core.adapters.exchanges.factory import ExchangeFactory
from core.adapters.exchanges.interface import ExchangeInterface, ExchangeConfig, ExchangeType
from core.adapters.exchanges.adapters.paper import create_paper_adapter
from core.infrastructure.database.ledger_backfill import SCHEDULER_BOT_ID


def compute_next_rebalance_run(
    now: datetime,
    rebalance_days: int = 7,
    execution_weekday: int = 3,
    execution_minute: int = 5,
    anchor_date: Optional[date] = None
) -> datetime:
    """計算下次換倉的 UTC 時刻（固定於 00:{execution_minute} UTC）。

    * rebalance_days == 7（稽核驗證過的主方案）：
      固定在每週的 execution_weekday 星期幾執行
      （execution_weekday 採用 Python weekday：Monday=0 ... Sunday=6，週四 = 3）。
    * rebalance_days != 7（低頻/高頻替代方案）：
      自 anchor_date 起每 N 日一次（相位固定；N 不是 7 的倍數時換倉日會在週內漂移，
      與研究重建的 "drift" 行為一致）。
    """
    t_hour, t_min = 0, int(execution_minute) % 60
    n = max(1, int(rebalance_days))

    if n == 7:
        weekday = int(execution_weekday) % 7
        for i in range(0, 8):
            cand = datetime.combine(now.date() + timedelta(days=i), datetime.min.time(), tzinfo=timezone.utc)
            cand = cand.replace(hour=t_hour, minute=t_min)
            if cand.weekday() == weekday and cand > now:
                return cand
    else:
        anchor = anchor_date or now.date()
        k = 0
        while True:
            cand = datetime.combine(anchor + timedelta(days=k * n), datetime.min.time(), tzinfo=timezone.utc)
            cand = cand.replace(hour=t_hour, minute=t_min)
            if cand > now:
                return cand
            k += 1
            if k > 400:  # 安全閘門（理論上不會觸發）
                break
        return datetime.combine(now.date() + timedelta(days=n), datetime.min.time(), tzinfo=timezone.utc).replace(hour=t_hour, minute=t_min)

logger = logging.getLogger("CoinFullPrime.Service")


class CoinFullPrimeService(BaseService):
    """CoinFull Prime 策略整合服務"""

    _instance: Optional["CoinFullPrimeService"] = None

    @classmethod
    def instance(cls) -> "CoinFullPrimeService":
        if cls._instance is None:
            cls._instance = CoinFullPrimeService()
        return cls._instance

    def __init__(self, config_path: Optional[str] = None):
        super().__init__(name="CoinFullPrimeService")
        self.config_path = config_path or os.path.join(
            os.path.dirname(__file__), "../../../config/coinfull_prime/strategy_config.yaml"
        )
        self.config: Dict[str, Any] = {}
        self.quant_engine: Optional[CoinFullPrimeQuant] = None
        self.rebalancer: Optional[PortfolioRebalancer] = None
        self.regime_filter: Optional[RegimeFilter] = None
        self.exchange_adapter: Optional[ExchangeInterface] = None
        self._adapters: Dict[str, ExchangeInterface] = {}

        saved_state = self.load_scheduler_state()
        self.selected_account: str = saved_state.get("account_id") or "paper"
        self.exclude_tradfi: bool = bool(saved_state.get("exclude_tradfi", True))

        self.last_rank_result: Optional[UniverseRankResult] = None
        self._last_rank_result_exclude_tradfi: Optional[bool] = None
        self.last_rebalance_report: Optional[RebalanceReport] = None
        self.last_run_timestamp: Optional[str] = saved_state.get("last_run_timestamp")
        self.last_regime_decision: Optional[RegimeDecision] = None

        # T2 崩跌日延後換倉狀態（持久化於排程狀態檔，跨重啟保留）
        self._defer_count: int = int(saved_state.get("defer_count", 0) or 0)
        self._pending_defer_until: Optional[str] = saved_state.get("pending_defer_until")
        self._defer_task: Optional[asyncio.Task] = None
        # 非 7 日換倉週期的相位錨點日期
        self._schedule_anchor_date: Optional[date] = None
        if saved_state.get("schedule_anchor_date"):
            try:
                self._schedule_anchor_date = date.fromisoformat(str(saved_state["schedule_anchor_date"]))
            except Exception:
                self._schedule_anchor_date = None

        self._scheduler_task: Optional[asyncio.Task] = None
        self._is_running = False
        self.scheduler_dry_run: bool = saved_state.get("dry_run", False)
        self.scheduler_capital: Optional[float] = saved_state.get("capital")
        self.scheduler_leverage: Optional[float] = saved_state.get("leverage")
        self.scheduler_balance_pct: Optional[float] = saved_state.get("balance_pct")

    def get_external_bot_symbols(self, account_mode: Optional[str] = None) -> Set[str]:
        """
        動態獲取所有當前處於運行/啟用狀態的網格、DCA等外部機器人交易標的與基礎幣種。
        用於雙軌防護：
        1. 避免 CoinFull Prime 量化排名選中這些幣（防搶保證金、防對沖干擾）
        2. 平倉舊標的時絕對豁免保護這些部位，絕不誤殺
        """
        protected: Set[str] = set()

        # 1. 查詢 canonical ledger (bots 表)
        #    P4-1：改走 StrategyLedger（→ LedgerRepository → create_ledger_store），
        #    過去以 raw sqlite3 讀取硬編碼相對路徑 data/web_ui_runtime.sqlite3，
        #    在 PostgreSQL 真相源模式下會讀到不存在/過期的本機 SQLite，
        #    使「外部機器人避碰名單」靜默變空 → 可能與網格/DCA 搶同一標的保證金。
        try:
            from core.infrastructure.database.strategy_ledger import StrategyLedger

            for bot in StrategyLedger.instance().list_bots():
                strategy_type = (getattr(bot, "strategy_type", "") or "").lower()
                if strategy_type == "coinfull_prime":
                    continue  # 自身籃子不列入外部保護名單（由 managed_symbols 處理）
                status = (getattr(bot, "status", "") or "").lower()
                if getattr(bot, "archived_at", None) or status in ("stopped", "archived", "failed"):
                    continue
                norm = PortfolioRebalancer._normalize_symbol(getattr(bot, "symbol", "") or "")
                if norm:
                    protected.add(norm)
        except Exception as ex:
            logger.warning(f"讀取 canonical ledger 外部機器人標的失敗: {ex}")

        # 2. 掃描本地配置檔 config/grid 與 config/dca
        base_dir = Path(__file__).resolve().parent.parent.parent.parent
        for folder_rel in ["config/grid", "config/dca"]:
            folder = base_dir / folder_rel
            if folder.exists():
                for yml in folder.glob("*.yaml"):
                    try:
                        with open(yml, "r", encoding="utf-8") as f:
                            cfg = yaml.safe_load(f) or {}
                            sym = cfg.get("symbol") or cfg.get("market", {}).get("symbol")
                            if sym:
                                norm = PortfolioRebalancer._normalize_symbol(sym)
                                if norm:
                                    protected.add(norm)
                    except Exception:
                        pass

        return protected

    # ------------------------------------------------------------------
    # P4-1 統一記帳：專屬持倉籃子（DB 為唯一真相源，檔案僅為匯出鏡像）
    # ------------------------------------------------------------------
    @staticmethod
    def _ledger():
        from core.infrastructure.database.strategy_ledger import StrategyLedger

        return StrategyLedger.instance()

    @staticmethod
    def _basket_bot_id(account_id: str) -> str:
        return f"coinfull_prime:{account_id}"

    def _basket_file(self) -> Path:
        return self._get_data_dir() / "coinfull_prime_active_basket.json"

    def load_active_basket(self, account_id: str) -> Set[str]:
        """讀取指定帳號的受管持倉籃子。

        真相源為 canonical ledger 的 ``bot_baskets``；若 DB 尚無資料但存在舊檔
        （0.3.5 以前落地），自動執行一次性冪等回填後再讀 DB，確保遷移期間
        「平倉白名單」不會遺失（遺失會導致退榜幣種無法被平倉）。
        """
        ledger = self._ledger()
        bot_id = self._basket_bot_id(account_id)
        entries = ledger.load_basket(bot_id, account_id=account_id, active_only=True)
        if not entries:
            if self._backfill_side_files(reason="basket_empty"):
                entries = ledger.load_basket(bot_id, account_id=account_id, active_only=True)
        if entries:
            return {PortfolioRebalancer._normalize_symbol(e.symbol) for e in entries if e.symbol}

        # 最後手段：DB 與回填都不可用時，退回讀取鏡像檔（保持交易可繼續）
        basket_file = self._basket_file()
        if not basket_file.exists():
            return set()
        try:
            with open(basket_file, "r", encoding="utf-8") as f:
                data = json.load(f) or {}
            acc_data = data.get(account_id, {})
            symbols = acc_data.get("managed_symbols", [])
            logger.warning(
                f"⚠️ 籃子記帳降級：canonical ledger 無資料，改讀檔案鏡像 ({account_id})；"
                "請執行 tools/ledger_consistency_audit.py --backfill 修復"
            )
            return {PortfolioRebalancer._normalize_symbol(s) for s in symbols if s}
        except Exception as ex:
            logger.warning(f"讀取專屬持倉籃子鏡像檔失敗: {ex}")
            return set()

    def load_basket_entries(self, account_id: str) -> List[Any]:
        """讀取籃子完整成分（含方向/數量/均價/已實現損益），供 API 與稽核使用。"""
        return self._ledger().load_basket(
            self._basket_bot_id(account_id), account_id=account_id, active_only=True
        )

    def save_active_basket(self, account_id: str, symbols: List[str], sides: Optional[Dict[str, str]] = None):
        """持久化受管持倉籃子：先寫 canonical ledger，再寫檔案鏡像（best-effort）。"""
        clean_symbols = sorted({PortfolioRebalancer._normalize_symbol(s) for s in symbols if s})
        bot_id = self._basket_bot_id(account_id)
        ledger = self._ledger()
        now_ts = time.time()
        try:
            from core.infrastructure.database.models import BasketEntry

            entries = [
                BasketEntry(
                    bot_id=bot_id,
                    account_id=account_id,
                    symbol=sym,
                    side=((sides or {}).get(sym) or "long"),
                    quantity=0.0,          # 0 → 保留 DB 既有持倉數量（名單同步不洗掉部位）
                    status="active",
                    updated_at=now_ts,
                )
                for sym in clean_symbols
            ]
            if entries:
                ledger.save_basket(bot_id, account_id, entries, close_missing=True)
            logger.info(f"💾 已將專屬持倉籃子寫入 canonical ledger ({account_id}): {clean_symbols}")
        except Exception as ex:
            logger.error(f"持久化專屬持倉籃子至 ledger 失敗: {ex}")

        # 檔案鏡像：由 DB 反向產生，避免雙來源分歧
        try:
            self._export_basket_mirror(account_id)
        except Exception as ex:
            logger.warning(f"寫入籃子檔案鏡像失敗（不影響記帳）: {ex}")

    def _export_basket_mirror(self, account_id: Optional[str] = None) -> None:
        """把 DB 籃子真相源匯出成 ``coinfull_prime_active_basket.json`` 鏡像。"""
        ledger = self._ledger()
        accounts = [account_id] if account_id else ["paper", "binance_testnet", "binance_live"]
        mirror: Dict[str, Any] = {}
        for acc in accounts:
            entries = ledger.load_basket(self._basket_bot_id(acc), account_id=acc, active_only=True)
            if not entries:
                continue
            symbols = sorted({e.symbol for e in entries})
            mirror[acc] = {
                "managed_symbols": symbols,
                "count": len(symbols),
                "source": "canonical_ledger:bot_baskets",
                "entries": [
                    {
                        "symbol": e.symbol,
                        "side": e.side,
                        "quantity": e.quantity,
                        "entry_price": e.entry_price,
                        "notional_usdt": e.notional_usdt,
                        "realized_pnl": e.realized_pnl,
                        "status": e.status,
                    }
                    for e in entries
                ],
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        if not mirror:
            return
        path = self._basket_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_file = path.with_suffix(".tmp")
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(mirror, f, indent=2, ensure_ascii=False, default=str)
        temp_file.replace(path)

    def _backfill_side_files(self, reason: str = "startup") -> bool:
        """把 0.3.5 以前的檔案型帳本冪等回填進 canonical ledger（自我修復）。"""
        try:
            from core.infrastructure.database.ledger_backfill import backfill_coinfull_prime_side_files

            stats = backfill_coinfull_prime_side_files(data_dir=self._get_data_dir(), ledger=self._ledger())
            imported = (
                stats.get("baskets", {}).get("symbols", 0)
                + stats.get("regime_decisions", {}).get("imported", 0)
                + stats.get("run_snapshots", {}).get("imported", 0)
                + stats.get("scheduler_states", {}).get("imported", 0)
            )
            if imported:
                logger.info(f"🔁 檔案型帳本已回填 canonical ledger (reason={reason}): {stats}")
            return imported > 0
        except Exception as ex:
            logger.warning(f"檔案型帳本回填失敗 (reason={reason}): {ex}")
            return False

    def load_config(self) -> Dict[str, Any]:
        """加載 YAML 策略配置檔"""
        cfg_file = Path(self.config_path)
        if cfg_file.exists():
            try:
                with open(cfg_file, "r", encoding="utf-8") as f:
                    self.config = yaml.safe_load(f) or {}
                if "exclude_tradfi" in self.config.get("universe", {}):
                    self.exclude_tradfi = bool(self.config["universe"]["exclude_tradfi"])
            except Exception as e:
                logger.error(f"讀取 CoinFull Prime 設定檔失敗: {e}，將使用預設值")
                self.config = {}
        else:
            self.config = {}
        return self.config

    def register_bot_identity(self, account_id: str = "paper", status: Optional[str] = None):
        """向 Canonical Ledger 登記 CoinFull Prime 機器人身分 (遵守 P1-6 帳本完整性規範)

        P4-1：改走 ``StrategyLedger``（統一記帳門面，自動路由 SQLite/PostgreSQL）。
        """
        try:
            mode = "live" if "live" in account_id.lower() else "paper"
            self._ledger().ensure_bot(
                bot_id=f"coinfull_prime:{account_id}",
                strategy_type="coinfull_prime",
                symbol="BASKET_16",
                exchange=account_id,
                account_mode=mode,
                status=status or ("running" if self._is_running else "idle"),
                config_path=self.config_path,
            )
        except Exception as ex:
            logger.warning(f"登記 CoinFull Prime 機器人身分異常 ({account_id}): {ex}")

    async def initialize(self) -> bool:
        """初始化量化引擎、風險閘門與預設適配器"""
        self.load_config()
        self.quant_engine = CoinFullPrimeQuant(self.config)
        self.quant_engine.exclude_tradfi = self.exclude_tradfi
        self.rebalancer = PortfolioRebalancer(self.config)
        self.regime_filter = RegimeFilter(self.config)

        # P4-1 記帳收斂：先把 0.3.5 以前的檔案型帳本冪等回填進 canonical ledger
        self._backfill_side_files(reason="initialize")
        # 只登記「實際使用的帳號環境」身分，避免未使用的環境在 bots 表產生幽靈機器人
        # （稽核 L9：幽靈身分會污染儀表板計數與外部避碰名單）
        self.register_bot_identity(self.selected_account or "paper")

        # 預設初始化 paper 適配器
        try:
            self.exchange_adapter = await self.get_adapter("paper")
            logger.info("CoinFull Prime 預設適配器已就緒: PAPER 模擬盤")
        except Exception as e:
            logger.warning(f"預設交易所適配器初始化異常: {e}")

        self.initialized = True
        return True

    async def get_adapter(self, account_id: str = "paper") -> Optional[ExchangeInterface]:
        """
        動態獲取或初始化對應帳號環境的交易所適配器實例
        支援: paper, binance_testnet, binance_live
        """
        acc_id = (account_id or "paper").lower().strip()

        if acc_id in self._adapters and self._adapters[acc_id] is not None:
            return self._adapters[acc_id]

        if acc_id == "paper":
            paper_config = {
                "grid_system": {
                    "symbol": "BTC/USDT",
                    "exchange": "paper",
                    "account_mode": "paper"
                }
            }
            adapter = create_paper_adapter(paper_config)
            await adapter.connect()
            self._adapters["paper"] = adapter
            return adapter

        elif acc_id == "binance_testnet":
            testnet_key = (os.getenv("BINANCE_TESTNET_API_KEY") or os.getenv("BINANCE_API_TESTNET_KEY") or "").strip()
            testnet_secret = (os.getenv("BINANCE_TESTNET_API_SECRET") or os.getenv("BINANCE_API_TESTNET_SECRET") or "").strip()

            if not testnet_key or not testnet_secret:
                logger.warning("未配置 BINANCE_TESTNET_API_KEY / SECRET，無法建立測試網連線")
                return None

            factory = ExchangeFactory()
            cfg = ExchangeConfig(
                exchange_id="binance",
                name="Binance Futures Testnet",
                exchange_type=ExchangeType.FUTURES,
                api_key=testnet_key,
                api_secret=testnet_secret,
                testnet=True,
                enable_websocket=True,
                enable_auto_reconnect=True
            )
            adapter = factory.create_adapter("binance", cfg)
            await adapter.connect()
            self._adapters["binance_testnet"] = adapter
            logger.info("✅ Binance 合約測試網適配器已成功建立並連接")
            return adapter

        elif acc_id == "binance_live":
            from web_ui.backend.lifecycle import live_trading_enabled
            if not live_trading_enabled():
                logger.warning("GRID_LIVE_TRADING_ENABLED 未開啟，拒絕建立實盤適配器")
                return None

            live_key = os.getenv("BINANCE_API_KEY", "").strip()
            live_secret = os.getenv("BINANCE_API_SECRET", "").strip()

            if not live_key or not live_secret:
                logger.warning("未配置 BINANCE_API_KEY / SECRET，無法建立實盤連線")
                return None

            factory = ExchangeFactory()
            cfg = ExchangeConfig(
                exchange_id="binance",
                name="Binance Futures Live",
                exchange_type=ExchangeType.FUTURES,
                api_key=live_key,
                api_secret=live_secret,
                testnet=False,
                enable_websocket=True,
                enable_auto_reconnect=True
            )
            adapter = factory.create_adapter("binance", cfg)
            await adapter.connect()
            self._adapters["binance_live"] = adapter
            logger.info("🟢 Binance 合約實盤適配器已成功建立並連接")
            return adapter

        else:
            logger.warning(f"不支援的帳號環境: {account_id}，退回 paper")
            return await self.get_adapter("paper")

    async def get_accounts_overview(self) -> List[Dict[str, Any]]:
        """
        取得所有可用帳號環境之狀態、就緒程度與 USDT 餘額
        """
        from web_ui.backend.lifecycle import live_trading_enabled

        accounts = []

        # 1. Paper 模擬盤
        accounts.append({
            "id": "paper",
            "name": "Paper 本地模擬盤",
            "environment": "paper",
            "is_ready": True,
            "balance_usdt": 10000.0,
            "missing_keys": [],
            "description": "本地撮合引擎模擬成交，完全無網路連線與資金風險",
            "endpoint": "local://in-memory"
        })

        # 2. Binance Testnet 測試網
        testnet_key = (os.getenv("BINANCE_TESTNET_API_KEY") or os.getenv("BINANCE_API_TESTNET_KEY") or "").strip()
        testnet_sec = (os.getenv("BINANCE_TESTNET_API_SECRET") or os.getenv("BINANCE_API_TESTNET_SECRET") or "").strip()
        has_testnet_keys = bool(testnet_key and testnet_sec)
        missing_testnet = []
        if not testnet_key:
            missing_testnet.append("BINANCE_TESTNET_API_KEY")
        if not testnet_sec:
            missing_testnet.append("BINANCE_TESTNET_API_SECRET")

        testnet_bal = None
        testnet_error = None
        if has_testnet_keys:
            try:
                adapter = await self.get_adapter("binance_testnet")
                if adapter:
                    balances = await adapter.get_balances()
                    for b in balances:
                        curr = str(getattr(b, "currency", "") or getattr(b, "asset", "")).upper()
                        if curr == "USDT":
                            testnet_bal = round(float(b.free or b.total or 0.0), 2)
                            break
            except Exception as e:
                testnet_error = str(e)
                logger.warning(f"查詢 Binance Testnet 餘額異常: {e}")

        accounts.append({
            "id": "binance_testnet",
            "name": "Binance 合約測試網",
            "environment": "testnet",
            "is_ready": has_testnet_keys,
            "balance_usdt": testnet_bal,
            "error": testnet_error,
            "missing_keys": missing_testnet,
            "description": "幣安官方永續合約測試網沙盒，真實下單撤單與WS回報，使用免費水龍頭測試金",
            "endpoint": "https://testnet.binancefuture.com"
        })

        # 3. Binance Live 實盤
        live_key = os.getenv("BINANCE_API_KEY", "").strip()
        live_sec = os.getenv("BINANCE_API_SECRET", "").strip()
        has_live_keys = bool(live_key and live_sec)
        is_live_on = live_trading_enabled()
        missing_live = []
        if not live_key:
            missing_live.append("BINANCE_API_KEY")
        if not live_sec:
            missing_live.append("BINANCE_API_SECRET")
        if not is_live_on:
            missing_live.append("GRID_LIVE_TRADING_ENABLED=1")

        live_bal = None
        live_error = None
        if has_live_keys and is_live_on:
            try:
                adapter = await self.get_adapter("binance_live")
                if adapter:
                    balances = await adapter.get_balances()
                    for b in balances:
                        curr = str(getattr(b, "currency", "") or getattr(b, "asset", "")).upper()
                        if curr == "USDT":
                            live_bal = round(float(b.free or b.total or 0.0), 2)
                            break
            except Exception as e:
                live_error = str(e)
                logger.warning(f"查詢 Binance Live 餘額異常: {e}")

        accounts.append({
            "id": "binance_live",
            "name": "Binance 合約實盤",
            "environment": "live",
            "is_ready": has_live_keys and is_live_on,
            "balance_usdt": live_bal,
            "error": live_error,
            "missing_keys": missing_live,
            "description": "幣安實盤永續合約，真實資金交易，受實盤全域開關保護",
            "endpoint": "https://fapi.binance.com"
        })

        return accounts

    async def get_positions(self, account_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """取得指定帳號的當前合約持倉清單"""
        target_acc = account_id or self.selected_account or "paper"
        adapter = await self.get_adapter(target_acc)
        if not adapter or target_acc == "paper":
            return []

        try:
            raw_positions = await adapter.get_positions()
            results = []
            managed_symbols = self.load_active_basket(target_acc)
            protected_symbols = self.get_external_bot_symbols()

            clean_protected = set()
            clean_protected_base = set()
            for s in protected_symbols:
                ns = PortfolioRebalancer._normalize_symbol(s)
                clean_protected.add(ns)
                for quote in ["USDT", "USDC", "BUSD", "USD"]:
                    if ns.endswith(quote) and len(ns) > len(quote):
                        clean_protected_base.add(ns[:-len(quote)])

            def _check_protected(sym: str) -> bool:
                if not sym:
                    return False
                ns = PortfolioRebalancer._normalize_symbol(sym)
                if ns in clean_protected:
                    return True
                for quote in ["USDT", "USDC", "BUSD", "USD"]:
                    if ns.endswith(quote) and len(ns) > len(quote):
                        base = ns[:-len(quote)]
                        if base in clean_protected_base or base in clean_protected:
                            return True
                return False

            for p in raw_positions:
                raw_size = getattr(p, "size", None) if getattr(p, "size", None) is not None else getattr(p, "amount", 0)
                if raw_size is None:
                    continue
                try:
                    size = float(raw_size)
                except Exception:
                    continue
                if abs(size) < 1e-8:
                    continue

                clean_sym = PortfolioRebalancer._normalize_symbol(p.symbol)
                side_str = str(getattr(p.side, "value", p.side) if hasattr(p, "side") else "").lower()
                if "long" in side_str:
                    side = "long"
                elif "short" in side_str:
                    side = "short"
                else:
                    side = "long" if size > 0 else "short"

                entry_px = float(getattr(p, "entry_price", 0.0) or 0.0)
                mark_px = float(getattr(p, "mark_price", 0.0) or getattr(p, "current_price", 0.0) or 0.0)
                unrealized = float(getattr(p, "unrealized_pnl", 0.0) or 0.0)
                calc_px = mark_px if mark_px > 0 else entry_px
                notional = round(abs(size * calc_px), 2)

                is_managed = clean_sym in managed_symbols
                is_protected = _check_protected(clean_sym)
                if is_managed:
                    role = "coinfull_prime"
                elif is_protected:
                    role = "external_bot"
                else:
                    role = "unmanaged"

                results.append({
                    "symbol": clean_sym,
                    "raw_symbol": p.symbol,
                    "side": side,
                    "size": abs(size),
                    "signed_size": abs(size) if side == "long" else -abs(size),
                    "entry_price": entry_px,
                    "mark_price": mark_px,
                    "unrealized_pnl": unrealized,
                    "notional_usdt": notional,
                    "is_managed": is_managed,
                    "is_protected": is_protected,
                    "role": role
                })
            results.sort(key=lambda x: x["notional_usdt"], reverse=True)
            return results
        except Exception as e:
            logger.warning(f"取得持倉清單失敗 ({target_acc}): {e}", exc_info=True)
            return []

    async def trigger_once(
        self,
        dry_run: Optional[bool] = None,
        account_id: str = "paper",
        capital: Optional[float] = None,
        leverage: Optional[float] = None,
        balance_pct: Optional[float] = None,
        ignore_crash_defer: bool = False,
        exclude_tradfi: Optional[bool] = None
    ) -> Tuple[UniverseRankResult, RebalanceReport]:
        """
        立即執行一次換倉管線 (支援指定帳號環境與動態本金/槓桿覆蓋)

        流程（依 docs/CoinFull_Prime_03_NoTrade_Trigger_Spec.md 送單前檢查清單）：
        1. 取宇宙 → 2. 風險閘門 (pts) → 3. T1 平倉停單 / T2 崩跌日延後
        → 4. 算分 → 5. 依閘門參數換倉 → 6. 落地 regime history 與每日快照。
        ignore_crash_defer=True 供 T2 延後 24 小時後的補跑使用（避免再被延後）。
        """
        if not self.quant_engine or not self.rebalancer or not self.regime_filter:
            await self.initialize()

        target_acc = account_id or self.selected_account or "paper"
        # P4-1：本批換倉的穩定識別碼 → bot_events 的 dedupe_key（同批重跑冪等）
        batch_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        logger.info(f"⚡ 手動/排程觸發 CoinFull Prime 全管線運行 (帳號: {target_acc}, batch={batch_id})...")

        # 1. 取得目標適配器
        adapter = await self.get_adapter(target_acc)

        # 2. 計算動態本金規模 (若指定 balance_pct 則依帳戶可用保證金計算)
        effective_capital = capital
        if balance_pct is not None and float(balance_pct) > 0:
            pct = min(max(float(balance_pct), 1.0), 100.0)
            account_usdt = 10000.0  # paper 預設
            if adapter and target_acc != "paper":
                try:
                    balances = await adapter.get_balances()
                    for b in balances:
                        curr = str(getattr(b, "currency", "") or getattr(b, "asset", "")).upper()
                        if curr == "USDT":
                            account_usdt = float(b.free or b.total or 0.0)
                            break
                except Exception as ex:
                    logger.warning(f"動態讀取帳戶餘額失敗: {ex}，退回預設本金")
            effective_capital = round(account_usdt * (pct / 100.0), 2)
            logger.info(f"💰 依帳戶可用餘額 ({account_usdt} USDT) 的 {pct}% 計算有效本金: {effective_capital} USDT")

        # 3. 提取目標適配器支援之線性合約標的清單（實盤/測試網自動過濾）
        allowed_symbols = None
        if adapter and target_acc != "paper":
            markets = getattr(adapter, "_market_info", {})
            if not markets and hasattr(adapter, "_rest"):
                markets = getattr(adapter._rest, "_market_info", {})
            if markets:
                allowed_symbols = {
                    (m.get("id") or "").upper().replace("/", "").replace(":", "")
                    for m in markets.values()
                    if isinstance(m, dict) and (m.get("linear") or m.get("contract") or m.get("swap")) and m.get("id")
                }
                logger.info(f"🎯 依交易所環境 ({target_acc}) 提取到 {len(allowed_symbols)} 個有效合約交易對")

        # 3.5 獲取外部機器人保護名單與專屬持倉籃子 (三層防護護欄)
        protected_symbols = self.get_external_bot_symbols(account_mode="live" if "live" in target_acc else "testnet")
        managed_symbols = self.load_active_basket(target_acc)
        logger.info(
            f"🛡️ 防護護欄啟動 ({target_acc}): 外部受保護機器人標的共 {len(protected_symbols)} 個，"
            f"CoinFull Prime 專屬持倉籃子現有 {len(managed_symbols)} 個"
        )

        is_dry_run = dry_run if dry_run is not None else self.rebalancer.dry_run
        should_exclude_tradfi = self.exclude_tradfi if exclude_tradfi is None else bool(exclude_tradfi)

        # 4. 取宇宙 + 風險閘門判定 (換倉前必算 pts；資料抓取失敗 → 閘門保守處理)
        universe: List[str] = []
        try:
            async with aiohttp.ClientSession() as session:
                universe = await self.quant_engine.fetch_universe(
                    session,
                    allowed_symbols=allowed_symbols,
                    excluded_symbols=protected_symbols,
                    exclude_tradfi=should_exclude_tradfi
                )
                gate_inputs = await self.regime_filter.collect(session, universe)
        except Exception as ex:
            logger.error(f"風險閘門資料抓取失敗，所有元件保守計 0: {ex}", exc_info=True)
            gate_inputs = {
                "btc_above_ma50": None, "btc_close": None, "btc_ma50": None,
                "breadth_pct": None, "funding_pctile": None, "btc_ret_1d_pct": None
            }

        decision = self.regime_filter.decide(
            btc_above_ma50=gate_inputs.get("btc_above_ma50"),
            breadth_pct=gate_inputs.get("breadth_pct"),
            funding_pctile=gate_inputs.get("funding_pctile"),
            btc_ret_1d_pct=gate_inputs.get("btc_ret_1d_pct"),
            btc_close=gate_inputs.get("btc_close"),
            btc_ma50=gate_inputs.get("btc_ma50"),
        )
        self.last_regime_decision = decision
        self.append_regime_history(decision, target_acc, note="trigger_once")
        logger.info(
            f"🚦 風險閘門判定: pts={decision.pts}/3 (趨勢={decision.trend_flag} 廣度={decision.breadth_flag} "
            f"費率={decision.funding_flag}), 廣度={decision.breadth_pct if decision.breadth_pct is None else round(decision.breadth_pct, 1)}%, "
            f"費率百分位={decision.funding_pctile if decision.funding_pctile is None else round(decision.funding_pctile, 1)}, "
            f"scale={decision.scale}x, flatten={decision.force_flatten}, crash_defer={decision.crash_defer}"
            f"{' [DEGRADED 資料缺漏]' if decision.degraded else ''}"
        )
        for r in decision.reasons:
            logger.info(f"   └ {r}")

        # log_only 模式：只記錄不改變下單行為（上線初期 2 週建議）
        if not self.regime_filter.enabled or self.regime_filter.log_only:
            if decision.force_flatten or not decision.allow_new_entries or decision.scale != 1.0 or decision.crash_defer:
                logger.info("ℹ️ 風險閘門 log_only/停用模式：決策僅記錄，不改變下單行為")
            decision = decision.model_copy(
                update={"scale": 1.0, "allow_new_entries": True, "force_flatten": False, "crash_defer": False}
            )

        effective_capital_val = effective_capital if effective_capital and float(effective_capital) > 0 \
            else self.rebalancer.total_capital

        # 5. T2 崩跌日延後換倉（最多連續 max_defers 次；若同時 pts=0 則 T1 平倉優先）
        if decision.crash_defer and not ignore_crash_defer and not decision.force_flatten:
            if self._defer_count < self.regime_filter.crash_defer_max:
                self._defer_count += 1
                defer_until = datetime.now(timezone.utc) + timedelta(hours=24)
                self._pending_defer_until = defer_until.isoformat()
                self.save_scheduler_state()
                logger.warning(
                    f"⏸️ T2 崩跌日延後 (BTC 收盤日 {decision.btc_ret_1d_pct:+.2f}% ≤ "
                    f"{self.regime_filter.crash_defer_pct:+.1f}%)：第 {self._defer_count}/{self.regime_filter.crash_defer_max} 次延後，"
                    f"換倉推遲至 {defer_until.isoformat()} 執行"
                )
                rank_result = UniverseRankResult(
                    universe_size=len(universe),
                    status="success",
                    message="T2 crash-day defer: rebalance postponed 24h"
                )
                self.last_rank_result = rank_result
                deferred_report = RebalanceReport(
                    dry_run=is_dry_run,
                    exchange=target_acc,
                    total_capital_usdt=effective_capital_val,
                    leverage=self.rebalancer.leverage,
                    status="deferred_crash_day",
                    gate_pts=decision.pts,
                    gate_scale=decision.scale,
                    gate_allow_new_entries=decision.allow_new_entries,
                    gate_force_flatten=decision.force_flatten
                )
                self.last_rebalance_report = deferred_report
                self.last_run_timestamp = datetime.now(timezone.utc).isoformat()
                self.selected_account = target_acc
                self._schedule_deferred_rebalance(defer_until, target_acc, is_dry_run, effective_capital, leverage, balance_pct)
                return rank_result, deferred_report
            else:
                logger.warning(f"⚠️ T2 延後次數已达上限 ({self._defer_count}/{self.regime_filter.crash_defer_max})，本次照常換倉")
                self._defer_count = 0
                self._pending_defer_until = None
                self.save_scheduler_state()

        # 6. T1 風險關閉 → 強制平倉（不重新算分）；否則正常算分
        if decision.force_flatten:
            rank_result = UniverseRankResult(
                universe_size=len(universe),
                status="success",
                message="regime gate T1: force flatten (pts=0)"
            )
            logger.warning(f"⛔ T1 風險關閉 (pts=0)：本次僅平倉受管部位，不建立新倉")
        else:
            # 抓取特徵與算分 (避碰排除外部機器人標的，帶入支援標的過濾與動態本金/槓桿)
            async with aiohttp.ClientSession() as session:
                features = await self.quant_engine.fetch_features(session, universe)
            rank_result = self.quant_engine.compute_ranks(
                features,
                capital_override=effective_capital,
                leverage_override=leverage
            )
        self.last_rank_result = rank_result
        self._last_rank_result_exclude_tradfi = should_exclude_tradfi

        # 7. 執行換倉調度 (帶入專屬持倉籃子隔離、外部機器人絕對保護與風險閘門參數)
        rebalance_report = await self.rebalancer.execute_rebalance(
            rank_result=rank_result,
            exchange_adapter=adapter,
            dry_run=is_dry_run,
            capital_override=effective_capital,
            leverage_override=leverage,
            managed_symbols=managed_symbols,
            protected_symbols=protected_symbols,
            account_id=target_acc,
            gross_scale=decision.scale,
            allow_new_entries=decision.allow_new_entries,
            force_flatten=decision.force_flatten,
            gate_pts=decision.pts,
            batch_id=batch_id
        )
        rebalance_report.exchange = target_acc
        self.last_rebalance_report = rebalance_report
        self.last_run_timestamp = datetime.now(timezone.utc).isoformat()
        self.selected_account = target_acc

        # 8. 更新並持久化專屬持倉籃子 (僅在實際換倉時登記新籃子)
        if rank_result and rank_result.status == "success" and rank_result.long_list:
            ranked_items = list(rank_result.long_list) + list(rank_result.short_list)
            new_basket_symbols = [x.symbol for x in ranked_items]
            basket_sides = {
                PortfolioRebalancer._normalize_symbol(x.symbol): ("short" if x.side == "short" else "long")
                for x in ranked_items
            }
            self.save_active_basket(target_acc, new_basket_symbols, sides=basket_sides)

        # 9. 成功執行一次換倉 → 重置 T2 延後計數；並落地每日快照（權益/PnL 可追蹤）
        self._defer_count = 0
        self._pending_defer_until = None
        self.save_scheduler_state()
        self._persist_run_snapshot(target_acc, decision, rebalance_report)

        logger.info(f"CoinFull Prime 管線執行完成 (帳號={target_acc}, 本金={rebalance_report.total_capital_usdt}, 槓桿={rebalance_report.leverage}x, DryRun={is_dry_run}, 閘門 pts={decision.pts}/scale={decision.scale}x)")
        return rank_result, rebalance_report

    # ------------------------------------------------------------------
    # 風險閘門：歷史落地 / T2 延後任務 / 每日快照
    # ------------------------------------------------------------------

    def _get_data_dir(self) -> Path:
        base_dir = Path(__file__).resolve().parent.parent.parent.parent
        return base_dir / "data"

    def append_regime_history(self, decision: RegimeDecision, account_id: str, note: str = ""):
        """記錄一次風險閘門決策。

        P4-1：真相源為 canonical ledger 的 ``bot_regime_decisions``（+ ``bot_events``
        的 ``regime_gate`` 事件，供事件溯源回放）；JSONL 檔案僅為匯出鏡像。
        以 ``dedupe_key=regime:{account}:{timestamp}`` 保證重跑冪等。
        """
        payload = decision.model_dump(mode="json")
        payload["account_id"] = account_id
        payload["note"] = note
        decided_at = time.time()
        bot_id = self._basket_bot_id(account_id)
        ledger = self._ledger()
        try:
            from core.infrastructure.database.models import RegimeDecisionRecord

            dedupe_key = f"regime:{account_id}:{payload.get('timestamp') or int(decided_at)}"
            ledger.ensure_bot(
                bot_id=bot_id,
                strategy_type="coinfull_prime",
                symbol="BASKET_16",
                exchange=account_id,
                account_mode="live" if "live" in account_id.lower() else "paper",
                status="running" if self._is_running else "idle",
                config_path=self.config_path,
            )
            ledger.record_regime_decision(
                RegimeDecisionRecord(
                    bot_id=bot_id,
                    account_id=account_id,
                    decided_at=decided_at,
                    pts=int(decision.pts or 0),
                    scale=float(decision.scale if decision.scale is not None else 1.0),
                    allow_new_entries=bool(decision.allow_new_entries),
                    force_flatten=bool(decision.force_flatten),
                    crash_defer=bool(decision.crash_defer),
                    degraded=bool(decision.degraded),
                    mode=decision.mode,
                    reason="; ".join(decision.reasons or [])[:2000] or None,
                    payload=payload,
                    dedupe_key=dedupe_key,
                )
            )
            ledger.append_event(
                bot_id=bot_id,
                event_type="regime_gate",
                payload=payload,
                created_at=decided_at,
                dedupe_key=dedupe_key,
            )
        except Exception as ex:
            logger.error(f"❌ 風險閘門決策記帳失敗（已計入記帳健康度，不影響交易）: {ex}")

        # 檔案鏡像（best-effort，永遠由 DB 語意產生，不作為真相源）
        try:
            path = self._get_data_dir() / "coinfull_prime_regime_history.jsonl"
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
        except Exception as ex:
            logger.warning(f"寫入風險閘門歷史鏡像失敗 (不影響記帳): {ex}")

    def load_regime_history(self, account_id: Optional[str] = None, limit: int = 200) -> List[Dict[str, Any]]:
        """自 canonical ledger 讀取風險閘門決策歷史（供 API / 稽核使用）。"""
        bot_id = self._basket_bot_id(account_id) if account_id else None
        out: List[Dict[str, Any]] = []
        for record in self._ledger().load_regime_decisions(bot_id=bot_id, limit=limit):
            payload = dict(record.payload or {})
            payload.update(
                {
                    "id": record.id,
                    "bot_id": record.bot_id,
                    "account_id": record.account_id,
                    "decided_at": record.decided_at,
                    "decided_at_iso": datetime.fromtimestamp(record.decided_at, tz=timezone.utc).isoformat(),
                    "pts": record.pts,
                    "scale": record.scale,
                    "allow_new_entries": record.allow_new_entries,
                    "force_flatten": record.force_flatten,
                    "crash_defer": record.crash_defer,
                    "degraded": record.degraded,
                    "mode": record.mode,
                    "reason": record.reason,
                    "source": "canonical_ledger:bot_regime_decisions",
                }
            )
            out.append(payload)
        return out

    def _persist_run_snapshot(self, target_acc: str, decision: RegimeDecision, report: RebalanceReport):
        """落地每次換倉的權益/持倉快照。

        P4-1：真相源為 canonical ledger（``bot_snapshots`` + ``bot_snapshot_history``，
        與 grid / DCA 完全同一套表與讀取路徑），JSONL 僅為匯出鏡像。
        籃子策略的淨曝險/UPnL 可正可負，故帶 ``_monotonic_bypass`` 說明其不適用
        累計計數單調護欄（P1-4 護欄語意僅針對單標的累計計數器）。
        """
        bot_id = self._basket_bot_id(target_acc)
        ledger = self._ledger()
        basket_entries = []
        try:
            basket_entries = [
                {
                    "symbol": e.symbol,
                    "side": e.side,
                    "quantity": e.quantity,
                    "entry_price": e.entry_price,
                    "notional_usdt": e.notional_usdt,
                    "realized_pnl": e.realized_pnl,
                }
                for e in ledger.load_basket(bot_id, account_id=target_acc, active_only=True)
            ]
        except Exception as ex:
            logger.debug(f"讀取籃子成分供快照失敗: {ex}")

        realized_total = round(sum(e.get("realized_pnl") or 0.0 for e in basket_entries), 8)
        gross_notional = round(sum(e.get("notional_usdt") or 0.0 for e in basket_entries), 2)
        long_notional = round(sum(e.get("notional_usdt") or 0.0 for e in basket_entries if e.get("side") == "long"), 2)
        short_notional = round(sum(e.get("notional_usdt") or 0.0 for e in basket_entries if e.get("side") == "short"), 2)

        line: Dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "account_id": target_acc,
            "bot_id": bot_id,
            "strategy_type": "coinfull_prime",
            "snapshot_kind": "run",
            "batch_id": getattr(report, "batch_id", None),
            "regime": {
                "pts": decision.pts,
                "scale": decision.scale,
                "allow_new_entries": decision.allow_new_entries,
                "force_flatten": decision.force_flatten,
                "degraded": decision.degraded,
                "breadth_pct": decision.breadth_pct,
                "funding_pctile": decision.funding_pctile,
                "btc_ret_1d_pct": decision.btc_ret_1d_pct,
            },
            "rebalance": {
                "status": report.status,
                "dry_run": report.dry_run,
                "capital": report.total_capital_usdt,
                "leverage": report.leverage,
                "orders_count": len(report.orders),
                "closed_symbols": report.closed_symbols,
                "opened_symbols": report.opened_symbols,
                "realized_pnl_usdt": getattr(report, "realized_pnl_usdt", None),
                "fees_usdt": getattr(report, "fees_usdt", None),
            },
            "basket": {
                "position_count": len(basket_entries),
                "gross_notional_usdt": gross_notional,
                "long_notional_usdt": long_notional,
                "short_notional_usdt": short_notional,
                "net_notional_usdt": round(long_notional - short_notional, 2),
                "cumulative_realized_pnl_usdt": realized_total,
                "entries": basket_entries,
            },
            "portfolio": None,
            "_monotonic_bypass": True,
        }

        def _write_snapshot(payload: Dict[str, Any]) -> None:
            """寫入 canonical ledger（force_history：每次換倉都是重大事件）+ 檔案鏡像。"""
            try:
                ledger.save_snapshot(
                    bot_id,
                    f"config/coinfull_prime/{target_acc}",
                    payload,
                    force_history=True,
                )
            except Exception as ex:
                logger.error(f"❌ 換倉快照記帳失敗（已計入記帳健康度）: {ex}")
            try:
                mirror = {k: v for k, v in payload.items() if not k.startswith("_")}
                path = self._get_data_dir() / "coinfull_prime_run_snapshots.jsonl"
                path.parent.mkdir(parents=True, exist_ok=True)
                with open(path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(mirror, ensure_ascii=False, default=str) + "\n")
            except Exception as ex:
                logger.warning(f"寫入換倉快照鏡像失敗 (不影響記帳): {ex}")

        # 交易所持倉補寫（非同步，不阻塞換倉主流程）；完成後以完整快照覆寫同一筆記錄
        async def _fill_portfolio():
            try:
                positions = await self.get_positions(target_acc)
                if positions:
                    gross = sum(p.get("notional_usdt", 0.0) for p in positions)
                    upnl = sum(p.get("unrealized_pnl", 0.0) for p in positions)
                    line["portfolio"] = {
                        "position_count": len(positions),
                        "gross_notional_usdt": round(gross, 2),
                        "long_notional_usdt": round(
                            sum(p.get("notional_usdt", 0.0) for p in positions if p.get("side") == "long"), 2
                        ),
                        "short_notional_usdt": round(
                            sum(p.get("notional_usdt", 0.0) for p in positions if p.get("side") == "short"), 2
                        ),
                        "unrealized_pnl_usdt": round(upnl, 2),
                    }
            except Exception as ex:
                logger.debug(f"快照持倉補寫失敗: {ex}")
            _write_snapshot(line)

        # 先以帳本（bot_baskets）可即時結算的內容落地，確保重啟/崩潰也有記帳
        _write_snapshot(line)
        try:
            asyncio.get_running_loop().create_task(_fill_portfolio())
        except RuntimeError:
            # 無事件迴圈（例如純同步測試環境）：已完成 DB 落地，無需補寫交易所持倉
            pass

    def _schedule_deferred_rebalance(
        self,
        defer_until: datetime,
        account_id: str,
        dry_run: bool,
        capital: Optional[float],
        leverage: Optional[float],
        balance_pct: Optional[float]
    ):
        """排定 24 小時後補跑的 T2 延後換倉 one-shot 任務"""
        if self._defer_task and not self._defer_task.done():
            logger.info("T2 延後補跑任務已存在，跳過重複排定")
            return

        async def _run():
            wait_secs = (defer_until - datetime.now(timezone.utc)).total_seconds()
            if wait_secs > 0:
                logger.info(f"⏳ T2 延後換倉任務休眠中，{wait_secs / 3600:.1f} 小時後補跑 ({account_id})...")
                await asyncio.sleep(wait_secs)
            try:
                await self.trigger_once(
                    dry_run=dry_run,
                    account_id=account_id,
                    capital=capital,
                    leverage=leverage,
                    balance_pct=balance_pct,
                    ignore_crash_defer=True
                )
                logger.info(f"✅ T2 延後換倉補跑完成 ({account_id})")
            except Exception as ex:
                logger.error(f"❌ T2 延後換倉補跑失敗: {ex}", exc_info=True)
            finally:
                self._defer_task = None

        try:
            self._defer_task = asyncio.get_running_loop().create_task(_run())
        except RuntimeError:
            logger.warning("無事件迴圈可排定 T2 延後補跑任務（重啟後將由排程器下次喚醒補上）")

    def _rearm_pending_defer(self):
        """開機恢復時，重排尚未到期的 T2 延後換倉任務"""
        if not self._pending_defer_until:
            return
        try:
            defer_until = datetime.fromisoformat(self._pending_defer_until)
        except (TypeError, ValueError):
            self._pending_defer_until = None
            return
        if defer_until <= datetime.now(timezone.utc):
            # 已過期：清除（將由下次正常排程喚醒處理）
            self._pending_defer_until = None
            self.save_scheduler_state()
            return
        self._schedule_deferred_rebalance(
            defer_until,
            self.selected_account or "paper",
            self.scheduler_dry_run,
            self.scheduler_capital,
            self.scheduler_leverage,
            self.scheduler_balance_pct
        )

    def _schedule_params(self) -> Dict[str, int]:
        """自 config 讀取排程參數（未設定時用稽核建議預設：週四 00:05 UTC 每週一次）"""
        sched = self.config.get("schedule", {}) or {}
        return {
            "rebalance_days": int(sched.get("rebalance_days", 7)),
            "execution_weekday": int(sched.get("execution_weekday", 3)),   # Python weekday: Mon=0 ... Thu=3
            "execution_minute_utc": int(sched.get("execution_minute_utc", 5)),
        }

    def _next_rebalance_run(self, now: Optional[datetime] = None) -> datetime:
        """下次換倉的 UTC 時刻"""
        p = self._schedule_params()
        if p["rebalance_days"] != 7 and self._schedule_anchor_date is None:
            # 首次以 N 日週期排程時，以今天為相位錨點並持久化
            self._schedule_anchor_date = (now or datetime.now(timezone.utc)).date()
            try:
                self.save_scheduler_state()
            except Exception:
                pass
        return compute_next_rebalance_run(
            now or datetime.now(timezone.utc),
            rebalance_days=p["rebalance_days"],
            execution_weekday=p["execution_weekday"],
            execution_minute=p["execution_minute_utc"],
            anchor_date=self._schedule_anchor_date,
        )

    def _seconds_until_next_run(self) -> float:
        """計算距離下次換倉的秒數 (零輪詢休眠)"""
        delta = (self._next_rebalance_run() - datetime.now(timezone.utc)).total_seconds()
        return max(delta, 1.0)

    def _schedule_description(self) -> str:
        p = self._schedule_params()
        weekday_names = ["週一 Mon", "週二 Tue", "週三 Wed", "週四 Thu", "週五 Fri", "週六 Sat", "週日 Sun"]
        if p["rebalance_days"] == 7:
            return f"每週{weekday_names[p['execution_weekday'] % 7]} UTC 00:{p['execution_minute_utc']:02d} 換倉"
        return f"每 {p['rebalance_days']} 日 UTC 00:{p['execution_minute_utc']:02d} 換倉（相位錨點 {self._schedule_anchor_date or '未設定'}）"

    async def _execute_rebalance_with_retry(self, max_retries: int = 3) -> bool:
        """執行定時換倉，遭遇瞬時故障時進行退避重試 (5m / 15m / 30m)，避免錯過當週換倉"""
        retry_delays = [300, 900, 1800]
        retry_count = 0

        while retry_count <= max_retries and self._is_running:
            try:
                rank_res, report = await self.trigger_once(
                    dry_run=self.scheduler_dry_run,
                    account_id=self.selected_account or "paper",
                    capital=self.scheduler_capital,
                    leverage=self.scheduler_leverage,
                    balance_pct=self.scheduler_balance_pct
                )
                is_success = report and getattr(report, "status", "") in ("completed", "completed_dry_run")
                # 預防性檢查：若訂單清單非空，但所有訂單全數 skipped 或 failed，視為需重試之異常
                if is_success and getattr(report, "orders", None):
                    has_filled = any(getattr(o, "status", "") in ("filled", "simulated") for o in report.orders)
                    if not has_filled and len(report.orders) > 0:
                        is_success = False
                        logger.warning("⚠️ [排程重試守護] 本次換倉無任何訂單成交 (全數被跳過或異常)，觸發自動重試")

                if is_success:
                    logger.info(f"✅ 定時換倉執行成功 (狀態={getattr(report, 'status', 'ok')})")
                    self.save_scheduler_state()
                    return True
                else:
                    err_msg = getattr(report, "error", "未完全撮合") if report else "無回報"
                    logger.error(f"❌ 定時換倉執行未成功 (原因={err_msg})")
            except Exception as ex:
                logger.error(f"❌ 定時換倉執行拋出未捕獲異常: {ex}", exc_info=True)

            retry_count += 1
            if retry_count <= max_retries and self._is_running:
                delay = retry_delays[retry_count - 1]
                logger.warning(f"🔄 [排程重試守護] 啟動第 {retry_count}/{max_retries} 次自動重試，將於 {delay // 60} 分鐘後重新執行...")
                await asyncio.sleep(delay)
            else:
                logger.critical(f"⛔ [排程重試守護] 定時換倉已達最大重試次數 ({max_retries} 次) 仍未成功，終止重試")
                return False
        return False

    async def _scheduler_loop(self):
        """排程背景協程 (常態 0.0% CPU 休眠，具備失敗自動退避重試與服務重啟補跑機制)"""
        logger.info(f"CoinFull Prime 排程監聽器啟動，{self._schedule_description()} 自動喚醒 (帳號={self.selected_account}, DryRun={self.scheduler_dry_run})")
        self._is_running = True

        # 1. 服務重啟錯過補跑檢測 (Missed-Run Catch-Up)
        try:
            now = datetime.now(timezone.utc)
            p = self._schedule_params()
            is_rebalance_day = (p["rebalance_days"] == 7 and now.weekday() == p["execution_weekday"] % 7)
            window_passed = (now.hour > 0 or (now.hour == 0 and now.minute >= p["execution_minute_utc"]))
            already_ran_today = False
            if self.last_run_timestamp:
                try:
                    last_dt = datetime.fromisoformat(self.last_run_timestamp)
                    if last_dt.date() == now.date() and (now - last_dt).total_seconds() < 86400:
                        already_ran_today = True
                except Exception:
                    pass

            if is_rebalance_day and window_passed and not already_ran_today:
                logger.warning("⚠️ [排程補跑守護] 檢測到今日為排程換倉日且已過時間窗口，但尚未有今日換倉紀錄，將於 15 秒後自動補跑換倉！")
                await asyncio.sleep(15)
                if self._is_running:
                    await self._execute_rebalance_with_retry()
        except Exception as ex:
            logger.warning(f"排程啟動補跑檢測異常: {ex}")

        # 2. 正常週期休眠迴圈
        try:
            while self._is_running:
                next_run = self._next_rebalance_run()
                sleep_secs = (next_run - datetime.now(timezone.utc)).total_seconds()
                hours = int(sleep_secs // 3600)
                mins = int((sleep_secs % 3600) // 60)
                logger.info(
                    f"CoinFull Prime 進入休眠，預計 {hours} 小時 {mins} 分後喚醒 "
                    f"(下次換倉: {next_run.isoformat()} / {self._schedule_description()})"
                )
                await asyncio.sleep(max(sleep_secs, 1.0))

                # 喚醒執行
                logger.info(f"⏰ 排程定時器喚醒，開始執行 CoinFull Prime (目標帳號={self.selected_account}, DryRun={self.scheduler_dry_run})...")
                await self._execute_rebalance_with_retry()
                # 避免同一分鐘內重複觸發
                await asyncio.sleep(65)
        except asyncio.CancelledError:
            logger.info("CoinFull Prime 排程已取消")
        finally:
            self._is_running = False

    def _get_scheduler_state_file(self) -> Path:
        base_dir = Path(__file__).resolve().parent.parent.parent.parent
        return base_dir / "data" / "coinfull_prime_scheduler.json"

    def load_scheduler_state(self) -> Dict[str, Any]:
        """讀取排程器配置與狀態。

        P4-1：真相源為 canonical ledger 的 ``bot_runtime_state``
        （bot_id=``coinfull_prime:scheduler``, key=``scheduler``）；
        DB 無資料時退回舊檔案（並在下次 initialize 時冪等回填）。
        """
        try:
            from core.infrastructure.database.strategy_ledger import StrategyLedger

            state = StrategyLedger.instance().get_state(SCHEDULER_BOT_ID, "scheduler")
            if isinstance(state, dict) and state:
                return state
        except Exception as ex:
            logger.warning(f"讀取 ledger 排程狀態失敗，退回檔案鏡像: {ex}")

        path = self._get_scheduler_state_file()
        if not path.exists():
            return {}
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f) or {}
        except Exception as ex:
            logger.warning(f"讀取 CoinFull Prime 排程持久化狀態失敗: {ex}")
            return {}

    def save_scheduler_state(self):
        """持久化排程器狀態：先寫 canonical ledger，再原子寫入檔案鏡像。"""
        data = {
            "account_id": self.selected_account,
            "dry_run": self.scheduler_dry_run,
            "capital": self.scheduler_capital,
            "leverage": self.scheduler_leverage,
            "balance_pct": self.scheduler_balance_pct,
            "exclude_tradfi": getattr(self, "exclude_tradfi", True),
            "enabled": getattr(self, "scheduler_enabled", True),
            "is_running": self._is_running,
            "defer_count": self._defer_count,
            "pending_defer_until": self._pending_defer_until,
            "schedule_anchor_date": self._schedule_anchor_date.isoformat() if self._schedule_anchor_date else None,
            "last_run_timestamp": self.last_run_timestamp,
            "updated_at": datetime.now(timezone.utc).isoformat()
        }
        # 1) canonical ledger（真相源）
        try:
            from core.infrastructure.database.strategy_ledger import StrategyLedger

            StrategyLedger.instance().set_state(SCHEDULER_BOT_ID, "scheduler", data)
        except Exception as ex:
            logger.error(f"❌ 排程狀態記帳失敗（已計入記帳健康度）: {ex}")

        # 2) 檔案鏡像（原子寫入，best-effort）
        path = self._get_scheduler_state_file()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temp_file = path.with_suffix(".tmp")
            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            temp_file.replace(path)
            logger.debug(f"💾 已成功持久化 CoinFull Prime 排程狀態: {data}")
        except Exception as ex:
            logger.warning(f"持久化 CoinFull Prime 排程狀態鏡像失敗 (不影響記帳): {ex}")

    def restore_scheduler(self):
        """
        伺服器開機時調用：恢復先前的排程配置並啟動排程。
        若無歷史保存檔，則以預設測試網環境啟動。
        """
        saved = self.load_scheduler_state()
        if saved:
            acc_id = saved.get("account_id") or "binance_testnet"
            dry_run = saved.get("dry_run", False)
            cap = saved.get("capital")
            lev = saved.get("leverage")
            bp = saved.get("balance_pct")
            ex_tradfi = saved.get("exclude_tradfi", True)
            self.exclude_tradfi = bool(ex_tradfi)
            should_run = saved.get("enabled", saved.get("is_running", True))
            logger.info(f"🔄 從持久化紀錄恢復 CoinFull Prime 排程: 帳號={acc_id}, DryRun={dry_run}, 排除TradFi={self.exclude_tradfi}, 啟用狀態={should_run}")
            if should_run:
                self.start_scheduler(
                    account_id=acc_id,
                    dry_run=dry_run,
                    capital=cap,
                    leverage=lev,
                    balance_pct=bp,
                    exclude_tradfi=self.exclude_tradfi
                )
                # 重啟補排：若重啟前已排定 T2 崩跌日延後換倉且尚未到期，重新排定 one-shot 任務
                self._rearm_pending_defer()
            else:
                self.selected_account = acc_id
                self.scheduler_dry_run = dry_run
                self.scheduler_capital = cap
                self.scheduler_leverage = lev
                self.scheduler_balance_pct = bp
                self.scheduler_enabled = False
                self._rearm_pending_defer()
        else:
            logger.info("ℹ️ 無 CoinFull Prime 歷史排程紀錄，以預設測試網環境啟動排程")
            self.start_scheduler(account_id="binance_testnet", dry_run=False, exclude_tradfi=True)

    def start_scheduler(
        self,
        account_id: Optional[str] = None,
        dry_run: Optional[bool] = None,
        capital: Optional[float] = None,
        leverage: Optional[float] = None,
        balance_pct: Optional[float] = None,
        exclude_tradfi: Optional[bool] = None
    ):
        """啟動定時排程 (支援配置定時換倉的目標環境、資金配置與 TradFi 過濾)"""
        self.scheduler_enabled = True
        if account_id:
            self.selected_account = account_id
        if dry_run is not None:
            self.scheduler_dry_run = dry_run
        elif account_id == "binance_testnet":
            self.scheduler_dry_run = False
        elif account_id == "binance_live":
            self.scheduler_dry_run = True
        if capital is not None:
            self.scheduler_capital = capital
        if leverage is not None:
            self.scheduler_leverage = leverage
        if balance_pct is not None:
            self.scheduler_balance_pct = balance_pct
        if exclude_tradfi is not None:
            self.exclude_tradfi = bool(exclude_tradfi)
            if self.quant_engine:
                self.quant_engine.exclude_tradfi = self.exclude_tradfi

        if self._scheduler_task and not self._scheduler_task.done():
            logger.warning("排程已經在運行中，已即時更新配置參數")
            self.save_scheduler_state()
            return
        self._is_running = True
        self._scheduler_task = asyncio.create_task(self._scheduler_loop())
        self.save_scheduler_state()

    def set_scheduler_target(
        self,
        account_id: str,
        dry_run: Optional[bool] = None,
        capital: Optional[float] = None,
        leverage: Optional[float] = None,
        balance_pct: Optional[float] = None,
        exclude_tradfi: Optional[bool] = None
    ) -> Dict[str, Any]:
        """即時變更或同步定時排程器的目標帳號環境、資金參數與 TradFi 過濾設定"""
        if account_id:
            self.selected_account = account_id
        if dry_run is not None:
            self.scheduler_dry_run = dry_run
        elif account_id == "binance_testnet":
            self.scheduler_dry_run = False
        elif account_id == "binance_live":
            self.scheduler_dry_run = True
        if capital is not None:
            self.scheduler_capital = capital
        if leverage is not None:
            self.scheduler_leverage = leverage
        if balance_pct is not None:
            self.scheduler_balance_pct = balance_pct
        if exclude_tradfi is not None:
            self.exclude_tradfi = bool(exclude_tradfi)
            if self.quant_engine:
                self.quant_engine.exclude_tradfi = self.exclude_tradfi

        self.save_scheduler_state()

        logger.info(
            f"🔄 CoinFull Prime 定時排程目標已更新: 目標帳號={self.selected_account}, "
            f"DryRun={self.scheduler_dry_run}, 本金={self.scheduler_capital}, 槓桿={self.scheduler_leverage}, 餘額%={self.scheduler_balance_pct}, 排除TradFi={self.exclude_tradfi}"
        )
        return self.get_status()

    def stop_scheduler(self, disable: bool = True):
        """停止定時排程 (disable=False 代表服務重啟時的優雅關閉，不改變用戶設定的啟用狀態)"""
        if disable:
            self.scheduler_enabled = False
        self._is_running = False
        if self._scheduler_task:
            self._scheduler_task.cancel()
            self._scheduler_task = None
        if disable:
            self.save_scheduler_state()

    def get_status(self) -> Dict[str, Any]:
        """取得當前運行狀態（含風險閘門最近一次判定、下次換倉時間與 TradFi 設定）"""
        now = datetime.now(timezone.utc)
        next_run_dt = self._next_rebalance_run(now)
        next_run = next_run_dt.isoformat()
        secs_left = (next_run_dt - now).total_seconds()
        hours_left = round(secs_left / 3600.0, 1)

        long_summary = [
            {"symbol": x.symbol, "score": x.score, "rank": x.rank} 
            for x in (self.last_rank_result.long_list if self.last_rank_result else [])
        ]
        short_summary = [
            {"symbol": x.symbol, "score": x.score, "rank": x.rank} 
            for x in (self.last_rank_result.short_list if self.last_rank_result else [])
        ]

        target_acc = self.selected_account or "paper"
        active_basket = self.load_basket_entries(target_acc)
        protected_bots = self.get_external_bot_symbols()
        sched_p = self._schedule_params()

        return {
            "service": self.name,
            "initialized": self.initialized,
            "is_running": self._is_running,
            "last_run_timestamp": self.last_run_timestamp,
            "next_scheduled_run_utc": next_run,
            "seconds_until_next_run": round(secs_left, 1),
            "hours_until_next_run": hours_left,
            "exclude_tradfi": getattr(self, "exclude_tradfi", True),
            "scheduler_config": {
                "account_id": self.selected_account or "paper",
                "dry_run": getattr(self, "scheduler_dry_run", True),
                "capital": getattr(self, "scheduler_capital", None),
                "leverage": getattr(self, "scheduler_leverage", None),
                "balance_pct": getattr(self, "scheduler_balance_pct", None),
                "exclude_tradfi": getattr(self, "exclude_tradfi", True),
                "rebalance_days": sched_p["rebalance_days"],
                "execution_weekday": sched_p["execution_weekday"],
                "execution_minute_utc": sched_p["execution_minute_utc"],
                "description": self._schedule_description()
            },
            "regime": (
                self.last_regime_decision.model_dump(mode="json")
                if self.last_regime_decision else None
            ),
            "managed_symbols": sorted(list({e.symbol for e in active_basket})),
            "protected_bot_symbols": sorted(list(protected_bots)),
            "current_portfolio": {
                "long_top8": long_summary,
                "short_bottom8": short_summary
            },
            "last_rebalance_status": self.last_rebalance_report.status if self.last_rebalance_report else "never_run",
            "dry_run": self.config.get("portfolio", {}).get("dry_run", True),
            "selected_account": self.selected_account,
            "exchange": self.selected_account or self.config.get("portfolio", {}).get("exchange", "paper")
        }

    async def get_regime(self, refresh: bool = False, exclude_tradfi: Optional[bool] = None) -> RegimeDecision:
        """取得最近一次風險閘門判定（refresh=True 時重新抓取即時資料計算）

        供 GET /api/v1/coinfull-prime/regime 端點使用。
        """
        if not self.quant_engine or not self.regime_filter:
            await self.initialize()

        should_exclude_tradfi = self.exclude_tradfi if exclude_tradfi is None else bool(exclude_tradfi)
        if refresh or self.last_regime_decision is None:
            protected_symbols = self.get_external_bot_symbols()
            async with aiohttp.ClientSession() as session:
                universe = await self.quant_engine.fetch_universe(
                    session,
                    excluded_symbols=protected_symbols,
                    exclude_tradfi=should_exclude_tradfi
                )
                gate_inputs = await self.regime_filter.collect(session, universe)
            self.last_regime_decision = self.regime_filter.decide(
                btc_above_ma50=gate_inputs.get("btc_above_ma50"),
                breadth_pct=gate_inputs.get("breadth_pct"),
                funding_pctile=gate_inputs.get("funding_pctile"),
                btc_ret_1d_pct=gate_inputs.get("btc_ret_1d_pct"),
                btc_close=gate_inputs.get("btc_close"),
                btc_ma50=gate_inputs.get("btc_ma50"),
            )
            self.append_regime_history(self.last_regime_decision, self.selected_account or "paper", note="api_refresh")
        return self.last_regime_decision
