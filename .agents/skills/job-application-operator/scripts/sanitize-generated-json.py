from __future__ import annotations

import json
import re
from pathlib import Path


def root() -> Path:
    for candidate in Path(__file__).resolve().parents:
        if (candidate / ".jobops-root").is_file():
            return candidate
    raise SystemExit("JOBOPS_PROJECT_ROOT_NOT_FOUND")


PROJECT = root()
WINDOWS_PATH = re.compile(r"(?i)^[A-Z]:[\\/].+")


def safe(value):
    if isinstance(value, dict):
        return {str(key): safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [safe(item) for item in value]
    if isinstance(value, str) and WINDOWS_PATH.match(value):
        try:
            suffix = Path(value).relative_to(PROJECT).as_posix()
            return "$PROJECT_ROOT/" + suffix
        except ValueError:
            name = Path(value).name
            return "$EXTERNAL_PATH/" + (name or "location")
    return value


def main() -> None:
    changed = 0
    for folder in (PROJECT / "state", PROJECT / "reports", PROJECT / "workspace" / "mock-sites"):
        for path in folder.rglob("*.json"):
            try:
                value = json.loads(path.read_text(encoding="utf-8-sig"))
            except Exception:
                continue
            normalized = safe(value)
            if normalized != value:
                path.write_text(json.dumps(normalized, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                changed += 1
    print(json.dumps({"status": "SANITIZED", "changed_files": changed}))


if __name__ == "__main__":
    main()
