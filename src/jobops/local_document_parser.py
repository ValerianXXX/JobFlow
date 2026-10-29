"""Opt-in local PDF adapters; no OCR weights, remote services or Claim approval."""

from __future__ import annotations

import base64
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time

from .errors import JobOpsError
from .util import sha256_bytes


def validate_native_result(raw: bytes, result: dict) -> tuple[str, dict]:
    if not isinstance(result, dict) or set(result) != {
        "pages",
        "parser_version",
        "backend_version",
    }:
        raise JobOpsError(
            "LOCAL_PARSER_RESPONSE_INVALID", "The local parser response is incomplete."
        )
    pages = result["pages"]
    # Independently count the original pages; a worker cannot assert its own coverage.
    import io
    from pypdf import PdfReader

    count = len(PdfReader(io.BytesIO(raw)).pages)
    if not isinstance(pages, list) or not 1 <= count <= 6 or len(pages) != count:
        raise JobOpsError(
            "LOCAL_PARSER_COVERAGE_INVALID",
            "The local parser did not cover every original page.",
        )
    lines, regions = [], []
    for number, page in enumerate(pages, 1):
        if (
            not isinstance(page, dict)
            or page.get("page") != number
            or type(page.get("page")) is not int
            or page.get("blank") is not False
        ):
            raise JobOpsError("LOCAL_PARSER_RESPONSE_INVALID", "Invalid parsed page.")
        blocks = page.get("blocks")
        if not isinstance(blocks, list) or not 1 <= len(blocks) <= 500:
            raise JobOpsError(
                "LOCAL_PARSER_NEEDS_OCR", "A page needs OCR or original-file review."
            )
        for block in blocks:
            if not isinstance(block, dict):
                raise JobOpsError(
                    "LOCAL_PARSER_RESPONSE_INVALID", "Invalid parsed region."
                )
            text, box = block.get("text"), block.get("bbox")
            if (
                not isinstance(text, str)
                or not 1 <= len(text) <= 1200
                or "\n" in text
                or "\r" in text
            ):
                raise JobOpsError(
                    "LOCAL_PARSER_RESPONSE_INVALID", "Invalid parsed text."
                )
            if (
                not isinstance(box, list)
                or len(box) != 4
                or any(
                    type(v) not in (int, float)
                    or not math.isfinite(v)
                    or not 0 <= v <= 1
                    for v in box
                )
                or box[0] >= box[2]
                or box[1] >= box[3]
            ):
                raise JobOpsError(
                    "LOCAL_PARSER_RESPONSE_INVALID", "Invalid source coordinates."
                )
            lines.append(text)
            regions.append(
                {
                    "page": number,
                    "bbox": box,
                    "line_start": len(lines),
                    "line_end": len(lines),
                }
            )
    return "\n".join(lines), {
        "document_page_count": count,
        "visual_regions": regions,
        "visual_source_sha256": sha256_bytes(raw),
        "visual_verification_required": True,
        "visual_mode": "LOCAL_NATIVE_TEXT_UNVERIFIED",
        "automatic_claim_selection": False,
        "local_parser_version": result["parser_version"],
        "local_parser_backend_version": result["backend_version"],
        "model_calls": 0,
        "visual_cost_usd": 0.0,
    }


def _read_pdf_worker(raw: bytes, worker_module: str) -> tuple[str, dict]:
    if not raw or len(raw) > 16 * 1024 * 1024:
        raise JobOpsError(
            "LOCAL_PARSER_LIMIT",
            "Local PDF parsing supports at most 16 MiB and six pages.",
        )
    started = time.monotonic()
    environment = {
        **os.environ,
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
        "PYTHONUTF8": "1",
        "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
    }
    try:
        completed = subprocess.run(
            [sys.executable, "-m", worker_module],
            input=json.dumps(
                {"pdf_base64": base64.b64encode(raw).decode("ascii")}
            ).encode("utf-8"),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=60,
            check=False,
            env=environment,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if completed.returncode:
            raise JobOpsError(
                "LOCAL_PARSER_FAILED", "The isolated local parser exited before producing a result.",
                failure_stage="WORKER_EXIT", worker_exit_code=completed.returncode,
            )
        if len(completed.stdout) > 4 * 1024 * 1024:
            raise JobOpsError(
                "LOCAL_PARSER_FAILED", "The isolated local parser exceeded its output limit.",
                failure_stage="OUTPUT_LIMIT",
            )
        response = json.loads(completed.stdout)
        if not isinstance(response, dict):
            raise ValueError()
        if response.get("ok") is not True:
            code = response.get("code")
            if code not in {
                "LOCAL_PARSER_NOT_INSTALLED",
                "LOCAL_PARSER_NEEDS_OCR",
                "LOCAL_PARSER_LIMIT",
                "LOCAL_PARSER_INCOMPLETE_PAGINATION",
            }:
                code = "LOCAL_PARSER_FAILED"
            raise JobOpsError(
                code,
                "The optional local parser could not read this PDF. Use the existing text/vision path or check the original.",
            )
        text, metadata = validate_native_result(raw, response["result"])
        metadata["local_parser_column_pages"] = sum(
            page.get("column_reordered") is True for page in response["result"]["pages"]
        )
        metadata["local_parser_table_pages"] = sum(
            page.get("table_order_retained") is True for page in response["result"]["pages"]
        )
        metadata["visual_elapsed_ms"] = round((time.monotonic() - started) * 1000)
        return text, metadata
    except JobOpsError:
        raise
    except subprocess.TimeoutExpired as exc:
        raise JobOpsError(
            "LOCAL_PARSER_TIMEOUT", "The local parser exceeded its one-minute limit.",
            failure_stage="WORKER_TIMEOUT",
        ) from exc
    except Exception as exc:
        raise JobOpsError(
            "LOCAL_PARSER_FAILED",
            "The optional local PDF parser failed; no source was imported.",
            failure_stage="INVALID_RESULT",
        ) from exc


def read_local_pdf(raw: bytes) -> tuple[str, dict]:
    return _read_pdf_worker(raw, "jobops._docling_worker")


def read_region_pdf(raw: bytes) -> tuple[str, dict]:
    """Opt-in local geometry extraction; the original still requires review."""
    text, metadata = _read_pdf_worker(raw, "jobops._pdf_region_worker")
    metadata["visual_mode"] = "LOCAL_PDF_REGIONS_UNVERIFIED"
    metadata["pdf_extraction_strategy"] = "LOCAL_PDF_REGIONS"
    return text, metadata
