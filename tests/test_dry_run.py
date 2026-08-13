from __future__ import annotations

import json
import unittest

from _support import PROJECT


class DryRunPolicyTests(unittest.TestCase):
    def test_real_external_actions_are_disabled(self) -> None:
        policy = json.loads((PROJECT / "config" / "policy.json").read_text(encoding="utf-8"))
        self.assertFalse(policy["real_site_prefill_enabled"])
        self.assertFalse(policy["unattended_submission_enabled"])
        self.assertFalse(policy["external_actions_enabled"])
        self.assertEqual(policy["active_phases"], [0, 1, 2, 3, 4])
        self.assertEqual(policy["planned_phases"], [5, 6])
        self.assertFalse(policy["phase_5_6_operational"])
        self.assertEqual(policy["phase_5_6_authorization"], "ABSENT")
        self.assertEqual(policy["real_transport_adapters_registered"], 0)
        self.assertEqual(policy["requires_new_authorization"], [5, 6])

    def test_submission_unknown_auto_retry_is_disabled(self) -> None:
        policy = json.loads((PROJECT / "config" / "policy.json").read_text(encoding="utf-8"))
        self.assertFalse(policy["submission_unknown_auto_retry"])


if __name__ == "__main__":
    unittest.main()
