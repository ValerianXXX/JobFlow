"""Locate an explicit isolated test prerequisite without changing host runtimes."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil


PINNED_NODE_SHA256 = "3602f2bb1a10f2cbab4c36886218a33c1ab3db87290e73b033c46c77147d0237"


def pinned_node_path() -> Path:
    explicit = os.environ.get("JOBFLOW_TEST_NODE_PATH")
    candidates = [Path(explicit)] if explicit is not None else [
        Path.home() / ".cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe",
        Path(shutil.which("node") or "__missing_ci_node__"),
    ]
    source = next((path for path in candidates if path.is_file()), None)
    if source is None or not source.is_absolute() or hashlib.sha256(source.read_bytes()).hexdigest() != PINNED_NODE_SHA256:
        raise AssertionError("Provide the exact official Node 24.19.0 prerequisite via JOBFLOW_TEST_NODE_PATH; host runtimes are not modified or trusted implicitly")
    return source
