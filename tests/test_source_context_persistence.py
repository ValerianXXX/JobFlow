from __future__ import annotations

from copy import deepcopy
import json
import unittest
from unittest import mock

from _support import project_temp
import test_onboarding_center as fixtures
from jobops.ai_runtime import AI_PROTOCOL_VERSION
from jobops.onboarding_center import OnboardingCenterService
from jobops.source_context import (
    DocumentAnalysisResult, SourceContextAccumulator, bind_context_notes, quoted_context,
)
from jobops.util import sha256_bytes


TEXT = "Remote work only.\nWork authorization is unknown.\nReferences require verification."


def selector(line=1, field="remote_preference", kind="profile_constraint"):
    return {"kind": kind, "field_id": field, "line_start": line, "line_end": line}


def response(notes):
    return {"schema_version": AI_PROTOCOL_VERSION, "entities": [], "candidates": [], "review_notes": notes}


class SourceContextBoundaryTests(unittest.TestCase):
    def test_global_chunk_coordinates_and_cap_are_preserved(self):
        context = SourceContextAccumulator("SRC-SYNTHETIC")
        for start in (1, 33, 65):
            lines = [f"Synthetic verification need {i}" for i in range(start, start + 32)]
            context.add(response([selector(i, "", "evidence_gap") for i in range(start, start + 32)]),
                        lines, line_number_start=start)
        self.assertEqual(len(context.notes), 64)
        self.assertEqual(context.notes[-1]["quote"], "Synthetic verification need 64")
        self.assertEqual(context.summary()["filtered"], 32)

    def test_disabled_chat_export_cannot_retain_conversation_quotes(self):
        context = SourceContextAccumulator("SRC-CHAT", enabled=False)
        context.add(response([selector()]), TEXT.splitlines())
        self.assertEqual(context.notes, [])
        self.assertEqual(context.summary()["status"], "DISABLED_FOR_CHAT_EXPORT")

    def test_result_copy_keeps_separate_notes_without_mutable_alias(self):
        note = quoted_context(selector(), source_id="SRC-SYNTHETIC", source_lines=TEXT.splitlines())
        result = DocumentAnalysisResult([], {"safe": True}, [note])
        note["quote"] = "Forged"
        exposed = result.context_notes
        exposed[0]["quote"] = "Also forged"
        copied = deepcopy(result)
        self.assertEqual(copied.context_notes[0]["quote"], "Remote work only.")
        self.assertEqual(tuple(copied), ([], {"safe": True}))
        self.assertNotIn("Remote work", json.dumps(copied))

    def test_source_binding_rejects_stale_or_forged_text_and_resets_approval(self):
        note = quoted_context(selector(), source_id="SRC-SYNTHETIC", source_lines=TEXT.splitlines())
        for bad in ({**note, "quote": "Forged"}, {**note, "quote_sha256": "wrong"},
                    {**note, "source_id": "SRC-OTHER"}):
            self.assertEqual(bind_context_notes([bad], text=TEXT, source_id="SRC-SYNTHETIC", source_sha256="current"), [])
        rebound = bind_context_notes([{**note, "approved_for_external": True, "value": "onsite"}],
                                     text=TEXT, source_id="SRC-SYNTHETIC", source_sha256="current")
        self.assertFalse(rebound[0]["approved_for_external"])
        self.assertNotIn("value", rebound[0])
        self.assertEqual(rebound[0]["source_sha256"], "current")


