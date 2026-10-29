from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from _support import PROJECT
from jobops.secure_store import WindowsDPAPIStore


@unittest.skipUnless(os.name == "nt", "Windows DPAPI module resolution")
class SecureStoreModuleTests(unittest.TestCase):
    def test_dpapi_uses_system_modules_without_ambient_module_discovery(self) -> None:
        script = PROJECT / ".agents/skills/job-application-operator/scripts/secure-store.ps1"
        with tempfile.TemporaryDirectory(prefix="jobflow-module-resolution-") as raw:
            root = Path(raw)
            modules = root / "modules"
            utility = modules / "Microsoft.PowerShell.Utility"
            utility.mkdir(parents=True)
            # A test-only command shadow: loading it must never be necessary
            # for the real encrypted-store roundtrip, even with a cold cache.
            (utility / "Microsoft.PowerShell.Utility.psm1").write_text(
                "function Add-Type { throw 'SYNTHETIC_AMBIENT_MODULE_LOADED' }\n"
                "Export-ModuleMember -Function Add-Type\n", encoding="utf-8",
            )
            (utility / "Microsoft.PowerShell.Utility.psd1").write_text(
                "@{RootModule='Microsoft.PowerShell.Utility.psm1';ModuleVersion='1.0.0';"
                "FunctionsToExport=@('Add-Type');CmdletsToExport=@();AliasesToExport=@()}\n",
                encoding="utf-8",
            )
            store = WindowsDPAPIStore(script, local_app_data=root / "local")
            with patch.dict(os.environ, {"PSModulePath": str(modules)}):
                stored = store.put_bytes(b"fixed synthetic roundtrip")
                reference = str(stored["secure_ref"])
                try:
                    self.assertEqual(store.get_bytes(reference), b"fixed synthetic roundtrip")
                    self.assertTrue(store.test(reference))
                finally:
                    store.delete(reference)
            self.assertFalse(store.cipher_path(reference).exists())


if __name__ == "__main__":
    unittest.main()
