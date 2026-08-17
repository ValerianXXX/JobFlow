from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
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


def build(output: Path | None = None) -> dict[str, object]:
    manifest = json.loads((SOURCE / "manifest.json").read_text(encoding="utf-8"))
    version = str(manifest.get("version", ""))
    if manifest.get("manifest_version") != 3 or not version:
        raise SystemExit("Browser Companion manifest is not store-ready.")
    missing = [name for name in ALLOWED_FILES if not (SOURCE / name).is_file()]
    if missing:
        raise SystemExit(f"Browser Companion package files are missing: {', '.join(missing)}")
    forbidden = [path for path in SOURCE.rglob("*") if path.is_file() and path.name.casefold() == "binding.json"]
    if forbidden:
        raise SystemExit("A private Browser Companion binding must never enter the store package.")

    output = output or ROOT / "dist" / f"JobFlow-Browser-Companion-v{version}-store.zip"
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name in sorted(ALLOWED_FILES):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            info.create_system = 3
            archive.writestr(info, (SOURCE / name).read_bytes(), compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
    digest = hashlib.sha256(output.read_bytes()).hexdigest().upper()
    result = {
        "status": "BUILT",
        "version": version,
        "path": output.name,
        "sha256": digest,
        "file_count": len(ALLOWED_FILES),
        "private_binding_files": 0,
    }
    print(json.dumps(result, indent=2))
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the deterministic JobFlow Browser Companion store ZIP.")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    build(args.output)


if __name__ == "__main__":
    main()
