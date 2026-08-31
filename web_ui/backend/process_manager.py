import subprocess
import threading
import os
import signal
import sys
import asyncio
from typing import Dict, Optional, Deque, List, Callable
from collections import deque
from datetime import datetime
from dataclasses import dataclass, field
import logging

import json

# Configure logging for the process manager itself
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ProcessManager")

@dataclass
class ProcessInfo:
    pid: int
    script: str
    config_path: str
    process: Optional[subprocess.Popen]  # Optional because we might recover from PID only
    start_time: datetime
    status: str  # running, stopped, error
    log_buffer: Deque[str]
    stdout_thread: Optional[threading.Thread]
    stderr_thread: Optional[threading.Thread]
    listeners: List[Callable[[str], None]] = field(default_factory=list)

class ProcessManager:
    _instance = None
    _lock = threading.Lock()
    STATE_FILE = "processes.json"

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super(ProcessManager, cls).__new__(cls)
                    cls._instance.processes: Dict[int, ProcessInfo] = {}
                    cls._instance.pm2_tailers = {}
                    cls._instance.log_lock = threading.Lock()
                    cls._instance.project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../"))
                    cls._instance.state_path = os.path.join(os.path.dirname(__file__), cls.STATE_FILE)
                    cls._instance._load_state()
        return cls._instance

    def _get_pm2_processes(self) -> List[dict]:
        try:
            output = subprocess.check_output(["pm2", "jlist"], text=True)
            pm2_list = json.loads(output)
            results = []
            for p in pm2_list:
                pm2_env = p.get("pm2_env", {})
                args = pm2_env.get("args", [])
                config_path = args[0] if args else ""
                
                start_time_ts = pm2_env.get("created_at", 0) / 1000
                start_time = datetime.fromtimestamp(start_time_ts) if start_time_ts else datetime.now()
                uptime_ms = pm2_env.get("pm_uptime", 0)
                uptime = datetime.now() - datetime.fromtimestamp(uptime_ms/1000) if uptime_ms else datetime.now() - start_time
                
                status = pm2_env.get("status", "unknown")
                if status == "online": status = "running"
                elif status == "stopped": status = "stopped"
                elif status == "errored": status = "error"

                results.append({
                    "pid": p.get("pm_id", 0),
                    "script": p.get("name", ""),
                    "config_path": config_path,
                    "start_time": start_time.isoformat(),
                    "status": status,
                    "uptime": str(uptime).split('.')[0],
                    "is_pm2": True,
                    "pm_out_log_path": pm2_env.get("pm_out_log_path", "")
                })
            return results
        except Exception as e:
            logger.error(f"Failed to get pm2 processes: {e}")
            return []

    def _save_state(self):
        """Save current processes to a JSON file."""
        data = {}
        for pid, info in self.processes.items():
            if info.status == "running":
                data[pid] = {
                    "script": info.script,
                    "config_path": info.config_path,
                    "start_time": info.start_time.isoformat()
                }
        try:
            with open(self.state_path, 'w') as f:
                json.dump(data, f)
        except Exception as e:
            logger.error(f"Failed to save state: {e}")

    def _load_state(self):
        """Load processes from JSON file and re-attach if possible."""
        if not os.path.exists(self.state_path):
            return

        try:
            with open(self.state_path, 'r') as f:
                data = json.load(f)
            
            for pid_str, p_data in data.items():
                pid = int(pid_str)
                # Check if process is still running
                try:
                    # Signal 0 checks if process exists
                    os.kill(pid, 0)
                except OSError:
                    logger.info(f"Process {pid} from state file is no longer running.")
                    continue

                # Reconstruct ProcessInfo
                # Note: We cannot recover stdout/stderr streams from an attached process easily
                # without advanced techniques (like re-parenting or reading proc files if redirected).
                # For now, we just track it as running but without log streaming for old processes.
                start_time = datetime.fromisoformat(p_data["start_time"])
                
                info = ProcessInfo(
                    pid=pid,
                    script=p_data["script"],
                    config_path=p_data["config_path"],
                    process=None, # We don't have the Popen object
                    start_time=start_time,
                    status="running",
                    log_buffer=deque(maxlen=1000),
                    stdout_thread=None,
                    stderr_thread=None
                )
                info.log_buffer.append(f"[SYSTEM] Process recovered from state file. Real-time logs unavailable for recovered processes.")
                self.processes[pid] = info
                logger.info(f"Recovered process {pid} from state file.")
                
        except Exception as e:
            logger.error(f"Failed to load state: {e}")

    def _resolve_config_path(self, symbol: Optional[str]) -> Optional[str]:
        if not symbol:
            return None
        
        # 讀取 active_grids.json 裡的所有 config_path
        active_grids_path = os.path.join(self.project_root, "config", "active_grids.json")
        if not os.path.exists(active_grids_path):
            return None
            
        try:
            with open(active_grids_path, "r", encoding="utf-8") as f:
                active_grids = json.load(f)
        except Exception:
            return None
            
        for config_path in active_grids.keys():
            # 讀取 YAML 中的 grid_system.symbol
            yaml_path = os.path.join(self.project_root, config_path)
            if os.path.exists(yaml_path):
                try:
                    import yaml
                    with open(yaml_path, "r", encoding="utf-8") as yf:
                        yaml_data = yaml.safe_load(yf)
                        if yaml_data and yaml_data.get("grid_system", {}).get("symbol") == symbol:
                            return config_path
                except Exception:
                    continue
        return None

    def _parse_stats_line(self, line: str, default_config_path: Optional[str] = None):
        """
        解析 [STATS_JSON] 並更新 GridStateStore
        """
        import re
        from web_ui.backend.grid_state_store import GridStateStore
        
        # 相容 [STATS_JSON][SYMBOL] {...} 和 [STATS_JSON] {...}
        STATS_PATTERN = re.compile(r'\[STATS_JSON\](?:\[(.+?)\])?\s*(\{.+\})')
        match = STATS_PATTERN.search(line)
        if not match:
            return
            
        symbol = match.group(1)
        try:
            stats = json.loads(match.group(2))
        except Exception:
            return
            
        # 決定 config_path
        config_path = default_config_path
        if not config_path and symbol:
            config_path = self._resolve_config_path(symbol)
            
        if config_path:
            # 確保 stats 裡面有 symbol 與 grid_id，便於 state store 使用
            if symbol and "symbol" not in stats:
                stats["symbol"] = symbol
            elif "symbol" not in stats and config_path:
                # 嘗試從 YAML 讀取 symbol
                yaml_path = os.path.join(self.project_root, config_path)
                if os.path.exists(yaml_path):
                    try:
                        import yaml
                        with open(yaml_path, "r", encoding="utf-8") as yf:
                            yaml_data = yaml.safe_load(yf)
                            if yaml_data:
                                stats["symbol"] = yaml_data.get("grid_system", {}).get("symbol")
                    except Exception:
                        pass
            
            if "grid_id" not in stats:
                # 從 config_path 推導 grid_id
                base = os.path.basename(config_path)
                stats["grid_id"] = os.path.splitext(base)[0]
                
            GridStateStore.instance().update(config_path, stats)

    def _parse_lifecycle_line(self, line: str):
        """Consume explicit grid engine acknowledgements from stdout/PM2 logs."""
        marker = "[GRID_LIFECYCLE]"
        if marker not in line:
            return
        try:
            payload = json.loads(line.split(marker, 1)[1].strip())
            config_path = payload.get("config_path")
            lifecycle_status = payload.get("status")
            if not config_path or lifecycle_status not in {"running", "stopped", "failed"}:
                return
            from web_ui.backend.command_repository import GridCommandRepository
            grid_id = os.path.splitext(os.path.basename(config_path))[0]
            repository = GridCommandRepository.instance()
            repository.record_event(grid_id, "engine_lifecycle", lifecycle_status, payload.get("detail"))
            command = repository.latest_for_grid(grid_id)
            if command is None or command["status"] != "pending_engine":
                return
            expected_action = "start" if lifecycle_status == "running" else "stop"
            if lifecycle_status == "failed" or command["action"] == expected_action:
                repository.update_status(command["command_id"], lifecycle_status, payload.get("detail"))
        except (TypeError, ValueError, json.JSONDecodeError):
            logger.warning("Ignored malformed GRID_LIFECYCLE record")

    def add_log_listener(self, pid: int, listener: Callable[[str], None]):
        if pid in self.processes:
            with self.log_lock:
                self.processes[pid].listeners.append(listener)
            return

        pm2_procs = self._get_pm2_processes()
        for p in pm2_procs:
            if p["pid"] == pid:
                with self.log_lock:
                    if pid not in self.pm2_tailers:
                        log_path = p["pm_out_log_path"]
                        if not log_path or not os.path.exists(log_path):
                            return
                        tail_proc = subprocess.Popen(["tail", "-f", "-n", "0", log_path], stdout=subprocess.PIPE, text=True, bufsize=1)
                        listeners = [listener]
                        
                        def tail_reader(stream, pid_val, conf_path_val):
                            try:
                                for line in iter(stream.readline, ''):
                                    if not line: break
                                    
                                    # 攔截引擎統計與明確生命週期確認
                                    self._parse_stats_line(line, conf_path_val)
                                    self._parse_lifecycle_line(line)
                                    
                                    with self.log_lock:
                                        # Get a copy of the listeners to avoid modification during iteration
                                        current_listeners = []
                                        if pid_val in self.pm2_tailers:
                                            current_listeners = list(self.pm2_tailers[pid_val]["listeners"])
                                        elif pid_val in self.processes:
                                            current_listeners = list(self.processes[pid_val].listeners)
                                            
                                    for l in current_listeners:
                                        try: l(line.rstrip())
                                        except Exception: pass
                            except Exception: pass
                            finally: 
                                try: stream.close()
                                except Exception: pass
                        
                        t = threading.Thread(target=tail_reader, args=(tail_proc.stdout, pid, config_path), daemon=True)
                        t.start()
                        self.pm2_tailers[pid] = {"process": tail_proc, "listeners": listeners, "thread": t}
                    else:
                        self.pm2_tailers[pid]["listeners"].append(listener)
                return

    def remove_log_listener(self, pid: int, listener: Callable[[str], None]):
        with self.log_lock:
            if pid in self.processes and listener in self.processes[pid].listeners:
                self.processes[pid].listeners.remove(listener)
                return
                
            if pid in self.pm2_tailers:
                if listener in self.pm2_tailers[pid]["listeners"]:
                    self.pm2_tailers[pid]["listeners"].remove(listener)
                if not self.pm2_tailers[pid]["listeners"]:
                    try:
                        self.pm2_tailers[pid]["process"].terminate()
                    except Exception: pass
                    del self.pm2_tailers[pid]

    def start_process(self, script: str, config_path: str) -> int:
        """
        Start a new process with the given script and config.
        """
        script_path = os.path.join(self.project_root, script)
        
        # Verify script exists
        if not os.path.exists(script_path):
            raise FileNotFoundError(f"Script not found: {script}")

        # Construct command
        # Use python3 to run the script
        cmd = [sys.executable, script_path, config_path]
        
        # Add --no-ui flag if script is run_grid_trading.py
        if "run_grid_trading.py" in script:
            cmd.append("--no-ui")
        
        # Start process with pipes for stdout and stderr
        # bufsize=1 means line buffered
        process = subprocess.Popen(
            cmd,
            cwd=self.project_root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env=os.environ.copy()  # Inherit environment variables
        )

        pid = process.pid
        start_time = datetime.now()
        log_buffer = deque(maxlen=1000)  # Keep last 1000 lines
        listeners: List[Callable[[str], None]] = []

        # Create threads to read stdout and stderr
        stdout_thread = threading.Thread(
            target=self._read_stream,
            args=(process.stdout, log_buffer, listeners, config_path),
            daemon=True
        )
        stderr_thread = threading.Thread(
            target=self._read_stream,
            args=(process.stderr, log_buffer, listeners, config_path),
            daemon=True
        )

        stdout_thread.start()
        stderr_thread.start()

        process_info = ProcessInfo(
            pid=pid,
            script=script,
            config_path=config_path,
            process=process,
            start_time=start_time,
            status="running",
            log_buffer=log_buffer,
            stdout_thread=stdout_thread,
            stderr_thread=stderr_thread,
            listeners=listeners
        )

        self.processes[pid] = process_info
        self._save_state()
        logger.info(f"Started process {pid}: {script} {config_path}")
        return pid

    def stop_process(self, pid: int) -> bool:
        """
        Stop a running process.
        """
        if pid not in self.processes:
            pm2_procs = self._get_pm2_processes()
            for p in pm2_procs:
                if p["pid"] == pid:
                    try:
                        subprocess.run(["pm2", "stop", str(pid)], check=True)
                        return True
                    except subprocess.CalledProcessError:
                        return False
            return False

        info = self.processes[pid]
        if info.process is None:
            # Recovered process, just kill it via os.kill
            try:
                os.kill(pid, signal.SIGTERM)
                # Wait a bit? We can't wait on it since it's not a child
                info.status = "stopped"
                self._save_state()
                return True
            except OSError:
                info.status = "error"
                return False

        if info.process.poll() is not None:
            info.status = "stopped"
            self._save_state()
            return True

        try:
            logger.info(f"Stopping process {pid}...")
            # Try graceful termination first
            info.process.terminate()
            try:
                info.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                # Force kill if it doesn't stop
                logger.warning(f"Process {pid} did not terminate, forcing kill...")
                info.process.kill()
                info.process.wait()
            
            info.status = "stopped"
            self._save_state()
            return True
        except Exception as e:
            logger.error(f"Error stopping process {pid}: {e}")
            info.status = "error"
            return False

    def get_process_info(self, pid: int) -> Optional[dict]:
        if pid not in self.processes:
            pm2_procs = self._get_pm2_processes()
            for p in pm2_procs:
                if p["pid"] == pid:
                    return p
            return None
        
        info = self.processes[pid]
        
        # Update status if process has exited
        if info.status == "running":
            if info.process:
                if info.process.poll() is not None:
                    return_code = info.process.returncode
                    info.status = "stopped" if return_code == 0 else "error"
                    self._save_state()
            else:
                # Recovered process check
                try:
                    os.kill(info.pid, 0)
                except OSError:
                    info.status = "stopped" # or error? assume stopped
                    self._save_state()

        uptime = datetime.now() - info.start_time
        
        return {
            "pid": info.pid,
            "script": info.script,
            "config_path": info.config_path,
            "start_time": info.start_time.isoformat(),
            "status": info.status,
            "uptime": str(uptime).split('.')[0]  # Remove microseconds
        }

    def get_all_processes(self) -> List[dict]:
        # Cleanup processes that are no longer in memory? 
        # For now, we keep history until restart of the web server
        # But we should update statuses
        result = []
        for pid in list(self.processes.keys()):
            info = self.get_process_info(pid)
            if info:
                result.append(info)
        
        # Add PM2 processes
        pm2_procs = self._get_pm2_processes()
        result.extend(pm2_procs)
        
        return result

    def get_logs(self, pid: int) -> List[str]:
        if pid in self.processes:
            return list(self.processes[pid].log_buffer)
            
        pm2_procs = self._get_pm2_processes()
        for p in pm2_procs:
            if p["pid"] == pid and p.get("pm_out_log_path"):
                try:
                    out = subprocess.check_output(["tail", "-n", "1000", p["pm_out_log_path"]], text=True)
                    return out.splitlines()
                except Exception as e:
                    logger.error(f"Failed to read pm2 logs: {e}")
                    return []
        return []

    def _read_stream(self, stream, buffer: Deque, listeners: List[Callable[[str], None]], config_path: Optional[str] = None):
        """
        Read from a stream line by line and append to buffer.
        """
        try:
            for line in iter(stream.readline, ''):
                if not line:
                    break
                
                # 攔截引擎統計與明確生命週期確認
                self._parse_stats_line(line, config_path)
                self._parse_lifecycle_line(line)
                
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                formatted_line = f"[{timestamp}] {line.rstrip()}"
                buffer.append(formatted_line)
                
                # Notify listeners
                for listener in listeners:
                    try:
                        listener(formatted_line)
                    except Exception as e:
                        logger.error(f"Error in log listener: {e}")
                        
        except ValueError:
            pass  # Stream closed
        except Exception as e:
            logger.error(f"Error reading stream: {e}")
        finally:
            stream.close()

# Global instance
process_manager = ProcessManager()
