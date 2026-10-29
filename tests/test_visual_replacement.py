from copy import deepcopy
import io
import json
import unittest
from unittest.mock import Mock, patch

import _support
from PIL import Image
from jobops.errors import JobOpsError
from jobops.multimodal import read_visual


class VisualReplacementTests(unittest.TestCase):
    def fixture(self):
        output = io.BytesIO()
        Image.new("RGB", (100, 300), "white").save(output, format="PNG")
        return output.getvalue()

    def response(self, text="Synthetic team verified 14 stores."):
        return {"pages": [{"page": 1, "blank": False, "blocks": [{"text": text, "bbox": [0, 0, 1, 1]}]}]}

    def engine(self, *responses):
        engine = Mock()
        engine.public_status.return_value = {"private_transport": "LOCAL_LOOPBACK_HTTP"}
        engine.execute_structured_task.side_effect = list(responses)
        return engine

    def test_fresh_full_replacement_preserves_images_and_does_not_repair_partial_text(self):
        partial = json.dumps(self.response("PRIVATE-FAILED-TEXT"))[:-2]
        engine = self.engine(partial, self.response())
        with patch.dict("os.environ", {"JOBFLOW_VISUAL_MAX_REPAIR": "1"}):
            text, receipt = read_visual(self.fixture(), ".png", engine)
        self.assertEqual(text, "Synthetic team verified 14 stores.")
        first, second = [c.args[0] for c in engine.execute_structured_task.call_args_list]
        self.assertEqual(first["pages"], second["pages"])
        self.assertEqual(second["physical_source_pages"], [{"page": 1, "width": 100, "height": 300}])
        self.assertEqual(second["replacement_request"]["previous_validation_code"], "VISUAL_RESPONSE_INVALID")
        self.assertNotIn("PRIVATE-FAILED-TEXT", repr(second))
        self.assertEqual(receipt["visual_model_requests"], 2)
        self.assertIsNone(receipt["visual_cost_usd"])
        self.assertTrue(receipt["visual_verification_required"])
        self.assertFalse(receipt["automatic_claim_selection"])

    def test_printed_pages_cannot_create_physical_pages_in_one_image(self):
        wrong = self.response()
        wrong["pages"].append({**deepcopy(wrong["pages"][0]), "page": 2})
        engine = self.engine(wrong, self.response())
        with patch.dict("os.environ", {"JOBFLOW_VISUAL_MAX_REPAIR": "1"}):
            _, receipt = read_visual(self.fixture(), ".png", engine)
        self.assertEqual(receipt["document_page_count"], 1)
        self.assertEqual(receipt["visual_initial_validation_code"], "VISUAL_COVERAGE_INCOMPLETE")

    def test_two_invalid_responses_fail_closed_without_third_call(self):
        engine = self.engine('{"pages": [', self.response(" "))
        with patch.dict("os.environ", {"JOBFLOW_VISUAL_MAX_REPAIR": "1"}), self.assertRaises(JobOpsError) as error:
            read_visual(self.fixture(), ".png", engine)
        self.assertEqual(error.exception.code, "VISUAL_RESPONSE_REPAIR_FAILED")
        self.assertEqual(engine.execute_structured_task.call_count, 2)

    def test_opt_out_and_invalid_configuration_bound_cost_before_call(self):
        engine = self.engine('{"pages": [')
        with patch.dict("os.environ", {"JOBFLOW_VISUAL_MAX_REPAIR": "0"}), self.assertRaises(JobOpsError) as error:
            read_visual(self.fixture(), ".png", engine)
        self.assertEqual(error.exception.code, "VISUAL_RESPONSE_INVALID")
        self.assertEqual(engine.execute_structured_task.call_count, 1)
        for value in ("", "2", "true", "01", " 1"):
            engine = self.engine()
            with self.subTest(value=value), patch.dict("os.environ", {"JOBFLOW_VISUAL_MAX_REPAIR": value}), patch("jobops.multimodal.render_pages") as render:
                with self.assertRaises(JobOpsError) as error:
                    read_visual(self.fixture(), ".png", engine)
                self.assertEqual(error.exception.code, "VISUAL_REPAIR_CONFIGURATION_INVALID")
                render.assert_not_called()
                engine.execute_structured_task.assert_not_called()

    def test_transport_failure_is_never_retried_at_either_attempt(self):
        for responses, count in (([JobOpsError("AI_ENGINE_TIMEOUT", "Synthetic")], 1),
                                 (['{"pages": [', JobOpsError("AI_ENGINE_UNAVAILABLE", "Synthetic")], 2)):
            engine = self.engine(*responses)
            with self.subTest(count=count), patch.dict("os.environ", {"JOBFLOW_VISUAL_MAX_REPAIR": "1"}), self.assertRaises(JobOpsError) as error:
                read_visual(self.fixture(), ".png", engine)
            self.assertIn(error.exception.code, {"AI_ENGINE_TIMEOUT", "AI_ENGINE_UNAVAILABLE"})
            self.assertEqual(engine.execute_structured_task.call_count, count)


if __name__ == "__main__":
    unittest.main()
