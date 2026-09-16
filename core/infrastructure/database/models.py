"""Data models for normalized relational bot ledger."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class BotRecord:
    bot_id: str
    strategy_type: str  # 'grid', 'dca', 'volume'
    symbol: str
    exchange: str
    account_mode: str   # 'live', 'paper'
    status: str
    created_at: float
    config_path: Optional[str] = None
    archived_at: Optional[float] = None


@dataclass
class TradeFill:
    fill_id: str
    bot_id: str
    client_order_id: str
    side: str           # 'buy', 'sell'
    role: str           # 'base', 'safety', 'tp', 'grid', 'sl'
    price: float
    quantity: float
    executed_at: float
    fee: float = 0.0
    fee_currency: Optional[str] = None
    realized_pnl: float = 0.0
    exchange_order_id: Optional[str] = None


@dataclass
class BotPosition:
    bot_id: str
    active_direction: Optional[str] = None  # 'long', 'short', 'neutral'
    quantity: float = 0.0
    average_cost: Optional[float] = None
    total_cost: float = 0.0
    entry_price: Optional[float] = None
    completed_cycles: int = 0
    realized_profit: float = 0.0
    total_fees: float = 0.0
    version: int = 1
    updated_at: float = 0.0


@dataclass
class BotSnapshot:
    bot_id: str
    payload: dict
    config_path: Optional[str] = None
    version: int = 1
    updated_at: float = 0.0


@dataclass
class BotIdentityEvent:
    """審計事件：bot 身份的建立/連結/改名/重映射（P1-6）。"""
    bot_id: str
    event_type: str      # 'created' | 'config_linked' | 'renamed' | 'remapped'
    config_path: Optional[str] = None
    old_bot_id: Optional[str] = None
    new_bot_id: Optional[str] = None
    recorded_at: float = 0.0


@dataclass
class BotEvent:
    """Phase 3 (P3-1): append-only 事件流（事件溯源 canonical 記錄）。

    ``seq`` 為 per-bot 單調遞增序號（由 store 事務內配置）；``dedupe_key``
    提供時 (bot_id, dedupe_key) 唯一，重送同一事件為冪等 no-op。
    """
    bot_id: str
    seq: int
    event_type: str      # bot_created/bot_renamed/order_placed/order_cancelled/fill/take_profit/stop_loss/reset/state_transition/reconcile
    payload: dict
    created_at: float
    dedupe_key: Optional[str] = None


@dataclass
class BasketEntry:
    """Phase 4 (P4-1): 多標的策略的「受管持倉籃子」單一成分。

    統一記帳層以本結構取代 ``data/coinfull_prime_active_basket.json`` 檔案型帳本：
    * ``symbol`` 為策略內部正規化標的（如 ``BTCUSDT``）
    * ``side``   為 ``long`` / ``short``（市場中性策略兩者並存）
    * ``status`` 為 ``active``（受管中，可被平倉白名單引用）/ ``closed``（已退出）
    * ``realized_pnl`` 為該成分累計已實現損益（由 ``apply_basket_fill`` 以均價法結算）
    """
    bot_id: str
    account_id: str
    symbol: str
    side: str = "long"                 # 'long' | 'short'
    quantity: float = 0.0
    entry_price: Optional[float] = None
    notional_usdt: float = 0.0
    realized_pnl: float = 0.0
    status: str = "active"             # 'active' | 'closed'
    opened_at: Optional[float] = None
    updated_at: float = 0.0


@dataclass
class RegimeDecisionRecord:
    """Phase 4 (P4-1): 風險閘門（regime gate）決策之 canonical 記帳紀錄。

    取代 ``data/coinfull_prime_regime_history.jsonl``；``dedupe_key`` 讓同一次
    決策（同一 bot + 同一時間戳）在重跑/補寫時保持冪等。
    """
    bot_id: str
    decided_at: float
    pts: int = 0
    scale: float = 1.0
    allow_new_entries: bool = True
    force_flatten: bool = False
    crash_defer: bool = False
    degraded: bool = False
    account_id: Optional[str] = None
    mode: Optional[str] = None         # 'flatten' | 'soft' | 'crowded' | 'boost' | ...
    reason: Optional[str] = None
    payload: Optional[dict] = None
    dedupe_key: Optional[str] = None
    id: Optional[int] = None


@dataclass
class RuntimeStateEntry:
    """Phase 4 (P4-1): per-bot 執行期狀態 KV（排程器、延後補跑、回填標記等）。

    取代 ``data/coinfull_prime_scheduler.json`` 等散落檔案，使跨重啟狀態與
    帳本同源（single source of truth），並可被一致性稽核工具檢查。
    """
    bot_id: str
    key: str
    value: object = None
    updated_at: float = 0.0