class SourceContextPersistenceTests(unittest.TestCase):
    def make_service(self, root):
        service, onboarding, store, _, _ = fixtures.OnboardingCenterTests().make_service(root)
        self.addCleanup(service.close)
        return service, onboarding, store

    def preview(self, service, notes, text=TEXT):
        with mock.patch.object(service.ai_engine, "execute_structured_task", return_value=response(notes)):
            return service.preview_source("project_case", ".txt", text.encode("utf-8"))

    def test_preview_commit_reload_keep_context_private_and_answers_unchanged(self):
        with project_temp() as root:
            service, onboarding, _ = self.make_service(root)
            before = service.bootstrap()
            result = self.preview(service, [selector(), selector(3, "", "evidence_gap")])
            self.assertEqual(result["context_note_count"], 2)
            self.assertNotIn("Remote work", json.dumps(result))
            pending = service.bootstrap()["pending_sources"][0]
            notes = pending["context_notes"]
            self.assertEqual(notes[0]["source_sha256"], sha256_bytes(TEXT.encode()))
            self.assertEqual(notes[1]["quote"], TEXT.splitlines()[2])
            self.assertEqual(service.bootstrap()["context_notes"], [])
            service.commit_source(result["source_id"], [])
            reopened = OnboardingCenterService(root / "project", service.database, onboarding, ai_engine=service.ai_engine)
            self.addCleanup(reopened.close)
            after = reopened.bootstrap()
            self.assertEqual(after["context_notes"], notes)
            self.assertEqual(after["answers"], before["answers"])
            self.assertEqual(after["claims"], before["claims"])
            self.assertEqual(after["sources"][-1]["source_context_review"]["retained"], 2)
            self.assertNotIn("Remote work", json.dumps(after["sources"]))
            for path in (root / "project" / "state").glob("*"):
                if path.is_file():
                    self.assertNotIn(TEXT.splitlines()[0].encode(), path.read_bytes())

    def test_reanalysis_is_pending_until_confirmed_and_discard_preserves_previous_notes(self):
        with project_temp() as root:
            service, _, _ = self.make_service(root)
            source_id = self.preview(service, [selector()])["source_id"]
            service.commit_source(source_id, [])
            original = service.bootstrap()["context_notes"]
            with mock.patch.object(service.ai_engine, "execute_structured_task", return_value=response([selector(2, "work_authorization")])):
                service.reprocess_source(source_id)
            state = service.bootstrap()
            self.assertEqual(state["context_notes"], original)
            self.assertEqual(state["pending_sources"][0]["context_notes"][0]["quote"], TEXT.splitlines()[1])
            service.discard_source_preview(source_id)
            self.assertEqual(service.bootstrap()["context_notes"], original)
            with mock.patch.object(service.ai_engine, "execute_structured_task", return_value=response([])):
                service.reprocess_source(source_id)
            service.commit_source(source_id, [])
            self.assertEqual(service.bootstrap()["context_notes"], [])

    def test_delete_removes_only_that_sources_context(self):
        with project_temp() as root:
            service, _, _ = self.make_service(root)
            first = self.preview(service, [selector()])["source_id"]
            service.commit_source(first, [])
            second = self.preview(service, [selector()], "Remote work preferred.")["source_id"]
            service.commit_source(second, [])
            service.delete_source(first, user_confirmed=True)
            notes = service.bootstrap()["context_notes"]
            self.assertEqual(len(notes), 1)
            self.assertEqual(notes[0]["source_id"], second)

    def test_failed_commit_does_not_promote_pending_notes(self):
        with project_temp() as root:
            service, _, _ = self.make_service(root)
            source_id = self.preview(service, [selector()])["source_id"]
            with mock.patch.object(service, "_save_state", side_effect=RuntimeError("synthetic save failure")):
                with self.assertRaises(RuntimeError):
                    service.commit_source(source_id, [])
            state = service.bootstrap()
            self.assertEqual(state["context_notes"], [])
            self.assertEqual(len(state["pending_sources"][0]["context_notes"]), 1)

    def test_legacy_state_without_context_is_usable(self):
        with project_temp() as root:
            service, _, _ = self.make_service(root)
            reference, state = service.ensure_state()
            state.pop("source_context_notes", None)
            service._save_state(reference, state)
            self.assertEqual(service.bootstrap()["context_notes"], [])


if __name__ == "__main__":
    unittest.main()
