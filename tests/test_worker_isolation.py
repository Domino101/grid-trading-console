"""
Unit tests for Worker Isolation and MasterSupervisor (P1-3).

Verifies:
1. Master supervisor starts and stops child worker processes.
2. Worker crashes (non-zero exit) are captured, logged, and trigger exponential backoff auto-healing.
3. Manually stopped workers are respected and not falsely marked as crashes.
4. Redis command events dispatch start/stop actions to worker pool.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from core.supervisor.master_daemon import MasterSupervisor, WorkerInfo


class TestWorkerIsolation(unittest.TestCase):
    def setUp(self):
        self.master = MasterSupervisor(debug=False)

    def tearDown(self):
        self.master.shutdown_all()

    def test_worker_info_properties(self):
        """Test WorkerInfo property methods for alive check and pid."""
        info = WorkerInfo(config_path="config/test.yaml", bot_type="grid")
        self.assertFalse(info.is_alive)
        self.assertIsNone(info.pid)

        # Mock Popen
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_proc.pid = 99999
        info.process = mock_proc

        self.assertTrue(info.is_alive)
        self.assertEqual(info.pid, 99999)

        mock_proc.poll.return_value = 0
        self.assertFalse(info.is_alive)

    def test_start_and_stop_worker_process(self):
        """Test starting a real lightweight subprocess and gracefully terminating it."""
        config_path = "config/test_mock_config.yaml"
        # Mock file existence
        with patch.object(self.master, "_abs_path") as mock_abs:
            mock_file = MagicMock()
            mock_file.exists.return_value = True
            mock_abs.return_value = mock_file

            # Use python sleep as the dummy worker executable
            orig_popen = subprocess.Popen
            dummy_cmd = [sys.executable, "-c", "import time, signal; signal.signal(signal.SIGTERM, lambda s,f: exit(0)); time.sleep(10)"]
            with patch("subprocess.Popen", side_effect=lambda *args, **kwargs: orig_popen(dummy_cmd)):
                started = self.master.start_worker(config_path, "grid")
                self.assertTrue(started)
                self.assertIn(config_path, self.master.workers)
                worker = self.master.workers[config_path]
                self.assertTrue(worker.is_alive)
                self.assertIsNotNone(worker.pid)

                # Now stop worker
                stopped = self.master.stop_worker(config_path, timeout=3.0)
                self.assertTrue(stopped)
                self.assertFalse(worker.is_alive)
                self.assertTrue(worker.manually_stopped)

    def test_worker_crash_captures_exit_code_and_schedules_backoff(self):
        """Test that unhandled worker crash increments restart counter and calculates exponential backoff."""
        config_path = "config/grid/test_crash.yaml"
        mock_proc = MagicMock()
        mock_proc.poll.return_value = 137  # e.g., SIGKILL / out-of-memory crash
        mock_proc.pid = 12345

        worker = WorkerInfo(
            config_path=config_path,
            bot_type="grid",
            process=mock_proc,
            started_at=time.time() - 10,
            manually_stopped=False,
        )
        self.master.workers[config_path] = worker

        with patch("core.supervisor.master_daemon.publish_lifecycle") as mock_lifecycle:
            self.master.check_health()

            # Process should be cleared
            self.assertIsNone(worker.process)
            # Restart count incremented
            self.assertEqual(worker.restarts, 1)
            # Next retry timestamp scheduled with backoff >= 2.0s
            self.assertGreater(worker.next_retry_at, time.time())
            # Lifecycle event emitted
            mock_lifecycle.assert_called_with(config_path, "failed", detail="進程異常崩潰 (exit=137)")

    def test_manually_stopped_worker_does_not_trigger_auto_healing(self):
        """Test that a manually stopped worker does not schedule restart on exit."""
        config_path = "config/grid/test_stopped.yaml"
        mock_proc = MagicMock()
        mock_proc.poll.return_value = 0
        mock_proc.pid = 54321

        worker = WorkerInfo(
            config_path=config_path,
            bot_type="grid",
            process=mock_proc,
            started_at=time.time() - 10,
            manually_stopped=True,
        )
        self.master.workers[config_path] = worker

        with patch("core.supervisor.master_daemon.publish_lifecycle") as mock_lifecycle:
            self.master.check_health()

            self.assertIsNone(worker.process)
            self.assertEqual(worker.restarts, 0)
            self.assertEqual(worker.next_retry_at, 0.0)
            mock_lifecycle.assert_called_with(config_path, "stopped")

    def test_poll_redis_commands_dispatches_start_and_stop(self):
        """Test that RedisCommandQueue commands properly call start_worker and stop_worker."""
        mock_queue = MagicMock()
        mock_queue.read_commands.return_value = [
            {"_msg_id": "1-0", "action": "START", "target_type": "grid", "config_path": "config/grid/a.yaml", "command_id": "c1"},
            {"_msg_id": "2-0", "action": "STOP", "target_type": "grid", "config_path": "config/grid/b.yaml", "command_id": "c2"},
        ]
        self.master.command_queue = mock_queue

        with patch.object(self.master, "start_worker") as mock_start, \
             patch.object(self.master, "stop_worker") as mock_stop:
            self.master.poll_redis_commands()

            mock_start.assert_called_once_with("config/grid/a.yaml", "grid")
            mock_stop.assert_called_once_with("config/grid/b.yaml")
            self.assertEqual(mock_queue.ack.call_count, 2)


if __name__ == "__main__":
    unittest.main()
