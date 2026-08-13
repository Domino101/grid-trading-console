"""
Grid Lifecycle CRUD 端點

POST   /api/v1/grids              — 建立新網格
GET    /api/v1/grids              — 列出所有網格
GET    /api/v1/grids/{grid_id}    — 單一網格詳細資訊
POST   /api/v1/grids/{grid_id}/start  — 啟動網格
POST   /api/v1/grids/{grid_id}/stop   — 停止網格
DELETE /api/v1/grids/{grid_id}    — 停止+刪除
"""

import os
import sys
import json
import logging
from pathlib import Path
from typing import Optional

import yaml
from fastapi import APIRouter, Depends, Header, HTTPException, status, Query
from audit import AuditLogger

# 加入專案根目錄到 sys.path 以便匯入 core 模組
_project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../"))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from auth import verify_token
from schemas import (
    CreateGridRequest,
    GridCreateResponse,
    GridActionResponse,
    GridInfo,
    GridListResponse,
    GridStatsResponse,
    GridSummaryResponse,
)
from yaml_generator import GridYAMLGenerator
from safety_guard import SafetyGuard
from grid_state_store import GridStateStore
from command_repository import GridCommandRepository

logger = logging.getLogger("GridAPI.Grids")

router = APIRouter(prefix="/api/v1/grids", tags=["grids"])

# ── 共用工具 ──

_generator = GridYAMLGenerator()


def _get_project_root() -> str:
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../"))


def _get_active_grids_path() -> str:
    return os.path.join(_get_project_root(), "config", "active_grids.json")


def _read_active_grids() -> dict:
    path = _get_active_grids_path()
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"讀取 active_grids.json 失敗: {e}")
        return {}


def _write_active_grids(data: dict) -> None:
    path = _get_active_grids_path()
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)


def _read_yaml_config(config_path: str) -> Optional[dict]:
    """安全地讀取 YAML 配置檔"""
    project_root = _get_project_root()
    abs_path = os.path.join(project_root, config_path)

    # 路徑安全檢查：確保在 config/grid/ 目錄下
    config_grid_dir = os.path.join(project_root, "config", "grid")
    resolved = os.path.realpath(abs_path)
    if not resolved.startswith(os.path.realpath(config_grid_dir) + os.sep):
        return None

    if not os.path.exists(abs_path):
        return None
    try:
        with open(abs_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)
    except Exception:
        return None


def _build_grid_info(config_path: str, active_grids: dict) -> GridInfo:
    """從配置路徑構建 GridInfo"""
    grid_id = GridYAMLGenerator.grid_id_from_path(config_path)
    is_active = active_grids.get(config_path, False)

    info = GridInfo(
        grid_id=grid_id,
        config_path=config_path,
        status="running" if is_active else "stopped",
    )

    # 嘗試讀取 YAML 擴充資訊
    yaml_data = _read_yaml_config(config_path)
    if yaml_data:
        gs = yaml_data.get("grid_system", {})
        info.symbol = gs.get("symbol")
        info.exchange = gs.get("exchange")
        info.grid_type = gs.get("grid_type")
        info.grid_interval = gs.get("grid_interval")
        info.order_amount = gs.get("order_amount")
        info.leverage = gs.get("leverage")
        pr = gs.get("price_range", {})
        if pr:
            info.lower_price = pr.get("lower_price")
            info.upper_price = pr.get("upper_price")

    return info


# ────────────────────────────────────────────────────────
# GET /api/v1/grids — 列出所有網格
# ────────────────────────────────────────────────────────

@router.get("", response_model=GridListResponse)
async def list_grids(_token: str = Depends(verify_token)):
    """列出所有已註冊的網格（掃描 active_grids.json + config/grid/ 目錄）"""
    project_root = _get_project_root()
    active_grids = _read_active_grids()

    # 收集所有 config/grid/ 下的 YAML（以 active_grids 為主，再補上磁碟中的）
    config_grid_dir = os.path.join(project_root, "config", "grid")
    known_paths = set(active_grids.keys())

    # 掃描磁碟上的 YAML 檔案
    if os.path.isdir(config_grid_dir):
        for f in os.listdir(config_grid_dir):
            if f.endswith(".yaml") and not f.startswith("README"):
                rel_path = f"config/grid/{f}"
                known_paths.add(rel_path)

    grids = []
    for config_path in sorted(known_paths):
        grids.append(_build_grid_info(config_path, active_grids))

    active_count = sum(1 for g in grids if g.status == "running")

    return GridListResponse(
        total=len(grids),
        active=active_count,
        grids=grids,
    )


