from __future__ import annotations

import base64
import csv
import hashlib
import io
import re
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from _support import PROJECT


BUILDER = PROJECT / "scripts" / "build-windows-runtime-closure.ps1"
VERSION = "0.4.1"
DIST_INFO = f"jobflow_local-{VERSION}.dist-info"


def _validator_program() -> str:
    text = BUILDER.read_text(encoding="utf-8-sig")
    function = text[text.index("function Get-ApplicationWheelIdentity") :]
    match = re.search(r"\$program = @'\n(?P<program>.*?)\n'@", function, re.DOTALL)
    if match is None:
        raise AssertionError("application-wheel validator program was not found")
    return match.group("program")


def _record(entries: dict[str, bytes]) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n")
    for name, data in entries.items():
        digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode("ascii")
        writer.writerow((name, f"sha256={digest}", str(len(data))))
    writer.writerow((f"{DIST_INFO}/RECORD", "", ""))
    return stream.getvalue().encode("utf-8")


def _write_wheel(
    path: Path,
    additions: dict[str, bytes] | None = None,
    *,
    metadata: bytes | None = None,
    wheel_metadata: bytes | None = None,
) -> None:
    entries = {
        "jobops/__init__.py": b"__version__ = '0.4.1'\n",
        "jobops/cli.py": b"def main(): return 0\n",
        "jobops/runtime_health.py": b"def main(): return 0\n",
        f"{DIST_INFO}/METADATA": metadata or (
            b"Metadata-Version: 2.4\n"
            b"Name: jobflow-local\n"
            b"Version: 0.4.1\n"
            b"Requires-Python: >=3.11,<3.14\n"
        ),
        f"{DIST_INFO}/WHEEL": wheel_metadata or (
            b"Wheel-Version: 1.0\n"
            b"Generator: provenance-test\n"
            b"Root-Is-Purelib: true\n"
            b"Tag: py3-none-any\n"
        ),
    }
    entries.update(additions or {})
    entries[f"{DIST_INFO}/RECORD"] = _record(entries)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in entries.items():
            archive.writestr(name, data)


class WindowsRuntimeApplicationWheelTests(unittest.TestCase):
    def _validate(self, wheel: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-I", "-c", _validator_program(), str(wheel), VERSION],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )

    def test_valid_wheel_passes_deep_record_validation(self) -> None:
        with tempfile.TemporaryDirectory(prefix="jobflow-wheel-") as raw:
            wheel = Path(raw) / f"jobflow_local-{VERSION}-py3-none-any.whl"
            _write_wheel(wheel)
            completed = self._validate(wheel)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertIn('"tag":"py3-none-any"', completed.stdout)

    def test_canonical_metadata_specifier_reordering_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory(prefix="jobflow-wheel-") as raw:
            wheel = Path(raw) / f"jobflow_local-{VERSION}-py3-none-any.whl"
            _write_wheel(
                wheel,
                metadata=(
                    b"Metadata-Version: 2.4\n"
                    b"Name: jobflow-local\n"
                    b"Version: 0.4.1\n"
                    b"Requires-Python: <3.14,>=3.11\n"
                ),
            )
            completed = self._validate(wheel)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertIn('"tag":"py3-none-any"', completed.stdout)

    def test_file_directory_collision_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="jobflow-wheel-") as raw:
            wheel = Path(raw) / f"jobflow_local-{VERSION}-py3-none-any.whl"
            _write_wheel(wheel, {"jobflow": b"ambiguous"})
            completed = self._validate(wheel)
            self.assertNotEqual(completed.returncode, 0)

    def test_second_dist_info_tree_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="jobflow-wheel-") as raw:
            wheel = Path(raw) / f"jobflow_local-{VERSION}-py3-none-any.whl"
            _write_wheel(wheel, {"other-1.0.dist-info/METADATA": b"unexpected"})
            completed = self._validate(wheel)
            self.assertNotEqual(completed.returncode, 0)

    def test_ambiguous_or_incompatible_metadata_and_install_remapping_are_rejected(self) -> None:
        valid_metadata = (
            b"Metadata-Version: 2.4\nName: jobflow-local\nVersion: 0.4.1\n"
            b"Requires-Python: >=3.11,<3.14\n"
        )
        valid_wheel = (
            b"Wheel-Version: 1.0\nGenerator: provenance-test\n"
            b"Root-Is-Purelib: true\nTag: py3-none-any\n"
        )
        cases = {
            "missing_root_is_purelib": ({}, valid_metadata, valid_wheel.replace(b"Root-Is-Purelib: true\n", b"")),
            "false_root_is_purelib": ({}, valid_metadata, valid_wheel.replace(b"true", b"false")),
            "unknown_wheel_version": ({}, valid_metadata, valid_wheel.replace(b"1.0", b"99.0")),
            "incompatible_python": ({}, valid_metadata.replace(b">=3.11,<3.14", b">=3.14"), valid_wheel),
            "duplicate_name": ({}, valid_metadata.replace(b"Name: jobflow-local\n", b"Name: evil\nName: jobflow-local\n"), valid_wheel),
            "data_install_remap": ({f"jobflow_local-{VERSION}.data/purelib/jobops/__init__.py": b"shadow"}, valid_metadata, valid_wheel),
            "top_level_shadow": ({"json.py": b"shadow stdlib"}, valid_metadata, valid_wheel),
        }
        for label, (additions, metadata, wheel_metadata) in cases.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory(prefix="jobflow-wheel-") as raw:
                wheel = Path(raw) / f"jobflow_local-{VERSION}-py3-none-any.whl"
                _write_wheel(wheel, additions, metadata=metadata, wheel_metadata=wheel_metadata)
                completed = self._validate(wheel)
                self.assertNotEqual(completed.returncode, 0)


if __name__ == "__main__":
    unittest.main()
