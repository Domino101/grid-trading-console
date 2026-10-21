"""P4-1: 統一記帳層 (Unified Strategy Accounting Port).

本模組是 **grid / dca / coinfull_prime 三個策略唯一合法的帳本寫入路徑**。

設計目標（科學化、專業化的記帳結構）：

1. **單一真相源 (single source of truth)**
   所有寫入都經過 ``LedgerRepository`` → ``create_ledger_store()`` 工廠，因此
   ``DATABASE_URL`` 指向 PostgreSQL 時三個策略一起切換真相源；過去
   CoinFull Prime 直接 ``SqliteLedgerStore()`` 造成的「幽靈資料源」
   （寫 PG、讀本機 SQLite）在此被物理性消除。

2. **可觀測的降級 (observable degradation, 而非靜默失敗)**
   記帳失敗絕不阻斷交易（延續 P1-3 語意），但每一次失敗都會被計數、
   記錄最後錯誤與時間戳，並可透過 ``health()`` / ``/api/v1/system/ledger-audit``
   / ``tools/ledger_consistency_audit.py`` 查詢。靜默虧損軌跡不再可能。

3. **冪等 (idempotency)**
   成交以 ``fill_id`` 冪等；事件以 ``dedupe_key`` 冪等；籃子以
   ``(bot_id, account_id, symbol)`` upsert；風險閘門決策以 ``dedupe_key`` 冪等。
   重跑 / 補跑 / 重啟恢復都不會產生重複記帳。

4. **明確的記帳契約 (accounting contract)**
   每個 strategy_type 宣告它的部位承載表（單標的 → ``bot_positions``；
   籃子型 → ``bot_baskets``），一致性稽核工具依契約檢查，而不是用同一把
   尺量所有策略（避免把籃子策略的「無 bot_positions」誤判為缺陷）。
"""

from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from collections.abc import Mapping
from typing import Any, Dict, Iterable, List, Optional, Tuple

from core.infrastructure.database.models import (
    BasketEntry,
    BotEvent,
    BotPosition,
    BotRecord,
    RegimeDecisionRecord,
    RuntimeStateEntry,
    TradeFill,
)
from core.logging import get_logger

logger = get_logger(__name__)

#: 各策略的記帳契約：部位由哪張表承載、是否要求快照與事件流。
#: 一致性稽核（ledger_audit.LedgerConsistencyAuditor）以此為判定依據。
STRATEGY_ACCOUNTING_CONTRACT: Dict[str, Dict[str, Any]] = {
    "grid": {
        "position_store": "bot_positions",   # 單標的：淨部位 + 均價
        "basket_store": None,
        "requires_snapshot": True,
        "requires_fill_events": True,
        "fill_roles": {"base", "grid", "tp", "sl", "safety"},
    },
    "dca": {
        "position_store": "bot_positions",
        "basket_store": None,
        "requires_snapshot": True,
        "requires_fill_events": True,
        "fill_roles": {"base", "safety", "tp", "sl"},
    },
    "coinfull_prime": {
        "position_store": None,              # 籃子型：不套用單標的淨部位語意
        "basket_store": "bot_baskets",
        "requires_snapshot": True,
        "requires_fill_events": True,
        "fill_roles": {
            "rebalance_entry", "rebalance_exit",
            "simulated_entry", "simulated_exit",
            "flatten_exit",
        },
    },
    "volume": {
        "position_store": "bot_positions",
        "basket_store": None,
        "requires_snapshot": False,
        "requires_fill_events": False,
        "fill_roles": {"maker", "taker"},
    },
}


def contract_for(strategy_type: Optional[str]) -> Dict[str, Any]:
    """回傳策略記帳契約（未知策略採用最保守的單標的契約）。"""
    return STRATEGY_ACCOUNTING_CONTRACT.get((strategy_type or "").lower(), STRATEGY_ACCOUNTING_CONTRACT["grid"])


