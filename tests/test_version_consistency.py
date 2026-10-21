import json
import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.version import read_version  # noqa: E402


class TestVersionConsistency(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.version = read_version()
        cls.package_json = json.loads((ROOT / "web_ui" / "frontend" / "package.json").read_text(encoding="utf-8"))
        cls.package_lock = json.loads((ROOT / "web_ui" / "frontend" / "package-lock.json").read_text(encoding="utf-8"))

    def test_version_file_is_semver(self) -> None:
        self.assertRegex(self.version, r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")

    def test_package_json_matches_version_file(self) -> None:
        self.assertEqual(self.package_json["version"], self.version)

    def test_package_lock_matches_version_file(self) -> None:
        self.assertEqual(self.package_lock["version"], self.version)
        self.assertEqual(self.package_lock["packages"][""]["version"], self.version)

    def test_backend_app_uses_version_file(self) -> None:
        from web_ui.backend.main import app

        self.assertEqual(app.version, self.version)

    def test_frontend_display_uses_injected_version(self) -> None:
        app_tsx = (ROOT / "web_ui" / "frontend" / "src" / "App.tsx").read_text(encoding="utf-8")
        self.assertIn("__APP_VERSION__", app_tsx)
        self.assertNotRegex(app_tsx, r"v0\.\d+\.\d+")

        vite_config = (ROOT / "web_ui" / "frontend" / "vite.config.ts").read_text(encoding="utf-8")
        self.assertIn("VERSION", vite_config)
        self.assertIn("__APP_VERSION__", vite_config)

        declarations = (ROOT / "web_ui" / "frontend" / "src" / "vite-env.d.ts").read_text(encoding="utf-8")
        self.assertIn("declare const __APP_VERSION__: string", declarations)

    def test_sync_check_passes(self) -> None:
        result = subprocess.run(
            [sys.executable, "scripts/sync_version.py", "--check"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)


if __name__ == "__main__":
    unittest.main()
