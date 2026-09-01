"""Unit tests for EnvironmentGuard database protection."""

import os
import tempfile
import unittest

from core.infrastructure.database.guard import EnvironmentGuard, ProductionDatabaseProtectionError
from web_ui.backend.lifecycle import connect_sqlite


class TestEnvironmentGuard(unittest.TestCase):
    def test_test_environment_detection(self):
        self.assertTrue(EnvironmentGuard.is_test_environment())

    def test_production_path_assertion_raises_error(self):
        prod_path = EnvironmentGuard.default_database_path()
        with self.assertRaises(ProductionDatabaseProtectionError):
            EnvironmentGuard.assert_safe_connection(prod_path)

    def test_production_path_auto_redirected_in_connect_sqlite(self):
        prod_path = EnvironmentGuard.default_database_path()
        conn = connect_sqlite(prod_path)
        self.assertIsNotNone(conn)
        # Verify it did not connect to the real production file
        safe_path = EnvironmentGuard.get_safe_database_path(prod_path)
        self.assertNotEqual(safe_path, prod_path)
        self.assertTrue(safe_path.startswith(EnvironmentGuard.get_test_database_dir()))
        conn.close()

    def test_temp_path_allowed_in_test_env(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            temp_db = os.path.join(tmpdir, "test.sqlite3")
            EnvironmentGuard.assert_safe_connection(temp_db)
            conn = connect_sqlite(temp_db)
            self.assertIsNotNone(conn)
            conn.close()

    def test_memory_path_allowed_in_test_env(self):
        EnvironmentGuard.assert_safe_connection(":memory:")
        conn = connect_sqlite(":memory:")
        self.assertIsNotNone(conn)
        conn.close()


if __name__ == "__main__":
    unittest.main()
