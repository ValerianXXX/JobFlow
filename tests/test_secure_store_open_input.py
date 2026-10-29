from __future__ import annotations
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import _support
from _support import PROJECT
from jobops.secure_store import WindowsDPAPIStore


@unittest.skipUnless(os.name == "nt", "Actual Windows DPAPI host behavior")
class OpenParentInputTests(unittest.TestCase):
    def test_get_test_and_delete_finish_while_parent_control_pipe_stays_open(self):
        with tempfile.TemporaryDirectory(prefix="jobflow-open-input-") as raw:
            root = Path(raw)
            script = PROJECT / ".agents/skills/job-application-operator/scripts/secure-store.ps1"
            store = WindowsDPAPIStore(script, local_app_data=root)
            record = store.put_bytes(b"Original synthetic DPAPI transport fixture")
            program = """
from pathlib import Path
import sys
from jobops.secure_store import WindowsDPAPIStore
store = WindowsDPAPIStore(Path(sys.argv[1]), local_app_data=Path(sys.argv[2]))
assert store.get_bytes(sys.argv[3]) == b'Original synthetic DPAPI transport fixture'
assert store._run('Test', reference=sys.argv[3]).returncode == 0
assert store._run('Delete', reference=sys.argv[3]).returncode == 0
assert not store.cipher_path(sys.argv[3]).exists()
print('SYNTHETIC_DPAPI_LIFECYCLE_PASS', flush=True)
"""
            env = dict(os.environ, PYTHONPATH=str(PROJECT / "src"))
            child = subprocess.Popen([sys.executable, "-c", program, str(script), str(root), record["secure_ref"]],
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
                                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            try:
                # communicate() would close stdin and hide the original defect.
                child.wait(timeout=10)
                self.assertEqual(child.returncode, 0)
                self.assertIn(b"SYNTHETIC_DPAPI_LIFECYCLE_PASS", child.stdout.read())
                self.assertFalse(child.stdin.closed)
                self.assertFalse(store.cipher_path(record["secure_ref"]).exists())
            finally:
                child.stdin.close()
                try:
                    child.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=5)
                child.stdout.close()
                child.stderr.close()


if __name__ == "__main__":
    unittest.main()
