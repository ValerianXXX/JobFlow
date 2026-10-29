from __future__ import annotations
import base64
import importlib.util
import io
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import _support
from PIL import Image
from jobops.errors import JobOpsError


def load(name, filename):
    path = Path(__file__).resolve().parents[1] / "scripts" / filename
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


evaluation = load("scan_eval_test", "evaluate-scan-resolution.py")
ocr = load("scan_ocr_test", "tesseract-probe-adapter.py")


class ScanEvaluationTests(unittest.TestCase):
    def test_four_request_budget_counts_failed_calls(self):
        engine = evaluation.ObservedVision({"model": "synthetic", "provider": "synthetic"})
        with patch.object(evaluation.WSLHermesCLIEngine, "_invoke", side_effect=ValueError("synthetic")) as call:
            for _ in range(4):
                with self.assertRaises(ValueError):
                    engine._invoke({"task": "synthetic"})
            with self.assertRaises(JobOpsError) as error:
                engine._invoke({"task": "synthetic"})
        self.assertEqual(error.exception.code, "EVALUATION_CALL_LIMIT")
        self.assertEqual(call.call_count, 4)
        self.assertTrue(all(c["status"] == "FAILED" for c in engine.calls))

    def test_transcript_score_does_not_hide_changed_number_or_ownership(self):
        case = next(case for case in evaluation.corpus.load_cases() if case["id"] == "zh-scan")
        text = "\n".join(line for entity in case["entities"] for line in entity["lines"])
        original = evaluation.assess(case, text)
        self.assertEqual(original["verbatim_facts_retained"], 4)
        self.assertEqual(original["entity_contexts_retained"], 2)
        changed = evaluation.assess(case, text.replace("15份报告", "5份报告").replace("团队管理预算", "申请人管理预算"))
        self.assertEqual(changed["verbatim_facts_retained"], 2)
        self.assertEqual(changed["entity_contexts_retained"], 0)
        self.assertTrue(changed["manual_adjudication_required"])

    def test_ocr_uses_image_stdin_and_retains_line_coordinates(self):
        image = io.BytesIO()
        Image.new("RGB", (40, 40), "white").save(image, format="PNG")
        request = {"png_base64": base64.b64encode(image.getvalue()).decode(), "dpi": 220, "language": "eng"}
        header = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
        tsv = header + "5\t1\t1\t1\t1\t1\t1\t2\t10\t10\t50\tTeam\n5\t1\t1\t1\t1\t2\t12\t2\t15\t10\t99\t21\n"
        calls = []
        def fake(command, **kwargs):
            calls.append((command, kwargs))
            return SimpleNamespace(returncode=0, stdout=tsv.encode())
        value = ocr.run(request, execute=fake)
        self.assertEqual(value["text"], "Team 21")
        self.assertEqual(value["lines"][0]["bbox_pixels"], [1, 2, 27, 12])
        self.assertEqual(calls[0][1]["input"], image.getvalue())
        self.assertNotIn(request["png_base64"], repr(calls[0][0]))
        self.assertFalse(value["factual_accuracy_verified"])

    def test_missing_tsv_contract_cannot_be_reported_as_empty_success(self):
        image = io.BytesIO()
        Image.new("RGB", (40, 40), "white").save(image, format="PNG")
        request = {"png_base64": base64.b64encode(image.getvalue()).decode(), "dpi": 220, "language": "eng"}
        def wrong_format(command, **kwargs):
            self.assertIn("tessedit_create_tsv=1", command)
            self.assertNotIn("tsv", command)
            return SimpleNamespace(returncode=0, stdout=b"Synthetic plain text instead of TSV\n")
        with self.assertRaisesRegex(ValueError, "TSV contract"):
            ocr.run(request, execute=wrong_format)


if __name__ == "__main__":
    unittest.main()