@dataclass
class AccountingHealth:
    """記帳層健康度計數器（可觀測降級的核心載體）。"""

    writes_ok: int = 0
    writes_failed: int = 0
    last_error: Optional[str] = None
    last_error_op: Optional[str] = None
    last_error_at: Optional[float] = None
    errors_by_op: Dict[str, int] = field(default_factory=dict)

    @property
    def degraded(self) -> bool:
        return self.writes_failed > 0

    def record_ok(self) -> None:
        self.writes_ok += 1

    def record_failure(self, op: str, exc: BaseException) -> None:
        self.writes_failed += 1
        self.last_error = f"{type(exc).__name__}: {exc}"[:512]
        self.last_error_op = op
        self.last_error_at = time.time()
        self.errors_by_op[op] = self.errors_by_op.get(op, 0) + 1

    def snapshot(self) -> Dict[str, Any]:
        total = self.writes_ok + self.writes_failed
        return {
            "writes_ok": self.writes_ok,
            "writes_failed": self.writes_failed,
            "write_success_rate": round(self.writes_ok / total, 6) if total else None,
            "degraded": self.degraded,
            "last_error": self.last_error,
            "last_error_op": self.last_error_op,
            "last_error_at": self.last_error_at,
            "errors_by_op": dict(self.errors_by_op),
        }


