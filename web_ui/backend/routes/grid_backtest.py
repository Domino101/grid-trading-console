"""
web_ui/backend/routes/grid_backtest.py —— 網格完整回測端點

POST /api/v1/grid-backtest/run
POST /api/v1/grid-backtest/stress
POST /api/v1/grid-backtest/sweep
"""

from __future__ import annotations

import asyncio
import os
import sys
import logging
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, status

_project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../"))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from web_ui.backend.auth import verify_token
from web_ui.backend.audit import AuditLogger
from web_ui.backend.grid_backtest_service import (
    run_grid_backtest,
    run_grid_stress,
    run_grid_sweep,
)
from web_ui.backend.schemas import (
    GridBacktestRequest,
    GridBacktestResponse,
    GridBacktestStressResponse,
    GridBacktestSweepResponse,
)

logger = logging.getLogger("GridAPI.GridBacktest")
router = APIRouter(prefix="/api/v1/grid-backtest", tags=["grid-backtest"])


def _audit(action: str, params: Dict[str, Any], result: str, error_msg: str = "") -> None:
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../"))
    logger_audit = AuditLogger.get_instance(project_root)
    logger_audit.log(
        source=str(params.get("source") or "manual"),
        action=action,
        grid_id=str(params.get("symbol") or ""),
        params={k: v for k, v in params.items() if k not in ("token",)},
        result=result,
        reason=None,
        error_msg=error_msg or None,
    )


@router.post("/run", response_model=GridBacktestResponse)
async def post_grid_backtest(
    payload: GridBacktestRequest,
    _token: str = Depends(verify_token),
):
    params = payload.model_dump()
    try:
        result = await run_grid_backtest(params)
        _audit("run_grid_backtest", params, "success")
        return GridBacktestResponse(result=result)
    except ValueError as exc:
        _audit("run_grid_backtest", params, "rejected", str(exc))
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except asyncio.TimeoutError:
        _audit("run_grid_backtest", params, "error", "回測逾時")
        raise HTTPException(status_code=status.HTTP_408_REQUEST_TIMEOUT, detail="回測執行逾時")
    except Exception as exc:  # pragma: no cover - defensive
        logger.error("grid backtest failed: %s", exc, exc_info=True)
        _audit("run_grid_backtest", params, "error", str(exc))
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"回測執行失敗: {exc}")


@router.post("/stress", response_model=GridBacktestStressResponse)
async def post_grid_stress(
    payload: GridBacktestRequest,
    _token: str = Depends(verify_token),
):
    params = payload.model_dump()
    try:
        result = await run_grid_stress(params)
        _audit("run_grid_stress", params, "success")
        return result
    except ValueError as exc:
        _audit("run_grid_stress", params, "rejected", str(exc))
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except Exception as exc:  # pragma: no cover - defensive
        logger.error("grid stress failed: %s", exc, exc_info=True)
        _audit("run_grid_stress", params, "error", str(exc))
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"壓力情境執行失敗: {exc}")


@router.post("/sweep", response_model=GridBacktestSweepResponse)
async def post_grid_sweep(
    payload: GridBacktestRequest,
    _token: str = Depends(verify_token),
):
    params = payload.model_dump()
    try:
        result = await run_grid_sweep(params)
        _audit("run_grid_sweep", params, "success")
        return result
    except ValueError as exc:
        _audit("run_grid_sweep", params, "rejected", str(exc))
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except Exception as exc:  # pragma: no cover - defensive
        logger.error("grid sweep failed: %s", exc, exc_info=True)
        _audit("run_grid_sweep", params, "error", str(exc))
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"參數掃描執行失敗: {exc}")
