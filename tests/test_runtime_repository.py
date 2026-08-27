"""Persistence tests for the single-host runtime snapshot repository."""

import os
import sys
import tempfile
import unittest

BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../web_ui/backend"))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)


class RuntimeSnapshotRepositoryTests(unittest.TestCase):
    def test_snapshot_survives_repository_recreation(self):
        from web_ui.backend.runtime_repository import RuntimeSnapshotRepository

        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, "runtime.sqlite3")
            writer = RuntimeSnapshotRepository(path)
            writer.save("config/grid/test.yaml", {"profit": {"net": 12.5}}, updated_at=123.0)

            reader = RuntimeSnapshotRepository(path)
            restored = reader.load_all()
            self.assertEqual(restored["config/grid/test.yaml"], ({"profit": {"net": 12.5}}, 123.0))

            reader.save("config/grid/test.yaml", {"current_price": 100.0}, updated_at=124.0)
            history = reader.history("config/grid/test.yaml", since=100.0)
            self.assertEqual(len(history), 2)
            self.assertEqual(history[-1], ({"current_price": 100.0}, 124.0))

            reader.remove("config/grid/test.yaml")
            self.assertEqual(reader.load_all(), {})
            self.assertEqual(reader.history("config/grid/test.yaml", since=0), [])


if __name__ == "__main__":
    unittest.main()