# ────────────────────────────────────────────────────────
# GET /api/v1/grids/summary — 全局 PnL 聚合
# ────────────────────────────────────────────────────────

@router.get("/summary", response_model=GridSummaryResponse)
async def get_grids_summary(_token: str = Depends(verify_token)):
    """取得所有運行中網格的全局 PnL 與統計數據聚合"""
    store = GridStateStore.instance()
    summary = store.get_summary()
    
    # 我們需要更新 grids 的 status，因為 store 只知道 grid 丟過來的資訊。
    # status 應該跟 active_grids.json 的運行狀態對齊
    active_grids = _read_active_grids()
    
    store_grids_paths = {g["config_path"] for g in summary["grids"]}
    
    updated_grids = []
    active_count = 0
    
    for g in summary["grids"]:
        config_path = g["config_path"]
        is_active = active_grids.get(config_path, False)
        g["status"] = "running" if is_active else "stopped"
        if is_active:
            active_count += 1
        updated_grids.append(g)
        
    # 補上那些在 active_grids 裡是 True，但是 state store 裡還沒有數據的網格
    for config_path, is_active in active_grids.items():
        if is_active and config_path not in store_grids_paths:
            # 讀取 YAML 獲得 symbol
            yaml_data = _read_yaml_config(config_path)
            symbol = "unknown"
            if yaml_data:
                symbol = yaml_data.get("grid_system", {}).get("symbol", "unknown")
            grid_id = GridYAMLGenerator.grid_id_from_path(config_path)
            updated_grids.append({
                "grid_id": grid_id,
                "config_path": config_path,
                "symbol": symbol,
                "realized_pnl": 0.0,
                "unrealized_pnl": 0.0,
                "total_pnl": 0.0,
                "current_price": 0.0,
                "status": "running",
                "stale": True,
            })
            active_count += 1
            
    summary["grids"] = updated_grids
    summary["active_grids"] = active_count
    summary["total_grids"] = len(updated_grids)
    
    return summary


# ────────────────────────────────────────────────────────
# GET /api/v1/grids/{grid_id}/stats — 單一網格即時統計
# ────────────────────────────────────────────────────────

@router.get("/{grid_id}/stats", response_model=GridStatsResponse)
async def get_grid_stats(grid_id: str, _token: str = Depends(verify_token)):
    """取得單一網格的即時統計（PnL、掛單、持倉）"""
    config_path = GridYAMLGenerator.path_from_grid_id(grid_id)
    project_root = _get_project_root()
    abs_path = os.path.join(project_root, config_path)

    if not os.path.exists(abs_path):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"網格 {grid_id} 不存在",
        )

    active_grids = _read_active_grids()
    is_active = active_grids.get(config_path, False)
    
    store = GridStateStore.instance()
    stats = store.get(config_path)
    
    if not stats:
        # 如果 state store 裡還沒有數據，回傳一個空結構，並標記 stale: True
        # 讀取 YAML 獲得 symbol
        yaml_data = _read_yaml_config(config_path)
        symbol = "unknown"
        if yaml_data:
            symbol = yaml_data.get("grid_system", {}).get("symbol", "unknown")
            
        return GridStatsResponse(
            grid_id=grid_id,
            symbol=symbol,
            status="running" if is_active else "stopped",
            stale=True,
            current_price=0.0,
            current_position=0.0,
            average_cost=None,
            pending_orders={"buy": 0, "sell": 0, "total": 0},
            filled_orders={"buy": 0, "sell": 0, "cycles": 0},
            profit={"realized": 0.0, "unrealized": 0.0, "total": 0.0, "fees": 0.0, "net": 0.0, "rate": 0.0},
            grid_utilization=0.0,
            balance={"spot": 0.0, "collateral": 0.0, "total": 0.0, "utilization": 0.0},
            time={"running_time": "0s", "last_trade": None},
            last_update=None,
        )
        
    stats["status"] = "running" if is_active else "stopped"
    return stats


# ────────────────────────────────────────────────────────
# GET /api/v1/grids/{grid_id} — 單一網格詳細資訊
# ────────────────────────────────────────────────────────

