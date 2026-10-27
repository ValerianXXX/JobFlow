"""Offline compatibility diagnostics. Never grants pairing or release authority."""
from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path
from typing import Any

from . import __version__
from .browser_assist import COMPANION_EXTENSION_VERSION, COMPANION_PROTOCOL_VERSION

_VERSION = re.compile(r"(?:0|[1-9]\d{0,3})(?:\.(?:0|[1-9]\d{0,3})){2,3}\Z")


def companion_compatibility(protocol: object, version: object) -> dict[str, Any]:
    """Return bounded recovery guidance, without echoing malformed peer values."""
    valid_version = isinstance(version, str) and _VERSION.fullmatch(version) is not None
    valid_protocol = type(protocol) is int and protocol == COMPANION_PROTOCOL_VERSION
    exact = valid_protocol and version == COMPANION_EXTENSION_VERSION
    target = "NONE" if exact else "BOTH"
    if not exact and valid_protocol and valid_version:
        actual = tuple(int(part) for part in version.split("."))
        expected = tuple(int(part) for part in COMPANION_EXTENSION_VERSION.split("."))
        # Ordering is guidance only. A four-part alias is not an exact match.
        size = max(len(actual), len(expected))
        actual += (0,) * (size - len(actual))
        expected += (0,) * (size - len(expected))
        if actual != expected:
            target = "DESKTOP" if actual > expected else "EXTENSION"
    return {
        "status": "AVAILABLE" if exact else "UPDATE_REQUIRED",
        "expected_version": COMPANION_EXTENSION_VERSION,
        "observed_version": version if valid_version else None,
        "expected_protocol": COMPANION_PROTOCOL_VERSION,
        "update_target": target,
    }


def _js_constant(path: Path, name: str) -> str | None:
    matches = re.findall(r'const\s+' + re.escape(name) + r'\s*=\s*"([0-9.]+)";', path.read_text(encoding="utf-8"))
    return matches[0] if len(matches) == 1 else None


def check_source_compatibility(project: Path) -> dict[str, Any]:
    """Check every shipped version surface; store observations are not approvals."""
    findings: list[str] = []
    try:
        matrix = json.loads((project / "config/compatibility-matrix.json").read_text(encoding="utf-8"))
        desktops = matrix["desktops"]
        if (type(matrix.get("schema_version")) is not int or matrix["schema_version"] != 1
                or not isinstance(desktops, list) or not desktops
                or len({row["version"] for row in desktops}) != len(desktops)):
            raise ValueError("invalid matrix")
        for row in desktops:
            if (not isinstance(row, dict) or not _VERSION.fullmatch(row["version"])
                    or not _VERSION.fullmatch(row["companion_version"])
                    or type(row["companion_protocol"]) is not int
                    or row["companion_protocol"] < 1
                    or row["distribution"] not in {"legacy_source_release", "source_prerelease", "signed_runtime"}
                    or (row["manifest_schema"] is not None and type(row["manifest_schema"]) is not int)):
                raise ValueError("invalid desktop row")
        current = next(row for row in desktops if row["version"] == matrix["current_source"])
        stable = next(row for row in desktops if row["version"] == matrix["latest_stable"])
        if type(matrix["bootstrap_manifest_schema"]) is not int or matrix["bootstrap_manifest_schema"] != 2:
            raise ValueError("invalid bootstrap schema")
        python_version = tomllib.loads((project / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
        package = json.loads((project / "package.json").read_text(encoding="utf-8"))
        lock = json.loads((project / "package-lock.json").read_text(encoding="utf-8"))
        manifest = json.loads((project / "browser-companion/manifest.json").read_text(encoding="utf-8"))
        for label, value in {
            "python": python_version, "module": __version__, "javascript": package.get("version"),
            "lock": lock.get("version"), "lock_root": lock.get("packages", {}).get("", {}).get("version"),
        }.items():
            if value != current["version"]:
                findings.append(f"DESKTOP_VERSION_MISMATCH_{label.upper()}")
        for label, value in {
            "backend": COMPANION_EXTENSION_VERSION, "manifest": manifest.get("version"),
            "ui": _js_constant(project / "src/jobops/ui/app.js", "COMPANION_VERSION"),
            "popup": _js_constant(project / "browser-companion/popup.js", "SOURCE_EXTENSION_VERSION"),
        }.items():
            if value != current["companion_version"]:
                findings.append(f"COMPANION_VERSION_MISMATCH_{label.upper()}")
        if current["companion_protocol"] != COMPANION_PROTOCOL_VERSION:
            findings.append("COMPANION_PROTOCOL_MISMATCH")
        if f"## [{current['version']}]" not in (project / "CHANGELOG.md").read_text(encoding="utf-8"):
            findings.append("CHANGELOG_VERSION_MISSING")
    except (OSError, KeyError, TypeError, ValueError, StopIteration):
        return {"status": "FAIL", "findings": ["COMPATIBILITY_INPUT_INVALID"], "public_release_ready": False}
    return {
        "status": "PASS" if not findings else "FAIL",
        "findings": findings,
        "source_version": current["version"],
        "required_companion_version": current["companion_version"],
        "latest_stable": stable["version"],
        "stable_accepts_source_companion": stable["companion_version"] == current["companion_version"],
        "stable_usable_by_bootstrap": stable["manifest_schema"] == matrix["bootstrap_manifest_schema"],
        "scope": "OFFLINE_SOURCE_CONSISTENCY_ONLY",
        "public_release_ready": False,
    }


def main() -> int:
    result = check_source_compatibility(Path.cwd())
    print(json.dumps(result, indent=2))
    return 0 if result["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
