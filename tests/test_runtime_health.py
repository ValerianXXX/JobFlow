from __future__ import annotations

import ast
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
HEALTH_SOURCE = PROJECT_ROOT / "src" / "jobops" / "runtime_health.py"
SUCCESS_BYTES = b"JOBFLOW_RUNTIME_HEALTH_OK_V1\n"
FAILURE_BYTES = b"JOBFLOW_RUNTIME_HEALTH_FAILED_V1\n"

CONFIG_NAMES = {
    "browser-companion-stores.json",
    "github-release.json",
    "knowledge-sources.json",
    "policy.json",
    "python-support-policy.json",
    "public-release.json",
    "release-toolchain.json",
    "update-channel.json",
    "windows-runtime-source.json",
    "windows-cp313-build.lock",
    "windows-cp313-runtime.lock",
}
SCHEMA_NAMES = {
    "application-readiness.schema.json",
    "candidate-profile.schema.json",
    "external-claim-set.schema.json",
    "installed-pointer-v2.schema.json",
    "onboarding-answer-bank.schema.json",
    "onboarding-completion.schema.json",
    "python-support-policy.schema.json",
    "release-readiness.schema.json",
    "resume-tailoring-manifest.schema.json",
    "review-packet.schema.json",
    "runtime-closure.schema.json",
    "update-manifest-v2.schema.json",
}
PACKAGE_SENTINELS = {
    "cffi": ("app/cffi/__init__.py", "app/_cffi_backend.pyd"),
    "charset-normalizer": ("app/charset_normalizer/__init__.py",),
    "cryptography": ("app/cryptography/__init__.py",),
    "lxml": ("app/lxml/__init__.py",),
    "packaging": ("app/packaging/__init__.py",),
    "pdfminer.six": ("app/pdfminer/__init__.py",),
    "pdfplumber": ("app/pdfplumber/__init__.py",),
    "pillow": ("app/PIL/__init__.py",),
    "pycparser": ("app/pycparser/__init__.py",),
    "pypdf": ("app/pypdf/__init__.py",),
    "pypdfium2": ("app/pypdfium2/__init__.py",),
    "python-docx": ("app/docx/__init__.py",),
    "typing-extensions": ("app/typing_extensions.py",),
}


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")


def _runtime_lock() -> dict[str, object]:
    packages = []
    for index, name in enumerate(sorted(PACKAGE_SENTINELS), start=1):
        packages.append(
            {
                "name": name,
                "version": f"1.0.{index}",
                "filename": f"{name.replace('.', '_')}-1.0.{index}-py3-none-any.whl",
                "size": index,
                "sha256": "sha256:" + hashlib.sha256(name.encode("ascii")).hexdigest(),
            }
        )
    return {
        "schema_version": 1,
        "lock_type": "runtime-wheelhouse",
        "python_tag": "cp313",
        "abi": "cp313-or-abi3",
        "platform": "win_amd64",
        "only_binary": True,
        "packages": packages,
    }


def _policy() -> dict[str, object]:
    return {
        "schema_version": 3,
        "user_present_browser_assist_enabled": True,
        "external_actions_enabled": False,
        "final_submit_implementation_present": False,
        "unattended_submission_enabled": False,
        "account_creation_enabled": False,
        "submission_unknown_auto_retry": False,
        "phase_5_6_authorization": "PER_APPLICATION_USER_PRESENT_PREFILL_UPLOAD_AND_SCOPED_FORWARD_NAVIGATION_ONLY",
        "pending_approval_limit_bounds": {"minimum": 1, "maximum": 1000},
    }


def _runtime_source() -> dict[str, object]:
    return {
        "schema_version": 1,
        "status": "PINNED_OFFICIAL_SOURCE",
        "platform": "windows-x64",
        "python": {"version": "3.13.15"},
        "isolation": {
            "network_during_assembly": False,
            "network_during_smoke_test": False,
            "import_site": False,
            "end_user_pip": False,
        },
    }


