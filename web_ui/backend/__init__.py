"""``web_ui.backend`` package initialisation.

Import convention (single spelling)
-----------------------------------
Every module in this package must be imported with the **absolute package
spelling**::

    from web_ui.backend.auth import verify_token
    from web_ui.backend.routes.market import _lookup_market_from_ccxt_sync

The top-level spelling (``from auth import verify_token``, ``import
routes.dca``) is **forbidden** everywhere in the repository (backend, engine
``run_multiple_grids.py`` and tests).  Python treats ``auth`` and
``web_ui.backend.auth`` as two different modules and executes the same file
twice; every module-level singleton then exists twice — two API tokens, two
SQLite repository connections and two ``GridStateStore`` caches.  That
duplication was the root cause of the DCA HTTP contract failures
(``401 Invalid or missing API token`` after a test patched ``auth``) and of
``test_dashboard_snapshot`` reading the *real* runtime database.  It was
previously mitigated with a meta-path import-alias finder; once the codebase
was converged to a single spelling the finder became removable, and
``tests/test_backend_import_contract.py`` now guards the convention with a
grep contract plus a runtime single-loading check.

This file intentionally contains no imports and no logic: an empty package
marker is all that is needed.
"""
