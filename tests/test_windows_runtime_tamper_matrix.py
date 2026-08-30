from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path

from jobops.runtime_closure import inventory_runtime_tree, runtime_tree_digest


PROJECT = Path(__file__).resolve().parents[1]
VERIFY = PROJECT / "scripts" / "verify-windows-runtime-closure.ps1"
RUNTIME_LOCK = PROJECT / "config" / "windows-cp313-runtime.lock"
BUILD_LOCK = PROJECT / "config" / "windows-cp313-build.lock"
H = "sha256:" + "1" * 64
REQUIRED_RUNTIME_LAYOUT = (
    ".jobops-root",
    "app/jobops/__init__.py",
    "app/jobops/cli.py",
    "app/jobops/runtime_health.py",
    "config/windows-cp313-build.lock",
    "config/windows-cp313-runtime.lock",
    "runtime/python.exe",
    "runtime/python313._pth",
    "runtime/python313.dll",
    "runtime/python313.zip",
)


def _portable_text_sha256(path: Path) -> str:
    text = path.read_bytes().decode("utf-8")
    return "sha256:" + hashlib.sha256(text.replace("\r\n", "\n").encode("utf-8")).hexdigest()


@unittest.skipUnless(os.name == "nt", "The independent verifier is Windows-only")
class WindowsRuntimeTamperMatrixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.powershell = Path(os.environ["SystemRoot"]) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"

    def _runtime_lock_wheels(self) -> list[dict[str, object]]:
        lock = json.loads(RUNTIME_LOCK.read_text(encoding="utf-8"))
        result: list[dict[str, object]] = []
        for item in lock["packages"]:
            matched = re.search(r"-([^-]+-[^-]+-[^-]+)\.whl$", item["filename"])
            self.assertIsNotNone(matched)
            result.append(
                {
                    "name": item["name"],
                    "version": item["version"],
                    "tag": matched.group(1),
                    "size": item["size"],
                    "sha256": item["sha256"],
                }
            )
        return result

    def _reseal(self, root: Path, value: dict[str, object]) -> None:
        records = inventory_runtime_tree(root)
        value["files"] = records
        value["file_count"] = len(records)
        value["total_bytes"] = sum(record["size"] for record in records)
        value["tree_sha256"] = runtime_tree_digest(records)
        (root / "runtime-closure.json").write_text(
            json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )

    def _fixture(self, root: Path) -> dict[str, object]:
        (root / "runtime").mkdir(parents=True)
        (root / "app" / "jobops").mkdir(parents=True)
        (root / "config").mkdir(parents=True)
        (root / ".jobops-root").write_text("JobFlow runtime\n", encoding="utf-8")
        (root / "runtime" / "python.exe").write_bytes(b"synthetic python")
        (root / "runtime" / "python313.dll").write_bytes(b"synthetic python dll")
        (root / "runtime" / "python313.zip").write_bytes(b"synthetic stdlib")
        (root / "runtime" / "python313._pth").write_bytes(b"python313.zip\n.\n../app\n")
        (root / "app" / "jobops" / "__init__.py").write_text("__version__='0.6.0'\n", encoding="utf-8")
        (root / "app" / "jobops" / "cli.py").write_text("def main(): return 0\n", encoding="utf-8")
        (root / "app" / "jobops" / "runtime_health.py").write_text("def check(): return True\n", encoding="utf-8")
        shutil.copyfile(RUNTIME_LOCK, root / "config" / RUNTIME_LOCK.name)
        shutil.copyfile(BUILD_LOCK, root / "config" / BUILD_LOCK.name)
        build_lock_sha256 = _portable_text_sha256(BUILD_LOCK)
        value: dict[str, object] = {
            "schema_version": 1,
            "status": "BUILT_UNATTESTED",
            "artifact_type": "complete-runtime",
            "platform": "windows-x64",
            "application_version": "0.6.0",
            "source_commit": "a" * 40,
            "python": {
                "version": "3.13.15",
                "artifact_name": "python-3.13.15-embed-amd64.zip",
                "artifact_sha256": "sha256:d1f04d990aee1253d8569e8e5104e30fa9f5fa830899f14843448872d936a2cf",
                "sigstore_identity": "thomas@python.org",
                "sigstore_verified": False,
            },
            "build_inputs": {
                "wheel_lock_sha256": _portable_text_sha256(RUNTIME_LOCK),
                "wheelhouse_tree_sha256": H,
                "application_wheel_sha256": H,
                "application_wheel_provenance": {
                    "format": "JOBFLOW_APPLICATION_WHEEL_PROVENANCE_V1",
                    "source_commit": "a" * 40,
                    "source_git_tree_oid": "b" * 40,
                    "source_build_tree_sha256": "sha256:" + "2" * 64,
                    "source_archive_sha256": "sha256:" + "3" * 64,
                    "build_lock_sha256": build_lock_sha256,
                    "build_recipe_sha256": "sha256:" + "4" * 64,
                    "pass_a_wheel_sha256": H,
                    "pass_b_wheel_sha256": H,
                    "reproducible": True,
                },
                "builder_toolchain_sha256": H,
                "wheels": self._runtime_lock_wheels(),
            },
            "layout": {
                "python": "runtime/python.exe",
                "python_pth": "runtime/python313._pth",
                "application_root": "app",
                "module": "jobops.cli",
            },
            "file_count": 0,
            "total_bytes": 0,
            "tree_sha256": H,
            "files": [],
            "offline_smoke_tests": {"import_passed": True, "schema_passed": True, "external_actions": 0},
            "protected_builder": {
                "evidence_sha256": H,
                "deterministic_rebuild_match": True,
                "outer_signature_ready": False,
            },
        }
        self._reseal(root, value)
        return value

    def _run_root(
        self,
        root: Path,
        *,
        allow_unattested: bool = True,
        allow_pending_smoke: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        command = [
            str(self.powershell),
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(VERIFY),
            "-RuntimeRoot",
            str(root),
        ]
        if allow_unattested:
            command.append("-AllowUnattested")
        if allow_pending_smoke:
            command.append("-AllowPendingSmoke")
        return subprocess.run(command, text=True, capture_output=True, timeout=45, check=False)

    def _run_archive(self, archive: Path, *, allow_unattested: bool = True) -> subprocess.CompletedProcess[str]:
        command = [
            str(self.powershell),
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(VERIFY),
            "-ArchivePath",
            str(archive),
        ]
        if allow_unattested:
            command.append("-AllowUnattested")
        return subprocess.run(command, text=True, capture_output=True, timeout=45, check=False)

    def _assert_failed(self, completed: subprocess.CompletedProcess[str], token: str | None = None) -> None:
        combined = completed.stdout + completed.stderr
        self.assertNotEqual(completed.returncode, 0, combined)
        if token:
            self.assertIn(token, combined)

    def _archive(self, root: Path, archive: Path, *, transform=None, extras=None, prefix="JobFlow-v0.6.0-windows-x64/") -> None:
        entries: list[tuple[str, bytes]] = []
        for path in sorted(root.rglob("*")):
            if path.is_file():
                relative = path.relative_to(root).as_posix()
                name = prefix + relative
                if transform:
                    name = transform(name, relative)
                entries.append((name, path.read_bytes()))
        if extras:
            entries.extend(extras)
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as handle:
            for name, payload in entries:
                handle.writestr(name, payload)

    def _patch_central_sizes(self, archive: Path, sizes: dict[str, tuple[int, int]]) -> None:
        payload = bytearray(archive.read_bytes())
        cursor = 0
        patched: set[str] = set()
        while True:
            offset = payload.find(b"PK\x01\x02", cursor)
            if offset < 0:
                break
            name_length, extra_length, comment_length = struct.unpack_from("<HHH", payload, offset + 28)
            name_start = offset + 46
            name = bytes(payload[name_start : name_start + name_length]).decode("ascii")
            if name in sizes:
                compressed, uncompressed = sizes[name]
                struct.pack_into("<II", payload, offset + 20, compressed, uncompressed)
                patched.add(name)
            cursor = name_start + name_length + extra_length + comment_length
        self.assertEqual(patched, set(sizes))
        archive.write_bytes(payload)

    def test_stock_ps51_expanded_baseline_and_attestation_gate(self) -> None:
        with tempfile.TemporaryDirectory(prefix="jobflow-closure-") as raw:
            root = Path(raw)
            self._fixture(root)
            accepted = self._run_root(root)
            self.assertEqual(accepted.returncode, 0, accepted.stdout + accepted.stderr)
            payload = json.loads(accepted.stdout.strip())
            self.assertEqual(payload["status"], "RUNTIME_CLOSURE_VERIFIED")
            self.assertEqual(payload["closure_status"], "BUILT_UNATTESTED")
            self.assertEqual(payload["external_actions"], 0)
            gated = self._run_root(root, allow_unattested=False)
            self._assert_failed(gated, "JOBFLOW_RUNTIME_CLOSURE_UNATTESTED")

    def test_pending_smoke_is_a_separate_root_only_verification_stage(self) -> None:
        with tempfile.TemporaryDirectory(prefix="jobflow-closure-") as raw:
            root = Path(raw)
            value = self._fixture(root)
            value["offline_smoke_tests"]["import_passed"] = False
            value["offline_smoke_tests"]["schema_passed"] = False
            self._reseal(root, value)

            final = self._run_root(root)
            self._assert_failed(final, "JOBFLOW_RUNTIME_MANIFEST_INVALID")
            pending = self._run_root(root, allow_pending_smoke=True)
            self.assertEqual(pending.returncode, 0, pending.stdout + pending.stderr)
            payload = json.loads(pending.stdout.strip())
            self.assertEqual(payload["status"], "RUNTIME_CLOSURE_STRUCTURE_VERIFIED")
            self.assertEqual(payload["external_actions"], 0)

            value["offline_smoke_tests"]["import_passed"] = True
            self._reseal(root, value)
            mixed = self._run_root(root, allow_pending_smoke=True)
            self._assert_failed(mixed, "JOBFLOW_RUNTIME_MANIFEST_INVALID")

    def test_pending_smoke_cannot_bypass_attestation_or_verify_an_archive(self) -> None:
        with tempfile.TemporaryDirectory(prefix="jobflow-closure-") as raw:
            root = Path(raw)
            self._fixture(root)
            without_unattested = self._run_root(
                root,
                allow_unattested=False,
                allow_pending_smoke=True,
            )
            self._assert_failed(
                without_unattested,
                "JOBFLOW_RUNTIME_PENDING_SMOKE_SCOPE_INVALID",
            )
            archive = root.parent / (root.name + ".zip")
            try:
                self._archive(root, archive)
                command = [
                    str(self.powershell),
                    "-NoProfile",
                    "-NonInteractive",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(VERIFY),
                    "-ArchivePath",
                    str(archive),
                    "-AllowUnattested",
                    "-AllowPendingSmoke",
                ]
                completed = subprocess.run(
                    command,
                    text=True,
                    capture_output=True,
                    timeout=45,
                    check=False,
                )
                self._assert_failed(
                    completed,
                    "JOBFLOW_RUNTIME_PENDING_SMOKE_SCOPE_INVALID",
                )
            finally:
                archive.unlink(missing_ok=True)

    def test_inventory_failures_have_fixed_non_disclosing_subcodes(self) -> None:
        with tempfile.TemporaryDirectory(prefix="jobflow-closure-") as raw:
            root = Path(raw) / "count"
            value = self._fixture(root)

            value["files"] = value["files"][:-1]
            (root / "runtime-closure.json").write_text(
                json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n",
                encoding="utf-8",
            )
            count = self._run_root(root)
            self._assert_failed(count, "JOBFLOW_RUNTIME_INVENTORY_COUNT_MISMATCH")

            root = Path(raw) / "entry"
            value = self._fixture(root)
            value["files"][0]["sha256"] = "sha256:" + "0" * 64
            (root / "runtime-closure.json").write_text(
                json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n",
                encoding="utf-8",
            )
            entry = self._run_root(root)
            self._assert_failed(entry, "JOBFLOW_RUNTIME_INVENTORY_ENTRY_MISMATCH")

            root = Path(raw) / "summary"
            value = self._fixture(root)
            value["tree_sha256"] = "sha256:" + "0" * 64
            (root / "runtime-closure.json").write_text(
                json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n",
                encoding="utf-8",
            )
            summary = self._run_root(root)
            self._assert_failed(summary, "JOBFLOW_RUNTIME_INVENTORY_SUMMARY_MISMATCH")

    def test_unsigned_local_claims_cannot_promote_to_attested(self) -> None:
        with tempfile.TemporaryDirectory(prefix="jobflow-closure-") as raw:
            root = Path(raw)
            value = self._fixture(root)
            value["status"] = "ATTESTED"
            value["python"]["sigstore_verified"] = True
            value["offline_smoke_tests"]["import_passed"] = True
            value["offline_smoke_tests"]["schema_passed"] = True
            value["protected_builder"]["deterministic_rebuild_match"] = True
            value["protected_builder"]["outer_signature_ready"] = True
            self._reseal(root, value)

            for allow_unattested in (True, False):
                with self.subTest(allow_unattested=allow_unattested):
                    completed = self._run_root(root, allow_unattested=allow_unattested)
                    self._assert_failed(completed, "JOBFLOW_RUNTIME_ATTESTATION_UNVERIFIABLE")

    def test_mutation_extra_file_and_exact_pth_fail_closed(self) -> None:
        for mutation in ("content", "extra", "site", "duplicate", "order", "crlf"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory(prefix="jobflow-closure-") as raw:
                root = Path(raw)
                value = self._fixture(root)
                if mutation == "content":
                    (root / "runtime" / "python313.zip").write_bytes(b"changed")
                elif mutation == "extra":
                    (root / "app" / "unexpected.py").write_text("pass\n", encoding="utf-8")
                else:
                    payload = {
                        "site": b"python313.zip\n.\n../app\nimport site\n",
                        "duplicate": b"python313.zip\n.\n../app\n.\n",
                        "order": b".\npython313.zip\n../app\n",
                        "crlf": b"python313.zip\r\n.\r\n../app\r\n",
                    }[mutation]
                    (root / "runtime" / "python313._pth").write_bytes(payload)
                    self._reseal(root, value)
                completed = self._run_root(root)
                self._assert_failed(completed)
                if mutation not in {"content", "extra"}:
                    self.assertIn("JOBFLOW_RUNTIME_PTH_INVALID", completed.stdout + completed.stderr)

    def test_manifest_path_and_absolute_leak_matrix(self) -> None:
        for mutation in ("traversal", "reserved", "unicode", "absolute"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory(prefix="jobflow-closure-") as raw:
                root = Path(raw)
                value = self._fixture(root)
                if mutation == "absolute":
                    value["leak"] = r"C:\private\builder"
                else:
                    value["files"][0]["path"] = {
                        "traversal": "../escape",
                        "reserved": "runtime/CON",
                        "unicode": "runtime/café.txt",
                    }[mutation]
                (root / "runtime-closure.json").write_text(json.dumps(value), encoding="utf-8")
                completed = self._run_root(root)
                self._assert_failed(completed)

    def test_runtime_lock_is_cryptographically_bound(self) -> None:
        with tempfile.TemporaryDirectory(prefix="jobflow-closure-") as raw:
            root = Path(raw)
            value = self._fixture(root)
            lock_path = root / "config" / RUNTIME_LOCK.name
            lock = json.loads(lock_path.read_text(encoding="utf-8"))
            lock["packages"][0]["version"] = "999.0"
            lock_path.write_text(json.dumps(lock), encoding="utf-8")
            self._reseal(root, value)
            completed = self._run_root(root)
            self._assert_failed(completed, "JOBFLOW_RUNTIME_LOCK_BINDING_INVALID")

    def test_expanded_verifier_requires_exact_launcher_layout(self) -> None:
        for omitted in REQUIRED_RUNTIME_LAYOUT:
            with self.subTest(omitted=omitted), tempfile.TemporaryDirectory(prefix="jobflow-closure-") as raw:
                root = Path(raw)
                value = self._fixture(root)
                (root / omitted).unlink()
                self._reseal(root, value)
                self._assert_failed(self._run_root(root), "JOBFLOW_RUNTIME_LAYOUT_MISSING")

    def test_single_wheel_and_lock_package_objects_are_not_coerced_to_arrays(self) -> None:
        with tempfile.TemporaryDirectory(prefix="jobflow-closure-") as raw:
            root = Path(raw)
            value = self._fixture(root)
            value["build_inputs"]["wheels"] = value["build_inputs"]["wheels"][0]
            self._reseal(root, value)
            self._assert_failed(self._run_root(root), "JOBFLOW_RUNTIME_MANIFEST_INVALID")

        with tempfile.TemporaryDirectory(prefix="jobflow-closure-") as raw:
            root = Path(raw)
            value = self._fixture(root)
            lock_path = root / "config" / RUNTIME_LOCK.name
            lock = json.loads(lock_path.read_text(encoding="utf-8"))
            lock["packages"] = lock["packages"][0]
            lock_path.write_text(json.dumps(lock), encoding="utf-8")
            value["build_inputs"]["wheel_lock_sha256"] = _portable_text_sha256(lock_path)
            self._reseal(root, value)
            self._assert_failed(self._run_root(root), "JOBFLOW_RUNTIME_LOCK_BINDING_INVALID")

    def test_git_crlf_checkout_of_lock_remains_portably_bound(self) -> None:
        with tempfile.TemporaryDirectory(prefix="jobflow-closure-") as raw:
            root = Path(raw)
            value = self._fixture(root)
            lock_path = root / "config" / RUNTIME_LOCK.name
            lock_path.write_bytes(lock_path.read_bytes().replace(b"\n", b"\r\n"))
            self._reseal(root, value)
            completed = self._run_root(root)
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            self.assertEqual(json.loads(completed.stdout)["external_actions"], 0)

    def test_hardlink_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory(prefix="jobflow-closure-") as raw:
            root = Path(raw)
            self._fixture(root)
            os.link(root / "runtime" / "python.exe", root / "runtime" / "python-copy.exe")
            self._assert_failed(self._run_root(root), "JOBFLOW_RUNTIME_FILE_IDENTITY_INVALID")

    def test_alternate_data_stream_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory(prefix="jobflow-closure-") as raw:
            root = Path(raw)
            self._fixture(root)
            with open(str(root / "runtime" / "python.exe") + ":hidden", "wb") as handle:
                handle.write(b"hidden")
            self._assert_failed(self._run_root(root), "JOBFLOW_RUNTIME_ADS_REJECTED")

    def test_reparse_fails_closed_when_symlink_privilege_is_available(self) -> None:
        with tempfile.TemporaryDirectory(prefix="jobflow-closure-") as raw:
            root = Path(raw)
            self._fixture(root)
            target = root / "app" / "link.py"
            try:
                target.symlink_to(root / "app" / "jobops" / "__init__.py")
            except OSError:
                self.skipTest("Symlink creation is unavailable on this Windows host")
            self._assert_failed(self._run_root(root), "JOBFLOW_RUNTIME_REPARSE_REJECTED")

    def test_archive_baseline_and_path_tamper_matrix(self) -> None:
        with tempfile.TemporaryDirectory(prefix="jobflow-archive-") as raw:
            base = Path(raw)
            root = base / "root"
            root.mkdir()
            self._fixture(root)
            archive = base / "good.zip"
            self._archive(root, archive)
            accepted = self._run_archive(archive)
            self.assertEqual(accepted.returncode, 0, accepted.stdout + accepted.stderr)
            self.assertEqual(json.loads(accepted.stdout)["external_actions"], 0)

            traversal = base / "traversal.zip"
            self._archive(
                root,
                traversal,
                transform=lambda name, relative: "JobFlow-v0.6.0-windows-x64/../escape" if relative == "runtime/python.exe" else name,
            )
            self._assert_failed(self._run_archive(traversal), "JOBFLOW_RUNTIME_ARCHIVE_INVALID")

            alias = base / "alias.zip"
            self._archive(root, alias, extras=[("JobFlow-v0.6.0-windows-x64/RUNTIME/python.exe", b"alias")])
            self._assert_failed(self._run_archive(alias), "JOBFLOW_RUNTIME_ARCHIVE_INVALID")

            reserved = base / "reserved.zip"
            self._archive(root, reserved, extras=[("JobFlow-v0.6.0-windows-x64/CONIN$/value.txt", b"x")])
            self._assert_failed(self._run_archive(reserved), "JOBFLOW_RUNTIME_ARCHIVE_INVALID")

            overlong = base / "overlong.zip"
            self._archive(root, overlong, extras=[("JobFlow-v0.6.0-windows-x64/" + ("a" * 256), b"x")])
            self._assert_failed(self._run_archive(overlong), "JOBFLOW_RUNTIME_ARCHIVE_INVALID")

            shadow = base / "file-directory-shadow.zip"
            self._archive(
                root,
                shadow,
                extras=[
                    ("JobFlow-v0.6.0-windows-x64/shadow", b"file"),
                    ("JobFlow-v0.6.0-windows-x64/shadow/child.txt", b"child"),
                ],
            )
            self._assert_failed(self._run_archive(shadow), "JOBFLOW_RUNTIME_ARCHIVE_INVALID")

            directory_entry = base / "directory-entry.zip"
            self._archive(
                root,
                directory_entry,
                extras=[("JobFlow-v0.6.0-windows-x64/empty/", b"")],
            )
            self._assert_failed(
                self._run_archive(directory_entry),
                "JOBFLOW_RUNTIME_ARCHIVE_INVALID",
            )

            prefix = base / "prefix.zip"
            self._archive(root, prefix, prefix="not-jobflow/")
            self._assert_failed(self._run_archive(prefix), "JOBFLOW_RUNTIME_ARCHIVE_INVALID")

    def test_archive_resource_boundaries_fail_before_extraction(self) -> None:
        with tempfile.TemporaryDirectory(prefix="jobflow-archive-bounds-") as raw:
            base = Path(raw)
            root = base / "root"
            root.mkdir()
            self._fixture(root)
            prefix = "JobFlow-v0.6.0-windows-x64/"

            oversized = base / "oversized-entry.zip"
            oversized_name = prefix + "oversized.bin"
            self._archive(root, oversized, extras=[(oversized_name, b"x")])
            self._patch_central_sizes(oversized, {oversized_name: (536870913, 536870913)})
            self._assert_failed(self._run_archive(oversized), "JOBFLOW_RUNTIME_ARCHIVE_INVALID")

            over_total = base / "oversized-total.zip"
            total_entries = [(prefix + f"total-{index}.bin", b"x") for index in range(4)]
            self._archive(root, over_total, extras=total_entries)
            self._patch_central_sizes(
                over_total,
                {name: (500_000_000, 500_000_000) for name, _payload in total_entries},
            )
            self._assert_failed(self._run_archive(over_total), "JOBFLOW_RUNTIME_ARCHIVE_INVALID")

            ratio = base / "compression-ratio.zip"
            self._archive(root, ratio, extras=[(prefix + "ratio.bin", b"\0" * 2_000_000)])
            self._assert_failed(
                self._run_archive(ratio),
                "JOBFLOW_RUNTIME_ARCHIVE_INVALID",
            )

            too_many = base / "too-many-entries.zip"
            with zipfile.ZipFile(too_many, "w", compression=zipfile.ZIP_STORED) as handle:
                for index in range(65_536):
                    handle.writestr(prefix + f"empty/{index:05d}.txt", b"")
            self._assert_failed(
                self._run_archive(too_many),
                "JOBFLOW_RUNTIME_ARCHIVE_INVALID",
            )

    def test_builder_and_verifier_archive_limits_are_identical(self) -> None:
        builder = (PROJECT / "scripts" / "build-windows-runtime-closure.ps1").read_text(encoding="utf-8")
        verifier = VERIFY.read_text(encoding="utf-8")
        expected = {
            "RuntimeArchiveMaximumEntries": "65535",
            "RuntimeArchiveMaximumEntryBytes": "536870912",
            "RuntimeArchiveMaximumUncompressedBytes": "1610612736",
            "RuntimeArchiveCompressionRatioMinimumBytes": "1048576",
            "RuntimeArchiveMaximumCompressionRatio": "200.0",
        }
        for name, value in expected.items():
            pattern = rf"\$script:{name}\s*=\s*(?:\[long\]|\[double\])?{re.escape(value)}\b"
            with self.subTest(name=name):
                self.assertRegex(builder, pattern)
                self.assertRegex(verifier, pattern)
        self.assertIn("Assert-CompleteRuntimeArchiveBounds $stream $Prefix", builder)
        self.assertIn("JOBFLOW_RUNTIME_ARCHIVE_COMPRESSION_RATIO_INVALID", builder)
        self.assertIn("JOBFLOW_RUNTIME_ARCHIVE_COMPRESSION_RATIO_INVALID", verifier)


if __name__ == "__main__":
    unittest.main()