@router.get("/{grid_id}", response_model=GridInfo)
async def get_grid(grid_id: str, _token: str = Depends(verify_token)):
    """取得單一網格詳細資訊"""
    config_path = GridYAMLGenerator.path_from_grid_id(grid_id)
    project_root = _get_project_root()
    abs_path = os.path.join(project_root, config_path)

    if not os.path.exists(abs_path):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"網格 {grid_id} 不存在",
        )

    active_grids = _read_active_grids()
    return _build_grid_info(config_path, active_grids)


# ────────────────────────────────────────────────────────
# POST /api/v1/grids — 建立新網格
# ────────────────────────────────────────────────────────

@router.post("", response_model=GridCreateResponse, status_code=status.HTTP_201_CREATED)
async def create_grid(
    request: CreateGridRequest,
    _token: str = Depends(verify_token),
):
    """
    建立新網格。

    流程：
    1. 參數驗證
    2. 安全護欄檢查（頻率限制/熔斷/上限/引擎存活/目錄可寫/最小投資）
    3. 重複交易對檢查
    4. 生成 YAML 寫入磁碟
    5. 若 auto_start=true，寫入 active_grids.json 觸發 5 秒熱插拔
    """
    project_root = _get_project_root()
    guard = SafetyGuard(project_root)
    audit_logger = AuditLogger.get_instance(project_root)
    params = request.model_dump(exclude_none=True)

    metadata = params.get("metadata", {})
    source = metadata.get("source", "hermes")
    reason = metadata.get("reason")

    # 0. 寫操作頻率限制檢查
    can_proceed, limit_msg = guard.check_rate_limit(source)
    if not can_proceed:
        audit_logger.log(
            source=source,
            action="create_grid",
            grid_id=None,
            params=params,
            result="rejected",
            reason=reason,
            error_msg=limit_msg,
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=limit_msg,
        )

    # 1. 參數驗證
    errors = _generator.validate_params(params)
    if errors:
        audit_logger.log(
            source=source,
            action="create_grid",
            grid_id=None,
            params=params,
            result="rejected",
            reason=reason,
            error_msg=f"參數驗證失敗: {errors}",
        )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"message": "參數驗證失敗", "errors": errors},
        )

    # 2. 安全護欄
    can_create, err_msg = guard.check_can_create_grid()
    if not can_create:
        audit_logger.log(
            source=source,
            action="create_grid",
            grid_id=None,
            params=params,
            result="rejected",
            reason=reason,
            error_msg=err_msg,
        )
        # 根據錯誤類型回傳不同 HTTP 狀態碼
        if "熔斷" in (err_msg or ""):
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=err_msg)
        elif "最大" in (err_msg or ""):
            raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=err_msg)
        else:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=err_msg)

    # 2.5 最小投資額驗證
    can_invest, invest_msg = guard.check_min_investment(params)
    if not can_invest:
        audit_logger.log(
            source=source,
            action="create_grid",
            grid_id=None,
            params=params,
            result="rejected",
            reason=reason,
            error_msg=invest_msg,
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=invest_msg,
        )

    # 3. 重複交易對檢查
    can_proceed, dup_msg = guard.check_duplicate_symbol(params["symbol"])
    if not can_proceed:
        audit_logger.log(
            source=source,
            action="create_grid",
            grid_id=None,
            params=params,
            result="rejected",
            reason=reason,
            error_msg=dup_msg,
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=dup_msg,
        )

    # 4. 生成 YAML
    yaml_content, config_path = _generator.generate(params)
    grid_id = GridYAMLGenerator.grid_id_from_path(config_path)

    # 檢查配置檔案是否已存在
    can_write, exist_msg = guard.check_config_not_exists(config_path)
    if not can_write:
        audit_logger.log(
            source=source,
            action="create_grid",
            grid_id=grid_id,
            params=params,
            result="rejected",
            reason=reason,
            error_msg=exist_msg,
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=exist_msg,
        )

    # 寫入 YAML 檔案
    abs_yaml_path = os.path.join(project_root, config_path)
    try:
        with open(abs_yaml_path, "w", encoding="utf-8") as f:
            f.write(yaml_content)
        logger.info(f"✅ 已建立網格配置: {config_path}")
    except Exception as e:
        audit_logger.log(
            source=source,
            action="create_grid",
            grid_id=grid_id,
            params=params,
            result="error",
            reason=reason,
            error_msg=f"寫入 YAML 配置檔失敗: {e}",
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"寫入 YAML 配置檔失敗: {e}",
        )

    # 5. auto_start → 寫入 active_grids.json
    grid_status = "created"
    if params.get("auto_start", True):
        try:
            active_grids = _read_active_grids()
            active_grids[config_path] = True
            _write_active_grids(active_grids)
            grid_status = "created_and_started"
            logger.info(f"▶️ 已將 {config_path} 加入 active_grids.json 並啟用")
        except Exception as e:
            # YAML 已寫入但 active_grids 更新失敗
            logger.error(f"⚠️ 更新 active_grids.json 失敗: {e}")
            grid_status = "created"

    # 記錄寫操作時間戳到 RateLimiter
    guard.record_action(source)

    # 成功記錄審計日誌
    audit_logger.log(
        source=source,
        action="create_grid",
        grid_id=grid_id,
        params=params,
        result="success",
        reason=reason,
    )

    if metadata:
        logger.info(
            f"📝 [Hermes] 建立網格 {grid_id} | "
            f"source={source} | "
            f"reason={reason or 'N/A'} | "
            f"confidence={metadata.get('confidence', 'N/A')}"
        )

    return GridCreateResponse(
        grid_id=grid_id,
        config_path=config_path,
        status=grid_status,
        yaml_content=yaml_content,
        message=f"網格 {grid_id} 已成功建立" + (
            "，引擎將在 ~5 秒內自動啟動" if grid_status == "created_and_started" else ""
        ),
    )


