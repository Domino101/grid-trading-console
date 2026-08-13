import glob
import os
from typing import List, Tuple


class FileManager:
    def __init__(self):
        self.project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../"))
        self.config_dir = os.path.join(self.project_root, "config")

    def _safe_config_path(self, relative_path: str) -> str:
        root = os.path.realpath(self.project_root)
        config_dir = os.path.realpath(self.config_dir)
        abs_path = os.path.realpath(os.path.join(root, relative_path))
        if not (abs_path == config_dir or abs_path.startswith(config_dir + os.sep)):
            raise ValueError("Invalid path")
        relative = os.path.relpath(abs_path, config_dir)
        parts = relative.split(os.sep)
        if parts and parts[0] == "exchanges":
            raise PermissionError("exchange credentials are not readable via API")
        return abs_path

    def get_scripts(self) -> List[str]:
        pattern = os.path.join(self.project_root, "run_*.py")
        return [os.path.relpath(path, self.project_root) for path in sorted(glob.glob(pattern))]

    def get_configs(self) -> List[str]:
        pattern = os.path.join(self.config_dir, "**", "*.yaml")
        yaml_files = glob.glob(pattern, recursive=True)
        results = []
        for path in yaml_files:
            relative = os.path.relpath(path, self.project_root)
            try:
                self._safe_config_path(relative)
            except (ValueError, PermissionError):
                continue
            results.append(relative)
        return results

    def read_config(self, relative_path: str) -> str:
        abs_path = self._safe_config_path(relative_path)
        with open(abs_path, "r", encoding="utf-8") as handle:
            return handle.read()

    def write_config(self, relative_path: str, content: str) -> None:
        abs_path = self._safe_config_path(relative_path)
        with open(abs_path, "w", encoding="utf-8") as handle:
            handle.write(content)

    def save_config(self, relative_path: str, content: str) -> Tuple[bool, str]:
        try:
            self.write_config(relative_path, content)
            return True, "saved"
        except PermissionError as exc:
            return False, str(exc)
        except ValueError as exc:
            return False, str(exc)
        except OSError as exc:
            return False, str(exc)


file_manager = FileManager()
