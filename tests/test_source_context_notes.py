from __future__ import annotations

import copy
import json
import sys
import unittest
from unittest import mock

from jobops.ai_runtime import AI_PROTOCOL_VERSION, LocalSubprocessAIEngine


class SourceContextContracts(unittest.TestCase):
    text = "Remote work only.\nWork authorization is unknown.\nReferences require verification."

    def analyze(self, notes, text=None):
        response = {"schema_version": AI_PROTOCOL_VERSION, "entities": [], "candidates": [], "review_notes": notes}
        engine = LocalSubprocessAIEngine([sys.executable])
        with mock.patch.object(engine, "execute_structured_task", return_value=copy.deepcopy(response)):
            return engine.analyze_document(text or self.text, source_id="SRC-SYNTHETIC", source_type="resume")

    @staticmethod
    def note(kind="profile_constraint", field="remote_preference", start=1, end=1):
        return {"kind": kind, "field_id": field, "line_start": start, "line_end": end}

    def test_request_separates_context_selectors_from_claim_wording(self):
        request, _ = LocalSubprocessAIEngine._request(self.text, source_id="SRC-SYNTHETIC", source_type="resume")
        self.assertIn("review_notes", request["output_contract"])
        note = request["output_contract"]["review_notes"][0]
        self.assertEqual(set(note), {"kind", "field_id", "line_start", "line_end"})

    def test_context_is_host_quoted_separate_from_candidates_and_safe_summary(self):
        result = self.analyze([self.note(), self.note("evidence_gap", "", 3, 3)])
        candidates, summary = result  # Existing two-value interface stays valid.
        self.assertEqual(candidates, [])
        notes = list(getattr(result, "context_notes", ()))
        self.assertEqual([n["quote"] for n in notes], ["Remote work only.", "References require verification."])
        self.assertNotIn("Remote work", json.dumps(summary))
        self.assertFalse(any(n["approved_for_external"] for n in notes))
        self.assertTrue(all(n["status"] == "UNCONFIRMED_SOURCE_CONTEXT" for n in notes))
        self.assertEqual(summary["source_context_review"]["retained"], 2)

    def test_chinese_unknown_authorization_remains_a_quote_not_an_answer(self):
        text = "仅接受远程工作。\n工作授权状态未知。"
        result = self.analyze([self.note(), self.note(field="work_authorization", start=2, end=2)], text)
        notes = list(getattr(result, "context_notes", ()))
        self.assertEqual([n["quote"] for n in notes], text.splitlines())
        self.assertNotIn("value", notes[1])
        self.assertFalse(result[1]["source_context_review"]["automatic_profile_update"])

    def test_forged_values_unknown_destinations_and_model_approval_are_rejected_individually(self):
        valid = self.note()
        invalid = [dict(valid, value="onsite"), dict(valid, quote="Applicant is authorized."),
                   dict(valid, approved_for_external=True), self.note(field="email"),
                   self.note("evidence_gap", "work_authorization"), self.note(kind="execute_instruction")]
        result = self.analyze([valid, *invalid])
        self.assertEqual(len(getattr(result, "context_notes", ())), 1)
        self.assertEqual(result[1]["source_context_review"]["filtered"], 6)
        self.assertNotIn("authorized", json.dumps(result[1]))

    def test_noninteger_out_of_bounds_and_reversed_ranges_cannot_select_other_text(self):
        notes = [self.note(), self.note(start=True), self.note(start=1.0), self.note(start=0),
                 self.note(start=2, end=1), self.note(end=4), self.note(start="1")]
        result = self.analyze(notes)
        self.assertEqual(len(getattr(result, "context_notes", ())), 1)
        self.assertEqual(result[1]["source_context_review"]["filtered"], 6)

    def test_long_excerpt_is_not_silently_truncated_into_an_apparently_complete_note(self):
        result = self.analyze([self.note()], "X" * 2_001)
        self.assertEqual(list(getattr(result, "context_notes", ())), [])
        self.assertEqual(result[1]["source_context_review"]["filtered"], 1)

    def test_duplicate_selectors_do_not_inflate_context_count(self):
        result = self.analyze([self.note(), self.note()])
        self.assertEqual(len(getattr(result, "context_notes", ())), 1)
        self.assertEqual(result[1]["source_context_review"]["duplicates"], 1)

    def test_changed_source_excerpt_changes_note_identity(self):
        before = self.analyze([self.note()])
        after = self.analyze([self.note()], "Remote work preferred.")
        old = list(getattr(before, "context_notes", ()))
        new = list(getattr(after, "context_notes", ()))
        self.assertEqual(len(old), 1)
        self.assertEqual(len(new), 1)
        self.assertNotEqual(old[0]["note_id"], new[0]["note_id"])
        self.assertNotEqual(old[0]["quote_sha256"], new[0]["quote_sha256"])

    def test_missing_context_contract_is_visible_without_rejecting_legacy_response(self):
        engine = LocalSubprocessAIEngine([sys.executable])
        response = {"schema_version": AI_PROTOCOL_VERSION, "entities": [], "candidates": []}
        with mock.patch.object(engine, "execute_structured_task", return_value=response):
            result = engine.analyze_document(self.text, source_id="SRC-SYNTHETIC", source_type="resume")
        self.assertEqual(result[0], [])
        self.assertEqual(result[1].get("source_context_review", {}).get("status"), "NOT_PROVIDED")


if __name__ == "__main__":
    unittest.main()
