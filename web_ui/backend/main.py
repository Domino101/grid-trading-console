from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Body
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
from dotenv import load_dotenv
load_dotenv(os.path.join(_project_root, ".env"))

from process_manager import process_manager
from file_manager import file_manager
from models import ProcessStatus, StartProcessRequest

# 🆕 Hermes Grid API v1 路由
from routes.system import router as system_router
from routes.grids import router as grids_router
from routes.dashboard import router as dashboard_router
from routes.market import router as market_router

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("WebUI")

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Start process monitoring
    monitor_task = asyncio.create_task(monitor_processes())
    yield
    # Shutdown: Stop monitoring (cancelled automatically on shutdown usually, but good to be explicit if needed)
    monitor_task.cancel()
    try:
        await monitor_task
    except asyncio.CancelledError:
        pass

app = FastAPI(
    lifespan=lifespan,
    title="Grid Trading Control API",
    description="Hermes Agent × Grid Trading 控制接口",
    version="1.0.0",
)

# CORS — TODO(security): 階段三應收緊 allow_origins 白名單
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 🆕 掛載 v1 API 路由（與現有 /api/scripts 等端點共存）
app.include_router(system_router)
app.include_router(grids_router)
app.include_router(dashboard_router)
app.include_router(market_router)

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
async def get_scripts():
    return file_manager.get_scripts()

@app.get("/api/configs")
async def get_configs():
    return file_manager.get_configs()

@app.get("/api/config/{path:path}")
async def get_config(path: str):
    try:
        return file_manager.read_config(path)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="File not found")
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid path")

@app.put("/api/config/{path:path}")
async def update_config(path: str, content: str = Body(..., media_type="text/plain")):
    success, message = file_manager.save_config(path, content)
    if not success:
        raise HTTPException(status_code=400, detail=message)
    return {"message": message}

@app.get("/api/processes", response_model=List[ProcessStatus])
async def get_processes():
    return process_manager.get_all_processes()

@app.post("/api/process/start")
async def start_process(request: StartProcessRequest):
    try:
        pid = process_manager.start_process(request.script, request.config_path)
        return {"pid": pid, "status": "started"}
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/process/stop/{pid}")
async def stop_process(pid: int):
    success = process_manager.stop_process(pid)
    if not success:
        raise HTTPException(status_code=400, detail="Failed to stop process or process not found")
    return {"message": "Process stopped"}

@app.websocket("/ws/logs/{pid}")
async def websocket_endpoint(websocket: WebSocket, pid: int):
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
