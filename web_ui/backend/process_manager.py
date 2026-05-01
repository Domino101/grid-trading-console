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
                    cls._instance.project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../"))
                    cls._instance.state_path = os.path.join(os.path.dirname(__file__), cls.STATE_FILE)
                    cls._instance._load_state()
        return cls._instance

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

    def add_log_listener(self, pid: int, listener: Callable[[str], None]):
        if pid in self.processes:
            self.processes[pid].listeners.append(listener)

    def remove_log_listener(self, pid: int, listener: Callable[[str], None]):
        if pid in self.processes and listener in self.processes[pid].listeners:
            self.processes[pid].listeners.remove(listener)

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
            args=(process.stdout, log_buffer, listeners),
            daemon=True
        )
        stderr_thread = threading.Thread(
            target=self._read_stream,
            args=(process.stderr, log_buffer, listeners),
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
        return result

    def get_logs(self, pid: int) -> List[str]:
        if pid not in self.processes:
            return []
        return list(self.processes[pid].log_buffer)

    def _read_stream(self, stream, buffer: Deque, listeners: List[Callable[[str], None]]):
        """
        Read from a stream line by line and append to buffer.
        """
        try:
            for line in iter(stream.readline, ''):
                if not line:
                    break
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
