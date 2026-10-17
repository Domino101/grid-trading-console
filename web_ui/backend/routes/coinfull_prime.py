"""
CoinFull Prime 市場中性策略 Web 控制台 API 路由
"""

import json
import logging
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Query, BackgroundTasks

from web_ui.backend.auth import verify_token
from core.services.coinfull_prime.service import CoinFullPrimeService
from core.infrastructure.database.strategy_ledger import StrategyLedger

logger = logging.getLogger("WebUI.CoinFullPrime")

CFP_BOT_IDS = ("coinfull_prime:paper", "coinfull_prime:binance_testnet", "coinfull_prime:binance_live")

router = APIRouter(prefix="/api/v1/coinfull-prime", tags=["CoinFull Prime"])


@router.get("/status")
async def get_coinfull_prime_status(_token: str = Depends(verify_token)):
    """取得 CoinFull Prime 當前運行狀態、組合持倉摘要與下次換倉倒數"""
    service = CoinFullPrimeService.instance()
    return service.get_status()


@router.get("/regime")
async def get_coinfull_prime_regime(
    refresh: bool = Query(False, description="true = 重新抓取即時資料計算 pts 與決策"),
    _token: str = Depends(verify_token)
):
    """取得風險閘門 (regime gate) 最近一次判定：pts、建議倍數、平倉/停單狀態與原因"""
    service = CoinFullPrimeService.instance()
    try:
        decision = await service.get_regime(refresh=refresh)
        return {
            "status": "success",
            "regime": decision.model_dump(mode="json"),
            "schedule": {
                "rebalance_days": service._schedule_params()["rebalance_days"],
                "execution_weekday": service._schedule_params()["execution_weekday"],
                "execution_minute_utc": service._schedule_params()["execution_minute_utc"],
                "description": service._schedule_description(),
                "next_run_utc": service._next_rebalance_run().isoformat()
            }
        }
    except Exception as e:
        logger.error(f"風險閘門查詢失敗: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/rankings")
async def get_latest_rankings(
    refresh: bool = False,
    exclude_tradfi: Optional[bool] = Query(None, description="是否排除美股/ETF/大宗商品等 TradFi 合約"),
    _token: str = Depends(verify_token)
):
    """取得最新的 79 幣截面打分排名與 Z-score 詳細資料"""
    service = CoinFullPrimeService.instance()
    if not service.quant_engine:
        await service.initialize()

    ex_tradfi = service.exclude_tradfi if exclude_tradfi is None else bool(exclude_tradfi)
    need_refresh = (
        refresh
        or not service.last_rank_result
        or (getattr(service, "_last_rank_result_exclude_tradfi", None) != ex_tradfi)
    )

    if need_refresh:
        rank_result = await service.quant_engine.run_daily_pipeline(exclude_tradfi=ex_tradfi)
        service.last_rank_result = rank_result
        service._last_rank_result_exclude_tradfi = ex_tradfi
        if hasattr(service.quant_engine, "exclude_tradfi"):
            service.quant_engine.exclude_tradfi = ex_tradfi
    return service.last_rank_result


@router.get("/accounts")
async def get_trading_accounts(_token: str = Depends(verify_token)):
    """取得 CoinFull Prime 所有支援之交易帳號環境、就緒狀態與餘額"""
    service = CoinFullPrimeService.instance()
    accounts = await service.get_accounts_overview()
    return {
        "accounts": accounts,
        "selected_account": service.selected_account
    }


@router.get("/positions")
async def get_account_positions(
    account_id: str = Query("paper", description="帳號環境: paper / binance_testnet / binance_live"),
    _token: str = Depends(verify_token)
):
    """取得當前選定交易帳號之合約持倉清單"""
    service = CoinFullPrimeService.instance()
    positions = await service.get_positions(account_id)
    return {
        "account_id": account_id,
        "positions": positions,
        "total_count": len(positions)
    }


