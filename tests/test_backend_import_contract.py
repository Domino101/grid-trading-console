"""Import-spelling contract for the ``web_ui.backend`` package (收尾 C9).

Why this exists
---------------
Python treats ``auth`` and ``web_ui.backend.auth`` as **two different modules**
and executes the same file twice whenever both spellings are used in one
process.  Every module-level singleton then exists twice — two API tokens, two
SQLite repository connections, two ``GridStateStore`` caches — which was the
root cause of 17 CI failures before C8 (``401 Invalid or missing API token``,
dashboard tests reading the real runtime database).

C8 mitigated the symptom with a meta-path alias finder in
``web_ui/backend/__init__.py``.  The finishing phase converged the whole
repository to the single package spelling ``web_ui.backend.*`` and retired
the finder.  This module locks the convention in with:

* a **grep contract**: no bare ``from auth import …`` / ``import auth`` /
  ``import routes.dca`` / ``patch("routes.…")`` anywhere in the backend, the
  engine entry point, or the tests;
* a **runtime single-loading check**: each backend module file must appear as
  exactly one module object in ``sys.modules``, independent of test order.

Only ``web_ui.backend.*`` (and ``core.*`` / third-party) imports are allowed.
"""

import os
import re
import sys
import unittest

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

#: Bare top-level names that must never be imported without the package prefix.
BACKEND_MODULES = [
    "auth", "audit", "schemas", "lifecycle",
    "kline_repository", "kline_service",
    "runtime_repository", "command_repository",
    "grid_state_store", "process_manager", "control_plane",
    "safety_guard", "dca_yaml_generator", "models", "main",
    "file_manager", "grid_recommendation", "yaml_generator",
]
BACKEND_ROUTES = ["market", "grids", "dashboard", "system", "commands", "dca"]

_FORBIDDEN_RE = re.compile(
    r"^\s*from (?:{m})\s+import\b|^\s*import (?:{m})\b"
    r"|^\s*import routes\.(?:{r})\b"
    r"|patch\(\"(?:routes\.|{m}\.)".format(
        m="|".join(re.escape(x) for x in BACKEND_MODULES),
        r="|".join(re.escape(x) for x in BACKEND_ROUTES),
    ),
    re.MULTILINE,
)


def _collect_py_files():
    """All Python files owned by the backend / engine / tests."""
    files = []
    backend_dir = os.path.join(PROJECT_ROOT, "web_ui", "backend")
    for root, _dirs, names in os.walk(backend_dir):
        for name in sorted(names):
            if name.endswith(".py"):
                files.append(os.path.join(root, name))
    files.append(os.path.join(PROJECT_ROOT, "run_multiple_grids.py"))
    tests_dir = os.path.join(PROJECT_ROOT, "tests")
    for name in sorted(os.listdir(tests_dir)):
        if name.endswith(".py"):
            files.append(os.path.join(tests_dir, name))
    return files


def _code_without_docstrings_and_comments(source: str) -> str:
    """Strip docstrings and ``#`` comments so prose never trips the grep."""
    source = re.sub(r'"""(?:.|\n)*?"""', "", source)
    source = re.sub(r"'''(?:.|\n)*?'''", "", source)
    lines = []
    for line in source.splitlines():
        comment = line.find("#")
        lines.append(line[:comment] if comment >= 0 else line)
    return "\n".join(lines)


class TestBackendImportContract(unittest.TestCase):
    """Grep contract: the top-level import spelling is forbidden everywhere."""

    def test_no_top_level_backend_imports(self):
        offenders = []
        for path in _collect_py_files():
            with open(path, encoding="utf-8") as handle:
                code = _code_without_docstrings_and_comments(handle.read())
            for lineno, line in enumerate(code.splitlines(), 1):
                if _FORBIDDEN_RE.search(line):
                    offenders.append(f"{path}:{lineno}: {line.strip()}")
        self.assertEqual(
            offenders, [],
            "偵測到頂層拼法 import（必須改用 `web_ui.backend.*`，"
            "否則模組級單例會分岔）：\n" + "\n".join(offenders),
        )

    def test_patch_targets_use_package_spelling(self):
        """unittest.mock patch 目標也必須是套件拼法，否則會 patch 到別份模組。"""
        offenders = []
        for path in _collect_py_files():
            if not path.startswith(os.path.join(PROJECT_ROOT, "tests")):
                continue
            with open(path, encoding="utf-8") as handle:
                code = _code_without_docstrings_and_comments(handle.read())
            for lineno, line in enumerate(code.splitlines(), 1):
                if re.search(r'patch\("(?:routes\.|' + "|".join(
                    re.escape(m) for m in BACKEND_MODULES
                ) + r')\.', line):
                    offenders.append(f"{path}:{lineno}: {line.strip()}")
        self.assertEqual(offenders, [], "patch 目標使用頂層拼法：\n" + "\n".join(offenders))


class TestBackendSingleLoading(unittest.TestCase):
    """Runtime: every backend module file is loaded exactly once."""

    def _backend_module_files(self):
        """(module, real file path) for the core singleton-bearing modules."""
        from web_ui.backend import (
            auth, audit, command_repository, control_plane, grid_state_store,
            kline_repository, kline_service, lifecycle, runtime_repository,
            safety_guard, schemas,
        )
        from web_ui.backend.routes import dca, dashboard, grids, market, system

        return [
            auth, audit, command_repository, control_plane, grid_state_store,
            kline_repository, kline_service, lifecycle, runtime_repository,
            safety_guard, schemas, dca, dashboard, grids, market, system,
        ]

    def test_each_backend_file_loaded_exactly_once(self):
        for module in self._backend_module_files():
            module_file = os.path.realpath(module.__file__)
            loaded = [
                name for name, other in sys.modules.items()
                if getattr(other, "__file__", None)
                and os.path.realpath(other.__file__) == module_file
            ]
            self.assertEqual(
                len(loaded), 1,
                f"{module.__name__}（{module_file}）被載入 {len(loaded)} 次：{loaded}",
            )

    def test_package_entry_point_importable(self):
        """`web_ui.backend.main` 可從專案根以套件拼法匯入（uvicorn/測試入口）。"""
        from web_ui.backend.main import app  # noqa: F401
        self.assertIsNotNone(app)


if __name__ == "__main__":
    unittest.main()
