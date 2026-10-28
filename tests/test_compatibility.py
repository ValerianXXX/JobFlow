from __future__ import annotations

import json
import shutil
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from _support import PROJECT, project_temp
from jobops.compatibility import check_source_compatibility, companion_compatibility
from jobops.errors import JobOpsError
from jobops.onboarding_server import OnboardingRequestHandler


class CompatibilityTests(unittest.TestCase):
    def test_pair_endpoint_returns_recovery_target_before_reading_private_binding(self):
        for version, target in (("0.9.1", "EXTENSION"), ("0.9.3", "DESKTOP"), ("unsafe\nvalue", "BOTH")):
            request = SimpleNamespace(_optional_json_body=lambda: {
                "protocol_version": 2, "extension_version": version, "companion_binding": {},
            })
            with patch("jobops.onboarding_server.validate_pair_request") as validate:
                with self.assertRaises(JobOpsError) as caught:
                    OnboardingRequestHandler._companion_pair_body(request)
                self.assertEqual(caught.exception.code, "BROWSER_COMPANION_UPDATE_REQUIRED")
                self.assertEqual(caught.exception.details["update_target"], target)
                validate.assert_not_called()

    def test_pairing_remains_exact_and_recovery_targets_the_older_component(self):
        for protocol, version, target, status in (
            (2, "0.9.2", "NONE", "AVAILABLE"),
            (2, "0.9.1", "EXTENSION", "UPDATE_REQUIRED"),
            (2, "0.9.3", "DESKTOP", "UPDATE_REQUIRED"),
            (3, "0.9.2", "BOTH", "UPDATE_REQUIRED"),
            (2.0, "0.9.2", "BOTH", "UPDATE_REQUIRED"),
            (2, "0.9.2.0", "BOTH", "UPDATE_REQUIRED"),
            (2, "0.09.2", "BOTH", "UPDATE_REQUIRED"),
            (2, "0.9.2\nprivate", "BOTH", "UPDATE_REQUIRED"),
            (2, None, "BOTH", "UPDATE_REQUIRED"),
        ):
            with self.subTest(protocol=protocol, version=version):
                result = companion_compatibility(protocol, version)
                self.assertEqual((result["update_target"], result["status"]), (target, status))
        self.assertIsNone(companion_compatibility(2, "0.9.2\nprivate")["observed_version"])

    def test_current_tree_reports_known_stable_gaps_without_claiming_release_readiness(self):
        result = check_source_compatibility(PROJECT)
        self.assertEqual(result["status"], "PASS", result)
        self.assertFalse(result["stable_accepts_source_companion"])
        self.assertFalse(result["stable_usable_by_bootstrap"])
        self.assertFalse(result["public_release_ready"])

    def test_version_drift_in_each_packaged_surface_fails_the_gate(self):
        files = (
            "config/compatibility-matrix.json", "pyproject.toml", "package.json", "package-lock.json",
            "browser-companion/manifest.json", "browser-companion/popup.js", "src/jobops/ui/app.js", "CHANGELOG.md",
        )
        with project_temp() as root:
            for name in files:
                destination = root / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(PROJECT / name, destination)
            for name, old, new in (
                ("package-lock.json", '"0.7.0"', '"0.7.1"'),
                ("browser-companion/manifest.json", '"0.9.2"', '"0.9.3"'),
                ("src/jobops/ui/app.js", 'COMPANION_VERSION = "0.9.2"', 'COMPANION_VERSION = "0.9.3"'),
            ):
                with self.subTest(file=name):
                    path = root / name
                    original = path.read_text(encoding="utf-8")
                    path.write_text(original.replace(old, new), encoding="utf-8")
                    self.assertEqual(check_source_compatibility(root)["status"], "FAIL")
                    path.write_text(original, encoding="utf-8")
            path = root / files[0]
            matrix = json.loads(path.read_text(encoding="utf-8"))
            matrix["desktops"].append(matrix["desktops"][0])
            path.write_text(json.dumps(matrix), encoding="utf-8")
            self.assertEqual(check_source_compatibility(root)["status"], "FAIL")