class StrategyLedger:
    """三策略共用的統一記帳門面（thread-safe 單例）。

    使用方式::

        from core.infrastructure.database.strategy_ledger import StrategyLedger
        ledger = StrategyLedger.instance()
        ledger.ensure_bot(...)
        ledger.record_fill(fill)                 # 同時寫 trade_fills + fill 事件
        ledger.append_event(bot_id, "daily_rebalance", payload, dedupe_key=...)
        ledger.save_basket(bot_id, account_id, entries)
        ledger.record_regime_decision(record)
        ledger.set_state(bot_id, "scheduler", {...})

    所有方法 **絕不拋出例外**（交易優先），失敗以 ERROR 日誌 + 健康度計數呈現。
    """

    _instance: Optional["StrategyLedger"] = None
    _instance_lock = threading.Lock()

    def __init__(self, repository: Optional[Any] = None):
        self._repository = repository
        self._lock = threading.RLock()
        self._health = AccountingHealth()

    # ── 單例 / 後端資訊 ────────────────────────────────────────────────────
    @classmethod
    def instance(cls) -> "StrategyLedger":
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        with cls._instance_lock:
            cls._instance = None

    @property
    def repo(self):
        """底層 LedgerRepository。

        未注入 repository 時 **每次動態解析** ``LedgerRepository.instance()``，
        因此測試重置單例（reset_instance）或切換 DATABASE_URL 後不會抓到過期實例；
        注入時（例如 RuntimeSnapshotRepository 以自訂路徑建立）則固定使用該實例。
        """
        if self._repository is not None:
            return self._repository
        from core.infrastructure.database.ledger_repository import LedgerRepository

        return LedgerRepository.instance()

    @property
    def store(self):
        """底層 canonical LedgerStore。

        容錯：注入物件可以是 ``LedgerRepository``（有 ``.store``）或直接是
        ``LedgerStore`` 實作（例如稽核 CLI 以 ``--db`` 指定檔案、或既有程式碼
        直接持有 store）。兩種形態都解析到同一個 canonical store，避免
        ``AttributeError: ... has no attribute 'store'`` 讓記帳靜默失敗。
        """
        repo = self.repo
        inner = getattr(repo, "store", None)
        return inner if inner is not None else repo

    @property
    def backend(self) -> str:
        """實際使用的帳本後端：``postgresql`` / ``sqlite``。"""
        try:
            url = str(getattr(self.store, "database_url", "") or "")
        except Exception:
            return "unknown"
        if url.startswith("postgresql"):
            return "postgresql"
        if url.startswith("sqlite"):
            return "sqlite"
        return "unknown"

    @property
    def database_path(self) -> Optional[str]:
        return getattr(self.repo, "database_path", None)

    def health(self) -> Dict[str, Any]:
        """記帳健康度（供 API / 稽核工具 / 策略狀態卡片使用）。"""
        with self._lock:
            data = self._health.snapshot()
        data["backend"] = self.backend
        data["database_path"] = self.database_path
        return data

    def reset_health(self) -> None:
        with self._lock:
            self._health = AccountingHealth()

    @contextmanager
    def _write(self, op: str):
        """記帳寫入護欄：成功計數、失敗記錄 ERROR 與計數，但絕不阻斷交易。"""
        try:
            yield
        except Exception as exc:
            with self._lock:
                self._health.record_failure(op, exc)
            logger.error(f"❌ [UnifiedLedger] 記帳寫入失敗 op={op}: {type(exc).__name__}: {exc}")
        else:
            with self._lock:
                self._health.record_ok()

    # ── 身分 (P1-6) ───────────────────────────────────────────────────────
    def ensure_bot(
        self,
        bot_id: str,
        strategy_type: str,
        symbol: str,
        exchange: str,
        account_mode: str = "paper",
        status: str = "running",
        config_path: Optional[str] = None,
        created_at: Optional[float] = None,
    ) -> bool:
        """登記 / 更新機器人身分（含身分稽核事件，由 store 內部自動記錄）。

        ``account_mode`` 只允許 ``live`` / ``paper``（schema 語意）；測試網屬於
        非真實資金，一律歸類 ``paper``，環境細節由 ``exchange`` 欄位保留
        （如 ``binance_testnet``），避免 dashboard 過濾邏輯分歧。
        """
        if not bot_id:
            return False
        mode = account_mode if account_mode in ("live", "paper") else "paper"
        record = BotRecord(
            bot_id=bot_id,
            strategy_type=strategy_type,
            symbol=symbol,
            exchange=exchange,
            account_mode=mode,
            config_path=config_path,
            status=status,
            created_at=created_at if created_at is not None else time.time(),
        )
        with self._write(f"ensure_bot:{strategy_type}"):
            self.repo.register_or_update_bot(record)
        return True

    def get_bot(self, bot_id: str) -> Optional[BotRecord]:
        try:
            return self.repo.get_bot(bot_id)
        except Exception as exc:
            logger.error(f"❌ [UnifiedLedger] 讀取 bot 失敗 bot_id={bot_id}: {exc}")
            return None

    def list_bots(self) -> List[BotRecord]:
        try:
            return list(self.repo.get_all_bots())
        except Exception as exc:
            logger.error(f"❌ [UnifiedLedger] 讀取 bots 清單失敗: {exc}")
            return []

    # ── 成交流水 (trade_fills + canonical fill 事件) ───────────────────────
    def record_fill(self, fill: TradeFill, emit_event: bool = True) -> bool:
        """寫入一筆不可變成交流水，並同步 canonical ``fill`` 事件（事件溯源主路徑）。

        三個策略共用此路徑，因此 ``event_fold`` 一律走 events-first，
        不再有任何策略長期停留在 legacy trade_fills 回退路徑。

        順序保證：先寫 ``trade_fills``，**成功後**才 emit ``fill`` 事件；
        寫入失敗時不產生孤立事件（避免 events 有、fills 無的反向不一致），
        失敗僅計入 health 計數，交易流程不受阻斷。
        """
        if fill is None or not fill.bot_id or not fill.fill_id:
            return False
        wrote = False
        with self._write("record_fill"):
            self.repo.record_fill(fill)
            wrote = True  # _write 會吞掉例外：只有真正寫成功才會走到這裡
        if emit_event and wrote:
            # 必須走「同一個注入的 store」：bot_events.emit_fill_event() 會解析全域
            # LedgerRepository 單例，在注入自訂 store 的情境（稽核 CLI --db、
            # RuntimeSnapshotRepository 自訂路徑、測試隔離）會把事件寫進另一個 DB，
            # 造成 trade_fills 有資料、bot_events 缺事件的 L5 事件溯源缺口。
            try:
                from core.infrastructure.database.event_fold import fill_event_payload

                self.append_event(
                    fill.bot_id,
                    "fill",
                    payload=fill_event_payload(fill),
                    created_at=float(fill.executed_at),
                    dedupe_key=f"fill:{fill.fill_id}",
                )
            except Exception as exc:
                logger.warning(f"⚠️ [UnifiedLedger] fill 事件寫入失敗（不影響流水）: {exc}")
        return True

    def record_fills(self, fills: Iterable[TradeFill], emit_event: bool = True) -> int:
        written = 0
        for fill in fills or []:
            if self.record_fill(fill, emit_event=emit_event):
                written += 1
        return written

    def get_fills(
        self,
        bot_id: str,
        limit: int = 500,
        since: float = 0.0,
        order_desc: bool = False,
    ) -> List[TradeFill]:
        try:
            return self.repo.get_fills(bot_id, limit=limit, since=since, order_desc=order_desc)
        except Exception as exc:
            logger.error(f"❌ [UnifiedLedger] 讀取 fills 失敗 bot_id={bot_id}: {exc}")
            return []

    # ── 事件流 (bot_events) ───────────────────────────────────────────────
    def append_event(
        self,
        bot_id: str,
        event_type: str,
        payload: Optional[dict] = None,
        created_at: Optional[float] = None,
        dedupe_key: Optional[str] = None,
    ) -> Optional[int]:
        """附加事件；**可重跑的事件必須帶 dedupe_key**（換倉/閘門/生命週期）。"""
        if not bot_id:
            return None
        seq: Optional[int] = None
        with self._write(f"append_event:{event_type}"):
            seq = self.repo.append_event(
                bot_id, event_type, payload=payload, created_at=created_at, dedupe_key=dedupe_key
            )
        return seq

    def get_events(self, bot_id: str, since_seq: int = 0, limit: int = 100000) -> List[BotEvent]:
        try:
            return self.repo.get_events(bot_id, since_seq=since_seq, limit=limit)
        except Exception as exc:
            logger.error(f"❌ [UnifiedLedger] 讀取 events 失敗 bot_id={bot_id}: {exc}")
            return []

    # ── 歷史資料修復（僅供 tools/coinfull_legacy_repair.py 使用） ──────────
    def count_legacy_event_dedupe_gaps(self, bot_prefix: str = "coinfull_prime") -> int:
        """計算指定 bot 前綴下、可重跑事件仍缺 dedupe_key 的筆數。"""
        try:
            return int(self.store.count_legacy_event_dedupe_gaps(bot_prefix))
        except Exception as exc:
            logger.error(f"❌ [UnifiedLedger] 統計 legacy dedupe 缺口失敗: {exc}")
            return 0

    def backfill_legacy_event_dedupe_keys(self, bot_prefix: str = "coinfull_prime") -> int:
        """為既有可重跑事件補上穩定 dedupe_key；只處理 NULL，重跑冪等。"""
        with self._write("backfill_legacy_event_dedupe_keys"):
            return int(self.store.backfill_legacy_event_dedupe_keys(bot_prefix))

    def backfill_missing_fill_events(self, bot_id: str) -> int:
        """由 trade_fills 補齊缺少的 canonical fill 事件（L5）；重跑冪等。"""
        fills = self.get_fills(bot_id, limit=100000)
        events = self.get_events(bot_id, limit=1000000)
        existing_fill_ids = {
            str((e.payload or {}).get("fill_id"))
            for e in events
            if e.event_type == "fill" and isinstance(e.payload, dict)
        }
        from core.infrastructure.database.event_fold import fill_event_payload

        written = 0
        for fill in fills:
            if fill.fill_id in existing_fill_ids:
                continue
            seq = self.append_event(
                bot_id,
                event_type="fill",
                payload=fill_event_payload(fill),
                created_at=fill.executed_at,
                dedupe_key=f"fill:{fill.fill_id}",
            )
            if seq is not None:
                written += 1
        return written

    # ── 快照 (bot_snapshots / bot_snapshot_history) ───────────────────────
    def save_snapshot(
        self,
        bot_id: str,
        config_path: Optional[str],
        stats: dict,
        timestamp: Optional[float] = None,
        force_history: bool = False,
    ) -> Optional[float]:
        """寫入策略運行快照（三者共用 bot_snapshots + 歷史表）。"""
        ts: Optional[float] = None
        with self._write("save_snapshot"):
            ts = self.repo.save_snapshot(
                bot_id, config_path or bot_id, stats, timestamp=timestamp, force_history=force_history
            )
        return ts

    def load_snapshot(self, bot_id: str) -> Optional[Tuple[dict, float]]:
        try:
            return self.repo.load_snapshot_by_bot_id(bot_id)
        except Exception as exc:
            logger.error(f"❌ [UnifiedLedger] 讀取快照失敗 bot_id={bot_id}: {exc}")
            return None

    def load_history(self, bot_id_or_config: str, limit: int = 1000) -> List[Tuple[dict, float]]:
        try:
            return self.repo.load_history(bot_id_or_config, limit=limit)
        except Exception as exc:
            logger.error(f"❌ [UnifiedLedger] 讀取快照歷史失敗 key={bot_id_or_config}: {exc}")
            return []

    # ── 部位（單標的策略） ────────────────────────────────────────────────
    def get_position(self, bot_id: str) -> Optional[BotPosition]:
        try:
            return self.repo.get_position(bot_id)
        except Exception as exc:
            logger.error(f"❌ [UnifiedLedger] 讀取部位失敗 bot_id={bot_id}: {exc}")
            return None

    def update_position(self, bot_id: str, position: BotPosition, expected_version: Optional[int] = None) -> bool:
        """OCC 更新部位；``expected_version=None`` 時自動讀取目前版本再寫。"""
        ok = False
        with self._write("update_position"):
            version = expected_version
            if version is None:
                current = self.repo.get_position(bot_id)
                version = current.version if current else 0
            ok = self.repo.update_position_occ(bot_id, position, version)
        return ok

    def rehydrate_position(self, bot_id: str) -> Optional[BotPosition]:
        try:
            return self.repo.rehydrate_position_from_fills(bot_id)
        except Exception as exc:
            logger.error(f"❌ [UnifiedLedger] 重算部位失敗 bot_id={bot_id}: {exc}")
            return None

    # ── 籃子（多標的策略） ────────────────────────────────────────────────
    @staticmethod
    def _coerce_basket_entries(
        bot_id: str, account_id: str, entries: Iterable[Any]
    ) -> List[BasketEntry]:
        """把 dict 形態的籃子成分正規化為 ``BasketEntry``。

        檔案型帳本（``coinfull_prime_active_basket.json``）與 API 載入使用的是
        ``avg_price`` / ``qty`` 等舊鍵名；若直接送進 store 會拋
        ``AttributeError: 'dict' object has no attribute 'symbol'``，
        並被寫入守門吞掉 → 記帳靜默失敗。此處統一在門面收斂，
        讓「DB 為唯一真相源」的寫入路徑對呼叫端型態容錯。
        """
        coerced: List[BasketEntry] = []
        for item in entries or []:
            if isinstance(item, BasketEntry):
                coerced.append(item)
                continue
            if isinstance(item, Mapping):
                data = dict(item)
                symbol = str(data.get("symbol") or "").strip()
                if not symbol:
                    continue
                coerced.append(
                    BasketEntry(
                        bot_id=str(data.get("bot_id") or bot_id),
                        account_id=str(data.get("account_id") or account_id),
                        symbol=symbol,
                        side=str(data.get("side") or "long"),
                        quantity=float(data.get("quantity", data.get("qty", 0.0)) or 0.0),
                        entry_price=(
                            float(data["entry_price"])
                            if data.get("entry_price") is not None
                            else (float(data["avg_price"]) if data.get("avg_price") is not None else None)
                        ),
                        notional_usdt=float(data.get("notional_usdt", 0.0) or 0.0),
                        realized_pnl=float(data.get("realized_pnl", 0.0) or 0.0),
                        status=str(data.get("status") or "active"),
                        opened_at=data.get("opened_at"),
                        updated_at=float(data.get("updated_at", 0.0) or 0.0),
                    )
                )
                continue
            # 其他 duck-typed 物件：逐欄位取值
            symbol = str(getattr(item, "symbol", "") or "").strip()
            if not symbol:
                continue
            coerced.append(
                BasketEntry(
                    bot_id=str(getattr(item, "bot_id", "") or bot_id),
                    account_id=str(getattr(item, "account_id", "") or account_id),
                    symbol=symbol,
                    side=str(getattr(item, "side", "long") or "long"),
                    quantity=float(getattr(item, "quantity", 0.0) or 0.0),
                    entry_price=getattr(item, "entry_price", None),
                    notional_usdt=float(getattr(item, "notional_usdt", 0.0) or 0.0),
                    realized_pnl=float(getattr(item, "realized_pnl", 0.0) or 0.0),
                    status=str(getattr(item, "status", "active") or "active"),
                    opened_at=getattr(item, "opened_at", None),
                    updated_at=float(getattr(item, "updated_at", 0.0) or 0.0),
                )
            )
        return coerced

    def save_basket(
        self,
        bot_id: str,
        account_id: str,
        entries: Iterable[Any],
        close_missing: bool = True,
    ) -> int:
        touched = 0
        normalized = self._coerce_basket_entries(bot_id, account_id, entries)
        with self._write("save_basket"):
            touched = self.store.save_basket_entries(
                bot_id, account_id, normalized, close_missing=close_missing
            )
        return touched

    def close_basket_entry(
        self,
        bot_id: str,
        account_id: str,
        symbol: str,
        reason: str = "reconcile_missing",
    ) -> Optional[dict]:
        """對帳修復專用：關閉交易所已不存在的籃子成分（不捏造 realized_pnl）。"""
        with self._write("close_basket_entry"):
            return self.store.close_basket_entry(
                bot_id, account_id, symbol, reason=reason
            )

    def load_basket(
        self,
        bot_id: str,
        account_id: Optional[str] = None,
        active_only: bool = True,
    ) -> List[BasketEntry]:
        try:
            return self.store.get_basket(bot_id, account_id=account_id, active_only=active_only)
        except Exception as exc:
            logger.error(f"❌ [UnifiedLedger] 讀取籃子失敗 bot_id={bot_id}: {exc}")
            return []

    def apply_basket_fill(
        self,
        bot_id: str,
        account_id: str,
        symbol: str,
        side: str,
        price: float,
        quantity: float,
        reduce_only: bool = False,
    ) -> float:
        """把成交套用到籃子成分並回傳本次已實現損益（失敗回傳 0.0，不阻斷交易）。"""
        realized = 0.0
        with self._write("apply_basket_fill"):
            realized = self.store.apply_basket_fill(
                bot_id, account_id, symbol, side, price, quantity, reduce_only=reduce_only
            )
        return float(realized or 0.0)

    # ── 風險閘門決策 ──────────────────────────────────────────────────────
    def record_regime_decision(self, record: RegimeDecisionRecord) -> Optional[int]:
        rid: Optional[int] = None
        with self._write("record_regime_decision"):
            rid = self.store.record_regime_decision(record)
        return rid

    def load_regime_decisions(
        self,
        bot_id: Optional[str] = None,
        limit: int = 200,
        since: float = 0.0,
    ) -> List[RegimeDecisionRecord]:
        try:
            return self.store.get_regime_decisions(bot_id=bot_id, limit=limit, since=since)
        except Exception as exc:
            logger.error(f"❌ [UnifiedLedger] 讀取風險閘門決策失敗: {exc}")
            return []

    # ── 執行期狀態 KV ─────────────────────────────────────────────────────
    def set_state(self, bot_id: str, key: str, value: Any) -> bool:
        with self._write(f"set_state:{key}"):
            self.store.set_runtime_state(bot_id, key, value)
        return True

    def get_state(self, bot_id: str, key: str, default: Any = None) -> Any:
        try:
            return self.store.get_runtime_state(bot_id, key, default)
        except Exception as exc:
            logger.error(f"❌ [UnifiedLedger] 讀取執行期狀態失敗 bot_id={bot_id} key={key}: {exc}")
            return default

    def get_states(self, bot_id: str) -> Dict[str, Any]:
        try:
            return self.store.get_runtime_states(bot_id)
        except Exception as exc:
            logger.error(f"❌ [UnifiedLedger] 讀取執行期狀態失敗 bot_id={bot_id}: {exc}")
            return {}

    def get_state_entries(self, bot_id: Optional[str] = None) -> List[RuntimeStateEntry]:
        try:
            return self.store.get_runtime_state_entries(bot_id)
        except Exception as exc:
            logger.error(f"❌ [UnifiedLedger] 讀取執行期狀態清單失敗: {exc}")
            return []

    # ── 身分稽核事件 ──────────────────────────────────────────────────────
    def record_identity_event(
        self,
        bot_id: str,
        event_type: str,
        config_path: Optional[str] = None,
        old_bot_id: Optional[str] = None,
        new_bot_id: Optional[str] = None,
    ) -> bool:
        with self._write(f"identity_event:{event_type}"):
            self.repo.record_identity_event(bot_id, event_type, config_path, old_bot_id, new_bot_id)
        return True


__all__ = [
    "STRATEGY_ACCOUNTING_CONTRACT",
    "AccountingHealth",
    "StrategyLedger",
    "contract_for",
]
