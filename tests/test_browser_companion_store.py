from __future__ import annotations

import base64
import ctypes
import hashlib
import importlib.util
import json
import os
import struct
import subprocess
import tempfile
import textwrap
import unittest
import uuid
import zipfile
from pathlib import Path

from _support import PROJECT


def _windows_local_app_data() -> Path:
    """Resolve LocalAppData without trusting an inherited environment value."""

    class GUID(ctypes.Structure):
        _fields_ = [
            ("Data1", ctypes.c_ulong),
            ("Data2", ctypes.c_ushort),
            ("Data3", ctypes.c_ushort),
            ("Data4", ctypes.c_ubyte * 8),
        ]

    value = uuid.UUID("F1B32785-6FBA-4FCF-9D55-7B8E7F157091")
    guid = GUID(
        value.time_low,
        value.time_mid,
        value.time_hi_version,
        (ctypes.c_ubyte * 8)(*value.bytes[8:]),
    )
    pointer = ctypes.c_wchar_p()
    result = ctypes.windll.shell32.SHGetKnownFolderPath(
        ctypes.byref(guid),
        0,
        None,
        ctypes.byref(pointer),
    )
    if result != 0 or not pointer.value:
        raise OSError(f"SHGetKnownFolderPath(LocalAppData) failed with HRESULT {result}.")
    try:
        return Path(pointer.value).resolve(strict=True)
    finally:
        ctypes.windll.ole32.CoTaskMemFree(pointer)


