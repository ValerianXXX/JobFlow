from __future__ import annotations

import base64
import hashlib
import json
import re
import unittest

from _support import PROJECT
from jobops.browser_assist import COMPANION_EXTENSION_ID, COMPANION_EXTENSION_ORIGIN


class BrowserCompanionStaticTests(unittest.TestCase):
    def test_manifest_has_stable_identity_and_least_privilege_defaults(self) -> None:
        manifest = json.loads((PROJECT / "browser-companion" / "manifest.json").read_text(encoding="utf-8"))
        digest = hashlib.sha256(base64.b64decode(manifest["key"])).hexdigest()[:32]
        derived = "".join(chr(ord("a") + int(character, 16)) for character in digest)
        self.assertEqual(derived, COMPANION_EXTENSION_ID)
        self.assertEqual(COMPANION_EXTENSION_ORIGIN, f"chrome-extension://{derived}")
        self.assertEqual(manifest["manifest_version"], 3)
        self.assertEqual(set(manifest["permissions"]), {"activeTab", "alarms", "scripting", "storage"})
        self.assertEqual(set(manifest["host_permissions"]), {"http://127.0.0.1/*", "http://localhost/*"})
        self.assertEqual(manifest["optional_host_permissions"], ["https://*/*"])
        self.assertNotIn("cookies", manifest["permissions"])
        self.assertNotIn("webRequest", manifest["permissions"])

    def test_companion_contains_no_programmatic_submit_or_navigation_call(self) -> None:
        sources = "\n".join(
            (PROJECT / "browser-companion" / name).read_text(encoding="utf-8")
            for name in ("dom.js", "service-worker.js", "pair.js", "popup.js")
        )
        for forbidden in (
            r"\.requestSubmit\s*\(",
            r"\.submit\s*\(",
            r"\.click\s*\(",
            r"chrome\.tabs\.update\s*\(",
            r"chrome\.tabs\.create\s*\(",
        ):
            self.assertIsNone(re.search(forbidden, sources), forbidden)
        self.assertIn('event.isTrusted', sources)
        self.assertIn('trusted_user_event: true', sources)
        self.assertIn('result-unavailable', sources)

    def test_companion_install_helper_and_bilingual_ui_entry_are_packaged(self) -> None:
        wrapper = (PROJECT / "Install JobFlow Browser Companion.cmd").read_text(encoding="utf-8")
        helper = (PROJECT / "scripts" / "install-jobflow-browser-companion.ps1").read_text(encoding="utf-8")
        html = (PROJECT / "src" / "jobops" / "ui" / "index.html").read_text(encoding="utf-8")
        app = (PROJECT / "src" / "jobops" / "ui" / "app.js").read_text(encoding="utf-8")
        self.assertIn("pause", wrapper.casefold())
        self.assertIn(COMPANION_EXTENSION_ID, helper)
        self.assertIn("browserAssistTitle", html)
        self.assertIn("真实公司官网辅助投递", app)
        self.assertIn("Assisted application on a real company site", app)


if __name__ == "__main__":
    unittest.main()
