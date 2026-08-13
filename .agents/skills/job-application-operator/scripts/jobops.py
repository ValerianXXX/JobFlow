#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path


def find_project_root() -> Path:
    for candidate in (Path(__file__).resolve().parent, *Path(__file__).resolve().parents):
        if (candidate / ".jobops-root").is_file():
            return candidate
    raise SystemExit("JOBOPS_PROJECT_ROOT_NOT_FOUND")


root = find_project_root()
sys.path.insert(0, str(root / "src"))

from jobops.cli import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())

