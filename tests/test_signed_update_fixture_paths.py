from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

import test_signed_updates as signed


class SignedUpdateFixturePathTests(unittest.TestCase):
    """Exercise real copy/Git operations beyond legacy Windows path limits."""

    def setUp(self) -> None:
        self.fixture_tools = signed.SignedUpdateTests()
        self.root = self.enterContext(signed._bundle_fixture_root("fixture-path-"))
        self.assertTrue(self.root.is_relative_to(signed.SIGNED_UPDATE_TEST_ROOT.resolve()))

    @staticmethod
    def _extended(path: Path) -> Path:
        return Path(signed._windows_extended_path(path))

    def _source(self, relative: Path) -> tuple[Path, bytes, int]:
        source = self.root / "source"
        source.mkdir()
        file = self._extended(source / relative)
        file.parent.mkdir(parents=True)
        content = b"synthetic tracked bytes\x00\xff\n"
        file.write_bytes(content)
        self.fixture_tools._git(source, "init")
        # Seed the counterexample even when the host has long paths disabled.
        # This is one invocation in this disposable repository, not a setting.
        self.fixture_tools._git(source, "-c", "core.longpaths=true", "add", "--all")
        (source / "untracked.txt").write_text("must not enter the bundle", encoding="utf-8")
        return source, content, file.stat().st_mtime_ns

    def _copy_and_verify(self, source: Path, fixture: Path, relative: Path, content: bytes, mtime: int) -> None:
        with mock.patch.object(signed, "PROJECT", source):
            self.fixture_tools._copy_bundle_source(fixture)
        copied = self._extended(fixture / relative)
        self.assertEqual(copied.read_bytes(), content)
        self.assertEqual(copied.stat().st_mtime_ns, mtime)
        self.assertFalse((fixture / "untracked.txt").exists())

    def test_tracked_leaf_beyond_legacy_limit_keeps_bytes_and_metadata(self) -> None:
        fixture = self.root / "fixture"
        relative = Path("data") / ("x" * (263 - len(str(fixture / "data")) - 1 - 4) + ".bin")
        self.assertEqual(len(str(fixture / relative)), 263)
        source, content, mtime = self._source(relative)
        self.assertGreaterEqual(len(str(source / relative)), 260)
        self._copy_and_verify(source, fixture, relative, content, mtime)

    def test_tracked_parent_beyond_legacy_limit_is_created_and_copied(self) -> None:
        fixture = self.root / "fixture"
        relative = Path("d" * (270 - len(str(fixture)) - 1)) / "nested.bin"
        self.assertEqual(len(str((fixture / relative).parent)), 270)
        source, content, mtime = self._source(relative)
        self._copy_and_verify(source, fixture, relative, content, mtime)

    def test_fixture_git_indexes_long_file_without_persisting_configuration(self) -> None:
        fixture = self.root / "fixture"
        fixture.mkdir()
        self.fixture_tools._git(fixture, "init")
        self.fixture_tools._git(fixture, "config", "--local", "core.longpaths", "false")
        relative = "x" * (263 - len(str(fixture)) - 1 - 4) + ".bin"
        file = fixture / relative
        self.assertEqual(len(str(file)), 263)
        self._extended(file).write_bytes(b"synthetic Git fixture\n")
        before = (fixture / ".git" / "config").read_bytes()
        self.fixture_tools._git(fixture, "add", "--all")
        self.assertEqual(self.fixture_tools._git(fixture, "ls-files"), relative)
        self.assertEqual((fixture / ".git" / "config").read_bytes(), before)
        self.assertEqual(self.fixture_tools._git(fixture, "config", "--local", "--get", "core.longpaths"), "false")


if __name__ == "__main__":
    unittest.main()