def _python_support_policy() -> dict[str, object]:
    return {
        "schema_version": 1,
        "status": "ROLE_SPLIT_INTENTIONAL",
        "source_package": {
            "requires_python": ">=3.11,<3.14",
            "tested_minors": ["3.11", "3.12", "3.13"],
        },
        "legacy_windows_source_installer": {
            "allowed_minors": ["3.11", "3.12"],
            "distribution_policy": "PYTHON_SOFTWARE_FOUNDATION_SIGNED_SYSTEM_INSTALLATION",
        },
        "production_complete_windows_runtime": {
            "exact_version": "3.13.15",
            "python_tag": "cp313",
            "runtime_tag": "python313",
            "architecture": "AMD64",
            "source_policy": "config/windows-runtime-source.json",
            "runtime_lock": "config/windows-cp313-runtime.lock",
            "build_lock": "config/windows-cp313-build.lock",
        },
    }


def _update_channel() -> dict[str, object]:
    return {
        "schema_version": 1,
        "product": "JobFlow",
        "channel": "stable",
        "signature": {"algorithm": "RSA-PKCS1-v1_5-SHA256"},
    }


def make_runtime(parent: Path) -> Path:
    root = parent / "candidate-runtime"
    (root / "app" / "jobops").mkdir(parents=True)
    (root / "runtime").mkdir()
    (root / "config").mkdir()
    (root / "schemas").mkdir()
    (root / ".jobops-root").write_text("jobops-root-v1\n", encoding="ascii")
    (root / "app" / "jobops" / "__init__.py").write_text("", encoding="ascii")
    shutil.copyfile(HEALTH_SOURCE, root / "app" / "jobops" / "runtime_health.py")
    shutil.copyfile(sys._base_executable, root / "runtime" / "python.exe")
    shutil.copyfile(
        Path(sys.base_prefix) / f"python{sys.version_info.major}{sys.version_info.minor}.dll",
        root / "runtime" / f"python{sys.version_info.major}{sys.version_info.minor}.dll",
    )
    (root / "runtime" / "python313.zip").write_bytes(b"test-stdlib")
    (root / "runtime" / "python313._pth").write_bytes(b"python313.zip\n.\n../app\n")
    test_pth = root / "runtime" / f"python{sys.version_info.major}{sys.version_info.minor}._pth"
    if test_pth.name != "python313._pth":
        base = Path(sys.base_prefix)
        test_pth.write_text(
            "\n".join(
                (
                    str(base / f"python{sys.version_info.major}{sys.version_info.minor}.zip"),
                    str(base / "DLLs"),
                    str(base / "Lib"),
                    "../app",
                    "",
                )
            ),
            encoding="utf-8",
        )

    config_values: dict[str, object] = {name: {"schema_version": 1} for name in CONFIG_NAMES}
    config_values["policy.json"] = _policy()
    config_values["python-support-policy.json"] = _python_support_policy()
    config_values["windows-runtime-source.json"] = _runtime_source()
    config_values["update-channel.json"] = _update_channel()
    config_values["windows-cp313-runtime.lock"] = _runtime_lock()
    for name, value in config_values.items():
        _write_json(root / "config" / name, value)
    for name in SCHEMA_NAMES:
        _write_json(
            root / "schemas" / name,
            {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "$id": f"https://jobflow.local/schemas/{name}",
                "type": "object",
            },
        )
    for sentinels in PACKAGE_SENTINELS.values():
        for relative in sentinels:
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"dependency-sentinel")
    return root


def make_data(parent: Path, name: str = "Data") -> Path:
    root = parent / name / "JobOps" / "Data"
    root.mkdir(parents=True)
    _write_json(root / ".jobflow-data-root", {"schema_version": 1, "kind": "JOBFLOW_RUNTIME_DATA"})
    return root


def make_database(data_root: Path, *, schema_version: str = "15", queue_row: bool = True) -> Path:
    state = data_root / "state"
    state.mkdir(exist_ok=True)
    database = state / "jobops.db"
    connection = sqlite3.connect(database)
    try:
        connection.execute("CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL)")
        connection.execute("INSERT INTO metadata(key,value) VALUES('schema_version',?)", (schema_version,))
        connection.execute(
            "CREATE TABLE queue_settings("
            "singleton_id INTEGER PRIMARY KEY CHECK(singleton_id=1),"
            "pending_approval_limit INTEGER NOT NULL CHECK(pending_approval_limit>=1),"
            "continue_after_awaiting_approval INTEGER NOT NULL CHECK(continue_after_awaiting_approval=1),"
            "updated_at TEXT NOT NULL)"
        )
        if queue_row:
            connection.execute(
                "INSERT INTO queue_settings VALUES(1,10,1,'2026-08-28 00:00:00')"
            )
        connection.commit()
    finally:
        connection.close()
    return database


