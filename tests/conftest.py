"""Global pytest configuration and fixtures for grid-trading-console."""

import os
import sys
import pytest

# Ensure APP_ENV is explicitly set to test
os.environ["APP_ENV"] = "test"
os.environ["GRID_ENV"] = "test"


@pytest.fixture(autouse=True)
def isolate_repositories():
    """Reset repository singletons before and after every test to guarantee test isolation."""
    _reset_all_singletons()
    yield
    _reset_all_singletons()


def _reset_all_singletons():
    try:
        from web_ui.backend.runtime_repository import RuntimeSnapshotRepository
        RuntimeSnapshotRepository.reset_instance()
    except Exception:
        pass

    try:
        from web_ui.backend.command_repository import GridCommandRepository
        GridCommandRepository.reset_instance()
    except Exception:
        pass

    try:
        from web_ui.backend.grid_state_store import GridStateStore
        GridStateStore.reset_instance()
    except Exception:
        pass

    try:
        from web_ui.backend.kline_repository import KlineRepository
        KlineRepository.reset_instance()
    except Exception:
        pass

    try:
        from web_ui.backend.kline_service import KlineService
        KlineService._instance = None
    except Exception:
        pass
