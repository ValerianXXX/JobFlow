from __future__ import annotations

import importlib.util
import os
import platform
import shutil
import sys
from pathlib import Path

from .util import iso_utc


def _command(name: str) -> dict[str, object]:
    path = shutil.which(name)
    return {"name": name, "present": path is not None, "path": path, "health": "AVAILABLE_NOT_EXECUTED" if path else "MISSING_NOT_REQUIRED"}


def audit_environment() -> dict[str, object]:
    local = Path(os.environ.get("LOCALAPPDATA", ""))
    program_files = [Path(os.environ.get("ProgramFiles", "")), Path(os.environ.get("ProgramFiles(x86)", ""))]
    browser_paths = {
        "edge": program_files[1] / "Microsoft/Edge/Application/msedge.exe",
        "chrome-user": local / "Google/Chrome/Application/chrome.exe",
        "chrome-system": program_files[0] / "Google/Chrome/Application/chrome.exe",
    }
    return {
        "schema_version": 1,
        "audited_at": iso_utc(),
        "platform": {"system": platform.system(), "release": platform.release(), "architecture": platform.machine()},
        "python": {"version": platform.python_version(), "executable": sys.executable, "sqlite_builtin": importlib.util.find_spec("sqlite3") is not None},
        "commands": [_command(name) for name in ("git", "pandoc", "pdftoppm", "pdftotext", "sqlite3", "node", "npm")],
        "document_modules": {
            "python_docx": importlib.util.find_spec("docx") is not None,
            "pypdf": importlib.util.find_spec("pypdf") is not None,
            "pdfplumber": importlib.util.find_spec("pdfplumber") is not None,
            "reportlab": importlib.util.find_spec("reportlab") is not None,
        },
        "browser_candidates": [{"name": name, "present": path.is_file(), "path": str(path)} for name, path in browser_paths.items()],
        "database_strategy": "python_stdlib_sqlite3",
        "external_action_policy": "DISABLED",
        "install_actions": [],
        "summary": "Healthy existing components are reused; no installation or login was attempted.",
    }

