from __future__ import annotations

import json
import shutil
import socket
import tempfile
import unittest
import urllib.request
from pathlib import Path
from unittest.mock import patch

from _support import PROJECT, project_temp
from jobops.approvals import ApprovalContext, UploadBinding
from jobops.ats_browser import analyze_local_ats_form, build_browser_action_plan
from jobops.db import JobOpsDB
from jobops.ephemeral_payload import EphemeralATSPayloadBroker
from jobops.errors import JobOpsError
from jobops.private_onboarding import PrivateOnboarding
from jobops.secure_store import WindowsDPAPIStore
from jobops.sourcing import verify_source_route
from jobops.util import canonical_json


H = "sha256:" + "a" * 64


class EphemeralPayloadTests(unittest.TestCase):
    def build(self, root: Path) -> PrivateOnboarding:
        database = JobOpsDB(root / "jobops.db")
        database.initialize()
        private_root = Path(tempfile.mkdtemp(prefix="jobflow-ephemeral-private-"))
        self.addCleanup(shutil.rmtree, private_root, True)
        return PrivateOnboarding(
            database,
            WindowsDPAPIStore(
                PROJECT / ".agents" / "skills" / "job-application-operator" / "scripts" / "secure-store.ps1",
                local_app_data=private_root,
            ),
        )

    @staticmethod
    def route() -> dict:
        official_hash = "sha256:" + "b" * 64
        jd_hash = "sha256:" + "c" * 64
        return verify_source_route(
            company_domain="example.com",
            official_entry_url="https://example.com/careers/analyst",
            current_url="https://jobs.lever.co/example/abc-123",
            navigation_history=[
                "https://example.com/careers/analyst",
                "https://jobs.lever.co/example/abc-123",
            ],
            approved_ats_hosts=["lever.co"],
            guest_available=True,
            tenant_binding={
                "provider": "lever",
                "company_registrable_domain": "example.com",
                "ats_host": "jobs.lever.co",
                "tenant": "example",
                "board": "default",
                "job_identity": "abc-123",
                "official_page_hash": official_hash,
                "jd_snapshot_hash": jd_hash,
            },
            official_page_hash=official_hash,
            jd_snapshot_hash=jd_hash,
        ).as_dict()

    def prepared(self, root: Path) -> dict:
        onboarding = self.build(root)
        profile = onboarding.import_bytes(
            "candidate_profile",
            canonical_json({"candidate_display_name": "Synthetic Candidate"}),
            synthetic=True,
        )
        resume = onboarding.import_bytes("generated_resume_pdf", b"synthetic resume bytes", synthetic=True)
        cover = onboarding.import_bytes("generated_cover_letter_pdf", b"synthetic cover letter bytes", synthetic=True)
        route = self.route()
        form = analyze_local_ats_form(
            (PROJECT / "tests" / "fixtures" / "synthetic-lever-form.html").read_bytes(),
            route=route,
            blocked_categories=[],
        )
        full_name = next(item for item in form["fields"] if item["answer_key"] == "full_name")
        portfolio = next(item for item in form["fields"] if item["answer_key"] == "portfolio")
        public_portfolio = "https://portfolio.example.test/synthetic"
        browser_plan = build_browser_action_plan(form, {
            full_name["control_ref"]: {"kind": "secure_ref", "value": profile["secure_ref"]},
            portfolio["control_ref"]: {"kind": "public_value", "value": public_portfolio},
        })
        context = ApprovalContext(
            application_id="APP-ABCDEF123456",
            job_id="JOB-ABCDEF123456",
            jd_snapshot_hash=H,
            jd_freshness_hash=H,
            source_route_hash=route["route_hash"],
            canonical_url=route["current_url"],
            ats_tenant="example",
            ats_board="default",
            ats_job_identity="abc-123",
            profile_version="synthetic-v1",
            claim_set_hash=H,
            form_snapshot_hash=form["form_snapshot_hash"],
            answers_hash=H,
            review_packet_hash=H,
            uploads=(
                UploadBinding("resume.pdf", "resume", str(resume["content_sha256"])),
                UploadBinding("cover-letter.pdf", "cover_letter", str(cover["content_sha256"])),
            ),
            external_actions=("submit_application", "upload_material"),
            site_policy_version="synthetic-policy-v1",
        )
        return {
            "onboarding": onboarding,
            "form": form,
            "browser_plan": browser_plan,
            "context": context,
            "public_values": {portfolio["control_ref"]: public_portfolio},
            "material_references": {
                str(resume["content_sha256"]): str(resume["secure_ref"]),
                str(cover["content_sha256"]): str(cover["secure_ref"]),
            },
        }

    def test_synthetic_private_fields_and_files_are_ephemeral_and_redacted(self) -> None:
        with project_temp() as root:
            values = self.prepared(root)
            broker = EphemeralATSPayloadBroker(values["onboarding"], isolated_test_mode=True)

            def forbidden(*args, **kwargs):
                raise AssertionError("network or browser transport attempted")

            with patch.object(socket, "socket", forbidden), patch.object(socket, "getaddrinfo", forbidden), patch.object(
                socket, "create_connection", forbidden
            ), patch.object(urllib.request, "urlopen", forbidden):
                evidence = broker.run_isolated_probe(
                    context=values["context"],
                    form_snapshot=values["form"],
                    browser_plan=values["browser_plan"],
                    public_values=values["public_values"],
                    material_references=values["material_references"],
                )

            self.assertEqual(evidence["status"], "ISOLATED_EPHEMERAL_PAYLOAD_VALIDATED")
            self.assertEqual(evidence["field_count"], 2)
            self.assertEqual(evidence["file_count"], 2)
            self.assertTrue(evidence["temporary_files_removed"])
            self.assertFalse(evidence["production_activation"])
            self.assertEqual(evidence["private_values_emitted"], 0)
            self.assertEqual(evidence["network_actions"], 0)
            serialized = json.dumps(evidence)
            for forbidden_value in (
                "Synthetic Candidate", "portfolio.example.test", "synthetic resume bytes",
                str(next(iter(values["material_references"].values()))),
            ):
                self.assertNotIn(forbidden_value, serialized)
            staging = values["onboarding"].store.private_root / "staging"
            self.assertEqual(list(staging.rglob("*")), [])

    def test_production_mode_cannot_decrypt_or_stage_payload(self) -> None:
        with project_temp() as root:
            values = self.prepared(root)
            broker = EphemeralATSPayloadBroker(values["onboarding"])
            with patch.object(values["onboarding"], "read_bytes", side_effect=AssertionError("decryption attempted")):
                with self.assertRaises(JobOpsError) as blocked:
                    broker.run_isolated_probe(
                        context=values["context"],
                        form_snapshot=values["form"],
                        browser_plan=values["browser_plan"],
                        public_values=values["public_values"],
                        material_references=values["material_references"],
                    )
            self.assertEqual(blocked.exception.code, "PHASE_NOT_AUTHORIZED")

    def test_changed_public_value_and_incomplete_upload_bindings_fail_closed(self) -> None:
        with project_temp() as root:
            values = self.prepared(root)
            broker = EphemeralATSPayloadBroker(values["onboarding"], isolated_test_mode=True)
            control_ref = next(iter(values["public_values"]))
            with self.assertRaises(JobOpsError) as changed:
                broker.run_isolated_probe(
                    context=values["context"],
                    form_snapshot=values["form"],
                    browser_plan=values["browser_plan"],
                    public_values={control_ref: "https://changed.example.test"},
                    material_references=values["material_references"],
                )
            self.assertEqual(changed.exception.code, "EPHEMERAL_PUBLIC_VALUE_CHANGED")

            one_material = dict(values["material_references"])
            one_material.pop(next(iter(one_material)))
            with self.assertRaises(JobOpsError) as incomplete:
                broker.run_isolated_probe(
                    context=values["context"],
                    form_snapshot=values["form"],
                    browser_plan=values["browser_plan"],
                    public_values=values["public_values"],
                    material_references=one_material,
                )
            self.assertEqual(incomplete.exception.code, "EPHEMERAL_UPLOAD_BINDINGS_INCOMPLETE")
            staging = values["onboarding"].store.private_root / "staging"
            self.assertFalse(staging.exists() and any(staging.rglob("*")))


if __name__ == "__main__":
    unittest.main()
