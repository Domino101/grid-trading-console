from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Body, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager
import asyncio
import logging
import sys
import os
from typing import List, Dict

# 確保專案根目錄在 sys.path 中，供 core 模組匯入
_project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../"))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

# 載入 .env 環境變數（需在 auth 等模組匯入前執行）
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(_project_root, ".env"))
except ImportError:
    pass

from web_ui.backend.process_manager import process_manager
from web_ui.backend.file_manager import file_manager
from web_ui.backend.models import ProcessStatus, StartProcessRequest
from web_ui.backend.auth import token_matches, verify_token
from web_ui.backend.lifecycle import cors_settings
from core.infrastructure.database.redis_support import RedisEventBus

# 🆕 Hermes Grid API v1 路由
from web_ui.backend.routes.system import router as system_router
from web_ui.backend.routes.grids import router as grids_router
from web_ui.backend.routes.dca import router as dca_router
from web_ui.backend.routes.dashboard import router as dashboard_router
from web_ui.backend.routes.commands import router as commands_router
from web_ui.backend.routes.market import router as market_router
from web_ui.backend.routes.grid_backtest import router as grid_backtest_router
from web_ui.backend.routes.coinfull_prime import router as coinfull_prime_router

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("WebUI")

# P2-4: WebSocket clients subscribed to grid/lifecycle events pushed via Redis.
_event_clients: set = set()
_redis_subscription = None


async def _broadcast_event(data: dict) -> None:
    stale = []
    for ws in list(_event_clients):
        try:
            await ws.send_json(data)
        except Exception:
            stale.append(ws)
    for ws in stale:
        _event_clients.discard(ws)


def _redis_event_callback(data: dict, loop: asyncio.AbstractEventLoop):
    def _pump():
        asyncio.create_task(_broadcast_event(data))
    try:
        loop.call_soon_threadsafe(_pump)
    except RuntimeError:
        pass


def _start_redis_subscriber():
    global _redis_subscription
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop is None:
        return None
    bus = RedisEventBus()
    _redis_subscription = bus.subscribe(lambda data: _redis_event_callback(data, loop))
    return _redis_subscription


def _stop_redis_subscriber():
    global _redis_subscription
    sub = _redis_subscription
    _redis_subscription = None
    if not sub:
        return
    # F5: prefer the handle's own close() (stops the listener loop first, then
    # closes the socket) so shutdown no longer leaks a traceback in the thread.
    close = sub.get("close") if isinstance(sub, dict) else None
    if callable(close):
        try:
            close()
        except Exception:
            pass
    else:
        try:
            pubsub = sub.get("pubsub")
            if pubsub is not None:
                pubsub.close()
        except Exception:
            pass
    thread = sub.get("thread")
    if thread is not None:
        thread.join(timeout=2)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Start process monitoring + optional Redis event subscription.
    monitor_task = asyncio.create_task(monitor_processes())
    _start_redis_subscriber()

    # Startup CoinFull Prime 每日定時換倉排程器 (零輪詢休眠，每日 UTC 00:00 自動喚醒)
    try:
        from core.services.coinfull_prime.service import CoinFullPrimeService
        prime_service = CoinFullPrimeService.instance()
        prime_service.start_scheduler(account_id="binance_testnet", dry_run=False)
    except Exception as e:
        logger.warning(f"CoinFull Prime 排程開機自啟異常: {e}")

    yield
    # Shutdown: Stop monitoring and Redis subscriber.
    _stop_redis_subscriber()
    try:
        from core.services.coinfull_prime.service import CoinFullPrimeService
        CoinFullPrimeService.instance().stop_scheduler()
    except Exception:
        pass
    monitor_task.cancel()
    try:
        await monitor_task
    except asyncio.CancelledError:
        pass

app = FastAPI(
    lifespan=lifespan,
    title="grid-trading-console API",
    description="Grid trading engine control API",
    version="0.3.0",
)

_cors_origins, _cors_credentials = cors_settings(os.getenv("GRID_CORS_ORIGINS"))
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=_cors_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/healthz", include_in_schema=False)
async def readiness_probe():
    """Unauthenticated process/readiness probe for systemd, PM2 and load balancers.

    It deliberately exposes no account, engine or connector information. Those
    details remain behind the authenticated /api/v1/system/health endpoint.
    """
    try:
        from web_ui.backend.runtime_repository import RuntimeSnapshotRepository
        RuntimeSnapshotRepository.instance().load_all()
        return {"status": "ok"}
    except Exception:
        logger.exception("Readiness probe failed")
        raise HTTPException(status_code=503, detail="service storage unavailable")