def data_snapshot(root: Path) -> tuple[tuple[str, str, bytes], ...]:
    records: list[tuple[str, str, bytes]] = []
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            records.append((relative, "link", os.readlink(path).encode("utf-8")))
        elif path.is_dir():
            records.append((relative, "dir", b""))
        elif path.is_file():
            records.append((relative, "file", path.read_bytes()))
        else:
            records.append((relative, "other", b""))
    return tuple(records)


def run_health(
    runtime_root: Path,
    data_root: Path,
    *,
    canary: str | None = None,
    data_root_override: str | None = None,
    include_data_root_override: bool = True,
    minimal_environment: bool = False,
) -> subprocess.CompletedProcess[bytes]:
    if minimal_environment:
        environment = {
            key: os.environ[key]
            for key in ("SystemRoot", "WinDir", "TEMP", "TMP")
            if key in os.environ
        }
    else:
        environment = os.environ.copy()
    environment["LOCALAPPDATA"] = str(data_root.parents[1])
    if include_data_root_override:
        environment["JOBFLOW_DATA_ROOT"] = data_root_override if data_root_override is not None else str(data_root)
    else:
        environment.pop("JOBFLOW_DATA_ROOT", None)
    if canary is not None:
        environment["JOBFLOW_PRIVATE_CANARY"] = canary
    return subprocess.run(
        [
            str(runtime_root / "runtime" / "python.exe"),
            "-I",
            "-B",
            "-X",
            "utf8",
            "-m",
            "jobops.runtime_health",
        ],
        cwd=runtime_root,
        env=environment,
        capture_output=True,
        text=False,
        timeout=20,
        check=False,
    )


