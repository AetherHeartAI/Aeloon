"""
Entry point for running aeloon as a module: python -m aeloon
"""

import json
import os
import sys
from pathlib import Path

# Ensure UTF-8 console output on Windows before any other import.
if sys.platform == "win32":
    try:
        import ctypes

        ctypes.windll.kernel32.SetConsoleOutputCP(65001)
        ctypes.windll.kernel32.SetConsoleCP(65001)
    except Exception:
        pass
    os.environ["PYTHONIOENCODING"] = "utf-8"
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

try:
    _default_config_path = Path.home() / ".aeloon" / "config.json"
    if _default_config_path.exists():
        with open(_default_config_path, encoding="utf-8") as _f:
            _boot_cfg = json.load(_f)
        if _boot_cfg.get("agents", {}).get("defaults", {}).get("fast", False) is True:
            os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "true"
except Exception:
    pass

from aeloon.cli.commands import app

if __name__ == "__main__":
    app()
