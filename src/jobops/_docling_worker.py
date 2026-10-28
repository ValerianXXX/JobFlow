"""Optional model-free PDF parser. Source bytes enter only through stdin."""

from __future__ import annotations

import base64
import io
import json
import logging
import sys


def parse_native_pdf(raw: bytes) -> dict:
    from importlib.metadata import version
    from docling.document_converter import DocumentConverter, NativePdfFormatOption
    from docling.datamodel.pipeline_options import NativePdfPipelineOptions
    from docling.datamodel.base_models import (
        DocumentStream,
        InputFormat,
        ConversionStatus,
    )

    options = NativePdfPipelineOptions(
        enable_remote_services=False,
        allow_external_plugins=False,
        do_picture_description=False,
        do_picture_classification=False,
        do_chart_extraction=False,
        document_timeout=40,
        parser_threads=1,
    )
    converter = DocumentConverter(
        allowed_formats=[InputFormat.PDF],
        format_options={
            InputFormat.PDF: NativePdfFormatOption(pipeline_options=options)
        },
    )
    result = converter.convert(
        DocumentStream(name="source.pdf", stream=io.BytesIO(raw)),
        max_num_pages=6,
        max_file_size=16 * 1024 * 1024,
        raises_on_error=True,
    )
    if result.status != ConversionStatus.SUCCESS:
        raise ValueError("LOCAL_PARSER_INCOMPLETE")
    document = result.document
    pages = {
        number: {"page": number, "blank": False, "blocks": []}
        for number in document.pages
    }
    for item, _level in document.iterate_items():
        text = getattr(item, "text", None)
        if not isinstance(text, str) or not text.strip():
            continue
        provenance = getattr(item, "prov", [])
        if len(provenance) != 1:
            raise ValueError("LOCAL_PARSER_PROVENANCE_INVALID")
        source = provenance[0]
        size = document.pages[source.page_no].size
        box = source.bbox.to_top_left_origin(page_height=size.height)
        for line in text.splitlines():
            if line.strip():
                pages[source.page_no]["blocks"].append(
                    {
                        "text": line,
                        "bbox": [
                            box.l / size.width,
                            box.t / size.height,
                            box.r / size.width,
                            box.b / size.height,
                        ],
                    }
                )
    if not pages or any(not page["blocks"] for page in pages.values()):
        # Native parsing cannot distinguish a blank page from a scan with no text layer.
        # Do not report a scan as successfully transcribed or silently drop its page.
        raise ValueError("LOCAL_PARSER_NEEDS_OCR")
    return {
        "pages": [pages[number] for number in sorted(pages)],
        "parser_version": version("docling-slim"),
        "backend_version": version("docling-parse"),
    }


def main() -> int:
    logging.disable(logging.CRITICAL)
    try:
        request = json.loads(sys.stdin.buffer.read(23 * 1024 * 1024 + 1))
        if not isinstance(request, dict) or set(request) != {"pdf_base64"}:
            raise ValueError("LOCAL_PARSER_REQUEST_INVALID")
        raw = base64.b64decode(request["pdf_base64"], validate=True)
        if not raw or len(raw) > 16 * 1024 * 1024:
            raise ValueError("LOCAL_PARSER_LIMIT")
        result = parse_native_pdf(raw)
        sys.stdout.write(json.dumps({"ok": True, "result": result}, ensure_ascii=True))
    except ImportError:
        sys.stdout.write(
            json.dumps({"ok": False, "code": "LOCAL_PARSER_NOT_INSTALLED"})
        )
    except Exception as exc:
        code = (
            str(exc)
            if isinstance(exc, ValueError)
            and str(exc)
            in {
                "LOCAL_PARSER_NEEDS_OCR",
                "LOCAL_PARSER_INCOMPLETE",
                "LOCAL_PARSER_PROVENANCE_INVALID",
                "LOCAL_PARSER_LIMIT",
                "LOCAL_PARSER_REQUEST_INVALID",
            }
            else "LOCAL_PARSER_FAILED"
        )
        sys.stdout.write(json.dumps({"ok": False, "code": code}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
