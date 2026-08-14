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

    def test_companion_has_one_scoped_navigation_call_and_no_programmatic_final_submit(self) -> None:
        sources = "\n".join(
            (PROJECT / "browser-companion" / name).read_text(encoding="utf-8")
            for name in ("dom.js", "service-worker.js", "pair.js", "popup.js")
        )
        for forbidden in (
            r"\.requestSubmit\s*\(",
            r"\.submit\s*\(",
            r"chrome\.tabs\.update\s*\(",
            r"chrome\.tabs\.create\s*\(",
        ):
            self.assertIsNone(re.search(forbidden, sources), forbidden)
        self.assertEqual(len(re.findall(r"\.click\s*\(", sources)), 1)
        self.assertIn('type: "JOBFLOW_CHECK_NAVIGATION"', sources)
        self.assertIn('type: "JOBFLOW_NAVIGATE_APPROVED"', sources)
        self.assertIn('final_submit: false', sources)
        self.assertIn('event.isTrusted', sources)
        self.assertIn('trusted_user_event: true', sources)
        self.assertIn('result-unavailable', sources)
        self.assertIn("const NAVIGATION_SETTLE_MS = 20000;", sources)
        self.assertIn("prior_page_observation_hash", sources)
        self.assertIn('status: "NAVIGATION_PENDING"', sources)
        self.assertIn('status: "NAVIGATION_STALLED"', sources)
        self.assertIn("automatic_retry: false", sources)
        self.assertIn('type: "JOBFLOW_COLLECT_JOB_PAGE"', sources)
        self.assertIn('message.type === "JOBFLOW_CAPTURE_CURRENT"', sources)
        self.assertIn('"JOBFLOW_INTAKE_STATUS"', sources)
        self.assertIn("collectJobPage", sources)
        self.assertNotIn("input.value", (PROJECT / "browser-companion" / "dom.js").read_text(encoding="utf-8"))

    def test_companion_install_helper_and_bilingual_ui_entry_are_packaged(self) -> None:
        wrapper = (PROJECT / "Install JobFlow Browser Companion.cmd").read_text(encoding="utf-8")
        helper = (PROJECT / "scripts" / "install-jobflow-browser-companion.ps1").read_text(encoding="utf-8")
        html = (PROJECT / "src" / "jobops" / "ui" / "index.html").read_text(encoding="utf-8")
        app = (PROJECT / "src" / "jobops" / "ui" / "app.js").read_text(encoding="utf-8")
        self.assertIn("pause", wrapper.casefold())
        self.assertIn(COMPANION_EXTENSION_ID, helper)
        self.assertIn("browserAssistTitle", html)
        self.assertIn("审阅后辅助填写", app)
        self.assertIn("Assisted filling after review", app)
        self.assertIn("最终 Submit", app)
        self.assertIn("Final Submit", app)
        pair = (PROJECT / "browser-companion" / "pair.js").read_text(encoding="utf-8")
        worker = (PROJECT / "browser-companion" / "service-worker.js").read_text(encoding="utf-8")
        self.assertIn("const PROTOCOL = 2;", pair)
        self.assertIn("const PROTOCOL = 2;", worker)
        self.assertIn("protocol_version:2", app)
        self.assertIn("pairing:{protocol_version:result.protocol_version", app)
        self.assertIn("start-guided-intake", app)
        self.assertIn("guidedIntakeTitle", html)
        self.assertIn("Advanced diagnostics and offline QA", app)


if __name__ == "__main__":
    unittest.main()