@router.post("/rebalance")
async def trigger_manual_rebalance(
    dry_run: bool = Query(True, description="是否以 Dry-Run 模式運行（不發送真實訂單）"),
    account_id: str = Query("paper", description="目標交易帳號環境: paper / binance_testnet / binance_live"),
    capital: Optional[float] = Query(None, description="自訂總名義本金 (USDT)"),
    leverage: Optional[float] = Query(None, description="自訂槓桿倍數 (例如 1.0, 1.5, 2.0)"),
    balance_pct: Optional[float] = Query(None, description="依帳戶可用餘額百分比計算 (0~100)"),
    exclude_tradfi: Optional[bool] = Query(None, description="是否排除 TradFi (美股/ETF/大宗商品)"),
    _token: str = Depends(verify_token)
):
    """手動觸發一次 CoinFull Prime 截面打分與一籃子換倉（支援動態本金與槓桿覆蓋）"""
    service = CoinFullPrimeService.instance()
    try:
        rank_result, rebalance_report = await service.trigger_once(
            dry_run=dry_run,
            account_id=account_id,
            capital=capital,
            leverage=leverage,
            balance_pct=balance_pct,
            exclude_tradfi=exclude_tradfi
        )
        return {
            "status": "success",
            "dry_run": dry_run,
            "account_id": account_id,
            "capital": rebalance_report.total_capital_usdt,
            "leverage": rebalance_report.leverage,
            "universe_size": rank_result.universe_size,
            "orders_count": len(rebalance_report.orders),
            "closed_symbols": rebalance_report.closed_symbols,
            "opened_symbols": rebalance_report.opened_symbols,
            "report": rebalance_report
        }
    except Exception as e:
        logger.error(f"手動換倉執行失敗: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/scheduler/target")
async def update_scheduler_target(
    account_id: str = Query(..., description="目標環境: binance_testnet / binance_live / paper"),
    dry_run: Optional[bool] = Query(None, description="是否以 Dry-Run 模擬"),
    capital: Optional[float] = Query(None, description="自訂本金"),
    leverage: Optional[float] = Query(None, description="槓桿倍數"),
    balance_pct: Optional[float] = Query(None, description="餘額百分比"),
    exclude_tradfi: Optional[bool] = Query(None, description="是否排除 TradFi 合約"),
    _token: str = Depends(verify_token)
):
    """即時更新並同步定時排程器的目標帳號、資金與 TradFi 設定"""
    service = CoinFullPrimeService.instance()
    status = service.set_scheduler_target(
        account_id=account_id,
        dry_run=dry_run,
        capital=capital,
        leverage=leverage,
        balance_pct=balance_pct,
        exclude_tradfi=exclude_tradfi
    )
    return {
        "status": "updated",
        "message": f"定時排程目標已更新為【{account_id}】",
        "scheduler_config": status.get("scheduler_config")
    }


@router.post("/scheduler/{action}")
async def control_scheduler(
    action: str,
    account_id: Optional[str] = Query(None, description="目標環境: binance_testnet / binance_live / paper"),
    dry_run: Optional[bool] = Query(None, description="是否僅為 Dry-Run 模擬"),
    capital: Optional[float] = Query(None, description="自訂本金"),
    leverage: Optional[float] = Query(None, description="槓桿倍數"),
    balance_pct: Optional[float] = Query(None, description="餘額百分比"),
    exclude_tradfi: Optional[bool] = Query(None, description="是否排除 TradFi 合約"),
    _token: str = Depends(verify_token)
):
    """控制排程器啟動 (start) 或停止 (stop)"""
    service = CoinFullPrimeService.instance()
    if action == "start":
        service.start_scheduler(
            account_id=account_id,
            dry_run=dry_run,
            capital=capital,
            leverage=leverage,
            balance_pct=balance_pct,
            exclude_tradfi=exclude_tradfi
        )
        return {
            "status": "started",
            "is_running": True,
            "message": "CoinFull Prime 自動定時換倉排程已啟動",
            "account_id": service.selected_account,
            "dry_run": service.scheduler_dry_run
        }
    elif action == "stop":
        service.stop_scheduler()
        return {"status": "stopped", "is_running": False, "message": "CoinFull Prime 排程已停止"}
    else:
        raise HTTPException(status_code=400, detail=f"不支援的操作: {action}，僅支援 start 或 stop")


