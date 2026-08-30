from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jobops.release_toolchain import (
    ReleaseToolchainError,
    load_python_support_policy,
    load_release_toolchain_policy,
    locked_authenticated_tool,
    sanitized_command_environment,
    verify_javascript_dependency_tree,
    windows_directory,
    windows_system_directory,
)


PROJECT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.name == "nt", "Windows release toolchain")
class ReleaseToolchainTests(unittest.TestCase):
    @staticmethod
    def _copy_policy_fixture(root: Path) -> Path:
        project = root / "project"
        config = project / "config"
        config.mkdir(parents=True)
        for name in (
            "python-support-policy.json",
            "release-toolchain.json",
            "windows-cp313-build.lock",
            "windows-cp313-runtime.lock",
            "windows-runtime-source.json",
        ):
            shutil.copy2(PROJECT / "config" / name, config / name)
        return project

    @staticmethod
    def _write_json(path: Path, value: dict[str, object]) -> None:
        path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")

    def test_policy_and_installed_javascript_tree_match_committed_digest(self) -> None:
        policy = load_release_toolchain_policy(PROJECT)
        self.assertEqual(policy["schema_version"], 1)
        result = verify_javascript_dependency_tree(PROJECT)
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["file_count"], 173)

    def test_production_execution_runtime_is_exactly_pinned_to_cpython_31315(self) -> None:
        policy = load_release_toolchain_policy(PROJECT)
        runtime = policy["python_execution_runtime"]
        source = json.loads(
            (PROJECT / "config" / "windows-runtime-source.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(source["python"]["version"], "3.13.15")
        self.assertEqual(
            source["python"]["artifact_name"],
            "python-3.13.15-embed-amd64.zip",
        )
        self.assertEqual(runtime["python_tag"], "python313")
        self.assertEqual(
            runtime["required_entries"],
            [
                "python.exe",
                "python3.dll",
                "python313.dll",
                "python313.zip",
                "python313._pth",
                "vcruntime140.dll",
                "vcruntime140_1.dll",
                "_hashlib.pyd",
                "unicodedata.pyd",
                "select.pyd",
            ],
        )
        self.assertNotIn("zlib.pyd", runtime["required_entries"])

    def test_python_role_split_is_machine_readable_and_runtime_bound(self) -> None:
        policy = load_python_support_policy(PROJECT)
        self.assertEqual(
            policy["source_package"],
            {
                "requires_python": ">=3.11,<3.14",
                "tested_minors": ["3.11", "3.12", "3.13"],
            },
        )
        self.assertEqual(
            policy["legacy_windows_source_installer"]["allowed_minors"],
            ["3.11", "3.12"],
        )
        self.assertEqual(
            policy["production_complete_windows_runtime"]["exact_version"],
            "3.13.15",
        )

    def test_python_role_policy_drift_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            project = self._copy_policy_fixture(Path(raw))
            policy_path = project / "config" / "python-support-policy.json"
            policy = json.loads(policy_path.read_text(encoding="utf-8"))
            policy["legacy_windows_source_installer"]["allowed_minors"].append("3.13")
            self._write_json(policy_path, policy)
            with self.assertRaisesRegex(
                ReleaseToolchainError,
                "PYTHON_SUPPORT_POLICY_INVALID",
            ):
                load_python_support_policy(project)
            with self.assertRaisesRegex(
                ReleaseToolchainError,
                "RELEASE_TOOLCHAIN_POLICY_INVALID",
            ):
                load_release_toolchain_policy(project)

    def test_python_execution_runtime_rejects_cross_layer_policy_mismatches(self) -> None:
        mutations: dict[str, tuple[str, object]] = {
            "test_only_source_status": ("source_status", "TEST_ONLY_LOCAL_RUNTIME"),
            "artifact_name": ("source", "python-not-the-version.zip"),
            "artifact_bytes_bool": ("source_bytes", True),
            "artifact_bytes_zero": ("source_bytes", 0),
            "artifact_bytes_oversize": ("source_bytes", 128 * 1024 * 1024 + 1),
            "artifact_sha256": ("source_hash", "sha256:not-a-digest"),
            "builder_version": ("builder", "0.0.0"),
            "python_tag": ("tag", "python312"),
            "active_pth_extra": ("pth", ["python313.zip", ".", "../app"]),
            "missing_select": ("missing", "select.pyd"),
            "missing_vcruntime140_1": ("missing", "vcruntime140_1.dll"),
        }
        for label, (kind, value) in mutations.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as raw:
                project = self._copy_policy_fixture(Path(raw))
                policy_path = project / "config" / "release-toolchain.json"
                source_path = project / "config" / "windows-runtime-source.json"
                policy = json.loads(policy_path.read_text(encoding="utf-8"))
                source = json.loads(source_path.read_text(encoding="utf-8"))
                if kind == "source_status":
                    source["status"] = value
                elif kind == "source":
                    source["python"]["artifact_name"] = value
                elif kind == "source_bytes":
                    source["python"]["artifact_bytes"] = value
                elif kind == "source_hash":
                    source["python"]["artifact_sha256"] = value
                elif kind == "builder":
                    source["builder"]["python_version"] = value
                elif kind == "tag":
                    policy["python_execution_runtime"]["python_tag"] = value
                elif kind == "pth":
                    policy["python_execution_runtime"]["active_pth_entries"] = value
                elif kind == "missing":
                    policy["python_execution_runtime"]["required_entries"].remove(value)
                else:  # pragma: no cover - test table invariant
                    self.fail(f"unknown mutation kind: {kind}")
                self._write_json(policy_path, policy)
                self._write_json(source_path, source)
                with self.assertRaisesRegex(
                    ReleaseToolchainError,
                    "RELEASE_TOOLCHAIN_POLICY_INVALID",
                ):
                    load_release_toolchain_policy(project)

    def test_fake_systemroot_cannot_redirect_system_directory(self) -> None:
        expected = windows_system_directory()
        with patch.dict(os.environ, {"SystemRoot": r"C:\attacker", "WINDIR": r"C:\attacker"}):
            self.assertEqual(windows_system_directory(), expected)

    def test_command_environment_uses_the_trusted_windows_drive(self) -> None:
        expected = windows_directory().drive
        with patch.dict(os.environ, {"SystemDrive": r"Z:", "SystemRoot": r"Z:\attacker"}):
            for tool in ("git", "node", "python"):
                value = sanitized_command_environment(tool, project=PROJECT)
                self.assertEqual(value["SystemDrive"], expected)

    def test_command_environment_supplies_a_fixed_native_executable_extension_set(self) -> None:
        with patch.dict(os.environ, {"PATHEXT": ".CPL;.JS;.ATTACKER"}):
            for tool in ("git", "node", "python"):
                value = sanitized_command_environment(tool, project=PROJECT)
                self.assertEqual(value["PATHEXT"], ".COM;.EXE;.BAT;.CMD")

    def test_extra_environment_cannot_replace_protected_windows_values(self) -> None:
        for name in ("COMSPEC", "PATH", "PATHEXT", "SYSTEMROOT", "TEMP"):
            with self.subTest(name=name):
                with self.assertRaisesRegex(
                    ReleaseToolchainError,
                    "RELEASE_TOOL_ENVIRONMENT_INVALID",
                ):
                    sanitized_command_environment("python", extra={name: "attacker"})

    def test_command_environment_drops_injection_variables(self) -> None:
        injected = {
            "GIT_DIR": r"C:\attacker\repo",
            "NODE_OPTIONS": "--require=C:\\attacker.js",
            "NODE_PATH": r"C:\attacker\modules",
            "NPM_CONFIG_PREFIX": r"C:\attacker",
            "PYTHONPATH": r"C:\attacker",
            "PYTHONHOME": r"C:\attacker",
        }
        with patch.dict(os.environ, injected, clear=False):
            for tool in ("git", "node", "python"):
                value = sanitized_command_environment(tool, project=PROJECT)
                for name in injected:
                    self.assertNotIn(name, value)

    def test_forbidden_extra_environment_is_rejected(self) -> None:
        with self.assertRaisesRegex(ReleaseToolchainError, "RELEASE_TOOL_ENVIRONMENT_INVALID"):
            sanitized_command_environment("node", extra={"NODE_OPTIONS": "--require=x"})

    def test_unpinned_tool_is_rejected_while_lock_is_held(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "node.exe"
            path.write_bytes(b"not-a-signed-node")
            with patch("jobops.release_toolchain._has_absolute_reparse_component", return_value=False):
                with self.assertRaisesRegex(ReleaseToolchainError, "RELEASE_NODE_UNTRUSTED"):
                    with locked_authenticated_tool(PROJECT, path.resolve(), "node"):
                        self.fail("untrusted tool was yielded")

    def test_similar_but_unpinned_signer_subject_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "node.exe"
            path.write_bytes(b"signed-by-an-unapproved-certificate")
            with (
                patch("jobops.release_toolchain._has_absolute_reparse_component", return_value=False),
                patch("jobops.release_toolchain._windows_signature_valid", return_value=True),
                patch(
                    "jobops.release_toolchain._embedded_signer_identity",
                    return_value=(
                        "CN=OpenJS Foundation Testing, O=OpenJS Foundation Evil, C=US",
                        "A" * 40,
                    ),
                ),
            ):
                with self.assertRaisesRegex(ReleaseToolchainError, "RELEASE_NODE_UNTRUSTED"):
                    with locked_authenticated_tool(PROJECT, path.resolve(), "node"):
                        self.fail("substring-matching signer was trusted")

    def test_tampered_installed_javascript_bytes_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            package = project / "node_modules" / "playwright"
            package.mkdir(parents=True)
            target = package / "index.js"
            target.write_bytes(b"trusted")
            import hashlib

            row = f"playwright/index.js\t7\t{hashlib.sha256(b'trusted').hexdigest()}"
            policy = {
                "javascript_dependencies": {
                    "packages": ["playwright"],
                    "file_count": 1,
                    "total_bytes": 7,
                    "tree_sha256": "sha256:" + hashlib.sha256(row.encode("utf-8")).hexdigest(),
                }
            }
            target.write_bytes(b"tampered")
            with patch("jobops.release_toolchain.load_release_toolchain_policy", return_value=policy):
                with self.assertRaisesRegex(
                    ReleaseToolchainError,
                    "RELEASE_JAVASCRIPT_DEPENDENCIES_INVALID",
                ):
                    verify_javascript_dependency_tree(project)

    def test_read_lock_denies_replacement_until_context_exits(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "python.exe"
            replacement = Path(temporary) / "replacement.exe"
            path.write_bytes(b"locked-tool")
            replacement.write_bytes(b"replacement")
            digest = "sha256:" + __import__("hashlib").sha256(path.read_bytes()).hexdigest()
            policy = load_release_toolchain_policy(PROJECT)
            policy["tools"]["python"]["allowed_unsigned_sha256"] = [digest]
            with (
                patch("jobops.release_toolchain.load_release_toolchain_policy", return_value=policy),
                patch("jobops.release_toolchain._has_absolute_reparse_component", return_value=False),
                patch("jobops.release_toolchain._windows_signature_valid", return_value=False),
                locked_authenticated_tool(PROJECT, path.resolve(), "python"),
            ):
                with self.assertRaises(PermissionError):
                    os.replace(replacement, path)


if __name__ == "__main__":
    unittest.main()