# ────────────────────────────────────────────────────────
# POST /api/v1/grids/{grid_id}/start — 啟動網格
# ────────────────────────────────────────────────────────

@router.post("/{grid_id}/start", response_model=GridActionResponse)
async def start_grid(
    grid_id: str,
    source: str = Query(default="hermes", description="來源"),
    reason: Optional[str] = Query(default=None, description="原因"),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    _token: str = Depends(verify_token),
):
    """
    啟動指定網格。

    更新 active_grids.json 中該配置為 true，
    run_multiple_grids.py 的 active_grids_monitor() 會在 5 秒內偵測到並啟動。
    """
    project_root = _get_project_root()
    guard = SafetyGuard(project_root)
    audit_logger = AuditLogger.get_instance(project_root)

    # 0. 寫操作頻率限制檢查
    can_proceed, limit_msg = guard.check_rate_limit(source)
    if not can_proceed:
        audit_logger.log(
            source=source,
            action="start",
            grid_id=grid_id,
            params={"grid_id": grid_id},
            result="rejected",
            reason=reason,
            error_msg=limit_msg,
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=limit_msg,
        )

    config_path = GridYAMLGenerator.path_from_grid_id(grid_id)
    abs_path = os.path.join(project_root, config_path)

    if not os.path.exists(abs_path):
        audit_logger.log(
            source=source,
            action="start",
            grid_id=grid_id,
            params={"grid_id": grid_id},
            result="rejected",
            reason=reason,
            error_msg=f"網格配置 {grid_id} 不存在",
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"網格配置 {grid_id} 不存在",
        )

    command_repo = GridCommandRepository.instance()
    if idempotency_key:
        existing = command_repo.get_by_idempotency_key("start", idempotency_key)
        if existing:
            return GridActionResponse(
                grid_id=existing["grid_id"], action="start", status=existing["status"],
                command_id=existing["command_id"], message="重複請求已回傳原始命令",
            )

    # 安全護欄
    can_create, err_msg = guard.check_can_create_grid()
    if not can_create:
        audit_logger.log(
            source=source,
            action="start",
            grid_id=grid_id,
            params={"grid_id": grid_id},
            result="rejected",
            reason=reason,
            error_msg=err_msg,
        )
        if "熔斷" in (err_msg or ""):
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=err_msg)
        else:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=err_msg)

    active_grids = _read_active_grids()

    # 已經在運行中
    if active_grids.get(config_path, False):
        return GridActionResponse(
            grid_id=grid_id,
            action="start",
            status="already_running",
            message=f"網格 {grid_id} 已在運行中",
        )

    command = GridCommandRepository.instance().create(
        grid_id=grid_id, config_path=config_path, action="start", source=source, reason=reason,
        idempotency_key=idempotency_key,
    )
    try:
        active_grids[config_path] = True
        _write_active_grids(active_grids)
        GridCommandRepository.instance().update_status(command["command_id"], "pending_engine")
        logger.info(f"▶️ 已接受啟動網格命令: {grid_id}, command={command['command_id']}")

        # 記錄寫操作時間戳
        guard.record_action(source)

        # 寫入審計日誌
        audit_logger.log(
            source=source,
            action="start",
            grid_id=grid_id,
            params={"grid_id": grid_id},
            result="success",
            reason=reason,
        )

        return GridActionResponse(
            grid_id=grid_id,
            action="start",
            status="pending_engine",
            command_id=command["command_id"],
            message=f"網格 {grid_id} 的啟動命令已接受，等待引擎 heartbeat 確認",
        )
    except Exception as e:
        GridCommandRepository.instance().update_status(command["command_id"], "failed", str(e))
        audit_logger.log(
            source=source,
            action="start",
            grid_id=grid_id,
            params={"grid_id": grid_id},
            result="error",
            reason=reason,
            error_msg=f"啟動網格異常: {e}",
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"啟動網格異常: {e}",
        )


