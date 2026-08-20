from __future__ import annotations

import copy
import json
import subprocess
import sys
import unittest

from _support import PROJECT
from jobops.errors import JobOpsError
from jobops.product_capabilities import product_capability_report, validate_product_capability_integrity


class ProductCapabilityReportTests(unittest.TestCase):
    def test_report_separates_available_live_acceptance_missing_and_user_boundaries(self) -> None:
        report = product_capability_report()
        by_id = {item["capability_id"]: item for item in report["capabilities"]}
        self.assertEqual(report["capability_count"], len(by_id))
        self.assertFalse(report["universal_live_compatibility_claimed"])
        self.assertEqual(report["final_submit"], "USER_ONLY")
        self.assertFalse(report["automatic_submission_retry"])
        self.assertTrue(report["unattended_operation"])
        self.assertEqual(report["unattended_operation_scope"], "READ_ONLY_DISCOVERY_ONLY")
        self.assertFalse(report["unattended_application_operation"])
        self.assertEqual(by_id["canonical_profile_reuse"]["availability"], "AVAILABLE")
        self.assertEqual(by_id["workday_browser_assist"]["live_acceptance"], "REQUIRED_PER_SITE")
        self.assertEqual(by_id["browser_companion_runtime"]["evidence_status"], "SYNTHETIC_VERTICAL_PASS")
        self.assertIn(
            "PROVIDER_SPECIFIC_ACCEPTANCE_REQUIRED",
            by_id["browser_companion_runtime"]["known_limit_codes"],
        )
        self.assertEqual(by_id["redacted_live_acceptance_evidence"]["availability"], "AVAILABLE")
        self.assertEqual(
            by_id["redacted_live_acceptance_evidence"]["live_acceptance"],
            "PAGE_ROUTE_SPECIFIC_ONLY",
        )
        self.assertIn(
            "PAGE_ROUTE_SPECIFIC_NOT_UNIVERSAL",
            by_id["redacted_live_acceptance_evidence"]["known_limit_codes"],
        )
        self.assertEqual(by_id["ashby_browser_assist"]["evidence_status"], "SINGLE_SNAPSHOT_PASS")
        self.assertEqual(by_id["smartrecruiters_browser_assist"]["evidence_status"], "SINGLE_SNAPSHOT_PASS")
        self.assertIn(
            "REVIEW_TO_BROWSER_EXECUTION_NOT_PROVEN",
            by_id["ashby_browser_assist"]["known_limit_codes"],
        )
        self.assertEqual(by_id["authorized_continuous_scheduler"]["availability"], "NOT_AVAILABLE")
        self.assertEqual(by_id["authorized_read_only_discovery_scheduler"]["availability"], "AVAILABLE")
        self.assertEqual(
            by_id["authorized_read_only_discovery_scheduler"]["safety_boundary"],
            "READ_ONLY_CANDIDATE_INBOX_ONLY",
        )
        self.assertEqual(by_id["user_present_local_wake_planner"]["availability"], "AVAILABLE")
        self.assertEqual(
            by_id["user_present_local_wake_planner"]["safety_boundary"],
            "NO_BACKGROUND_SERVICE_OR_SYSTEM_TASK",
        )
        self.assertIn(
            "READ_ONLY_DISCOVERY_ONLY",
            by_id["authorized_continuous_scheduler"]["known_limit_codes"],
        )
        self.assertEqual(by_id["desktop_manual_upgrade_rollback"]["availability"], "AVAILABLE")
        self.assertEqual(
            by_id["desktop_manual_upgrade_rollback"]["safety_boundary"],
            "HEALTH_CHECKED_VERSION_SWITCH_WITH_PERSISTENT_DATA",
        )
        self.assertEqual(by_id["desktop_self_update_rollback"]["availability"], "AVAILABLE")
        self.assertEqual(by_id["desktop_self_update_rollback"]["evidence_status"], "AUTOMATED_REPRODUCIBLE")
        self.assertEqual(
            by_id["desktop_self_update_rollback"]["safety_boundary"],
            "SIGNED_UPDATE_WITH_POST_SWITCH_HEALTH_ROLLBACK",
        )
        self.assertIn("NO_BACKGROUND_UPDATE_CHECK", by_id["desktop_self_update_rollback"]["known_limit_codes"])
        self.assertEqual(by_id["redacted_support_diagnostics"]["availability"], "AVAILABLE")
        self.assertEqual(
            by_id["redacted_support_diagnostics"]["safety_boundary"],
            "LOCAL_VALUE_FREE_EXPORT_ONLY",
        )
        self.assertEqual(by_id["opt_in_crash_reporter"]["availability"], "AVAILABLE")
        self.assertEqual(
            by_id["opt_in_crash_reporter"]["safety_boundary"],
            "LOCAL_CODE_ONLY_CAPTURE_EXPLICIT_EXPORT",
        )
        self.assertIn(
            "NO_AUTOMATIC_TRANSMISSION",
            by_id["opt_in_crash_reporter"]["known_limit_codes"],
        )
        self.assertEqual(by_id["final_application_submit"]["availability"], "USER_ONLY")

    def test_report_hashes_and_project_local_evidence_fail_closed(self) -> None:
        report = product_capability_report()
        tampered = copy.deepcopy(report)
        target = next(item for item in tampered["capabilities"] if item["capability_id"] == "canonical_profile_reuse")
        target["availability"] = "NOT_AVAILABLE"
        with self.assertRaises(JobOpsError) as integrity:
            validate_product_capability_integrity(tampered)
        self.assertEqual(integrity.exception.code, "PRODUCT_CAPABILITY_INTEGRITY_FAILED")

        missing = copy.deepcopy(report)
        item = missing["capabilities"][0]
        item["evidence_refs"] = ["tests/does-not-exist.py"]
        from jobops.product_capabilities import _capability_hash, _report_hash
        item["capability_hash"] = _capability_hash(item)
        missing["report_hash"] = _report_hash(missing)
        with self.assertRaises(JobOpsError) as evidence:
            validate_product_capability_integrity(missing)
        self.assertEqual(evidence.exception.code, "PRODUCT_CAPABILITY_EVIDENCE_MISSING")

    def test_public_cli_emits_the_same_no_overclaim_contract(self) -> None:
        command = [
            sys.executable,
            str(PROJECT / ".agents" / "skills" / "job-application-operator" / "scripts" / "jobops.py"),
            "product-capabilities",
        ]
        completed = subprocess.run(command, capture_output=True, text=True, timeout=30, check=False)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        report = json.loads(completed.stdout)
        self.assertEqual(report["status"], "PRODUCT_CAPABILITY_REPORT")
        self.assertFalse(report["universal_live_compatibility_claimed"])
        self.assertFalse(report["automatic_submission_retry"])
        self.assertEqual(report["next_safe_action"], "close-not-available-and-live-acceptance-gaps")


if __name__ == "__main__":
    unittest.main()
