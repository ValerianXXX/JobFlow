from __future__ import annotations
import base64
from contextlib import contextmanager
import io
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import _support
from PIL import Image
from jobops.errors import JobOpsError
from jobops.multimodal import read_visual, render_pages
from jobops.util import sha256_bytes


class VisualRenderPolicyTests(unittest.TestCase):
    def fixture(self):
        return (Path(__file__).parent / "fixtures/material-layout-probes-v1/zh-scan.pdf").read_bytes()

    def test_resolution_changes_actual_pixels_but_not_source_bytes(self):
        data = self.fixture()
        before_hash = sha256_bytes(data)
        # PDFium rounds ceil(page size * scale); 792 * (300 / 72) is just
        # above 3300 in binary floating point and therefore yields 3301.
        for dpi, size in ((110, (935, 1210)), (144, (1224, 1584)), (220, (1870, 2420)), (300, (2550, 3301))):
            with self.subTest(dpi=dpi), patch.dict("os.environ", {"JOBFLOW_VISUAL_PDF_DPI": str(dpi)}):
                pages = render_pages(data, ".pdf")
                self.assertEqual(len(pages), 1)
                self.assertEqual((pages[0]["width"], pages[0]["height"]), size)
                raw = base64.b64decode(pages[0]["data_url"].split(",", 1)[1])
                self.assertEqual(pages[0]["sha256"], sha256_bytes(raw))
                with Image.open(io.BytesIO(raw)) as image:
                    self.assertEqual(image.size, size)
        self.assertEqual(sha256_bytes(data), before_hash)

    def test_default_avoids_the_reproduced_low_resolution_scan_failure(self):
        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual(render_pages(self.fixture(), ".pdf")[0]["pdf_dpi"], 220)

    def test_invalid_resolution_does_not_render_or_call_provider(self):
        engine = Mock()
        engine.public_status.return_value = {"private_transport": "LOCAL_LOOPBACK_HTTP"}
        for value in ("", "109", "301", "220.0", "+220", " 220", "２２０", "NaN", "9" * 5000):
            with self.subTest(value=value[:20]), patch.dict("os.environ", {"JOBFLOW_VISUAL_PDF_DPI": value}), patch("pdfplumber.open") as opened:
                with self.assertRaises(JobOpsError) as error:
                    read_visual(b"synthetic", ".pdf", engine)
                self.assertEqual(error.exception.code, "VISUAL_RESOLUTION_INVALID")
                opened.assert_not_called()
        engine.execute_structured_task.assert_not_called()

    @contextmanager
    def document(self, pages):
        yield SimpleNamespace(pages=pages)

    def test_pixel_limit_checks_entire_document_before_rendering(self):
        # Each page is below 25 MP, but the whole source exceeds 60 MP.
        pages = [SimpleNamespace(width=2000, height=2000, to_image=Mock()) for _ in range(4)]
        with patch.dict("os.environ", {"JOBFLOW_VISUAL_PDF_DPI": "144"}), patch("pdfplumber.open", return_value=self.document(pages)):
            with self.assertRaises(JobOpsError) as error:
                render_pages(b"synthetic", ".pdf")
            self.assertEqual(error.exception.code, "VISUAL_IMAGE_LIMIT")
        for page in pages:
            page.to_image.assert_not_called()

    def test_payload_limit_aborts_without_sending_partial_pages(self):
        engine = Mock()
        engine.public_status.return_value = {"private_transport": "LOCAL_LOOPBACK_HTTP"}
        with patch("jobops.multimodal.MAX_PAYLOAD_CHARACTERS", 100):
            with self.assertRaises(JobOpsError) as error:
                read_visual(self.fixture(), ".pdf", engine)
        self.assertEqual(error.exception.code, "VISUAL_PAYLOAD_LIMIT")
        engine.execute_structured_task.assert_not_called()

    def test_metadata_records_rendered_input_without_image_payload(self):
        engine = Mock()
        engine.public_status.return_value = {"private_transport": "LOCAL_LOOPBACK_HTTP"}
        engine.execute_structured_task.return_value = {"pages": [{"page": 1, "blank": False, "blocks": [{"text": "Synthetic only", "bbox": [0, 0, 1, 1]}]}]}
        with patch.dict("os.environ", {"JOBFLOW_VISUAL_PDF_DPI": "220"}):
            _, meta = read_visual(self.fixture(), ".pdf", engine)
        self.assertEqual(meta["visual_render"]["pdf_dpi"], 220)
        self.assertEqual(meta["visual_render"]["pages"][0]["width"], 1870)
        self.assertNotIn("data_url", repr(meta))
        self.assertTrue(meta["visual_verification_required"])
        self.assertFalse(meta["automatic_claim_selection"])


if __name__ == "__main__":
    unittest.main()