@router.get("/fills")
async def get_coinfull_prime_fills(
    account_id: Optional[str] = Query(None, description="帳號環境: paper / binance_testnet / binance_live（未提供時查詢所有帳號）"),
    limit: int = Query(200, ge=1, le=1000, description="筆數限制"),
    include_simulated: bool = Query(True, description="是否包含 Dry-Run 模擬成交流水"),
    _token: str = Depends(verify_token)
):
    """取得 CoinFull Prime 持久化成交流水 (支援依帳號環境篩選與實盤/模擬切換)

    P4-1：讀取路徑與寫入路徑同源 —— 一律經 StrategyLedger（canonical ledger），
    PostgreSQL 真相源模式下不再讀到本機幽靈 SQLite。
    """
    try:
        ledger = StrategyLedger.instance()

        target_acc = account_id.strip() if account_id else None
        bot_ids = [f"coinfull_prime:{target_acc}"] if target_acc else list(CFP_BOT_IDS)

        all_fills = []
        for bid in bot_ids:
            all_fills.extend(ledger.get_fills(bid, limit=limit, order_desc=True))

        # 依執行時間倒序
        all_fills.sort(key=lambda x: getattr(x, "executed_at", 0.0), reverse=True)
        all_fills = all_fills[:limit]

        formatted = []
        for f in all_fills:
            is_sim = "simulated" in str(getattr(f, "role", "")).lower() or str(getattr(f, "fill_id", "")).startswith("sim_")
            if not include_simulated and is_sim:
                continue

            # 從 client_order_id / fill_id 推導 symbol（統一命名契約，見 _infer_symbol）
            sym = _infer_symbol(f)

            exec_ts = getattr(f, "executed_at", None)
            time_iso = (
                datetime.fromtimestamp(exec_ts, tz=timezone.utc).isoformat()
                if exec_ts
                else None
            )

            acc = str(getattr(f, "bot_id", "")).replace("coinfull_prime:", "")
            price = float(getattr(f, "price", 0.0))
            qty = float(getattr(f, "quantity", 0.0))
            notional = round(price * qty, 2)

            formatted.append({
                "fill_id": str(getattr(f, "fill_id", "")),
                "bot_id": str(getattr(f, "bot_id", "")),
                "account_id": acc,
                "symbol": sym,
                "side": str(getattr(f, "side", "")).lower(),
                "role": str(getattr(f, "role", "")),
                "price": price,
                "quantity": qty,
                "notional_usdt": notional,
                "fee": float(getattr(f, "fee", 0.0) or 0.0),
                "fee_currency": getattr(f, "fee_currency", "USDT") or "USDT",
                "time": time_iso,
                "is_simulated": is_sim
            })

        return {
            "account_id": target_acc,
            "total": len(formatted),
            "fills": formatted
        }
    except Exception as e:
        logger.error(f"取得 CoinFull Prime 成交流水失敗: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/history")
