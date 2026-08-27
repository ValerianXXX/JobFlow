from __future__ import annotations

import copy
import json
import unittest

from _support import PROJECT
from jobops.application_materials import build_material_plan, detect_material_requests
from jobops.errors import JobOpsError
from jobops.runtime_schema import validate_named
from jobops.util import sha256_bytes


H = "sha256:" + "a" * 64


def fields() -> list[dict[str, object]]:
    return [
        {"id": "resume", "answer_key": "resume", "classification": "file_upload_stop", "required": True},
        {"id": "cover", "answer_key": "cover_letter", "classification": "file_upload_stop", "required": True},
        {"id": "github", "answer_key": "github", "classification": "ordinary_fixed", "required": True},
        {"id": "portfolio-link", "answer_key": "portfolio", "classification": "ordinary_fixed", "required": False},
        {"id": "portfolio-file", "answer_key": "portfolio_file", "classification": "file_upload_stop", "required": False},
    ]


def build(**changes: object) -> dict[str, object]:
    values: dict[str, object] = {
        "master_resume_ref": "secure-ref:SYNTHETIC_MASTER",
        "master_resume_sha256": H,
        "tailored_docx_ref": "secure-ref:SYNTHETIC_DOCX",
        "tailored_docx_sha256": H,
        "tailored_pdf_ref": "secure-ref:SYNTHETIC_PDF",
        "tailored_pdf_sha256": H,
        "fields": fields(),
        "public_values": {
            "github": "https://github.com/synthetic-candidate",
            "portfolio": "https://portfolio.example.test/synthetic-candidate",
        },
        "cover_letter": {
            "docx_secure_ref": "secure-ref:SYNTHETIC_COVER_DOCX", "docx_sha256": H,
            "pdf_secure_ref": "secure-ref:SYNTHETIC_COVER_PDF", "pdf_sha256": H,
            "narrative_secure_ref": "secure-ref:SYNTHETIC_NARRATIVE", "narrative_sha256": H,
            "narrative_character_count": 600,
        },
        "portfolio_file": {
            "secure_ref": "secure-ref:SYNTHETIC_PORTFOLIO", "sha256": H,
            "safe_filename": "synthetic-portfolio.pdf",
        },
    }
    values.update(changes)
    return build_material_plan(**values)  # type: ignore[arg-type]