# ────────────────────────────────────────────────────────
# POST /api/v1/grids/{grid_id}/stop — 停止網格
# ────────────────────────────────────────────────────────

@router.post("/{grid_id}/stop", response_model=GridActionResponse)
async def stop_grid(
    grid_id: str,
    source: str = Query(default="hermes", description="來源"),
    reason: Optional[str] = Query(default=None, description="原因"),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    _token: str = Depends(verify_token),
):
    """
    停止指定網格。

    更新 active_grids.json 中該配置為 false，
    run_multiple_grids.py 的 active_grids_monitor() 會在 5 秒內偵測到並優雅停止。
    """
    project_root = _get_project_root()
    guard = SafetyGuard(project_root)
    audit_logger = AuditLogger.get_instance(project_root)

    # 0. 寫操作頻率限制檢查
    can_proceed, limit_msg = guard.check_rate_limit(source)
    if not can_proceed:
        audit_logger.log(
            source=source,
            action="stop",
            grid_id=grid_id,
            params={"grid_id": grid_id},
            result="rejected",
            reason=reason,
            error_msg=limit_msg,
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=limit_msg,
        )

    config_path = GridYAMLGenerator.path_from_grid_id(grid_id)
    active_grids = _read_active_grids()

    if config_path not in active_grids:
        audit_logger.log(
            source=source,
            action="stop",
            grid_id=grid_id,
            params={"grid_id": grid_id},
            result="rejected",
            reason=reason,
            error_msg=f"網格 {grid_id} 不在 active_grids 控制中",
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"網格 {grid_id} 不在 active_grids 控制中",
        )

    command_repo = GridCommandRepository.instance()
    if idempotency_key:
        existing = command_repo.get_by_idempotency_key("stop", idempotency_key)
        if existing:
            return GridActionResponse(
                grid_id=existing["grid_id"], action="stop", status=existing["status"],
                command_id=existing["command_id"], message="重複請求已回傳原始命令",
            )

    if not active_grids.get(config_path, False):
        return GridActionResponse(
            grid_id=grid_id,
            action="stop",
            status="already_stopped",
            message=f"網格 {grid_id} 已處於停止狀態",
        )

    command = GridCommandRepository.instance().create(
        grid_id=grid_id, config_path=config_path, action="stop", source=source, reason=reason,
        idempotency_key=idempotency_key,
    )
    try:
        active_grids[config_path] = False
        _write_active_grids(active_grids)
        GridCommandRepository.instance().update_status(command["command_id"], "pending_engine")
        logger.info(f"🛑 已接受停止網格命令: {grid_id}, command={command['command_id']}")

        # 記錄寫操作時間戳
        guard.record_action(source)

        # 寫入審計日誌
        audit_logger.log(
            source=source,
            action="stop",
            grid_id=grid_id,
            params={"grid_id": grid_id},
            result="success",
            reason=reason,
        )

        return GridActionResponse(
            grid_id=grid_id,
            action="stop",
            status="pending_engine",
            command_id=command["command_id"],
            message=f"網格 {grid_id} 的停止命令已接受，等待引擎取消掛單並確認",
        )
    except Exception as e:
        GridCommandRepository.instance().update_status(command["command_id"], "failed", str(e))
        audit_logger.log(
            source=source,
            action="stop",
            grid_id=grid_id,
            params={"grid_id": grid_id},
            result="error",
            reason=reason,
            error_msg=f"停止網格異常: {e}",
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"停止網格異常: {e}",
        )


