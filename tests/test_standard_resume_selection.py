from __future__ import annotations

import io
import json
import unittest

import _support
from _support import project_temp
from docx import Document
from jobops.errors import JobOpsError
from jobops.util import canonical_json
from test_standard_resume import approved_pdf_service, generate


def selected(service, ids, **extra):
    return service.create_standard_resume({
        "expected_review_hash": service.bootstrap()["standard_resume"]["review_hash"],
        "locale": "en", "included_claim_ids": ids, **extra,
    })


def preview_ids(result):
    return [item["claim_id"] for section in result["preview"]["sections"] for item in section["items"]]


class StandardResumeSelectionTests(unittest.TestCase):
    def test_subset_retains_exact_wording_and_never_changes_approved_registry_or_source(self):
        with project_temp() as root:
            service, onboarding, _, original = approved_pdf_service(root)
            before = generate(service)
            ids = preview_ids(before)
            self.assertGreater(len(ids), 1)
            registry = service._latest_ref("external_claim_set")
            registry_bytes = onboarding.read_bytes(registry)
            source_bytes = onboarding.read_bytes(original["secure_ref"])
            result = selected(service, [ids[0]])
            self.assertEqual(preview_ids(result), [ids[0]])
            self.assertEqual(result["preview"]["excluded_claim_count"], len(ids) - 1)
            paragraphs = [p.text for p in Document(io.BytesIO(service.standard_resume_file(result))).paragraphs]
            document = Document(io.BytesIO(service.standard_resume_file(result)))
            for paragraph in document.paragraphs:
                if paragraph._p.pPr is not None and paragraph._p.pPr.numPr is not None:
                    self.assertTrue(paragraph.paragraph_format.keep_together)
            for section in before["preview"]["sections"]:
                for item in section["items"]:
                    self.assertEqual(item["text"] in paragraphs, item["claim_id"] == ids[0])
            self.assertEqual(onboarding.read_bytes(registry), registry_bytes)
            self.assertEqual(onboarding.read_bytes(original["secure_ref"]), source_bytes)
            self.assertFalse(result["materials_validated"])

    def test_distinct_selections_never_reuse_the_other_draft_and_repeat_is_idempotent(self):
        with project_temp() as root:
            service, _, _, _ = approved_pdf_service(root)
            ids = preview_ids(generate(service))
            first = selected(service, [ids[0]])
            second = selected(service, [ids[-1]])
            self.assertNotEqual(first["draft_ref"], second["draft_ref"])
            self.assertNotEqual(first["selection_hash"], second["selection_hash"])
            self.assertEqual(selected(service, [ids[-1]])["draft_ref"], second["draft_ref"])
            self.assertEqual(preview_ids(first), [ids[0]])

    def test_reversed_selection_is_canonical_and_default_still_includes_all(self):
        with project_temp() as root:
            service, _, _, _ = approved_pdf_service(root)
            all_facts = generate(service)
            ids = preview_ids(all_facts)
            reordered = selected(service, list(reversed(ids)))
            self.assertEqual(reordered["draft_ref"], all_facts["draft_ref"])
            self.assertEqual(reordered["preview"]["included_claim_ids"], sorted(ids))

    def test_invalid_selection_rejects_before_creating_or_reusing_a_draft(self):
        with project_temp() as root:
            service, _, _, _ = approved_pdf_service(root)
            existing = generate(service)
            first = preview_ids(existing)[0]
            for value in ([], [first, first], ["UNKNOWN-CLAIM"], [first, 4], first, None, True, {}):
                with self.subTest(kind=type(value).__name__, value=value):
                    with self.assertRaises(JobOpsError) as caught:
                        selected(service, value)
                    self.assertEqual(caught.exception.code, "STANDARD_RESUME_SELECTION_INVALID")
                    self.assertEqual(service._latest_ref("standard_resume_draft"), existing["draft_ref"])

    def test_options_are_available_only_with_current_resume_approval(self):
        with project_temp() as root:
            service, _, _, _ = approved_pdf_service(root)
            generated = generate(service)
            options = service.bootstrap()["standard_resume"]["claim_options"]
            self.assertEqual(sorted(item["claim_id"] for item in options), sorted(preview_ids(generated)))
            approval = service.bootstrap()["external_claim_approval"]
            service.approve_external_claims({"user_confirmed": True, "expected_review_hash": approval["review_hash"], "allowed_uses": ["cover_letter"]})
            self.assertEqual(service.bootstrap()["standard_resume"]["claim_options"], [])

    def test_modified_selection_receipt_cannot_be_downloaded_or_adopted(self):
        with project_temp() as root:
            service, onboarding, _, _ = approved_pdf_service(root)
            all_ids = preview_ids(generate(service))
            result = selected(service, [all_ids[0]])
            draft = json.loads(onboarding.read_bytes(result["draft_ref"]))
            draft["preview"]["included_claim_ids"] = [all_ids[-1]]
            onboarding.rotate(result["draft_ref"], canonical_json(draft))
            for operation in (service.standard_resume_file, service.adopt_standard_resume):
                with self.subTest(operation=operation.__name__):
                    with self.assertRaises(JobOpsError) as caught:
                        operation({**result, "user_confirmed": True, "expected_docx_sha256": result["docx_sha256"]})
                    self.assertEqual(caught.exception.code, "STANDARD_RESUME_DRAFT_INVALID")

    def test_subset_adoption_keeps_original_evidence_and_requires_new_approvals(self):
        with project_temp() as root:
            service, _, _, original = approved_pdf_service(root)
            first = preview_ids(generate(service))[0]
            result = selected(service, [first])
            self.assertEqual(result["preview"]["claim_count"], 1)
            adopted = service.adopt_standard_resume({**result, "user_confirmed": True, "expected_docx_sha256": result["docx_sha256"]})
            self.assertEqual(adopted["status"], "STANDARD_RESUME_ADOPTED")
            _, state = service.ensure_state()
            self.assertEqual(state["base_claim_source"]["secure_ref"], original["secure_ref"])
            self.assertFalse(service.bootstrap()["external_claim_approval"]["current"])
            self.assertEqual(service.redacted_status()["real_external_actions"], 0)

    def test_restore_saved_selection_is_read_only_and_requires_current_review(self):
        with project_temp() as root:
            service, onboarding, _, _ = approved_pdf_service(root)
            first = preview_ids(generate(service))[0]
            result = selected(service, [first])
            before = onboarding.read_bytes(result["draft_ref"])
            request = {"expected_review_hash": service.bootstrap()["standard_resume"]["review_hash"]}
            restored = service.restore_standard_resume(request)
            self.assertEqual(restored, result)
            self.assertEqual(onboarding.read_bytes(result["draft_ref"]), before)
            with self.assertRaises(JobOpsError) as caught:
                service.restore_standard_resume({"expected_review_hash": "stale"})
            self.assertEqual(caught.exception.code, "EXTERNAL_CLAIM_REVIEW_STALE")

    def test_legacy_all_fact_draft_is_read_without_rewriting_its_receipt(self):
        with project_temp() as root:
            service, onboarding, _, _ = approved_pdf_service(root)
            result = generate(service)
            legacy = json.loads(onboarding.read_bytes(result["draft_ref"]))
            legacy["preview"]["layout"] = "STANDARD_SINGLE_COLUMN_V1"
            for key in ("included_claim_ids", "eligible_claim_count", "excluded_claim_count", "selection_hash"):
                legacy["preview"].pop(key, None)
            original = canonical_json(legacy)
            onboarding.rotate(result["draft_ref"], original)
            restored = service.restore_standard_resume({"expected_review_hash": service.bootstrap()["standard_resume"]["review_hash"]})
            self.assertEqual(restored["draft_ref"], result["draft_ref"])
            self.assertEqual(restored["selection_hash"], result["selection_hash"])
            self.assertEqual(onboarding.read_bytes(result["draft_ref"]), original)
            regenerated = generate(service)
            self.assertNotEqual(regenerated["draft_ref"], result["draft_ref"])
            self.assertEqual(regenerated["preview"]["layout"], "STANDARD_SINGLE_COLUMN_V2")


if __name__ == "__main__":
    unittest.main()
