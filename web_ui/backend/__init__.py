"""``web_ui.backend`` package initialisation.

Why this file exists
--------------------
The backend is imported under **two spellings** in the same process:

* top-level (``from auth import verify_token``) — used by ``main.py`` and the
  grid-era routes, because ``start_web_ui.sh`` runs uvicorn with
  ``web_ui/backend`` on ``sys.path``;
* absolute (``from web_ui.backend.auth import verify_token``) — used by the
  DCA-era routes and by the engine (``run_multiple_grids.py``), which run from
  the project root.

Without this module Python treats the two spellings as **different modules**
and executes each file twice.  Every module-level singleton then exists twice:

* ``auth._API_TOKEN`` — two independent tokens (an ephemeral token generated
  twice would make half the API unauthenticated-by-accident);
* ``RuntimeSnapshotRepository`` / ``GridCommandRepository`` — two SQLite
  connections and two ``_instance`` caches, so a write through one spelling is
  invisible to the reader that used the other;
* ``GridStateStore`` — two in-process runtime caches;
* ``AuditLogger`` — two log handles.

That duplication is the root cause of the DCA HTTP contract failures
(``401 Invalid or missing API token`` after the test patched ``auth``) and of
``test_dashboard_snapshot`` reading the *real* runtime database instead of the
temporary one.

The fix is an import alias: whichever spelling is imported second resolves to
the module object already created by the first.  The alias is applied **only**
when the counterpart module is already loaded *and* resolves to the very same
file on disk, so unrelated top-level packages (``models``, ``main``, third
party ``auth`` distributions, …) can never be hijacked.
"""

from __future__ import annotations

import os
import sys
from importlib.abc import Loader, MetaPathFinder
from importlib.machinery import ModuleSpec
from typing import Optional

_BACKEND_DIR = os.path.realpath(os.path.dirname(os.path.abspath(__file__)))
_PACKAGE_PREFIX = __name__ + "."


def _expected_paths(dotted_suffix: str) -> tuple[str, str]:
    """Return the two on-disk paths a backend module may live at."""
    base = os.path.join(_BACKEND_DIR, *dotted_suffix.split("."))
    return os.path.realpath(base + ".py"), os.path.realpath(os.path.join(base, "__init__.py"))


def _module_file(module: object) -> Optional[str]:
    file = getattr(module, "__file__", None)
    if file:
        return os.path.realpath(file)
    paths = list(getattr(module, "__path__", []) or [])
    if paths:
        return os.path.realpath(os.path.join(paths[0], "__init__.py"))
    return None


def _counterpart(fullname: str) -> Optional[object]:
    """Return an already-imported module that is the *same file* as ``fullname``."""
    if fullname.startswith(_PACKAGE_PREFIX):
        suffix = fullname[len(_PACKAGE_PREFIX):]
        other_name = suffix
    elif fullname == __name__ or fullname.startswith("web_ui"):
        return None
    else:
        suffix = fullname
        other_name = _PACKAGE_PREFIX + fullname

    existing = sys.modules.get(other_name)
    if existing is None:
        return None
    file = _module_file(existing)
    if file is None or file not in _expected_paths(suffix):
        return None
    return existing


class _AliasLoader(Loader):
    """Loader that hands back an existing module object instead of re-executing it."""

    def __init__(self, target: object) -> None:
        self._target = target

    def create_module(self, spec: ModuleSpec):  # noqa: D401 - importlib protocol
        return self._target

    def exec_module(self, module) -> None:  # noqa: D401 - importlib protocol
        return None


class _DualSpellingAliasFinder(MetaPathFinder):
    """Map ``web_ui.backend.X`` and top-level ``X`` onto one module object."""

    def find_spec(self, fullname: str, path=None, target=None) -> Optional[ModuleSpec]:
        existing = _counterpart(fullname)
        if existing is None:
            return None
        spec = ModuleSpec(fullname, _AliasLoader(existing), is_package=hasattr(existing, "__path__"))
        spec.origin = getattr(existing, "__file__", None)
        if hasattr(existing, "__path__"):
            spec.submodule_search_locations = list(existing.__path__)
        return spec


def _install_alias_finder() -> None:
    for finder in sys.meta_path:
        if isinstance(finder, _DualSpellingAliasFinder):
            return
    sys.meta_path.insert(0, _DualSpellingAliasFinder())


_install_alias_finder()

# The top-level spelling requires the backend directory on sys.path; ensure it
# is present so `web_ui.backend.routes.dca` can be imported from a process that
# only put the project root on sys.path (the trading engine does exactly that).
if _BACKEND_DIR not in sys.path:
    sys.path.append(_BACKEND_DIR)
