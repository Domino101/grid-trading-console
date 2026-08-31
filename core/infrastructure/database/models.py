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
