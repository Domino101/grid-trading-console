"""Database Environment Guard.

Protects production databases from accidental modification by unit tests,
test scripts, or unexpected execution contexts.
"""

from __future__ import annotations

import os
import sys
import tempfile
from typing import Optional


class ProductionDatabaseProtectionError(PermissionError):
    """Raised when a test environment attempts to access the production database."""
    pass


class EnvironmentGuard:
    """Safeguard asserting that unit tests cannot connect to or mutate production databases."""

    PROD_DB_FILENAMES = {
        "web_ui_runtime.sqlite3",
        "grid_audit.db",
    }
    _test_db_dir: Optional[str] = None

    @classmethod
    def is_test_environment(cls) -> bool:
        """Detect if the current process is running in a test suite."""
        if os.getenv("APP_ENV", "").lower() in {"test", "testing", "pytest"}:
            return True
        if os.getenv("GRID_ENV", "").lower() in {"test", "testing"}:
            return True
        if "PYTEST_CURRENT_TEST" in os.environ:
            return True
        if any("pytest" in arg or "unittest" in arg for arg in sys.argv):
            return True
        return False

    @classmethod
    def is_production_database_path(cls, path: str) -> bool:
        """Check if the given path targets a production database file."""
        if not path or path == ":memory:":
            return False
        normalized = os.path.abspath(path).replace("\\", "/")
        filename = os.path.basename(normalized)
        if filename in cls.PROD_DB_FILENAMES:
            # Check if it resides in the data directory or repo workspace
            if "/data/" in normalized or normalized.endswith(f"/data/{filename}"):
                return True
        return False

    @classmethod
    def get_test_database_dir(cls) -> str:
        """Get or initialize the isolated test directory for this test session."""
        if cls._test_db_dir is None or not os.path.exists(cls._test_db_dir):
            cls._test_db_dir = tempfile.mkdtemp(prefix="test_trading_db_")
        return cls._test_db_dir

    @classmethod
    def assert_safe_connection(cls, path: str) -> None:
        """Assert that opening the given database path is safe in the current environment."""
        if cls.is_test_environment() and cls.is_production_database_path(path):
            raise ProductionDatabaseProtectionError(
                f"FATAL: Test environment is strictly forbidden from opening production database '{path}'. "
                "Use a temporary directory or in-memory database (:memory:) for testing."
            )

    @classmethod
    def get_safe_database_path(cls, requested_path: Optional[str] = None) -> str:
        """Return a safe database path, automatically isolating production paths during tests."""
        if cls.is_test_environment():
            if not requested_path or cls.is_production_database_path(requested_path):
                filename = os.path.basename(requested_path) if requested_path else "test_runtime.sqlite3"
                return os.path.join(cls.get_test_database_dir(), filename)
            return requested_path

        if requested_path:
            return requested_path
        root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../"))
        return os.path.join(root, "data", "web_ui_runtime.sqlite3")
