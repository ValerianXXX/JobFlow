from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import tempfile
import zipfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "browser-companion"
ALLOWED_FILES = (
    "dom.js",
    "icons/icon-16.png",
    "icons/icon-32.png",
    "icons/icon-48.png",
    "icons/icon-128.png",
    "manifest.json",
    "pair.js",
    "popup.css",
    "popup.html",
    "popup.js",
    "service-worker.js",
)


def _store_manifest() -> tuple[dict[str, Any], str]:
    manifest = json.loads((SOURCE / "manifest.json").read_text(encoding="utf-8"))
    version = str(manifest.get("version", ""))
    if manifest.get("manifest_version") != 3 or not version:
        raise SystemExit("Browser Companion manifest is not store-ready.")
    description = str(manifest.get("description", ""))
    if not description or len(description) > 132:
        raise SystemExit("Browser Companion description must contain 1 to 132 characters.")
    missing = [name for name in ALLOWED_FILES if not (SOURCE / name).is_file()]
    if missing:
        raise SystemExit(f"Browser Companion package files are missing: {', '.join(missing)}")
    forbidden = [
        path
        for path in SOURCE.rglob("*")
        if path.is_file() and path.name.casefold() == "binding.json"
    ]
    if forbidden:
        raise SystemExit("A private Browser Companion binding must never enter the store package.")
    store_manifest = dict(manifest)
    # Store identities are assigned by Chrome and Edge. The source key remains
    # useful only for the deterministic unpacked-development identity.
    store_manifest.pop("key", None)
    return store_manifest, version


def _payloads() -> tuple[dict[str, bytes], str, str]:
    manifest, version = _store_manifest()
    payloads: dict[str, bytes] = {}
    source_digest = hashlib.sha256()
    for name in sorted(ALLOWED_FILES):
        payload = (
            (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
            if name == "manifest.json"
            else (SOURCE / name).read_bytes()
        )
        payloads[name] = payload
        source_digest.update(name.encode("utf-8"))
        source_digest.update(b"\0")
        source_digest.update(payload)
        source_digest.update(b"\0")
    return payloads, version, "sha256:" + source_digest.hexdigest()


def verify_store_package(path: Path) -> dict[str, object]:
    findings: list[str] = []
    names: list[str] = []
    version = ""
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            if archive.testzip() is not None:
                findings.append("corrupt_member")
            if names != sorted(ALLOWED_FILES):
                findings.append("file_set_mismatch")
            if any(Path(name).name.casefold() == "binding.json" for name in names):
                findings.append("private_binding_present")
            try:
                manifest = json.loads(archive.read("manifest.json"))
            except (KeyError, json.JSONDecodeError, UnicodeError):
                findings.append("manifest_invalid")
            else:
                version = str(manifest.get("version", ""))
                description = str(manifest.get("description", ""))
                if (
                    manifest.get("manifest_version") != 3
                    or not version
                    or not description
                    or len(description) > 132
                    or "key" in manifest
                ):
                    findings.append("manifest_not_store_ready")
    except (OSError, zipfile.BadZipFile):
        findings.append("archive_invalid")
    digest = ""
    if path.is_file():
        digest = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
    return {
        "status": "PASS" if not findings else "FAIL",
        "version": version,
        "sha256": digest,
        "file_count": len(names),
        "private_binding_files": sum(
            1 for name in names if Path(name).name.casefold() == "binding.json"
        ),
        "findings": sorted(set(findings)),
    }


def build(output: Path | None = None) -> dict[str, object]:
    payloads, version, source_sha256 = _payloads()
    output = output or ROOT / "dist" / f"JobFlow-Browser-Companion-v{version}-store.zip"
    output = Path(os.path.abspath(output))
    output.parent.mkdir(parents=True, exist_ok=True)
    staging: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix=f".{output.name}.",
            suffix=".tmp",
            dir=output.parent,
            delete=False,
        ) as temporary:
            staging = Path(temporary.name)
        with zipfile.ZipFile(staging, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for name, payload in payloads.items():
                info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o100644 << 16
                info.create_system = 3
                archive.writestr(
                    info,
                    payload,
                    compress_type=zipfile.ZIP_DEFLATED,
                    compresslevel=9,
                )
        # Windows requires a writable handle for fsync/FlushFileBuffers.
        descriptor = os.open(staging, os.O_RDWR | getattr(os, "O_BINARY", 0))
        try:
            opened = os.fstat(descriptor)
            if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1:
                raise SystemExit("Browser Companion staging package is unsafe.")
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        verification = verify_store_package(staging)
        if verification["status"] != "PASS" or verification["version"] != version:
            raise SystemExit("Browser Companion store package verification failed.")
        os.replace(staging, output)
        staging = None
    finally:
        if staging is not None:
            try:
                staging.unlink(missing_ok=True)
            except OSError:
                pass
    verification = verify_store_package(output)
    if verification["status"] != "PASS" or verification["version"] != version:
        raise SystemExit("Browser Companion committed store package verification failed.")
    return {
        "schema_version": 1,
        "status": "BUILT",
        "version": version,
        "path": output.name,
        "sha256": verification["sha256"],
        "source_sha256": source_sha256,
        "file_count": verification["file_count"],
        "private_binding_files": verification["private_binding_files"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build the deterministic JobFlow Browser Companion store ZIP."
    )
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    print(json.dumps(build(arguments.output), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
