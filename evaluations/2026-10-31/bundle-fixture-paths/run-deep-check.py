"""Reproduce the synthetic deep fixture check with the current tracked tree."""
from __future__ import annotations

import hashlib
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

project = Path(__file__).resolve().parents[3]
assert (project / '.jobops-root').is_file()
sys.path.insert(0, str(project / 'tests'))
import test_signed_updates as signed

tools = signed.SignedUpdateTests()
tracked = tools._git(project, 'ls-files').splitlines()
original_root = signed.SIGNED_UPDATE_TEST_ROOT
prefix = 'deep-' + 'x' * max(0, 150 - len(str(original_root)) - 1 - 8 - 5)
with signed._bundle_fixture_root(prefix) as deep_root:
    assert len(str(deep_root)) >= 150
    copied = deep_root / 'inventory'
    tools._copy_bundle_source(copied)
    mismatches = []
    for relative in tracked:
        source = Path(signed._windows_extended_path(project / relative))
        target = Path(signed._windows_extended_path(copied / relative))
        if source.read_bytes() != target.read_bytes() or source.stat().st_mtime_ns != target.stat().st_mtime_ns:
            mismatches.append(relative)
    assert not mismatches, mismatches
    print(json.dumps({'stage': 'all-tracked-copy', 'tracked_files': len(tracked),
        'mismatches': len(mismatches), 'deep_parent_characters': len(str(deep_root)),
        'maximum_destination_characters': max(len(str(copied / item)) for item in tracked),
        'tracked_list_sha256': hashlib.sha256('\n'.join(tracked).encode()).hexdigest()}), flush=True)
    with mock.patch.object(signed, 'SIGNED_UPDATE_TEST_ROOT', deep_root):
        suite = unittest.TestSuite([
            signed.SignedUpdateTests('test_bundle_builder_fails_closed_before_signer_or_formal_outputs'),
            signed.SignedUpdateTests('test_runtime_closure_guard_precedes_ignored_archive_processing'),
        ])
        result = unittest.TextTestRunner(verbosity=2).run(suite)
    assert sorted(p.name for p in deep_root.iterdir()) == ['inventory'], 'Nested fixture cleanup left files'
print(json.dumps({'stage': 'cleanup', 'owned_deep_root_removed': not deep_root.exists()}), flush=True)
sys.exit(0 if result.wasSuccessful() else 1)