# ────────────────────────────────────────────────────────
# DELETE /api/v1/grids/{grid_id} — 停止+刪除
# ────────────────────────────────────────────────────────

@router.delete("/{grid_id}", response_model=GridActionResponse)
async def delete_grid(
    grid_id: str,
    source: str = Query(default="hermes", description="來源"),
    reason: Optional[str] = Query(default=None, description="原因"),
    _token: str = Depends(verify_token),
):
    """
    停止並刪除指定網格。

    1. 先將 active_grids.json 中設為 false（觸發引擎停止）
    2. 從 active_grids.json 中移除該條目
    3. 將 YAML 檔案移至 config/grid/archived/ 歸檔（而非直接刪除）
    """
    project_root = _get_project_root()
    guard = SafetyGuard(project_root)
    audit_logger = AuditLogger.get_instance(project_root)

    # 0. 寫操作頻率限制檢查
    can_proceed, limit_msg = guard.check_rate_limit(source)
    if not can_proceed:
        audit_logger.log(
            source=source,
            action="delete",
            grid_id=grid_id,
            params={"grid_id": grid_id},
            result="rejected",
            reason=reason,
            error_msg=limit_msg,
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=limit_msg,
        )

    config_path = GridYAMLGenerator.path_from_grid_id(grid_id)
    abs_yaml = os.path.join(project_root, config_path)

    if not os.path.exists(abs_yaml):
        audit_logger.log(
            source=source,
            action="delete",
            grid_id=grid_id,
            params={"grid_id": grid_id},
            result="rejected",
            reason=reason,
            error_msg=f"網格配置 {grid_id} 不存在",
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"網格配置 {grid_id} 不存在",
        )

    try:
        # 1. 停止（若正在運行）
        active_grids = _read_active_grids()
        was_running = active_grids.get(config_path, False)
        if was_running:
            active_grids[config_path] = False
            _write_active_grids(active_grids)
            logger.info(f"🛑 刪除前已停止網格: {grid_id}")

        # 2. 從 active_grids 移除
        if config_path in active_grids:
            del active_grids[config_path]
            _write_active_grids(active_grids)

        # 清理 StateStore 中的快取
        GridStateStore.instance().remove(config_path)

        # 3. 歸檔 YAML（移動到 archived/ 目錄）
        archive_dir = os.path.join(project_root, "config", "grid", "archived")
        os.makedirs(archive_dir, exist_ok=True)
        archived_path = os.path.join(archive_dir, Path(config_path).name)

        archive_success = False
        try:
            os.rename(abs_yaml, archived_path)
            logger.info(f"📦 已歸檔: {config_path} → config/grid/archived/")
            archive_success = True
        except Exception as e:
            logger.error(f"⚠️ 歸檔失敗: {e}，嘗試直接刪除")
            try:
                os.remove(abs_yaml)
                logger.info(f"🗑️ 已刪除: {config_path}")
                archive_success = True
            except Exception as e2:
                logger.error(f"❌ 刪除也失敗: {e2}")

        if not archive_success:
            raise Exception("無法移動或刪除網格配置文件")

        # 記錄寫操作時間戳
        guard.record_action(source)

        # 寫入審計日誌
        audit_logger.log(
            source=source,
            action="delete",
            grid_id=grid_id,
            params={"grid_id": grid_id},
            result="success",
            reason=reason,
        )

        return GridActionResponse(
            grid_id=grid_id,
            action="delete",
            status="deleted",
            message=f"網格 {grid_id} 已{'停止並' if was_running else ''}刪除（YAML 已歸檔）",
        )
    except Exception as e:
        audit_logger.log(
            source=source,
            action="delete",
            grid_id=grid_id,
            params={"grid_id": grid_id},
            result="error",
            reason=reason,
            error_msg=f"刪除網格異常: {e}",
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"刪除網格異常: {e}",
        )

