from __future__ import annotations

import json
import shutil
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


PROJECT = Path(__file__).resolve().parents[1]
SRC = PROJECT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


@contextmanager
def project_temp() -> Iterator[Path]:
    base = PROJECT / "tests" / ".tmp"
    base.mkdir(parents=True, exist_ok=True)
    path = Path(tempfile.mkdtemp(prefix="jobops-test-", dir=base))
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def fixture_manifest(path: Path) -> Path:
    value = {
        "schema_version": 1,
        "candidate_root_markers": ["vault/marker.md"],
        "sources": [{
            "id": "personal_redacted",
            "classification": "personal-redacted",
            "root_subpath": "vault",
            "markers": ["marker.md"],
            "allowed_prefixes": ["."],
            "external_claim_policy": "approved_claim_only",
        }],
        "readable_extensions": [".md", ".txt", ".json"],
        "hard_excluded_segments": ["raw-attachments", "数据导入区", "cookies"],
        "hard_excluded_filenames": [".env", "credentials.json"],
    }
    write_json(path, value)
    return path


def make_knowledge_root(path: Path, text: str = "# Marker\n") -> Path:
    (path / "vault").mkdir(parents=True, exist_ok=True)
    (path / "vault" / "marker.md").write_text(text, encoding="utf-8")
    return path

