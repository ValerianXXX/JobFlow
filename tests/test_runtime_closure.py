from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from jobops.errors import JobOpsError
from jobops.runtime_closure import inventory_runtime_tree, normalize_runtime_path, runtime_tree_digest, verify_runtime_closure


PROJECT = Path(__file__).resolve().parents[1]
SCHEMAS = PROJECT / "schemas"
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


def provenance(*, wheel: str = H, commit: str = "a" * 40, build_lock: str = H) -> dict[str, object]:
    return {
        "format": "JOBFLOW_APPLICATION_WHEEL_PROVENANCE_V1",
        "source_commit": commit,
        "source_git_tree_oid": "b" * 40,
        "source_build_tree_sha256": "sha256:" + "2" * 64,
        "source_archive_sha256": "sha256:" + "3" * 64,
        "build_lock_sha256": build_lock,
        "build_recipe_sha256": "sha256:" + "4" * 64,
        "pass_a_wheel_sha256": wheel,
        "pass_b_wheel_sha256": wheel,
        "reproducible": True,
    }


class RuntimeClosureTests(unittest.TestCase):
    def _fixture(self, root: Path, *, status: str = "BUILT_UNATTESTED") -> dict[str, object]:
        (root / "runtime").mkdir(parents=True)
        (root / "app" / "jobops").mkdir(parents=True)
        (root / "config").mkdir(parents=True)
        (root / ".jobops-root").write_text("JobFlow runtime\n", encoding="utf-8")
        (root / "runtime" / "python.exe").write_bytes(b"synthetic python")
        (root / "runtime" / "python313.dll").write_bytes(b"synthetic python dll")
        (root / "runtime" / "python313._pth").write_bytes(b"python313.zip\n.\n../app\n")
        (root / "runtime" / "python313.zip").write_bytes(b"synthetic stdlib")
        (root / "app" / "jobops" / "__init__.py").write_text("__version__ = '0.6.0'\n", encoding="utf-8")
        (root / "app" / "jobops" / "cli.py").write_text("def main(): return 0\n", encoding="utf-8")
        (root / "app" / "jobops" / "runtime_health.py").write_text("def check(): return True\n", encoding="utf-8")
        (root / "config" / BUILD_LOCK.name).write_bytes(BUILD_LOCK.read_bytes())
        (root / "config" / RUNTIME_LOCK.name).write_bytes(RUNTIME_LOCK.read_bytes())
        records = inventory_runtime_tree(root)
        value: dict[str, object] = {
            "schema_version": 1,
            "status": status,
            "artifact_type": "complete-runtime",
            "platform": "windows-x64",
            "application_version": "0.6.0",
            "source_commit": "a" * 40,
            "python": {
                "version": "3.13.15",
                "artifact_name": "python-3.13.15-embed-amd64.zip",
                "artifact_sha256": H,
                "sigstore_identity": "https://www.python.org/",
                "sigstore_verified": False,
            },
            "build_inputs": {
                "wheel_lock_sha256": H,
                "wheelhouse_tree_sha256": H,
                "application_wheel_sha256": H,
                "application_wheel_provenance": provenance(),
                "builder_toolchain_sha256": H,
                "wheels": [],
            },
            "layout": {
                "python": "runtime/python.exe",
                "python_pth": "runtime/python313._pth",
                "application_root": "app",
                "module": "jobops.cli",
            },
            "file_count": len(records),
            "total_bytes": sum(record["size"] for record in records),
            "tree_sha256": runtime_tree_digest(records),
            "files": records,
            "offline_smoke_tests": {
                "import_passed": True,
                "schema_passed": True,
                "external_actions": 0,
            },
            "protected_builder": {
                "evidence_sha256": H,
                "deterministic_rebuild_match": True,
                "outer_signature_ready": False,
            },
        }
        (root / "runtime-closure.json").write_text(json.dumps(value), encoding="utf-8")
        return value

    def test_structural_complete_runtime_verifies(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            expected = self._fixture(root)
            self.assertEqual(verify_runtime_closure(root, schema_dir=SCHEMAS), expected)

    def test_structural_runtime_cannot_self_attest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._fixture(root)
            with self.assertRaises(JobOpsError) as raised:
                verify_runtime_closure(root, schema_dir=SCHEMAS, require_attested=True)
            self.assertEqual(raised.exception.code, "RUNTIME_CLOSURE_UNATTESTED")
            verify_runtime_closure(root, schema_dir=SCHEMAS, require_attested=False)

    def test_locally_forged_attested_status_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            value = self._fixture(root)
            value["status"] = "ATTESTED"
            value["python"]["sigstore_verified"] = True
            value["offline_smoke_tests"]["import_passed"] = True
            value["offline_smoke_tests"]["schema_passed"] = True
            value["protected_builder"]["deterministic_rebuild_match"] = True
            value["protected_builder"]["outer_signature_ready"] = True
            (root / "runtime-closure.json").write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaises(JobOpsError) as raised:
                verify_runtime_closure(root, schema_dir=SCHEMAS)
            self.assertEqual(raised.exception.code, "SCHEMA_VALIDATION_FAILED")

    def test_structural_runtime_rejects_external_verification_self_claims(self) -> None:
        for section, field in (
            ("python", "sigstore_verified"),
            ("protected_builder", "outer_signature_ready"),
        ):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                value = self._fixture(root)
                value[section][field] = True
                (root / "runtime-closure.json").write_text(json.dumps(value), encoding="utf-8")
                with self.assertRaises(JobOpsError) as raised:
                    verify_runtime_closure(root, schema_dir=SCHEMAS)
                self.assertEqual(raised.exception.code, "SCHEMA_VALIDATION_FAILED")

    def test_mutation_and_extra_file_fail_closed(self) -> None:
        for mutation in ("change", "extra"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                self._fixture(root)
                if mutation == "change":
                    (root / "runtime" / "python313.zip").write_bytes(b"changed")
                else:
                    (root / "app" / "unexpected.py").write_text("pass\n", encoding="utf-8")
                with self.assertRaises(JobOpsError) as raised:
                    verify_runtime_closure(root, schema_dir=SCHEMAS)
                self.assertEqual(raised.exception.code, "RUNTIME_CLOSURE_INVENTORY_MISMATCH")

    def test_every_required_runtime_layout_file_is_mandatory(self) -> None:
        for omitted in REQUIRED_RUNTIME_LAYOUT:
            with self.subTest(omitted=omitted), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                value = self._fixture(root)
                (root / omitted).unlink()
                records = inventory_runtime_tree(root)
                value["files"] = records
                value["file_count"] = len(records)
                value["total_bytes"] = sum(record["size"] for record in records)
                value["tree_sha256"] = runtime_tree_digest(records)
                (root / "runtime-closure.json").write_text(json.dumps(value), encoding="utf-8")
                with self.assertRaises(JobOpsError) as raised:
                    verify_runtime_closure(root, schema_dir=SCHEMAS)
                self.assertEqual(raised.exception.code, "RUNTIME_CLOSURE_LAYOUT_MISSING")

    def test_application_wheel_provenance_tamper_fails_closed(self) -> None:
        cases = {
            "source_commit": (
                "APPLICATION_WHEEL_SOURCE_COMMIT_MISMATCH",
                lambda value: value["build_inputs"]["application_wheel_provenance"].__setitem__(
                    "source_commit", "b" * 40
                ),
            ),
            "second_build": (
                "APPLICATION_WHEEL_REPRODUCIBILITY_MISMATCH",
                lambda value: value["build_inputs"]["application_wheel_provenance"].__setitem__(
                    "pass_b_wheel_sha256", "sha256:" + "f" * 64
                ),
            ),
            "extra_property": (
                "SCHEMA_VALIDATION_FAILED",
                lambda value: value["build_inputs"]["application_wheel_provenance"].__setitem__(
                    "unexpected", True
                ),
            ),
        }
        for name, (code, mutate) in cases.items():
            with self.subTest(case=name), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                value = self._fixture(root)
                mutate(value)
                (root / "runtime-closure.json").write_text(json.dumps(value), encoding="utf-8")
                with self.assertRaises(JobOpsError) as raised:
                    verify_runtime_closure(root, schema_dir=SCHEMAS)
                self.assertEqual(raised.exception.code, code)

    def test_dangerous_windows_paths_are_rejected(self) -> None:
        for value in (
            "../escape",
            "runtime\\python.exe",
            "C:/escape",
            "runtime/CON",
            "runtime/name. ",
            "runtime/\u2028.txt",
            "runtime/stra\u00dfe.txt",
        ):
            with self.subTest(value=value), self.assertRaises(JobOpsError):
                normalize_runtime_path(value)

    def test_pth_cannot_enable_site(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            value = self._fixture(root)
            (root / "runtime" / "python313._pth").write_text("python313.zip\n.\n../app\nimport site\n", encoding="utf-8")
            records = inventory_runtime_tree(root)
            value["files"] = records
            value["file_count"] = len(records)
            value["total_bytes"] = sum(record["size"] for record in records)
            value["tree_sha256"] = runtime_tree_digest(records)
            (root / "runtime-closure.json").write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaises(JobOpsError) as raised:
                verify_runtime_closure(root, schema_dir=SCHEMAS)
            self.assertEqual(raised.exception.code, "RUNTIME_CLOSURE_PTH_INVALID")

    def test_pth_requires_exact_bytes_and_order(self) -> None:
        variants = (
            b"python313.zip\n../app\n.\n",
            b"python313.zip\n.\n",
            b"python313.zip\r\n.\r\n../app\r\n",
            b"python313.zip\n.\n../app",
        )
        for content in variants:
            with self.subTest(content=content), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                value = self._fixture(root)
                (root / "runtime" / "python313._pth").write_bytes(content)
                records = inventory_runtime_tree(root)
                value["files"] = records
                value["file_count"] = len(records)
                value["total_bytes"] = sum(record["size"] for record in records)
                value["tree_sha256"] = runtime_tree_digest(records)
                (root / "runtime-closure.json").write_text(json.dumps(value), encoding="utf-8")
                with self.assertRaises(JobOpsError) as raised:
                    verify_runtime_closure(root, schema_dir=SCHEMAS)
                self.assertEqual(raised.exception.code, "RUNTIME_CLOSURE_PTH_INVALID")

    @unittest.skipUnless(os.name == "nt", "Windows alternate streams are platform-specific")
    def test_alternate_data_stream_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._fixture(root)
            target = root / "runtime" / "python.exe"
            with open(str(target) + ":jobflow-test", "wb") as handle:
                handle.write(b"hidden")
            with self.assertRaises(JobOpsError) as raised:
                inventory_runtime_tree(root)
            self.assertEqual(raised.exception.code, "RUNTIME_CLOSURE_ADS_REJECTED")

    def test_hardlinked_runtime_file_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._fixture(root)
            source = root / "runtime" / "python.exe"
            os.link(source, root / "runtime" / "python-copy.exe")
            with self.assertRaises(JobOpsError) as raised:
                inventory_runtime_tree(root)
            self.assertEqual(raised.exception.code, "RUNTIME_CLOSURE_HARDLINK_REJECTED")


if __name__ == "__main__":
    unittest.main()