async def get_coinfull_prime_history(
    account_id: Optional[str] = Query(None, description="帳號環境: paper / binance_testnet / binance_live"),
    limit: int = Query(50, ge=1, le=200, description="換倉批次限制"),
    _token: str = Depends(verify_token)
):
    """取得 CoinFull Prime 歷史換倉批次與詳細訂單紀錄（canonical ledger bot_events）"""
    try:
        ledger = StrategyLedger.instance()

        target_acc = account_id.strip() if account_id else None
        bot_ids = [f"coinfull_prime:{target_acc}"] if target_acc else list(CFP_BOT_IDS) + ["coinfull_prime"]

        all_events = []
        for bid in bot_ids:
            for ev in ledger.get_events(bid, limit=limit):
                if getattr(ev, "event_type", "") in ("daily_rebalance", "manual_rebalance"):
                    all_events.append(ev)

        # 依建立時間倒序
        all_events.sort(key=lambda x: getattr(x, "created_at", 0.0), reverse=True)
        all_events = all_events[:limit]

        batches = []
        for ev in all_events:
            payload = getattr(ev, "payload", {})
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except Exception:
                    payload = {}
            if not isinstance(payload, dict):
                payload = {}

            created_ts = getattr(ev, "created_at", 0.0)
            time_iso = (
                datetime.fromtimestamp(created_ts, tz=timezone.utc).isoformat()
                if created_ts
                else payload.get("timestamp")
            )

            batches.append({
                "id": getattr(ev, "id", 0),
                "seq": getattr(ev, "seq", 0),
                "bot_id": getattr(ev, "bot_id", ""),
                "timestamp": time_iso,
                "account_id": payload.get("account_id") or str(getattr(ev, "bot_id", "")).replace("coinfull_prime:", ""),
                "dry_run": bool(payload.get("dry_run", True)),
                "capital": payload.get("capital"),
                "leverage": payload.get("leverage"),
                "batch_id": payload.get("batch_id"),
                "orders_count": payload.get("orders_count") or len(payload.get("orders", [])),
                "realized_pnl_usdt": payload.get("realized_pnl_usdt"),
                "fees_usdt": payload.get("fees_usdt"),
                "gate": payload.get("gate"),
                "closed_symbols": payload.get("closed_symbols") or [],
                "opened_symbols": opened_syms if (opened_syms := payload.get("opened_symbols")) else [],
                "status": payload.get("status") or "completed",
                "orders": payload.get("orders") or []
            })

        return {
            "account_id": target_acc,
            "total": len(batches),
            "batches": batches
        }
    except Exception as e:
        logger.error(f"取得 CoinFull Prime 換倉歷史失敗: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


QUOTE_SUFFIXES = ("USDT", "USDC", "USD", "BUSD", "BTC", "ETH")


def _infer_symbol(fill: Any) -> str:
    """由 client_order_id / fill_id 推導標的。

    canonical ``trade_fills`` 沒有 symbol 欄位（稽核發現 D-08），因此各策略以
    穩定命名契約把 symbol 編入 client_order_id；本函式同時容忍舊格式。
    """
    for field_value in (getattr(fill, "client_order_id", ""), getattr(fill, "fill_id", "")):
        tokens = [t for t in str(field_value or "").split("_") if t]
        for token in reversed(tokens):
            upper = token.upper()
            if len(upper) >= 6 and upper.endswith(QUOTE_SUFFIXES) and not upper.startswith(("CFP", "SIM")):
                return upper
    return "UNKNOWN"


@router.get("/regime/history")
async def get_coinfull_prime_regime_history(
    account_id: Optional[str] = Query(None, description="帳號環境（未提供時查詢所有帳號）"),
    limit: int = Query(200, ge=1, le=2000, description="筆數限制"),
    _token: str = Depends(verify_token)
):
    """風險閘門決策歷史（canonical ledger ``bot_regime_decisions``，非 JSONL 檔案）"""
    service = CoinFullPrimeService.instance()
    try:
        return {
            "account_id": account_id,
            "source": "canonical_ledger:bot_regime_decisions",
            "total": len(service.load_regime_history(account_id=account_id, limit=limit)),
            "decisions": service.load_regime_history(account_id=account_id, limit=limit),
        }
    except Exception as e:
        logger.error(f"取得風險閘門歷史失敗: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/basket")
async def get_coinfull_prime_basket(
    account_id: Optional[str] = Query(None, description="帳號環境（預設為目前排程目標）"),
    _token: str = Depends(verify_token)
):
    """受管持倉籃子（canonical ledger ``bot_baskets``；含均價與已實現損益）"""
    service = CoinFullPrimeService.instance()
    target_acc = (account_id or service.selected_account or "paper").strip()
    try:
        entries = service.load_basket_entries(target_acc)
        gross = round(sum(e.notional_usdt or 0.0 for e in entries), 2)
        return {
            "account_id": target_acc,
            "source": "canonical_ledger:bot_baskets",
            "count": len(entries),
            "gross_notional_usdt": gross,
            "long_notional_usdt": round(sum(e.notional_usdt or 0.0 for e in entries if e.side == "long"), 2),
            "short_notional_usdt": round(sum(e.notional_usdt or 0.0 for e in entries if e.side == "short"), 2),
            "cumulative_realized_pnl_usdt": round(sum(e.realized_pnl or 0.0 for e in entries), 8),
            "managed_symbols": sorted({e.symbol for e in entries}),
            "entries": [
                {
                    "symbol": e.symbol,
                    "side": e.side,
                    "quantity": e.quantity,
                    "entry_price": e.entry_price,
                    "notional_usdt": e.notional_usdt,
                    "realized_pnl": e.realized_pnl,
                    "status": e.status,
                    "updated_at": e.updated_at,
                }
                for e in entries
            ],
        }
    except Exception as e:
        logger.error(f"取得受管籃子失敗: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/accounting")
async def get_coinfull_prime_accounting(_token: str = Depends(verify_token)):
    """統一記帳層健康度（寫入成功/失敗計數、後端、最近錯誤）— 供前端與稽核使用"""
    try:
        ledger = StrategyLedger.instance()
        health = ledger.health()
        service = CoinFullPrimeService.instance()
        target_acc = service.selected_account or "paper"
        bot_id = f"coinfull_prime:{target_acc}"
        snapshot = ledger.load_snapshot(bot_id)
        return {
            "backend": health.get("backend"),
            "database_path": health.get("database_path"),
            "health": health,
            "bot_id": bot_id,
            "latest_snapshot": (snapshot[0] if snapshot else None),
            "latest_snapshot_at": (snapshot[1] if snapshot else None),
            "fills_total": len(ledger.get_fills(bot_id, limit=100000)),
            "events_total": len(ledger.get_events(bot_id, limit=100000)),
        }
    except Exception as e:
        logger.error(f"取得記帳健康度失敗: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/stop-loss/check")
async def trigger_stop_loss_check(_token: str = Depends(verify_token)):
    """手動觸發一次盤中硬止損浮虧檢測並執行必要平倉（v3.0 Circuit Breaker）"""
    try:
        service = CoinFullPrimeService.instance()
        risk_cfg = service.config.get("risk_control", {}) or {}
        sl_pct = float(risk_cfg.get("intra_week_stop_loss_pct", -0.12))
        results = await service.check_and_trigger_stop_losses(sl_pct=sl_pct)
        return {
            "status": "success",
            "threshold_pct": sl_pct,
            "triggered_count": len(results),
            "results": results
        }
    except Exception as e:
        logger.error(f"觸發盤中止損檢測失敗: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

