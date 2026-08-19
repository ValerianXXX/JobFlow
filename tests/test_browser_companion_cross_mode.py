from __future__ import annotations

import threading
import time
import unittest
from unittest import mock

from jobops.errors import JobOpsError
from jobops.onboarding_center import OnboardingCenterService


class BrowserCompanionCrossModeTests(unittest.TestCase):
    @staticmethod
    def service_stub() -> OnboardingCenterService:
        service = object.__new__(OnboardingCenterService)
        service._lock = threading.RLock()
        service._guided_intakes = {}
        service.browser_assist = mock.Mock()
        service.intake_control = mock.Mock(spec=["assert_new_intake_allowed"])
        return service

    def test_guided_intake_cannot_start_while_application_assist_is_active(self) -> None:
        service = self.service_stub()
        service.browser_assist.public_status.return_value = {
            "active_assist_id": "BAS-SYNTHETICACTIVE",
            "active_status": "AWAITING_USER_SUBMIT",
        }

        with self.assertRaises(JobOpsError) as blocked:
            service.start_guided_intake({"user_confirmed": True})

        self.assertEqual(blocked.exception.code, "BROWSER_COMPANION_SESSION_ACTIVE")
        self.assertEqual(blocked.exception.details["active_mode"], "APPLICATION_ASSIST")
        self.assertFalse(blocked.exception.details["automatic_retry"])

    def test_application_assist_cannot_start_while_guided_intake_is_active(self) -> None:
        service = self.service_stub()
        service._guided_intakes["synthetic-token"] = {
            "status": "AWAITING_APPLICATION_FORM_CAPTURE",
            "expires_epoch": time.time() + 300,
        }

        with self.assertRaises(JobOpsError) as blocked:
            service.start_browser_assist({})

        self.assertEqual(blocked.exception.code, "BROWSER_COMPANION_SESSION_ACTIVE")
        self.assertEqual(blocked.exception.details["active_mode"], "JOB_CAPTURE")
        self.assertFalse(blocked.exception.details["automatic_retry"])

    def test_terminal_or_expired_guided_lease_does_not_block_application_validation(self) -> None:
        for status, expires_epoch in (
            ("REVIEW_PACKET_READY", time.time() + 300),
            ("AWAITING_JOB_PAGE_CAPTURE", time.time() - 1),
        ):
            with self.subTest(status=status):
                service = self.service_stub()
                service._guided_intakes["synthetic-token"] = {
                    "status": status,
                    "expires_epoch": expires_epoch,
                }
                with self.assertRaises(JobOpsError) as invalid_application:
                    service.start_browser_assist({})
                self.assertEqual(invalid_application.exception.code, "APPLICATION_ID_INVALID")


if __name__ == "__main__":
    unittest.main()
