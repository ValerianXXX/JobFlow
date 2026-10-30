from __future__ import annotations

import json
import sys
import unittest
from unittest import mock

from _support import project_temp
from jobops.ai_connections import (
    WSLHermesCLIEngine, WindowsHermesCLIEngine, LoopbackModelAIEngine, _analyze_all_chunks,
)


TEXT = "Remote work only.\nWork authorization is unknown."


def envelope(line=1, field="remote_preference"):
    payload = {"schema_version": 2, "entities": [], "candidates": [], "review_notes": [{
        "kind": "profile_constraint", "field_id": field, "line_start": line, "line_end": line,
    }]}
    return {"ok": True, "toolSummary": {"calls": 0, "tools": []}, "result": {"content": json.dumps(payload)}}


class SourceContextAdapterTests(unittest.TestCase):
    def check(self, result, quote="Remote work only."):
        candidates, summary = result
        self.assertEqual(candidates, [])
        notes = list(getattr(result, "context_notes", ()))
        self.assertEqual([note["quote"] for note in notes], [quote])
        self.assertFalse(notes[0]["approved_for_external"])
        self.assertNotIn(quote, json.dumps(summary))
        self.assertEqual(summary["source_context_review"]["retained"], 1)

    def test_shared_chunk_transport_preserves_private_context(self):
        self.check(_analyze_all_chunks(lambda _: envelope(), TEXT, source_id="SRC-SYNTHETIC", source_type="resume"))

    def test_wsl_hermes_keeps_context_separate_from_summary(self):
        engine = WSLHermesCLIEngine("wsl.exe", "Ubuntu", model="synthetic-model", provider_id="synthetic")
        with mock.patch.object(engine, "_invoke", return_value=envelope()):
            self.check(engine.analyze_document(TEXT, source_id="SRC-SYNTHETIC", source_type="resume"))

    def test_native_hermes_keeps_context_separate_from_summary(self):
        with project_temp() as root:
            engine = WindowsHermesCLIEngine(sys.executable, model="synthetic-model", provider_id="synthetic",
                                           hermes_home=root, working_directory=root)
            with mock.patch.object(engine, "_invoke", return_value=envelope()):
                self.check(engine.analyze_document(TEXT, source_id="SRC-SYNTHETIC", source_type="resume"))

    def test_loopback_model_keeps_context_separate_from_summary(self):
        engine = LoopbackModelAIEngine(connector_id="synthetic", label="Synthetic loopback", base_url="http://127.0.0.1:1",
                                      model="synthetic-model", api_style="openai", connection_kind="LOCAL_MODEL", data_route="LOCAL")
        with mock.patch.object(engine, "_complete", return_value=envelope()):
            self.check(engine.analyze_document(TEXT, source_id="SRC-SYNTHETIC", source_type="resume"))

    def test_repaired_response_does_not_keep_annotations_from_invalid_first_result(self):
        invalid = {"schema_version": 0, "entities": [], "candidates": [], "review_notes": []}
        invoke = mock.Mock(side_effect=[invalid, envelope(2, "work_authorization")])
        result = _analyze_all_chunks(invoke, TEXT, source_id="SRC-SYNTHETIC", source_type="resume")
        self.check(result, "Work authorization is unknown.")
        self.assertEqual(invoke.call_count, 2)
        self.assertEqual(result[1]["ai_repair_count"], 1)


if __name__ == "__main__":
    unittest.main()
