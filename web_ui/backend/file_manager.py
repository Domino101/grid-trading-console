import os
import glob
from typing import List

class FileManager:
    def __init__(self):
        self.project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../"))
        self.config_dir = os.path.join(self.project_root, "config")

    def get_configs(self) -> List[str]:
        # Recursively find yaml and json files in the config dir
        pattern = os.path.join(self.config_dir, "**", "*.yaml")
        yaml_files = glob.glob(pattern, recursive=True)
        # return relative paths starting with config/
        return [os.path.relpath(p, self.project_root) for p in yaml_files]

    def read_config(self, relative_path: str) -> str:
        # Prevent directory traversal
        abs_path = os.path.abspath(os.path.join(self.project_root, relative_path))
        if not abs_path.startswith(self.config_dir):
            raise ValueError("Invalid path")
        with open(abs_path, "r") as f:
            return f.read()

    def write_config(self, relative_path: str, content: str):
        # Prevent directory traversal
        abs_path = os.path.abspath(os.path.join(self.project_root, relative_path))
        if not abs_path.startswith(self.config_dir):
            raise ValueError("Invalid path")
        with open(abs_path, "w") as f:
            f.write(content)

file_manager = FileManager()