class ApplicationMaterialPlanTests(unittest.TestCase):
    def test_plan_binds_one_master_conditional_letter_links_and_portfolio_without_plain_urls(self) -> None:
        plan = build()
        validate_named("material-plan", plan, PROJECT / "schemas")
        self.assertEqual(plan["resume"]["derivation"], "TAILORED_COPY_OF_SINGLE_APPROVED_MASTER")
        self.assertTrue(plan["resume"]["generated_before_application"])
        self.assertEqual(plan["cover_letter"]["generation_status"], "GENERATED_ON_DEMAND")
        self.assertEqual(plan["portfolio_file"]["binding_status"], "BOUND_SECURE_FILE")
        self.assertTrue(all(item["binding_status"] == "BOUND_CONFIRMED_PUBLIC_VALUE" for item in plan["public_links"]))
        serialized = json.dumps(plan)
        self.assertNotIn("github.com", serialized)
        self.assertNotIn("portfolio.example.test", serialized)
        self.assertTrue(plan["all_uploads_and_submission_blocked"])
        self.assertEqual(plan["real_external_actions"], 0)

    def test_cover_letter_is_not_generated_when_form_does_not_request_it(self) -> None:
        requested_fields = [item for item in fields() if item["answer_key"] not in {"cover_letter", "portfolio_file"}]
        plan = build(fields=requested_fields, cover_letter=None, portfolio_file=None)
        validate_named("material-plan", plan, PROJECT / "schemas")
        self.assertEqual(plan["cover_letter"]["request_status"], "NOT_REQUESTED")
        self.assertEqual(plan["cover_letter"]["generation_status"], "NOT_GENERATED")
        self.assertEqual(plan["portfolio_file"]["binding_status"], "NOT_REQUESTED")

    def test_missing_required_link_or_file_stops_at_material_review(self) -> None:
        required_fields = copy.deepcopy(fields())
        required_fields[-1]["required"] = True
        plan = build(fields=required_fields, public_values={}, portfolio_file=None)
        validate_named("material-plan", plan, PROJECT / "schemas")
        self.assertEqual(plan["status"], "NEEDS_USER_MATERIAL")
        self.assertEqual(plan["portfolio_file"]["binding_status"], "MISSING_USER_MATERIAL")

    def test_material_detection_uses_semantics_not_field_order(self) -> None:
        detected = detect_material_requests(reversed(fields()))
        self.assertEqual({item["purpose"] for item in detected["uploads"]}, {"resume", "cover_letter", "portfolio"})
        self.assertEqual({item["kind"] for item in detected["public_links"]}, {"github", "portfolio"})

    def test_visible_semantics_veto_conflicting_machine_material_purposes(self) -> None:
        detected = detect_material_requests([
            {
                "id": "legal", "name": "cover_letter", "answer_key": "cover_letter",
                "classification": "unknown_stop", "control_type": "textarea",
                "display_label": "Legal Consent", "required": True,
            },
            {
                "id": "privacy", "name": "cover_letter", "answer_key": "cover_letter",
                "classification": "unknown_stop", "control_type": "textarea",
                "aria_label": "Privacy Agreement", "required": True,
            },
            {
                "id": "background", "name": "cover_letter", "answer_key": "cover_letter",
                "classification": "file_upload_stop", "control_type": "file",
                "placeholder": "Background check authorization", "required": True,
            },
            {
                "id": "resume-as-cover", "name": "cover_letter", "answer_key": "cover_letter",
                "classification": "file_upload_stop", "control_type": "file",
                "display_label": "Resume", "required": True,
            },
        ])

        self.assertEqual(detected["narratives"], [])
        self.assertEqual(detected["public_links"], [])
        self.assertEqual(detected["uploads"], [
            {"field_id": "background", "purpose": "attachment", "required": True},
            {"field_id": "resume-as-cover", "purpose": "resume", "required": True},
        ])

    def test_protected_ancillary_semantics_and_hash_bound_unknown_veto_cover_letter(self) -> None:
        detected = detect_material_requests([
            {
                "id": "signed-upload", "name": "cover_letter", "answer_key": "cover_letter",
                "classification": "file_upload_stop", "control_type": "file",
                "display_label": "Cover Letter", "help_text": "Electronic signature and consent",
                "required": True,
            },
            {
                "id": "reviewed-unknown", "name": "cover_letter", "answer_key": "UNKNOWN",
                "classification": "file_upload_stop", "control_type": "file",
                "display_label": "Cover Letter", "required": True,
                "logical_field_hash": H, "prompt_hash": H,
            },
            {
                "id": "privacy-text", "name": "cover_letter", "answer_key": "cover_letter",
                "classification": "application_narrative_review", "control_type": "textarea",
                "display_label": "Cover Letter", "section_heading": "Privacy Agreement",
                "required": True, "max_length": None, "max_length_status": "ABSENT",
            },
        ])

        self.assertEqual(detected["narratives"], [])
        self.assertEqual(detected["uploads"], [
            {"field_id": "signed-upload", "purpose": "attachment", "required": True},
            {"field_id": "reviewed-unknown", "purpose": "attachment", "required": True},
        ])

    def test_protected_words_in_direct_cover_letter_prompt_veto_narrative(self) -> None:
        detected = detect_material_requests([
            {
                "id": "cover-consent", "name": "cover_letter", "answer_key": "cover_letter",
                "classification": "application_narrative_review", "control_type": "textarea",
                "display_label": "Cover Letter and Legal Consent", "required": True,
                "max_length": 1200, "max_length_status": "VALID",
            },
            {
                "id": "cover-certify", "name": "cover_letter", "answer_key": "cover_letter",
                "classification": "application_narrative_review", "control_type": "textarea",
                "label": "Cover Letter - I certify this application", "required": True,
                "max_length": 1200, "max_length_status": "VALID",
            },
        ])

        self.assertEqual(detected["narratives"], [])
        self.assertEqual(detected["uploads"], [])

    def test_cover_letter_textarea_requests_the_same_on_demand_material(self) -> None:
        textarea = {
            "id": "cover-story", "answer_key": "cover_letter", "classification": "application_narrative_review",
            "control_type": "textarea", "required": True,
            "max_length": 900, "max_length_status": "VALID",
        }
        detected = detect_material_requests([textarea])
        self.assertEqual(detected["narratives"], [{
            "field_id": "cover-story", "purpose": "cover_letter", "required": True,
            "max_length": 900, "max_length_status": "VALID", "effective_max_characters": 900,
        }])
        plan = build(fields=[textarea])
        validate_named("material-plan", plan, PROJECT / "schemas")
        self.assertEqual(plan["cover_letter"]["request_status"], "REQUESTED_REQUIRED")
        self.assertEqual(plan["cover_letter"]["narrative_sha256"], H)
        self.assertEqual(plan["cover_letter"]["narrative_target_status"], "BOUND_EXACT_CONTROL")
        self.assertEqual(plan["cover_letter"]["narrative_control_ref"], "cover-story")
        self.assertEqual(plan["cover_letter"]["narrative_max_characters"], 900)

    def test_narrative_target_zero_multiple_and_invalid_maxlength_fail_closed(self) -> None:
        no_target = build()
        self.assertEqual(no_target["cover_letter"]["narrative_target_status"], "NOT_REQUESTED")
        self.assertEqual(no_target["cover_letter"]["narrative_target_count"], 0)
        self.assertIsNone(no_target["cover_letter"]["narrative_control_ref"])

        target = {
            "answer_key": "cover_letter", "classification": "application_narrative_review",
            "control_type": "textarea", "required": True,
            "max_length": None, "max_length_status": "ABSENT",
        }
        ambiguous = build(fields=[
            {**target, "id": "cover-one"}, {**target, "id": "cover-two"},
        ])
        validate_named("material-plan", ambiguous, PROJECT / "schemas")
        self.assertEqual(ambiguous["cover_letter"]["narrative_target_status"], "AMBIGUOUS")
        self.assertEqual(ambiguous["cover_letter"]["narrative_target_count"], 2)
        self.assertIsNone(ambiguous["cover_letter"]["narrative_control_ref"])

        invalid = build(fields=[{
            **target, "id": "cover-zero", "max_length_status": "INVALID",
        }])
        validate_named("material-plan", invalid, PROJECT / "schemas")
        self.assertEqual(invalid["cover_letter"]["narrative_target_status"], "INVALID_MAX_LENGTH")
        self.assertEqual(invalid["cover_letter"]["narrative_control_ref"], "cover-zero")
        self.assertIsNone(invalid["cover_letter"]["narrative_max_characters"])

    def test_schema_rejects_a_cover_letter_generated_without_a_form_request(self) -> None:
        plan = build()
        plan["cover_letter"]["request_status"] = "NOT_REQUESTED"
        with self.assertRaises(JobOpsError):
            validate_named("material-plan", plan, PROJECT / "schemas")

    def test_public_link_hash_is_bound_to_the_exact_confirmed_value(self) -> None:
        plan = build()
        github = next(item for item in plan["public_links"] if item["kind"] == "github")
        self.assertEqual(
            github["value_sha256"],
            sha256_bytes(b"https://github.com/synthetic-candidate"),
        )


if __name__ == "__main__":
    unittest.main()