class RuntimeHealthTests(unittest.TestCase):
    def test_missing_database_passes_without_creating_state_or_database(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            parent = Path(raw)
            runtime_root = make_runtime(parent)
            data_root = make_data(parent)
            before = data_snapshot(data_root)
            result = run_health(
                runtime_root,
                data_root,
                include_data_root_override=False,
                minimal_environment=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, SUCCESS_BYTES)
            self.assertEqual(result.stderr, b"")
            self.assertEqual(data_snapshot(data_root), before)
            self.assertFalse((data_root / "state").exists())
            self.assertFalse((data_root / "state" / "jobops.db").exists())

    def test_current_database_and_entire_data_fixture_are_byte_for_byte_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            parent = Path(raw)
            runtime_root = make_runtime(parent)
            data_root = make_data(parent)
            make_database(data_root)
            (data_root / "private-canary.bin").write_bytes(bytes(range(256)))
            (data_root / "nested").mkdir()
            (data_root / "nested" / "answer.txt").write_bytes(b"private-value-must-not-change")
            before = data_snapshot(data_root)
            result = run_health(runtime_root, data_root, canary="INHERITED_SECRET_CANARY")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, SUCCESS_BYTES)
            self.assertEqual(result.stderr, b"")
            self.assertEqual(data_snapshot(data_root), before)
            self.assertNotIn(b"INHERITED_SECRET_CANARY", result.stdout + result.stderr)

    def test_missing_queue_singleton_fails_and_is_not_repaired(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            parent = Path(raw)
            runtime_root = make_runtime(parent)
            data_root = make_data(parent)
            database = make_database(data_root, queue_row=False)
            before = data_snapshot(data_root)
            result = run_health(runtime_root, data_root)
            self.assertEqual(result.returncode, 1)
            self.assertEqual(result.stdout, b"")
            self.assertEqual(result.stderr, FAILURE_BYTES)
            self.assertEqual(data_snapshot(data_root), before)
            connection = sqlite3.connect(f"file:{database.as_posix()}?mode=ro&immutable=1", uri=True)
            try:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM queue_settings").fetchone(), (0,))
            finally:
                connection.close()

    def test_old_and_new_schema_versions_fail_without_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            parent = Path(raw)
            runtime_root = make_runtime(parent)
            for index, version in enumerate(("14", "16"), start=1):
                with self.subTest(version=version):
                    data_root = make_data(parent, f"Data{index}")
                    make_database(data_root, schema_version=version)
                    before = data_snapshot(data_root)
                    result = run_health(runtime_root, data_root)
                    self.assertEqual(result.returncode, 1)
                    self.assertEqual(result.stdout, b"")
                    self.assertEqual(result.stderr, FAILURE_BYTES)
                    self.assertEqual(data_snapshot(data_root), before)

    def test_sqlite_transient_and_master_journal_sidecars_are_each_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            parent = Path(raw)
            runtime_root = make_runtime(parent)
            for index, suffix in enumerate(("-journal", "-wal", "-shm", "-mj A1B2C3D4"), start=1):
                with self.subTest(suffix=suffix):
                    data_root = make_data(parent, f"Data{index}")
                    database = make_database(data_root)
                    Path(str(database) + suffix).write_bytes(b"sidecar-canary")
                    before = data_snapshot(data_root)
                    result = run_health(runtime_root, data_root)
                    self.assertEqual(result.returncode, 1)
                    self.assertEqual(result.stdout, b"")
                    self.assertEqual(result.stderr, FAILURE_BYTES)
                    self.assertEqual(data_snapshot(data_root), before)

    def test_orphan_sqlite_sidecars_are_each_rejected_without_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            parent = Path(raw)
            runtime_root = make_runtime(parent)
            data_root = make_data(parent, "UnrelatedOnly")
            state_root = data_root / "state"
            state_root.mkdir()
            (state_root / "unrelated.sqlite-wal").write_bytes(b"unrelated-file")
            before = data_snapshot(data_root)
            result = run_health(runtime_root, data_root)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, SUCCESS_BYTES)
            self.assertEqual(result.stderr, b"")
            self.assertEqual(data_snapshot(data_root), before)

        suffixes = ("-journal", "-wal", "-shm", "-mj A1B2C3D4")
        for index, suffix in enumerate(suffixes, start=1):
            with self.subTest(suffix=suffix), tempfile.TemporaryDirectory() as raw:
                parent = Path(raw)
                runtime_root = make_runtime(parent)
                data_root = make_data(parent, f"OrphanData{index}")
                state_root = data_root / "state"
                state_root.mkdir()
                (state_root / "unrelated.sqlite-wal").write_bytes(b"unrelated-file")
                (state_root / ("jobops.db" + suffix)).write_bytes(b"orphan-sidecar")
                before = data_snapshot(data_root)
                result = run_health(runtime_root, data_root)
                self.assertEqual(result.returncode, 1)
                self.assertEqual(result.stdout, b"")
                self.assertEqual(result.stderr, FAILURE_BYTES)
                self.assertEqual(data_snapshot(data_root), before)

    def test_corrupt_database_is_rejected_without_detail_leak(self) -> None:
        with tempfile.TemporaryDirectory(prefix="PRIVATE_PATH_CANARY_") as raw:
            parent = Path(raw)
            runtime_root = make_runtime(parent)
            data_root = make_data(parent)
            state = data_root / "state"
            state.mkdir()
            (state / "jobops.db").write_bytes(b"CORRUPT_PRIVATE_CANARY")
            before = data_snapshot(data_root)
            result = run_health(runtime_root, data_root, canary="ENV_PRIVATE_CANARY")
            self.assertEqual(result.returncode, 1)
            self.assertEqual(result.stdout, b"")
            self.assertEqual(result.stderr, FAILURE_BYTES)
            self.assertEqual(data_snapshot(data_root), before)
            combined = result.stdout + result.stderr
            self.assertNotIn(b"PRIVATE_PATH_CANARY", combined)
            self.assertNotIn(b"CORRUPT_PRIVATE_CANARY", combined)
            self.assertNotIn(b"ENV_PRIVATE_CANARY", combined)

    def test_database_hardlink_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            parent = Path(raw)
            runtime_root = make_runtime(parent)
            data_root = make_data(parent)
            database = make_database(data_root)
            linked = parent / "linked-database.db"
            try:
                os.link(database, linked)
            except OSError as exc:
                self.skipTest(f"hardlinks unavailable: {type(exc).__name__}")
            result = run_health(runtime_root, data_root)
            self.assertEqual(result.returncode, 1)
            self.assertEqual(result.stdout, b"")
            self.assertEqual(result.stderr, FAILURE_BYTES)

    def test_database_symlink_and_data_root_symlink_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            parent = Path(raw)
            runtime_root = make_runtime(parent)
            real_data = make_data(parent, "RealData")
            real_database = make_database(real_data)
            database_result: subprocess.CompletedProcess[bytes] | None = None
            try:
                state = real_data / "state"
                link_database = state / "jobops-link.db"
                os.symlink(real_database, link_database)
                database = state / "jobops.db"
                database.unlink()
                os.replace(link_database, database)
                database_result = run_health(runtime_root, real_data)
            except OSError:
                pass

            linked_data = parent / "LinkedLocalAppData" / "JobOps" / "Data"
            linked_data.parent.mkdir(parents=True)
            try:
                os.symlink(real_data, linked_data, target_is_directory=True)
            except OSError:
                if os.name != "nt":
                    self.skipTest("directory links unavailable")
                junction = subprocess.run(
                    ["cmd.exe", "/d", "/c", "mklink", "/J", str(linked_data), str(real_data)],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if junction.returncode != 0:
                    self.skipTest("directory reparse points unavailable")
            root_result = run_health(runtime_root, linked_data)
            results = [root_result]
            if database_result is not None:
                results.append(database_result)
            for result in results:
                self.assertEqual(result.returncode, 1)
                self.assertEqual(result.stdout, b"")
                self.assertEqual(result.stderr, FAILURE_BYTES)

    def test_marker_schema_config_and_dependency_fail_closed(self) -> None:
        mutations = (
            ("marker", lambda runtime: (runtime / ".jobops-root").write_text("wrong", encoding="ascii")),
            ("schema", lambda runtime: (runtime / "schemas" / "candidate-profile.schema.json").unlink()),
            ("config", lambda runtime: (runtime / "config" / "policy.json").write_text("{}", encoding="ascii")),
            (
                "python-role-policy",
                lambda runtime: (runtime / "config" / "python-support-policy.json").write_text(
                    "{}", encoding="ascii"
                ),
            ),
            ("dependency", lambda runtime: (runtime / "app" / "docx" / "__init__.py").unlink()),
        )
        for index, (name, mutate) in enumerate(mutations, start=1):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as raw:
                parent = Path(raw)
                runtime_root = make_runtime(parent)
                data_root = make_data(parent, f"Data{index}")
                mutate(runtime_root)
                result = run_health(runtime_root, data_root)
                self.assertEqual(result.returncode, 1)
                self.assertEqual(result.stdout, b"")
                self.assertEqual(result.stderr, FAILURE_BYTES)

    def test_source_ast_has_first_action_hook_and_no_mutating_or_external_paths(self) -> None:
        source = HEALTH_SOURCE.read_text(encoding="utf-8")
        tree = ast.parse(source)
        top_level_calls: list[ast.Call] = []
        for node in tree.body:
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
                top_level_calls.append(node.value)
        self.assertTrue(top_level_calls)
        first = top_level_calls[0].func
        self.assertIsInstance(first, ast.Attribute)
        self.assertEqual(first.attr, "addaudithook")

        banned_imports = {"subprocess", "socket", "tempfile", "requests", "urllib.request"}
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        self.assertFalse(any(name == banned or name.startswith(banned + ".") for name in imported for banned in banned_imports))
        self.assertFalse(any(name.startswith("jobops") for name in imported))

        stream_function = next(
            node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_assert_no_alternate_streams"
        )
        ctypes_imports = [
            node
            for node in ast.walk(stream_function)
            if (isinstance(node, ast.Import) and any(alias.name == "ctypes" for alias in node.names))
            or (isinstance(node, ast.ImportFrom) and node.module == "ctypes")
        ]
        self.assertEqual(len(ctypes_imports), 2)
        top_level_ctypes = [
            node
            for node in tree.body
            if (isinstance(node, ast.Import) and any(alias.name == "ctypes" for alias in node.names))
            or (isinstance(node, ast.ImportFrom) and node.module == "ctypes")
        ]
        self.assertEqual(top_level_ctypes, [])
        windll_calls = [
            node
            for node in ast.walk(stream_function)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "WinDLL"
        ]
        self.assertEqual(len(windll_calls), 1)
        self.assertEqual(ast.literal_eval(windll_calls[0].args[0]), "kernel32.dll")
        audit_function = next(
            node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_runtime_health_audit"
        )
        kernel32_allowlists = [
            {ast.literal_eval(item) for item in node.elts}
            for node in ast.walk(audit_function)
            if isinstance(node, ast.Set) and all(isinstance(item, ast.Constant) for item in node.elts)
        ]
        self.assertIn({"kernel32", "kernel32.dll"}, kernel32_allowlists)

        banned_calls = {
            "mkdir",
            "replace",
            "rename",
            "unlink",
            "write_bytes",
            "write_text",
            "executescript",
            "commit",
            "rollback",
        }
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                self.assertNotIn(node.func.attr, banned_calls)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute) or node.func.attr != "execute":
                continue
            self.assertTrue(node.args)
            statement = ast.literal_eval(node.args[0])
            self.assertIsInstance(statement, str)
            self.assertIn(statement.lstrip().split(None, 1)[0].upper(), {"PRAGMA", "SELECT"})
        self.assertNotIn("JobOpsDB", source)

        identity_function = next(
            node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_file_identity"
        )
        identity_attributes = {
            node.attr for node in ast.walk(identity_function) if isinstance(node, ast.Attribute)
        }
        self.assertTrue(
            {"st_dev", "st_ino", "st_mode", "st_nlink", "st_size", "st_mtime_ns"}.issubset(
                identity_attributes
            )
        )
        database_function = next(
            node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_validate_database"
        )
        database_same_file_calls = [
            node
            for node in ast.walk(database_function)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_assert_same_file"
        ]
        self.assertEqual(len(database_same_file_calls), 1)

    @unittest.skipUnless(os.name == "nt", "Windows ADS syntax is platform-specific")
    def test_ads_syntax_in_root_argument_is_rejected_with_fixed_output(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            parent = Path(raw)
            runtime_root = make_runtime(parent)
            data_root = make_data(parent)
            result = run_health(
                runtime_root,
                data_root,
                data_root_override=str(data_root) + ":PRIVATE_ADS_CANARY",
            )
            self.assertEqual(result.returncode, 1)
            self.assertEqual(result.stdout, b"")
            self.assertEqual(result.stderr, FAILURE_BYTES)
            self.assertNotIn(b"PRIVATE_ADS_CANARY", result.stdout + result.stderr)

    @unittest.skipUnless(os.name == "nt", "Windows ADS enumeration is platform-specific")
    def test_database_and_control_file_alternate_streams_are_rejected(self) -> None:
        for index, target_kind in enumerate(("database", "control"), start=1):
            with self.subTest(target_kind=target_kind), tempfile.TemporaryDirectory() as raw:
                parent = Path(raw)
                runtime_root = make_runtime(parent)
                data_root = make_data(parent, f"LocalAppData{index}")
                database = make_database(data_root)
                target = database if target_kind == "database" else runtime_root / ".jobops-root"
                stream_path = str(target) + ":PRIVATE_STREAM_CANARY"
                with open(stream_path, "wb") as handle:
                    handle.write(b"PRIVATE_STREAM_VALUE")
                result = run_health(runtime_root, data_root)
                self.assertEqual(result.returncode, 1)
                self.assertEqual(result.stdout, b"")
                self.assertEqual(result.stderr, FAILURE_BYTES)
                with open(stream_path, "rb") as handle:
                    self.assertEqual(handle.read(), b"PRIVATE_STREAM_VALUE")
                self.assertNotIn(b"PRIVATE_STREAM", result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
