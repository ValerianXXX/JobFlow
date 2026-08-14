from __future__ import annotations

import json
import unittest

from _support import PROJECT


class DryRunPolicyTests(unittest.TestCase):
    def test_only_user_present_assisted_actions_are_available(self) -> None:
        policy = json.loads((PROJECT / "config" / "policy.json").read_text(encoding="utf-8"))
        self.assertTrue(policy["real_site_prefill_enabled"])
        self.assertTrue(policy["real_site_material_upload_enabled"])
        self.assertTrue(policy["user_present_browser_assist_enabled"])
        self.assertFalse(policy["unattended_submission_enabled"])
        self.assertFalse(policy["external_actions_enabled"])
        self.assertFalse(policy["final_submit_implementation_present"])
        self.assertEqual(policy["active_phases"], [0, 1, 2, 3, 4, 5])
        self.assertEqual(policy["planned_phases"], [6])
        self.assertFalse(policy["phase_5_6_operational"])
        self.assertTrue(policy["phase_5_assisted_subset_operational"])
        self.assertFalse(policy["phase_6_operational"])
        self.assertEqual(policy["phase_5_6_authorization"], "PER_APPLICATION_USER_PRESENT_PREFILL_UPLOAD_ONLY")
        self.assertEqual(policy["real_transport_adapters_registered"], 1)
        self.assertEqual(policy["requires_new_authorization"], [6])

    def test_submission_unknown_auto_retry_is_disabled(self) -> None:
        policy = json.loads((PROJECT / "config" / "policy.json").read_text(encoding="utf-8"))
        self.assertFalse(policy["submission_unknown_auto_retry"])


if __name__ == "__main__":
    unittest.main()
