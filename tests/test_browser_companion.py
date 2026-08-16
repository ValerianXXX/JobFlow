from __future__ import annotations

import base64
import hashlib
import json
import re
import unittest

from _support import PROJECT
from jobops.browser_assist import (
    COMPANION_EXTENSION_ID,
    COMPANION_EXTENSION_ORIGIN,
    COMPANION_EXTENSION_VERSION,
    _semantic_field,
)


class BrowserCompanionStaticTests(unittest.TestCase):
    def test_semantic_matching_ignores_accessibility_decoration_but_not_prompt_drift(self) -> None:
        base = {
            "control_type": "text", "required": True, "classification": "private_fixed",
            "display_label": "First Name *", "prompt_hash": "sha256:" + "1" * 64,
            "option_count": 0, "display_options": [],
        }
        refreshed = {**base, "display_label": "First Name", "prompt_hash": "sha256:" + "2" * 64}
        changed = {**base, "display_label": "Last Name", "prompt_hash": "sha256:" + "3" * 64}
        self.assertEqual(_semantic_field(base), _semantic_field(refreshed))
        self.assertNotEqual(_semantic_field(base), _semantic_field(changed))

        uploaded = {
            **base, "control_type": "file", "required": False, "classification": "file_upload_stop",
            "display_label": "Click to browse & upload", "prompt_hash": "sha256:" + "4" * 64,
        }
        decorated = {
            **uploaded, "display_label": "File upload IconClick to browse & upload",
            "prompt_hash": "sha256:" + "5" * 64,
        }
        self.assertEqual(_semantic_field(uploaded), _semantic_field(decorated))

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
        self.assertEqual(manifest["optional_permissions"], ["search"])
        self.assertEqual(manifest["version"], COMPANION_EXTENSION_VERSION)
        self.assertEqual(
            manifest["externally_connectable"],
            {
                "matches": ["http://127.0.0.1/*", "http://localhost/*"],
                "accepts_tls_channel_id": False,
            },
        )
        self.assertNotIn("cookies", manifest["permissions"])
        self.assertNotIn("webRequest", manifest["permissions"])

    def test_pairing_is_retryable_versioned_and_does_not_require_all_sites(self) -> None:
        app = (PROJECT / "src" / "jobops" / "ui" / "app.js").read_text(encoding="utf-8")
        pair = (PROJECT / "browser-companion" / "pair.js").read_text(encoding="utf-8")
        worker = (PROJECT / "browser-companion" / "service-worker.js").read_text(encoding="utf-8")
        popup = (PROJECT / "browser-companion" / "popup.js").read_text(encoding="utf-8")
        server = (PROJECT / "src" / "jobops" / "onboarding_server.py").read_text(encoding="utf-8")

        self.assertIn("chrome.runtime.onMessageExternal.addListener", worker)
        self.assertIn('senderOrigin !== pairing.base_url', worker)
        self.assertIn('sender?.tab?.url || sender?.url', worker)
        self.assertIn('!senderOrigin || senderOrigin !== pairing.base_url', worker)
        self.assertIn('message.type === "JOBFLOW_PING"', worker)
        self.assertIn('message.type === "JOBFLOW_CANCEL_GUIDED"', worker)
        self.assertIn("COMPANION_CANCEL_BINDING_INVALID", worker)
        self.assertIn('code: "COMPANION_EXTERNAL_ACTION_FORBIDDEN"', worker)
        self.assertIn("extension_version: EXTENSION_VERSION", worker)
        self.assertIn('chrome.runtime.getURL("binding.json")', worker)
        self.assertIn("crypto.subtle.verify", worker)
        self.assertIn("COMPANION_BINDING_PROOF_INVALID", worker)
        self.assertNotIn("secret_b64url", "\n".join(line for line in worker.splitlines() if "postJSON" in line))
        self.assertIn("COMPANION_POLL_BASE_MS = 1500", app)
        self.assertIn("COMPANION_POLL_MAX_MS = 12000", app)
        self.assertIn("sessionStorage.setItem", app)
        self.assertIn("sessionStorage.removeItem", app)
        self.assertIn('api("cancel-guided-intake"', app)
        self.assertIn("function releaseGuidedCompanionBinding(record)", app)
        self.assertIn("function guidedFailureMessage(session)", app)
        self.assertIn('terminalGuided=["FORM_CAPTURE_FAILED","FAILED"]', app)
        self.assertIn("guidedLevelPreferenceMismatch", app)
        self.assertIn("Object.assign(STRINGS.zh", app)
        self.assertIn("Object.assign(STRINGS.en", app)
        self.assertNotIn("Object.assign(I18N.", app)
        self.assertNotIn("localStorage", app)
        self.assertIn("Number.isFinite(expiry)", app)
        self.assertIn('companionExternalMessage({type:"JOBFLOW_PAIR"', app)
        self.assertIn('window.postMessage({type:"JOBFLOW_PAIR_REQUEST"', app)
        self.assertIn("BROWSER_COMPANION_UPDATE_REQUIRED", app)
        self.assertIn("JOBFLOW_COMPANION_READY", pair)
        self.assertIn("__jobflowPairBridgeGeneration", pair)
        self.assertIn("globalThis.__jobflowPairBridgeGeneration !== GENERATION", pair)
        self.assertIn("chrome.runtime.getManifest().version", pair)
        self.assertIn("岗位任务", popup)
        self.assertIn("approved application", popup)
        self.assertIn('["MANUAL_NAVIGATION_RESTART_REQUIRED", "APPLY_RESTART_REQUIRED"].includes(status?.status) ? text.restartButton', popup)
        self.assertIn('"MANUAL_NAVIGATION_RESTART_REQUIRED", "APPLY_RESTART_REQUIRED", "CONFIRMED"', popup)
        self.assertIn("这次一次性下一步证明没有安全建立", popup)
        self.assertIn("The one-use Next proof was not armed safely", popup)
        self.assertIn("chrome.runtime.getManifest().version", popup)
        self.assertIn("chrome.permissions.request", popup)
        self.assertIn("async function ensureAutomationPermissions()", popup)
        self.assertIn('elements.fill.addEventListener("click"', popup)
        self.assertIn("assist_id: state.assist_id", worker)
        self.assertIn("loopbackOrigin(tab.url || \"\") !== state.base_url", worker)
        self.assertIn("state.jobflow_tab_id", worker)
        self.assertIn("chrome.storage.session.remove", worker)
        self.assertIn("new AbortController()", worker)
        self.assertIn('code: "COMPANION_LOCAL_REQUEST_TIMEOUT"', worker)
        self.assertIn('GUIDED_PREPARATION_ALARM = "jobflow-guided-preparation-observer"', worker)
        self.assertIn('endpoint(state, "/capture-form-status")', worker)
        self.assertIn('result.status === "PREPARING_APPLICATION"', worker)
        self.assertIn("本机 AI 正在后台生成审阅包", popup)
        self.assertIn("Local AI is building the review packet in the background", popup)
        self.assertIn('"PREPARING_APPLICATION", "REVIEW_PACKET_READY"', popup)
        self.assertIn("_companion_pair_body", server)
        self.assertIn("COMPANION_EXTENSION_VERSION", server)
        self.assertIn("validate_pair_request", server)
        self.assertIn("sign_pair_response", server)
        self.assertIn("_companion_local_app_data", server)
        self.assertIn('elif route == "cancel-guided-intake":', server)
        self.assertIn("cancel_guided_intake", server)
        self.assertIn("start_guided_application_form_preparation", server)
        self.assertIn("guided_application_form_preparation_status", server)
        self.assertIn('status: "MANUAL_NAVIGATION_RESTART_REQUIRED"', worker)
        self.assertIn('code: "COMPANION_MANUAL_NAVIGATION_RESTART_REQUIRED"', worker)
        self.assertIn('COMPANION_MANUAL_NAVIGATION_RESTART_REQUIRED:"browserAssistManualRestart"', app)
        self.assertIn('MANUAL_NAVIGATION_RESTART_REQUIRED:"browserAssistManualRestart"', app)
        self.assertIn('APPLY_RESTART_REQUIRED:"browserAssistApplyRestart"', app)
        self.assertIn("function browserAssistApplyFailureMessage(result)", app)
        self.assertIn("result.last_result||result", app)
        self.assertIn("failure_field_label", app)
        self.assertIn("failure_page_position", app)
        self.assertIn("COMPANION_CHOICE_OPTION_NOT_FOUND", app)
        self.assertIn('endpoint(state, "/abort-page-apply")', worker)
        self.assertIn('elif route_parts == ["abort-page-apply"]:', server)
        self.assertIn("attempted_field_bindings", worker)
        self.assertIn("attempted_material_bindings", worker)
        self.assertIn("页面可能已经填写或上传了一部分", popup)
        self.assertIn("The page may already contain some approved fields or an attachment", popup)
        self.assertIn("请结束并重新启动这项申请辅助", app)
        self.assertIn("End this application assist and start it again", app)

    def test_ui_pairing_is_identity_bound_explicit_and_transport_resilient(self) -> None:
        app = (PROJECT / "src" / "jobops" / "ui" / "app.js").read_text(encoding="utf-8")
        worker = (PROJECT / "browser-companion" / "service-worker.js").read_text(encoding="utf-8")

        self.assertIn(
            'result?.application_id!==state.browserAssistSession.application_id||result?.assist_id!==state.browserAssistSession.assist_id',
            app,
        )
        self.assertIn(
            'result?.assist_id!==record.session?.assist_id||result?.application_id!==record.session?.application_id',
            app,
        )
        self.assertIn(
            'result?.intake_id!==state.guidedIntakeSession.intake_id||result?.intake_id!==record.session?.intake_id',
            app,
        )
        self.assertIn("function awaitExplicitCompanionPairing(record)", app)
        self.assertNotIn("async function pairCompanion(record)", app)
        self.assertIn('type:"JOBFLOW_PAIR",pairing:', app)
        self.assertIn('return await pairWithJobFlow(message.pairing, senderOrigin, sender?.tab?.id)', worker)
        self.assertIn("state.companionPollFailures+=1", app)
        self.assertIn("COMPANION_POLL_MAX_MS", app)
        self.assertIn("Transport failures never erase a valid lease or silently re-pair", app)
        poll = app.split("async function pollCompanionStatus(){", 1)[1].split(
            "function startCompanionStatusPolling(){", 1
        )[0]
        self.assertNotIn("record.paired=false", poll)
        self.assertNotIn("awaitExplicitCompanionPairing", poll)
        self.assertNotIn("JOBFLOW_PAIR", poll)
        self.assertIn("scheduleCompanionStatusPoll", poll)
        self.assertIn('state.companionConnectionNotice=record.paired?null:"companionClickToPair"', app)
        self.assertIn('BROWSER_COMPANION_SESSION_ACTIVE:"companionSessionActive"', app)
        self.assertIn('companionModeConflict("guided")', app)
        self.assertIn('companionModeConflict("assist")', app)
        self.assertIn("guidedCompanionActive()||browserCompanionActive()", app)

    def test_companion_has_one_scoped_navigation_call_and_no_programmatic_final_submit(self) -> None:
        worker = (PROJECT / "browser-companion" / "service-worker.js").read_text(encoding="utf-8")
        guided = worker.split("async function runGuidedAutopilot()", 1)[1].split(
            "async function runApplicationAutopilot()", 1
        )[0]
        worker_without_guided = worker.replace(guided, "")
        other_sources = "\n".join(
            (PROJECT / "browser-companion" / name).read_text(encoding="utf-8")
            for name in ("dom.js", "pair.js", "popup.js")
        )
        sources = worker + "\n" + other_sources
        for forbidden in (
            r"\.requestSubmit\s*\(",
            r"\.submit\s*\(",
        ):
            self.assertIsNone(re.search(forbidden, sources), forbidden)
        # Opening a visible browser-search result and its verified direct Apply
        # URL is allowed only during read-only guided intake.  Application assist
        # still has no general tab-navigation capability and final Submit remains
        # a DOM/user-only boundary.
        self.assertRegex(guided, r"chrome\.tabs\.update\s*\(")
        self.assertRegex(guided, r"chrome\.tabs\.create\s*\(")
        self.assertIsNone(re.search(r"chrome\.tabs\.(?:update|create)\s*\(", worker_without_guided))
        self.assertIsNone(re.search(r"chrome\.tabs\.(?:update|create)\s*\(", other_sources))
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
        popup = (PROJECT / "browser-companion" / "popup.js").read_text(encoding="utf-8")
        self.assertIn("pause", wrapper.casefold())
        self.assertIn(COMPANION_EXTENSION_ID, helper)
        self.assertIn("BrowserCompanion", helper)
        self.assertIn("browser-companion-binding.json", helper)
        self.assertIn("RandomNumberGenerator", helper)
        self.assertNotIn("Write-Host $secret", helper)
        self.assertTrue(all(ord(character) < 128 for character in helper))
        self.assertIn("browserAssistTitle", html)
        self.assertIn("审阅后辅助填写", app)
        self.assertIn("Assisted filling after review", app)
        self.assertIn("最终 Submit", app)
        self.assertIn("Final Submit", app)
        pair = (PROJECT / "browser-companion" / "pair.js").read_text(encoding="utf-8")
        worker = (PROJECT / "browser-companion" / "service-worker.js").read_text(encoding="utf-8")
        self.assertIn("const PROTOCOL = 2;", pair)
        self.assertIn("const PROTOCOL = 2;", worker)
        self.assertIn('const SOURCE_EXTENSION_VERSION = "0.7.1";', popup)
        self.assertIn("chrome.runtime.reload()", popup)
        self.assertIn("protocol_version:2", app)
        self.assertIn("pairing:{protocol_version:result.protocol_version", app)
        self.assertIn("start-job-with-ai", app)
        self.assertIn("guidedIntakeTitle", html)
        self.assertIn('id="cancelGuidedIntake"', html)
        self.assertIn("取消本次读取并更换网址", app)
        self.assertIn("Cancel this read and choose another URL", app)
        self.assertIn("Advanced diagnostics and offline QA", app)


if __name__ == "__main__":
    unittest.main()
