"""
系統健康狀態端點

GET /api/v1/system/health — 回傳系統健康狀態（熔斷/API餘量/運行中網格數）
所有資料來源均為本地記憶體或檔案，零 API 權重。
"""

import os
import sys
import json
import time
import logging
from datetime import datetime
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query

# 加入專案根目錄到 sys.path 以便匯入 core 模組
_project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../"))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from web_ui.backend.auth import verify_token
from web_ui.backend.schemas import SystemHealthResponse, CircuitBreakerInfo, ApiThrottleInfo, EngineInfo, AuditLogListResponse
from web_ui.backend.safety_guard import SafetyGuard, MAX_CONCURRENT_GRIDS
from web_ui.backend.audit import AuditLogger
from web_ui.backend.grid_state_store import GridStateStore
from web_ui.backend.lifecycle import data_status_from_runtime, live_trading_enabled

logger = logging.getLogger("GridAPI.System")

router = APIRouter(prefix="/api/v1/system", tags=["system"])


@router.get("/health", response_model=SystemHealthResponse)
async def get_system_health(_token: str = Depends(verify_token)):
    """
    系統健康狀態。

    資料來源：
    - 熔斷狀態：BinanceRest 類變量（零 API 權重）
    - API 限速器統計：BinanceRest 類變量
    - 運行中網格：active_grids.json（本地檔案）
    - 引擎進程：/tmp/grid_multiple_runners.pid
    """
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../"))
    guard = SafetyGuard(project_root)

    # ── 熔斷器狀態 ──
    circuit_breaker = CircuitBreakerInfo(is_open=False, error_code=None, until=None)
    try:
        from core.adapters.exchanges.adapters.binance_rest import BinanceRest

        is_open = BinanceRest._is_adapter_circuit_open()
        circuit_breaker.is_open = is_open
        if is_open:
            circuit_breaker.error_code = BinanceRest._adapter_last_error_code
            until_ts = BinanceRest._adapter_circuit_until
            if until_ts != float("inf"):
                circuit_breaker.until = datetime.fromtimestamp(until_ts).isoformat()
            else:
                circuit_breaker.until = "permanent"
    except ImportError:
        pass

    # ── API 限速器統計 ──
    api_throttle = ApiThrottleInfo(total_calls=0, window_current=0, window_max=2400)
    try:
        from core.adapters.exchanges.adapters.binance_rest import BinanceRest as _BR

        ts = _BR.get_throttle_stats()
        api_throttle.total_calls = ts.get("total_calls", 0)
        api_throttle.window_current = ts.get("window_current", 0)
        api_throttle.window_max = ts.get("window_max", _BR._throttle_max_calls)
    except ImportError:
        pass

    # ── 引擎進程狀態 ──
    engine_pid = guard.get_engine_pid()
    engine = EngineInfo(
        process_running=engine_pid is not None,
        pid=engine_pid,
    )

    # Desired state is not evidence the engine is running.
    active_grids = guard._read_active_grids()
    desired_configs = [path for path, enabled in active_grids.items() if enabled]
    store = GridStateStore.instance()
    running_configs = [
        path for path in desired_configs
        if data_status_from_runtime(store.get(path)) == "live"
    ]
    running_count = len(running_configs)
    desired_count = len(desired_configs)

    if circuit_breaker.is_open:
        overall_status = "unhealthy"
    elif not engine.process_running:
        overall_status = "degraded"
    else:
        overall_status = "healthy"

    can_accept = (
        overall_status == "healthy"
        and running_count < MAX_CONCURRENT_GRIDS
        and not circuit_breaker.is_open
    )

    return SystemHealthResponse(
        status=overall_status,
        circuit_breaker=circuit_breaker,
        api_throttle=api_throttle,
        engine=engine,
        running_grids=running_count,
        desired_running_grids=desired_count,
        running_grid_configs=running_configs,
        max_recommended_grids=MAX_CONCURRENT_GRIDS,
        can_accept_new_grid=can_accept,
        live_trading_enabled=live_trading_enabled(),
    )


@router.get("/audit", response_model=AuditLogListResponse)
async def get_audit_logs(
    limit: int = Query(default=100, ge=1, le=1000, description="獲取限制數"),
    offset: int = Query(default=0, ge=0, description="偏移量"),
    source: Optional[str] = Query(default=None, description="依來源過濾"),
    _token: str = Depends(verify_token),
):
    """
    查詢網格系統的操作審計日誌。

    資料來源為本地 SQLite 資料庫，零 API 權重。
    """
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../"))
    logger.info(f"🔍 查詢審計日誌: limit={limit}, offset={offset}, source={source}")
    
    audit_logger = AuditLogger.get_instance(project_root)
    logs = audit_logger.get_logs(limit=limit, offset=offset, source=source)
    
    return AuditLogListResponse(
        total=len(logs),
        logs=logs
    )


@router.get("/ledger-audit")
async def get_ledger_audit(
    backfill: bool = Query(default=False, description="true = 先執行檔案型帳本冪等回填再稽核"),
    policy_scan: bool = Query(default=True, description="是否包含程式碼路徑政策掃描（L4）"),
    markdown: bool = Query(default=False, description="true = 回傳 Markdown 報告而非 JSON"),
    _token: str = Depends(verify_token),
):
    """統一記帳一致性稽核（grid / dca / coinfull_prime 三策略）。

    以帳本不變式驗證「三者是否真的共用同一套資料庫記帳」：
    孤兒成交、幽靈後端、事件冪等、記帳契約、檔案型帳本分歧、會計恆等式、
    操作稽核日誌健康度等 15 項檢查（L1–L15）。唯讀、零交易所 API 權重。
    """
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../"))
    try:
        from core.infrastructure.database.ledger_audit import run_audit
        from core.infrastructure.database.ledger_backfill import backfill_coinfull_prime_side_files

        backfill_stats = None
        if backfill:
            backfill_stats = backfill_coinfull_prime_side_files(
                data_dir=os.path.join(project_root, "data")
            )
        report = run_audit(
            project_root=project_root,
            include_policy_scan=policy_scan,
            data_dir=os.path.join(project_root, "data"),
        )
        payload = report.to_dict()
        payload["audit_log"] = AuditLogger.get_instance(project_root).health()
        if backfill_stats is not None:
            payload["backfill"] = backfill_stats
        if markdown:
            from fastapi.responses import PlainTextResponse

            # media_type 不可自帶 charset：PlainTextResponse 會再附加一次（content-type 重複）
            return PlainTextResponse(report.to_markdown(), media_type="text/markdown")
        return payload
    except Exception as exc:
        logger.error(f"統一記帳稽核執行失敗: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))
