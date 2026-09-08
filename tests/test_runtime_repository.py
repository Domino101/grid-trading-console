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

    def test_load_all_merges_partial_redis_cache_with_db(self):
        # DEF-05: 當 Redis 快取僅有部分機器人時，load_all 必須合併 DB，嚴禁遺失其餘機器人
        from web_ui.backend.runtime_repository import RuntimeSnapshotRepository

        class MockCache:
            def __init__(self, data):
                self.data = data
            def get_all(self):
                return self.data

        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, "runtime.sqlite3")
            repo = RuntimeSnapshotRepository(path, cache=None)
            # DB 寫入 3 隻機器人
            repo.save("config/grid/b1.yaml", {"bot_id": "b1", "price": 100.0}, updated_at=10.0)
            repo.save("config/grid/b2.yaml", {"bot_id": "b2", "price": 200.0}, updated_at=10.0)
            repo.save("config/grid/b3.yaml", {"bot_id": "b3", "price": 300.0}, updated_at=10.0)

            # Redis 僅快取 1 隻機器人 (b1，且時間戳較新)
            mock_cache = MockCache({"config/grid/b1.yaml": ({"bot_id": "b1", "price": 105.0}, 20.0)})
            repo._redis_cache = mock_cache

            all_snaps = repo.load_all()
            self.assertEqual(len(all_snaps), 3)
            self.assertEqual(all_snaps["config/grid/b1.yaml"][0]["price"], 105.0)
            self.assertEqual(all_snaps["config/grid/b2.yaml"][0]["price"], 200.0)
            self.assertEqual(all_snaps["config/grid/b3.yaml"][0]["price"], 300.0)


if __name__ == "__main__":
    unittest.main()