class BrowserCompanionStoreTests(unittest.TestCase):
    def test_store_package_is_deterministic_complete_and_contains_no_private_binding(self) -> None:
        script_path = PROJECT / "scripts" / "build_browser_companion_store_package.py"
        spec = importlib.util.spec_from_file_location("jobflow_store_builder", script_path)
        self.assertIsNotNone(spec)
        module = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory(prefix="jobflow-store-package-") as raw_temp:
            first = Path(raw_temp) / "first.zip"
            second = Path(raw_temp) / "second.zip"
            result = module.build(first)
            module.build(second)
            self.assertEqual(first.read_bytes(), second.read_bytes())
            self.assertEqual(result["version"], "0.9.2")
            self.assertEqual(result["private_binding_files"], 0)
            self.assertEqual(result["status"], "BUILT")
            self.assertTrue(str(result["sha256"]).startswith("sha256:"))
            self.assertTrue(str(result["source_sha256"]).startswith("sha256:"))
            self.assertEqual(module.verify_store_package(first)["status"], "PASS")
            with zipfile.ZipFile(first) as archive:
                names = archive.namelist()
                self.assertEqual(names, sorted(names))
                self.assertEqual(names.count("manifest.json"), 1)
                self.assertNotIn("binding.json", {Path(name).name.casefold() for name in names})
                manifest = json.loads(archive.read("manifest.json"))
                self.assertNotIn("key", manifest)
                self.assertGreater(len(manifest["description"]), 0)
                self.assertLessEqual(len(manifest["description"]), 132)
                self.assertIn("nativeMessaging", manifest["permissions"])
                for size in (16, 32, 48, 128):
                    self.assertIn(f"icons/icon-{size}.png", names)

    def test_store_package_atomically_replaces_a_stale_archive(self) -> None:
        script_path = PROJECT / "scripts" / "build_browser_companion_store_package.py"
        spec = importlib.util.spec_from_file_location("jobflow_store_rebuilder", script_path)
        self.assertIsNotNone(spec)
        module = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory(prefix="jobflow-store-rebuild-") as raw_temp:
            output = Path(raw_temp) / "store.zip"
            output.write_bytes(b"stale package")
            result = module.build(output)
            self.assertEqual(result["status"], "BUILT")
            self.assertEqual(module.verify_store_package(output)["status"], "PASS")
            self.assertEqual(list(output.parent.glob("*.tmp")), [])

    def test_store_listing_privacy_and_assets_are_public_english_artifacts(self) -> None:
        policy = (PROJECT / "PRIVACY.md").read_text(encoding="utf-8")
        listing = (PROJECT / "docs" / "browser-companion-store-listing.md").read_text(encoding="utf-8")
        privacy_html = (PROJECT / "docs" / "privacy.html").read_text(encoding="utf-8")
        support_html = (PROJECT / "docs" / "support.html").read_text(encoding="utf-8")
        chrome_values = (PROJECT / "docs" / "chrome-web-store-form-values.txt").read_text(encoding="utf-8")
        edge_values = (PROJECT / "docs" / "edge-addons-form-values.txt").read_text(encoding="utf-8")
        self.assertIn("Final submission always remains a user action", policy)
        self.assertIn("nativeMessaging", listing)
        self.assertIn("https://valerianxxx.github.io/JobFlow/privacy.html", listing)
        self.assertIn("JobFlow does not operate a remote collection server", policy)
        self.assertIn("Chrome Web Store User Data Policy, including the Limited Use requirements", policy)
        self.assertIn("Chrome Web Store User Data Policy, including the Limited Use requirements", privacy_html)
        self.assertIn("Final Submit", privacy_html)
        self.assertIn("JobFlow Support", support_html)
        self.assertIn("Never include a resume", support_html)
        self.assertIn("https://valerianxxx.github.io/JobFlow/support.html", chrome_values)
        self.assertIn("https://valerianxxx.github.io/JobFlow/support.html", edge_values)
        for path, size in {
            "small-promo-440x280.png": (440, 280),
            "screenshot-1-local-workflow-1280x800.png": (1280, 800),
            "screenshot-2-approved-prefill-1280x800.png": (1280, 800),
            "marquee-1400x560.png": (1400, 560),
        }.items():
            from PIL import Image

            with Image.open(PROJECT / "docs" / "store-assets" / path) as image:
                self.assertEqual(image.size, size)

    def test_native_host_installer_is_user_scoped_and_store_identity_bound(self) -> None:
        installer = (PROJECT / "scripts" / "install-jobflow-native-host.ps1").read_text(encoding="utf-8")
        source = (PROJECT / "scripts" / "native-messaging" / "JobFlowBrowserCompanionHost.cs").read_text(encoding="utf-8")
        identities = json.loads((PROJECT / "config" / "browser-companion-stores.json").read_text(encoding="utf-8"))
        self.assertIn("Software\\Google\\Chrome\\NativeMessagingHosts", installer)
        self.assertIn("Software\\Microsoft\\Edge\\NativeMessagingHosts", installer)
        self.assertIn("[Microsoft.Win32.Registry]::CurrentUser", installer)
        self.assertIn("Set-CurrentUserOnly", installer)
        self.assertIn("SetAccessRuleProtection($true, $false)", installer)
        self.assertIn("[Security.AccessControl.FileSystemRights]::FullControl", installer)
        self.assertIn("[IO.Directory]::SetAccessControl", installer)
        self.assertIn("[IO.File]::SetAccessControl", installer)
        self.assertNotIn("icacls.exe", installer)
        self.assertIn("ReparsePoint", installer)
        self.assertIn("allowed_origins", installer)
        self.assertIn('$developmentExtensionId = "hhlliaaafegldkmcgmaoaelabipcaooj"', installer)
        self.assertIn("$extensionIds = if ($Development)", installer)
        self.assertNotIn("Write-Host $secret", installer)
        self.assertEqual(
            identities["extension_ids"],
            [
                "hhlliaaafegldkmcgmaoaelabipcaooj",
                "pgcnlkfakkacphkdojdbphccjnbbefic",
                "cebejbohadiofomfiplljnpdefjeiccp",
            ],
        )
        self.assertEqual(
            identities["chrome_web_store_url"],
            "https://chromewebstore.google.com/detail/pgcnlkfakkacphkdojdbphccjnbbefic",
        )
        self.assertEqual(
            identities["edge_addons_url"],
            "https://microsoftedge.microsoft.com/addons/detail/cebejbohadiofomfiplljnpdefjeiccp",
        )
        self.assertIn("COMPANION_NATIVE_HOST_ORIGIN_FORBIDDEN", source)
        self.assertIn("ReadExactly(input, 4)", source)
        self.assertIn("ReparsePoint", source)
        self.assertNotIn("Console.WriteLine", source)

    @unittest.skipUnless(os.name == "nt", "ACL behavior is verified only on Windows.")
    def test_native_host_acl_keeps_installed_files_readable_by_current_user(self) -> None:
        installer = (PROJECT / "scripts" / "install-jobflow-native-host.ps1").read_text(encoding="utf-8")
        # Exercise the ACL helper with the same path, reparse, and hard-link
        # prerequisites that production calls. Extracting only the final helper
        # would bypass those prerequisites and no longer represents the real
        # installer contract.
        start = installer.index("function Assert-ExistingAncestorChainNoReparse")
        end = installer.index("function Read-RegistryDefault", start)
        acl_function = installer[start:end]
        local_app_data = _windows_local_app_data()
        with tempfile.TemporaryDirectory(prefix="jobflow-native-acl-", dir=local_app_data) as raw_temp:
            root = Path(raw_temp) / "host"
            root.mkdir()
            marker = root / "manifest.json"
            marker.write_text('{"status":"synthetic"}', encoding="utf-8")
            escaped_root = str(root).replace("'", "''")
            script = textwrap.dedent(
                f"""
                $ErrorActionPreference = 'Stop'
                $localAppDataRoot = [IO.Path]::GetFullPath('{str(Path(raw_temp)).replace("'", "''")}')
                $localRoot = $localAppDataRoot
                {acl_function}
                Set-CurrentUserOnly '{escaped_root}'
                $marker = Join-Path '{escaped_root}' 'manifest.json'
                $content = [IO.File]::ReadAllText($marker)
                $acl = [IO.File]::GetAccessControl($marker)
                $sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
                $full = @($acl.Access | Where-Object {{
                    $_.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value -eq $sid -and
                    ($_.FileSystemRights -band [Security.AccessControl.FileSystemRights]::FullControl) -ne 0 -and
                    $_.AccessControlType -eq [Security.AccessControl.AccessControlType]::Allow
                }}).Count -gt 0
                [ordered]@{{
                    content = $content
                    current_user_full_control = $full
                    inheritance_protected = $acl.AreAccessRulesProtected
                }} | ConvertTo-Json -Compress
                """
            )
            completed = subprocess.run(
                ["powershell.exe", "-NoLogo", "-NoProfile", "-Command", script],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=30,
            )
            self.assertEqual(
                completed.returncode,
                0,
                f"stdout={completed.stdout!r}\nstderr={completed.stderr!r}",
            )
            result = json.loads(completed.stdout.strip().splitlines()[-1])
            self.assertEqual(result["content"], '{"status":"synthetic"}')
            self.assertTrue(result["current_user_full_control"])
            self.assertTrue(result["inheritance_protected"])

    @unittest.skipUnless(os.name == "nt", "The native host is compiled only on Windows.")
    def test_native_host_returns_binding_only_to_an_allowed_extension(self) -> None:
        source = PROJECT / "scripts" / "native-messaging" / "JobFlowBrowserCompanionHost.cs"
        with tempfile.TemporaryDirectory(prefix="jobflow-native-host-") as raw_temp:
            root = Path(raw_temp) / "JobOps"
            host_root = root / "BrowserCompanionHost"
            host_root.mkdir(parents=True)
            executable = host_root / "JobFlowBrowserCompanionHost.exe"
            escaped_source = str(source).replace("'", "''")
            escaped_output = str(executable).replace("'", "''")
            command = (
                "$ErrorActionPreference='Stop';"
                f"Add-Type -Path '{escaped_source}' -ReferencedAssemblies @('System.Web.Extensions') "
                f"-OutputAssembly '{escaped_output}' -OutputType ConsoleApplication"
            )
            encoded = base64.b64encode(command.encode("utf-16le")).decode("ascii")
            subprocess.run(
                ["powershell.exe", "-NoLogo", "-NoProfile", "-EncodedCommand", encoded],
                check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30,
            )
            installation_id = "0123456789abcdef0123456789abcdef"
            secret = base64.urlsafe_b64encode(bytes([0x5A]) * 32).decode("ascii").rstrip("=")
            (root / "browser-companion-binding.json").write_text(json.dumps({
                "schema_version": 1, "installation_id": installation_id, "secret_b64url": secret,
            }), encoding="utf-8")
            allowed_origins = [
                "chrome-extension://hhlliaaafegldkmcgmaoaelabipcaooj/",
                "chrome-extension://pgcnlkfakkacphkdojdbphccjnbbefic/",
                "chrome-extension://cebejbohadiofomfiplljnpdefjeiccp/",
            ]
            (host_root / "com.jobflow.browser_companion.json").write_text(json.dumps({
                "name": "com.jobflow.browser_companion",
                "description": "Synthetic test host",
                "path": str(executable),
                "type": "stdio",
                "allowed_origins": allowed_origins,
            }), encoding="utf-8")
            request = json.dumps({
                "schema_version": 1,
                "type": "JOBFLOW_GET_INSTALLATION_BINDING",
                "protocol_version": 2,
                "extension_version": "0.9.2",
            }, separators=(",", ":")).encode("utf-8")
            framed = struct.pack("=I", len(request)) + request

            for origin in allowed_origins:
                allowed = subprocess.run(
                    [str(executable), origin], input=framed, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    check=True, timeout=10,
                )
                self.assertEqual(allowed.stderr, b"")
                length = struct.unpack("=I", allowed.stdout[:4])[0]
                response = json.loads(allowed.stdout[4:4 + length])
                self.assertEqual(response, {
                    "status": "READY", "schema_version": 1,
                    "installation_id": installation_id, "secret_b64url": secret,
                })

            denied = subprocess.run(
                [str(executable), "chrome-extension://aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/"],
                input=framed, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, timeout=10,
            )
            denied_length = struct.unpack("=I", denied.stdout[:4])[0]
            denied_response = json.loads(denied.stdout[4:4 + denied_length])
            self.assertEqual(denied_response["code"], "COMPANION_NATIVE_HOST_ORIGIN_FORBIDDEN")
            self.assertNotIn("secret_b64url", denied_response)


if __name__ == "__main__":
    unittest.main()
