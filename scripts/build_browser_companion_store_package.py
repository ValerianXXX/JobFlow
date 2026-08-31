from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from jobops.browser_companion_store import build, main, verify_store_package  # noqa: E402,F401


if __name__ == "__main__":
    raise SystemExit(main())
