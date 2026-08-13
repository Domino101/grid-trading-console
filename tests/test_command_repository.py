"""Tests for durable, auditable grid lifecycle commands."""

import os
import sys
import tempfile
import unittest

BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../web_ui/backend"))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)


class GridCommandRepositoryTests(unittest.TestCase):
    def test_command_can_be_retrieved_and_transitioned(self):
        from command_repository import GridCommandRepository

        with tempfile.TemporaryDirectory() as temp_dir:
            repository = GridCommandRepository(os.path.join(temp_dir, "runtime.sqlite3"))
            command = repository.create("strategy-a", "config/grid/strategy-a.yaml", "start", "dashboard", "paper test")
            self.assertEqual(command["status"], "accepted")

            repository.update_status(command["command_id"], "pending_engine")
            restored = repository.get(command["command_id"])
            assert restored is not None
            self.assertEqual(restored["status"], "pending_engine")
            self.assertEqual(repository.latest_for_grid("strategy-a")["command_id"], command["command_id"])


if __name__ == "__main__":
    unittest.main()
