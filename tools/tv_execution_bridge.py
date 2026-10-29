#!/usr/bin/env python3
"""
TradingView 決策執行橋接器 (TradingView Execution Bridge)

職責:
1. 連接 TradingView MCP 分析/決策 與 本地量化交易控制平面 (Hermes API / FastAPI)
2. 支援代號自動標準化 (tv_symbol -> canonical_symbol)
3. 安全護欄與雙重確認 (預設 Paper 模式，Live 模式強制 confirm_live 門禁)
4. 決策溯源機制：自動將 TradingView 技術指標、評級快照寫入 audit_log 之 reason 欄位
5. 支援無人值守/事件風控 (DCA 宏觀黑窗 pause_reentry / resume_reentry)
"""

from __future__ import annotations

import os
import sys
import json
import logging
import argparse
from pathlib import Path
from typing import Dict, Any, Optional, Tuple
import httpx

# 引入本專案符號解析模組
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.services.symbol_manager.tv_symbol_resolver import (
    parse_tv_symbol,
    resolve_to_project_symbol,
    to_tv_symbol,
    TradingViewSymbolInfo
)

logger = logging.getLogger("TvExecutionBridge")


class TradingViewExecutionBridge:
    """TradingView 決策執行橋接客戶端"""

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8001",
        token: Optional[str] = None,
        source: str = "tv_bridge",
        timeout: float = 15.0
    ):
        self.base_url = base_url.rstrip("/")
        self.source = source
        self.timeout = timeout
        self.token = self._resolve_token(token)

    def resolve_symbol(
        self,
        tv_symbol: str,
        target_exchange: Optional[str] = None
    ) -> TradingViewSymbolInfo:
        """解析 TradingView 符號為內部結構"""
        return parse_tv_symbol(tv_symbol, default_exchange=target_exchange or "binance")

    def _resolve_token(self, token: Optional[str]) -> str:
        if token and token.strip() and token != "xxx":
            return token.strip()
        env_tok = os.environ.get("GRID_API_TOKEN")
        if env_tok and env_tok.strip():
            return env_tok.strip()
        token_file = PROJECT_ROOT / ".grid_api_token"
        if token_file.exists():
            content = token_file.read_text(encoding="utf-8").strip()
            if content:
                return content
        return ""

    @property
    def _headers(self) -> Dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json"
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _format_decision_reason(
        self,
        action_name: str,
        user_reason: Optional[str] = None,
        tv_symbol: Optional[str] = None,
        tv_metrics: Optional[Dict[str, Any]] = None
    ) -> str:
        """格式化帶有 TV 指標審計溯源的原因標籤"""
        parts = [f"[{self.source.upper()}:{action_name.upper()}]"]
        if user_reason:
            parts.append(user_reason.strip())
        if tv_symbol:
            parts.append(f"tv_symbol={tv_symbol}")
        if tv_metrics:
            metrics_str = ",".join(f"{k}={v}" for k, v in sorted(tv_metrics.items()))
            parts.append(f"metrics=({metrics_str})")
        return " | ".join(parts)

    # ────────────────────────────────────────────────────────
    # 查詢與預檢端點 (Read-only / Safe Pre-flight)
    # ────────────────────────────────────────────────────────

    def get_system_health(self) -> Dict[str, Any]:
        """查詢系統健康狀態與熔斷狀態"""
        url = f"{self.base_url}/api/v1/system/health"
        with httpx.Client(timeout=self.timeout) as client:
            resp = client.get(url, headers=self._headers)
            resp.raise_for_status()
            return resp.json()

    def get_market_info(self, symbol: str, exchange: str = "binance") -> Dict[str, Any]:
        """查詢交易對精度與限制規則"""
        url = f"{self.base_url}/api/v1/market/info/{symbol}"
        params = {"exchange": exchange}
        with httpx.Client(timeout=self.timeout) as client:
            resp = client.get(url, headers=self._headers, params=params)
            resp.raise_for_status()
            return resp.json()

    def get_grid_recommendation(
        self,
        symbol: str,
        exchange: str = "binance",
        period: str = "7d"
    ) -> Dict[str, Any]:
        """依據歷史回測取得網格推薦參數"""
        url = f"{self.base_url}/api/v1/market/recommendation/{symbol}"
        params = {"exchange": exchange, "period": period}
        with httpx.Client(timeout=self.timeout) as client:
            resp = client.get(url, headers=self._headers, params=params)
            resp.raise_for_status()
            return resp.json()

    def preview_dca(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """純函數試算 DCA 階梯（不觸發任何交易所掛單或狀態變更）"""
        url = f"{self.base_url}/api/v1/dca/preview"
        with httpx.Client(timeout=self.timeout) as client:
            resp = client.post(url, headers=self._headers, json=payload)
            resp.raise_for_status()
            return resp.json()

    def list_active_bots(self) -> Dict[str, Any]:
        """查詢當前運行中的網格與 DCA 機器人"""
        with httpx.Client(timeout=self.timeout) as client:
            grids_resp = client.get(f"{self.base_url}/api/v1/grids", headers=self._headers)
            grids_data = grids_resp.json() if grids_resp.is_success else []
            dca_resp = client.get(f"{self.base_url}/api/v1/dca", headers=self._headers)
            dca_data = dca_resp.json() if dca_resp.is_success else {"bots": []}
            return {
                "grids": grids_data if isinstance(grids_data, list) else grids_data.get("grids", []),
                "dca": dca_data.get("bots", [])
            }

    # ────────────────────────────────────────────────────────
    # 策略建立與執行 (Strategy Execution)
    # ────────────────────────────────────────────────────────

    def create_grid(
        self,
        tv_symbol: str,
        lower_price: float,
        upper_price: float,
        grid_interval: float,
        order_amount: float,
        grid_type: str = "neutral",
        leverage: int = 10,
        account_mode: str = "paper",
        auto_start: bool = False,
        confirm_live: bool = False,
        target_exchange: Optional[str] = None,
        reason: Optional[str] = None,
        tv_metrics: Optional[Dict[str, Any]] = None,
        extra_params: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        根據 TradingView 決策建立網格策略。
        
        門禁檢查:
          - 若 account_mode == "live"，強制要求 confirm_live == True
        """
        if account_mode == "live" and not confirm_live:
            raise PermissionError(
                "❌ [安全守門] 建立 LIVE 網格必須顯式帶入 confirm_live=True 進行二度確認！"
            )

        exchange, canonical_symbol = resolve_to_project_symbol(
            tv_symbol, target_exchange=target_exchange
        )

        decision_reason = self._format_decision_reason(
            action_name=f"create_grid_{grid_type}",
            user_reason=reason,
            tv_symbol=tv_symbol,
            tv_metrics=tv_metrics
        )

        body: Dict[str, Any] = {
            "symbol": canonical_symbol,
            "exchange": exchange,
            "grid_type": grid_type,
            "lower_price": lower_price,
            "upper_price": upper_price,
            "grid_interval": grid_interval,
            "order_amount": order_amount,
            "leverage": leverage,
            "account_mode": account_mode,
            "auto_start": auto_start,
        }
        if extra_params:
            body.update(extra_params)

        url = f"{self.base_url}/api/v1/grids"
        query_params = {
            "source": self.source,
            "reason": decision_reason
        }

        with httpx.Client(timeout=self.timeout) as client:
            resp = client.post(url, headers=self._headers, params=query_params, json=body)
            if not resp.is_success:
                raise RuntimeError(f"POST /api/v1/grids 失敗 [{resp.status_code}]: {resp.text}")
            return resp.json()

    def create_dca(
        self,
        tv_symbol: str,
        base_order_amount: float,
        direction: str = "long",
        entry_price: Optional[float] = None,
        max_safety_orders: int = 10,
        price_deviation_percent: float = 1.5,
        price_deviation_multiplier: float = 1.2,
        order_size_multiplier: float = 1.2,
        take_profit_percent: float = 1.5,
        leverage: int = 5,
        account_mode: str = "paper",
        auto_start: bool = False,
        confirm_live: bool = False,
        target_exchange: Optional[str] = None,
        reason: Optional[str] = None,
        tv_metrics: Optional[Dict[str, Any]] = None,
        extra_params: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        根據 TradingView 決策建立 DCA 策略。
        
        門禁檢查:
          - 若 account_mode == "live"，強制要求 confirm_live == True
        """
        if account_mode == "live" and not confirm_live:
            raise PermissionError(
                "❌ [安全守門] 建立 LIVE DCA 機器人必須顯式帶入 confirm_live=True 進行二度確認！"
            )

        exchange, canonical_symbol = resolve_to_project_symbol(
            tv_symbol, target_exchange=target_exchange
        )

        decision_reason = self._format_decision_reason(
            action_name=f"create_dca_{direction}",
            user_reason=reason,
            tv_symbol=tv_symbol,
            tv_metrics=tv_metrics
        )

        body: Dict[str, Any] = {
            "symbol": canonical_symbol,
            "exchange": exchange,
            "direction": direction,
            "base_order_amount": base_order_amount,
            "max_safety_orders": max_safety_orders,
            "price_deviation_percent": price_deviation_percent,
            "price_deviation_multiplier": price_deviation_multiplier,
            "order_size_multiplier": order_size_multiplier,
            "take_profit_percent": take_profit_percent,
            "leverage": leverage,
            "account_mode": account_mode,
            "auto_start": auto_start,
        }
        if entry_price is not None:
            body["entry_price"] = entry_price
        if extra_params:
            body.update(extra_params)

        url = f"{self.base_url}/api/v1/dca"
        query_params = {
            "source": self.source,
            "reason": decision_reason
        }

        with httpx.Client(timeout=self.timeout) as client:
            resp = client.post(url, headers=self._headers, params=query_params, json=body)
            if not resp.is_success:
                raise RuntimeError(f"POST /api/v1/dca 失敗 [{resp.status_code}]: {resp.text}")
            return resp.json()

    # ────────────────────────────────────────────────────────
    # 風控管理與運行調度 (Risk Management & Lifecycle)
    # ────────────────────────────────────────────────────────

    def pause_dca_reentry(
        self,
        bot_id: str,
        reason: str = "tv_macro_blackout"
    ) -> Dict[str, Any]:
        """
        宏觀/指標風控暫停：關閉 DCA 止盈後重複循環（保持當前倉位平倉，但禁止開新循環）。
        此操作只降風險，不平倉。
        """
        url = f"{self.base_url}/api/v1/dca/{bot_id}/config"
        query_params = {
            "source": self.source,
            "reason": self._format_decision_reason("pause_dca_reentry", user_reason=reason)
        }
        body = {"repeat_after_take_profit": False}

        with httpx.Client(timeout=self.timeout) as client:
            resp = client.patch(url, headers=self._headers, params=query_params, json=body)
            if not resp.is_success:
                raise RuntimeError(f"PATCH /api/v1/dca/{bot_id}/config 失敗: {resp.text}")
            return resp.json()

    def resume_dca_reentry(
        self,
        bot_id: str,
        reason: str = "tv_macro_cleared"
    ) -> Dict[str, Any]:
        """宏觀/指標風控解除：恢復 DCA 止盈後循環"""
        url = f"{self.base_url}/api/v1/dca/{bot_id}/config"
        query_params = {
            "source": self.source,
            "reason": self._format_decision_reason("resume_dca_reentry", user_reason=reason)
        }
        body = {"repeat_after_take_profit": True}

        with httpx.Client(timeout=self.timeout) as client:
            resp = client.patch(url, headers=self._headers, params=query_params, json=body)
            if not resp.is_success:
                raise RuntimeError(f"PATCH /api/v1/dca/{bot_id}/config 失敗: {resp.text}")
            return resp.json()

    def stop_bot(
        self,
        strategy_type: str,
        bot_id: str,
        reason: str = "tv_signal_stop"
    ) -> Dict[str, Any]:
        """
        停止機器人策略。
        注意：停止策略只取消全部掛單，不主動平倉（持倉保留）。
        """
        endpoint = "grids" if strategy_type.lower() == "grid" else "dca"
        url = f"{self.base_url}/api/v1/{endpoint}/{bot_id}/stop"
        query_params = {
            "source": self.source,
            "reason": self._format_decision_reason("stop_bot", user_reason=reason)
        }

        with httpx.Client(timeout=self.timeout) as client:
            resp = client.post(url, headers=self._headers, params=query_params)
            if not resp.is_success:
                raise RuntimeError(f"POST {url} 失敗: {resp.text}")
            return resp.json()


# ────────────────────────────────────────────────────────
# CLI 指令介面
# ────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="TradingView 決策執行橋接器 (CLI)")
    parser.add_argument("--url", default="http://127.0.0.1:8001", help="API Base URL")
    parser.add_argument("--token", default=None, help="API Token")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # health
    subparsers.add_parser("health", help="檢查交易系統健康狀態")

    # parse
    p_parse = subparsers.add_parser("parse", help="解析 TradingView 符號")
    p_parse.add_argument("symbol", help="例如 BINANCE:SOLUSDC.P")

    # preview-dca
    p_prev = subparsers.add_parser("preview-dca", help="試算 DCA 階梯")
    p_prev.add_argument("symbol", help="例如 BINANCE:SOLUSDC.P")
    p_prev.add_argument("--price", type=float, required=True, help="入場價格")
    p_prev.add_argument("--amount", type=float, default=10.0, help="基礎單金額 (USD)")
    p_prev.add_argument("--direction", default="long", choices=["long", "short"])
    p_prev.add_argument("--orders", type=int, default=8, help="最大補倉次數")
    p_prev.add_argument("--leverage", type=int, default=3, help="槓桿倍數")

    # recommend-grid
    p_rec = subparsers.add_parser("recommend-grid", help="查詢網格推薦參數")
    p_rec.add_argument("symbol", help="例如 BINANCE:SOLUSDC.P")
    p_rec.add_argument("--period", default="7d", choices=["7d", "30d", "180d"])

    # create-dca-paper
    p_cdca = subparsers.add_parser("create-dca-paper", help="建立 Paper 模式 DCA")
    p_cdca.add_argument("symbol", help="例如 BINANCE:SOLUSDC.P")
    p_cdca.add_argument("--amount", type=float, required=True, help="基礎單金額")
    p_cdca.add_argument("--direction", default="long", choices=["long", "short"])
    p_cdca.add_argument("--price", type=float, default=None, help="參考入場價")
    p_cdca.add_argument("--reason", default="TV signal manual trigger", help="決策原因")

    # pause-dca
    p_pause = subparsers.add_parser("pause-dca", help="宏觀風控：暫停 DCA 止盈後重複循環")
    p_pause.add_argument("bot_id", help="DCA 機器人 ID")
    p_pause.add_argument("--reason", default="TV macro blackout", help="暫停原因")

    # resume-dca
    p_res = subparsers.add_parser("resume-dca", help="宏觀風控：恢復 DCA 止盈後循環")
    p_res.add_argument("bot_id", help="DCA 機器人 ID")
    p_res.add_argument("--reason", default="TV macro event cleared", help="恢復原因")

    # list
    subparsers.add_parser("list", help="列出當前所有運行中的機器人")

    args = parser.parse_args()
    bridge = TradingViewExecutionBridge(base_url=args.url, token=args.token)

    try:
        if args.command == "health":
            res = bridge.get_system_health()
            print(json.dumps(res, indent=2, ensure_ascii=False))

        elif args.command == "parse":
            info = bridge.resolve_symbol(args.symbol)
            print(f"TV Symbol       : {info.tv_symbol}")
            print(f"Exchange        : {info.exchange}")
            print(f"Base / Quote    : {info.base} / {info.quote}")
            print(f"Is Perpetual    : {info.is_perp}")
            print(f"Canonical Symbol: {info.canonical_symbol}")

        elif args.command == "preview-dca":
            ex, canonical = resolve_to_project_symbol(args.symbol)
            payload = {
                "symbol": canonical,
                "exchange": ex,
                "direction": args.direction,
                "entry_price": args.price,
                "base_order_amount": args.amount,
                "max_safety_orders": args.orders,
                "leverage": args.leverage
            }
            res = bridge.preview_dca(payload)
            print(json.dumps(res, indent=2, ensure_ascii=False))

        elif args.command == "recommend-grid":
            ex, canonical = resolve_to_project_symbol(args.symbol)
            res = bridge.get_grid_recommendation(canonical, exchange=ex, period=args.period)
            print(json.dumps(res, indent=2, ensure_ascii=False))

        elif args.command == "create-dca-paper":
            res = bridge.create_dca(
                tv_symbol=args.symbol,
                base_order_amount=args.amount,
                direction=args.direction,
                entry_price=args.price,
                account_mode="paper",
                auto_start=False,
                reason=args.reason
            )
            print("✅ 已成功建立 Paper DCA 配置:")
            print(json.dumps(res, indent=2, ensure_ascii=False))

        elif args.command == "pause-dca":
            res = bridge.pause_dca_reentry(args.bot_id, reason=args.reason)
            print(f"✅ 已暫停 DCA [{args.bot_id}] 止盈後循環")
            print(json.dumps(res, indent=2, ensure_ascii=False))

        elif args.command == "resume-dca":
            res = bridge.resume_dca_reentry(args.bot_id, reason=args.reason)
            print(f"✅ 已恢復 DCA [{args.bot_id}] 止盈後循環")
            print(json.dumps(res, indent=2, ensure_ascii=False))

        elif args.command == "list":
            res = bridge.list_active_bots()
            print(f"運行中網格: {len(res['grids'])} 台")
            for g in res["grids"]:
                print(f"  - Grid: {g.get('symbol')} ({g.get('grid_id')}) [{g.get('status')}]")
            print(f"運行中 DCA : {len(res['dca'])} 台")
            for d in res["dca"]:
                print(f"  - DCA : {d.get('symbol')} ({d.get('bot_id')}) [{d.get('status')}] repeat={d.get('repeat_after_take_profit', True)}")

    except Exception as e:
        print(f"❌ 執行失敗: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
