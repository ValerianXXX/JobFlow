"""Executable synthetic runtimes, never production runtime or signing evidence."""

from __future__ import annotations

from functools import lru_cache
import io
from pathlib import Path
import sys
import zipfile


@lru_cache(maxsize=1)
def _runtime_files() -> tuple[tuple[str, bytes], ...]:
    base = Path(sys.base_prefix)
    tag = f"python{sys.version_info.major}{sys.version_info.minor}"
    library = base / "Lib"
    stdlib = io.BytesIO()
    # A dummy ZIP cannot boot CPython when its own _pth file has the exact
    # production layout. Use the host's real stdlib, without third-party or
    # user modules, and retain the production _pth validation unchanged.
    excluded = {"site-packages", "__pycache__", "test", "tests", "idlelib", "tkinter", "turtledemo"}
    embedded_stdlib = base / f"{tag}.zip"
    if embedded_stdlib.is_file():
        if not zipfile.is_zipfile(embedded_stdlib):
            raise AssertionError("TEST_RUNTIME_STDLIB_ZIP_INVALID")
        stdlib.write(embedded_stdlib.read_bytes())
    else:
        if not (library / "encodings" / "__init__.py").is_file():
            raise AssertionError("TEST_RUNTIME_STDLIB_MISSING")
        with zipfile.ZipFile(stdlib, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for source in sorted(library.rglob("*.py")):
                relative = source.relative_to(library)
                if excluded.intersection(part.casefold() for part in relative.parts):
                    continue
                entry = zipfile.ZipInfo(relative.as_posix(), (2024, 1, 1, 0, 0, 0))
                entry.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(entry, source.read_bytes())
    files = {
        "runtime/python.exe": Path(sys._base_executable).read_bytes(),
        f"runtime/{tag}.dll": (base / f"{tag}.dll").read_bytes(),
        f"runtime/{tag}.zip": stdlib.getvalue(),
        f"runtime/{tag}._pth": f"{tag}.zip\n.\n../app\n".encode("ascii"),
    }
    for name in ("python3.dll", "vcruntime140.dll", "vcruntime140_1.dll"):
        source = base / name
        if source.is_file():
            files[f"runtime/{name}"] = source.read_bytes()
    for native_directory in (base / "DLLs", base):
        if native_directory.is_dir():
            for source in sorted(native_directory.iterdir()):
                if source.is_file() and source.suffix.casefold() in {".pyd", ".dll"}:
                    files[f"runtime/{source.name}"] = source.read_bytes()
    return tuple(sorted(files.items()))


def executable_runtime_files() -> dict[str, bytes]:
    # Callers may mutate their fixture without poisoning the per-process cache.
    return dict(_runtime_files())
