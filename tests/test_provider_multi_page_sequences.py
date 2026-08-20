from __future__ import annotations

import json
import socket
import subprocess
import sys
import unittest
import urllib.request
from unittest.mock import patch

from _support import PROJECT
from jobops.ats_browser import analyze_local_ats_form_sequence
from jobops.errors import JobOpsError
from jobops.sourcing import verify_source_route


H1 = "sha256:" + "a" * 64
H2 = "sha256:" + "b" * 64
ATS = [
    "greenhouse.io",
    "ashbyhq.com",
    "smartrecruiters.com",
]

PROVIDERS = {
    "greenhouse": {
        "host": "boards.greenhouse.io",
        "url": "https://boards.greenhouse.io/example/jobs/987654",
        "identity": "987654",
        "kinds": ["MY_INFORMATION", "APPLICATION_QUESTIONS", "REVIEW"],
    },
    "ashby": {
        "host": "jobs.ashbyhq.com",
        "url": "https://jobs.ashbyhq.com/example/11111111-1111-4111-8111-111111111111/application",
        "identity": "11111111-1111-4111-8111-111111111111",
        "kinds": ["MY_INFORMATION", "EXPERIENCE_EDUCATION", "REVIEW"],
    },
    "smartrecruiters": {
        "host": "jobs.smartrecruiters.com",
        "url": "https://jobs.smartrecruiters.com/example/12345-synthetic-credit-analyst/apply",
        "identity": "12345-synthetic-credit-analyst",
        "kinds": ["MY_INFORMATION", "APPLICATION_QUESTIONS", "VOLUNTARY_DISCLOSURE"],
    },
}


def provider_route(provider: str) -> dict:
    definition = PROVIDERS[provider]
    official_url = f"https://example.com/careers/{provider}-analyst"
    binding = {
        "provider": provider,
        "company_registrable_domain": "example.com",
        "ats_host": definition["host"],
        "tenant": "example",
        "board": "default",
        "job_identity": definition["identity"],
        "official_page_hash": H1,
        "jd_snapshot_hash": H2,
    }
    return verify_source_route(
        company_domain="example.com",
        official_entry_url=official_url,
        current_url=definition["url"],
        navigation_history=[official_url, definition["url"]],
        approved_ats_hosts=ATS,
        guest_available=True,
        tenant_binding=binding,
        official_page_hash=H1,
        jd_snapshot_hash=H2,
    ).as_dict()


class ProviderMultiPageSequenceTests(unittest.TestCase):
    def pages(self, provider: str) -> list[bytes]:
        manifest = json.loads(
            (PROJECT / "tests" / "fixtures" / f"synthetic-{provider}-sequence.json").read_text(encoding="utf-8")
        )
        return [(PROJECT / relative).read_bytes() for relative in manifest["pages"]]

    def test_saved_provider_sequences_are_bound_deduplicated_and_inert(self) -> None:
        def forbidden(*args, **kwargs):
            raise AssertionError("network or browser transport attempted")

        for provider, definition in PROVIDERS.items():
            with self.subTest(provider=provider), patch.object(socket, "socket", forbidden), patch.object(
                socket, "getaddrinfo", forbidden,
            ), patch.object(urllib.request, "urlopen", forbidden):
                sequence = analyze_local_ats_form_sequence(
                    self.pages(provider),
                    route=provider_route(provider),
                    blocked_categories=[],
                )
            serialized = json.dumps(sequence, sort_keys=True)
            self.assertEqual(sequence["provider"], provider)
            self.assertEqual(sequence["step_count"], 3)
            self.assertEqual([step["step_kind"] for step in sequence["steps"]], definition["kinds"])
            self.assertGreater(sequence["unique_field_count"], 3)
            self.assertEqual(sequence["duplicate_field_count"], 1)
            self.assertIn("NAVIGATION_ACTION_STOP", sequence["blockers"])
            self.assertIn("FILE_UPLOAD_STOP", sequence["blockers"])
            self.assertIn("FINAL_SUBMIT_STOP", sequence["blockers"])
            self.assertFalse(sequence["navigation_performed"])
            self.assertFalse(sequence["entered_values_retained"])
            self.assertEqual(sequence["browser_actions"], 0)
            self.assertEqual(sequence["network_actions"], 0)
            self.assertEqual(sequence["real_external_actions"], 0)
            self.assertNotIn("DO_NOT_RETAIN", serialized)

    def test_duplicate_saved_page_fails_closed_for_each_provider(self) -> None:
        for provider in PROVIDERS:
            pages = self.pages(provider)
            with self.subTest(provider=provider), self.assertRaises(JobOpsError) as duplicate:
                analyze_local_ats_form_sequence(
                    [pages[0], pages[0]],
                    route=provider_route(provider),
                    blocked_categories=[],
                )
            self.assertEqual(duplicate.exception.code, "FORM_SEQUENCE_DUPLICATE_PAGE")

    def test_public_cli_sequence_reports_are_value_and_path_free(self) -> None:
        for provider in PROVIDERS:
            command = [
                sys.executable,
                str(PROJECT / ".agents" / "skills" / "job-application-operator" / "scripts" / "jobops.py"),
                "analyze-ats-sequence",
                "--manifest",
                f"tests/fixtures/synthetic-{provider}-sequence.json",
                "--route",
                f"tests/fixtures/synthetic-{provider}-sequence-route.json",
            ]
            completed = subprocess.run(command, capture_output=True, text=True, timeout=30, check=False)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            report = json.loads(completed.stdout)
            self.assertEqual(report["provider"], provider)
            self.assertEqual(report["step_count"], 3)
            self.assertNotIn(str(PROJECT), completed.stdout)
            self.assertNotIn(f"synthetic-{provider}-step", completed.stdout)
            self.assertNotIn(f"synthetic-{provider}-sequence.json", completed.stdout)
            self.assertEqual(report["browser_actions"], 0)
            self.assertEqual(report["network_actions"], 0)
            self.assertEqual(report["real_external_actions"], 0)


if __name__ == "__main__":
    unittest.main()
