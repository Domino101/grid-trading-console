"""
CoinFull Prime 市場中性策略 Web 控制台 API 路由
"""

import logging
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, BackgroundTasks

from web_ui.backend.auth import verify_token
from core.services.coinfull_prime.service import CoinFullPrimeService

logger = logging.getLogger("WebUI.CoinFullPrime")

router = APIRouter(prefix="/api/v1/coinfull-prime", tags=["CoinFull Prime"])


@router.get("/status")
async def get_coinfull_prime_status(_token: str = Depends(verify_token)):
    """取得 CoinFull Prime 當前運行狀態、組合持倉摘要與下次換倉倒數"""
    service = CoinFullPrimeService.instance()
    return service.get_status()


@router.get("/rankings")
async def get_latest_rankings(refresh: bool = False, _token: str = Depends(verify_token)):
    """取得最新的 79 幣截面打分排名與 Z-score 詳細資料"""
    service = CoinFullPrimeService.instance()
    if refresh or not service.last_rank_result:
        if not service.quant_engine:
            await service.initialize()
        rank_result = await service.quant_engine.run_daily_pipeline()
        service.last_rank_result = rank_result
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
            balance_pct=balance_pct
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
    _token: str = Depends(verify_token)
):
    """即時更新並同步定時排程器的目標帳號與資金設定"""
    service = CoinFullPrimeService.instance()
    status = service.set_scheduler_target(
        account_id=account_id,
        dry_run=dry_run,
        capital=capital,
        leverage=leverage,
        balance_pct=balance_pct
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
            balance_pct=balance_pct
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