# 🆕 掛載 v1 API 路由（與現有 /api/scripts 等端點共存）
app.include_router(system_router)
app.include_router(grids_router)
app.include_router(dca_router)
app.include_router(dashboard_router)
app.include_router(commands_router)
app.include_router(market_router)
app.include_router(grid_backtest_router)
app.include_router(coinfull_prime_router)

async def monitor_processes():
    """Background task to monitor processes and update their status."""
    while True:
        try:
            # ProcessManager updates status lazily on get_process_info
            # We can force update or just let it be.
            # But we might want to clean up stopped processes after some time.
            # For now, just sleep.
            await asyncio.sleep(5)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Error in monitor task: {e}")
            await asyncio.sleep(5)

# API Routes

@app.get("/api/scripts")
async def get_scripts(_token: str = Depends(verify_token)):
    return file_manager.get_scripts()

@app.get("/api/configs")
async def get_configs(_token: str = Depends(verify_token)):
    return file_manager.get_configs()

@app.get("/api/config/{path:path}")
async def get_config(path: str, _token: str = Depends(verify_token)):
    try:
        return file_manager.read_config(path)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="File not found")
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid path")

@app.put("/api/config/{path:path}")
async def update_config(path: str, content: str = Body(..., media_type="text/plain"), _token: str = Depends(verify_token)):
    success, message = file_manager.save_config(path, content)
    if not success:
        code = 403 if "credentials" in message else 400
        raise HTTPException(status_code=code, detail=message)
    return {"message": message}

@app.get("/api/processes", response_model=List[ProcessStatus])
async def get_processes(_token: str = Depends(verify_token)):
    return process_manager.get_all_processes()

@app.post("/api/process/start")
async def start_process(request: StartProcessRequest, _token: str = Depends(verify_token)):
    try:
        pid = process_manager.start_process(request.script, request.config_path)
        return {"pid": pid, "status": "started"}
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/process/stop/{pid}")
async def stop_process(pid: int, _token: str = Depends(verify_token)):
    success = process_manager.stop_process(pid)
    if not success:
        raise HTTPException(status_code=400, detail="Failed to stop process or process not found")
    return {"message": "Process stopped"}

@app.websocket("/ws/logs/{pid}")
async def websocket_endpoint(websocket: WebSocket, pid: int, token: str = ""):
    if not token_matches(token):
        await websocket.close(code=4401, reason="Invalid or missing API token")
        return
    await websocket.accept()
    
    info = process_manager.get_process_info(pid)
    if not info:
        await websocket.close(code=1000, reason="Process not found")
        return

    # Send existing logs
    logs = process_manager.get_logs(pid)
    for log in logs:
        await websocket.send_text(log)

    # Queue for new logs
    queue = asyncio.Queue()
    loop = asyncio.get_running_loop()

    def log_listener(line: str):
        # This runs in thread, so use call_soon_threadsafe
        try:
            loop.call_soon_threadsafe(queue.put_nowait, line)
        except Exception:
            # Loop might be closed
            pass

    process_manager.add_log_listener(pid, log_listener)

    try:
        while True:
            line = await queue.get()
            await websocket.send_text(line)
    except WebSocketDisconnect:
        process_manager.remove_log_listener(pid, log_listener)
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        process_manager.remove_log_listener(pid, log_listener)

@app.websocket("/ws/grid-events")
async def grid_events_websocket(websocket: WebSocket, token: str = ""):
    if not token_matches(token):
        await websocket.close(code=4401, reason="Invalid or missing API token")
        return
    await websocket.accept()
    _event_clients.add(websocket)
    try:
        while True:
            # Keep the connection open; push events arrive via _broadcast_event.
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        _event_clients.discard(websocket)


# Mount frontend static files
# Ensure directory exists to avoid error on startup if frontend not built yet
import os
frontend_dist = os.path.join(os.path.dirname(__file__), "../frontend/dist")
if os.path.exists(frontend_dist):
    app.mount("/", StaticFiles(directory=frontend_dist, html=True), name="frontend")
else:
    logger.warning(f"Frontend dist not found at {frontend_dist}. UI will not be served.")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
