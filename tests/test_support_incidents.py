from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from _support import PROJECT
from jobops.errors import JobOpsError
from jobops.support_incidents import MAX_SUPPORT_INCIDENTS, SupportIncidentStore


class SupportIncidentStoreTests(unittest.TestCase):
    def make_store(self, root: Path) -> SupportIncidentStore:
        return SupportIncidentStore(
            root / "state" / "support-incidents.json",
            PROJECT / "schemas",
            ui_protocol=35,
        )

    @staticmethod
    def incident(code: str = "BROWSER_FORM_CHANGED") -> dict[str, object]:
        return {
            "code": code,
            "source": "UI_API_ERROR",
            "ui_protocol": 35,
            "observed_companion_version": "0.9.1",
        }

    def test_capture_is_off_by_default_and_never_transmits(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            store = self.make_store(Path(folder))
            initial = store.public_state()
            self.assertFalse(initial["enabled"])
            self.assertEqual(initial["record_count"], 0)
            self.assertFalse(initial["automatic_transmission"])
            ignored = store.record(self.incident())
            self.assertFalse(ignored["recorded"])
            self.assertFalse((Path(folder) / "state" / "support-incidents.json").exists())

    def test_explicit_opt_in_deduplicates_and_bounds_value_free_records(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            store = self.make_store(root)
            enabled = store.configure({"enabled": True, "user_confirmed": True})
            self.assertTrue(enabled["enabled"])
            store.record(self.incident())
            store.record(self.incident())
            first = store.diagnostic_summary()
            self.assertEqual(first["record_count"], 1)
            self.assertEqual(first["recent"][0]["occurrences"], 2)

            for index in range(MAX_SUPPORT_INCIDENTS + 8):
                store.record(self.incident(f"UI_TEST_CODE_{index}"))
            summary = store.diagnostic_summary()
            self.assertEqual(summary["record_count"], MAX_SUPPORT_INCIDENTS)
            self.assertEqual(len(summary["recent"]), 16)
            self.assertFalse(summary["automatic_transmission"])
            self.assertEqual(summary["private_values_read"], 0)
            self.assertEqual(summary["private_values_emitted"], 0)

            serialized = (root / "state" / "support-incidents.json").read_text(encoding="utf-8")
            for forbidden in ("message", "stack", "url", "path", "resume", "answer", "credential", "token"):
                self.assertNotIn(forbidden, serialized.lower())

    def test_messages_paths_and_unbounded_values_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            store = self.make_store(Path(folder))
            store.configure({"enabled": True, "user_confirmed": True})
            unsafe_payloads = (
                {**self.incident(), "message": "private marker"},
                {**self.incident(), "stack": "private marker"},
                {**self.incident(), "url": "https://private.invalid"},
                {**self.incident(), "path": "private-folder"},
                {**self.incident(), "code": "lowercase-private-value"},
                {**self.incident(), "observed_companion_version": "private-version"},
                {**self.incident(), "ui_protocol": 34},
            )
            for payload in unsafe_payloads:
                with self.subTest(payload=sorted(payload)):
                    with self.assertRaises(JobOpsError) as invalid:
                        store.record(payload)
                    self.assertEqual(invalid.exception.code, "SUPPORT_INCIDENT_INPUT_INVALID")
            self.assertEqual(store.public_state()["record_count"], 0)

    def test_corrupt_state_fails_closed_and_explicit_toggle_repairs_it(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / "state" / "support-incidents.json"
            path.parent.mkdir(parents=True)
            path.write_text('{"enabled":true,"records":["private marker"]}', encoding="utf-8")
            store = self.make_store(root)
            public = store.public_state()
            self.assertEqual(public["status"], "SUPPORT_INCIDENT_STATE_REPAIR_REQUIRED")
            self.assertFalse(public["enabled"])
            ignored = store.record(self.incident())
            self.assertFalse(ignored["recorded"])
            repaired = store.configure({"enabled": False, "user_confirmed": True})
            self.assertEqual(repaired["status"], "SUPPORT_INCIDENT_CAPTURE_DISABLED")
            self.assertEqual(repaired["record_count"], 0)
            parsed = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(parsed["records"], [])

    def test_setting_and_clear_require_exact_explicit_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            store = self.make_store(Path(folder))
            invalid_settings = (
                {"enabled": True},
                {"enabled": True, "user_confirmed": False},
                {"enabled": True, "user_confirmed": True, "extra": "value"},
            )
            for payload in invalid_settings:
                with self.assertRaises(JobOpsError):
                    store.configure(payload)
            with self.assertRaises(JobOpsError) as clear:
                store.clear({"user_confirmed": False})
            self.assertEqual(clear.exception.code, "EXPLICIT_CONFIRMATION_REQUIRED")


if __name__ == "__main__":
    unittest.main()
