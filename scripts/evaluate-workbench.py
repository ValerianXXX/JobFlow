"""Reproduce local parser and mail-file comparisons with original synthetic cases."""

from __future__ import annotations
import json
import os
import platform
import re
import statistics
import sys
import time
from importlib.metadata import version
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tests")]
from _support import project_temp
import test_onboarding_center as support
from workbench_fixtures import material_cases, mail_cases
from jobops.local_document_parser import read_local_pdf
from jobops.source_quality import document_text_preflight
from jobops.mail_tracking import LocalEmlAdapter, propose_status
from jobops.errors import JobOpsError
from jobops.util import iso_utc, sha256_bytes


def main():
    documents = []
    with (
        project_temp() as root,
        patch.dict(
            os.environ,
            {"JOBFLOW_VISUAL_ENABLE": "false", "JOBFLOW_DOCUMENT_PARSER": ""},
        ),
    ):
        service, *_ = support.OnboardingCenterTests().make_service(root)
        for case in material_cases():
            row = {key: case[key] for key in ("id", "kind", "extension")}
            row["source_sha256"] = sha256_bytes(case["raw"])
            for mode in ("existing_text", "docling_native"):
                started = time.perf_counter()
                try:
                    if mode == "existing_text":
                        text, _, metadata = service._extract_text(
                            case["raw"], case["extension"], "resume"
                        )
                    elif case["extension"] == ".pdf":
                        text, metadata = read_local_pdf(case["raw"])
                    else:
                        raise JobOpsError(
                            "LOCAL_PARSER_PDF_ONLY",
                            "Only PDF is supported by this candidate.",
                        )
                    quality = metadata.get(
                        "document_quality"
                    ) or document_text_preflight(
                        text,
                        extension=case["extension"],
                        page_count=metadata.get("document_page_count"),
                    )
                    if quality["status"] == "FAIL":
                        raise JobOpsError(
                            "ONBOARDING_DOCUMENT_QUALITY_FAILED",
                            "Extraction failed the existing source intake preflight.",
                        )
                    normalized = " ".join(text.split())
                    result = {
                        "status": "TEXT_EXTRACTED",
                        "numeric_omissions": sum(
                            not re.search(r"(?<!\d)" + number + r"(?!\d)", normalized)
                            for number in case["numbers"]
                        ),
                        "missing_whole_fact_spans": sum(
                            " ".join(fact.split()) not in normalized
                            for fact in case["facts"]
                        ),
                        "located_regions": len(metadata.get("visual_regions", [])),
                    }
                except JobOpsError as error:
                    result = {
                        "status": "BLOCKED",
                        "code": error.code,
                        "numeric_omissions": len(case["numbers"]),
                        "missing_whole_fact_spans": len(case["facts"]),
                        "located_regions": 0,
                    }
                result.update(
                    elapsed_ms=round((time.perf_counter() - started) * 1000),
                    model_calls=0,
                    model_cost_usd=0,
                )
                row[mode] = result
            documents.append(row)
    messages = []
    for case in mail_cases():
        started = time.perf_counter()
        adapter = LocalEmlAdapter(case["raw"])
        proposal = propose_status(
            adapter.read_messages()[0],
            [
                {
                    "application_id": "APP-AAAAAAAAAAAA",
                    "external_reference": "REQ-42",
                    "allowed_sender_domains": ["example.test"],
                }
            ],
        )
        messages.append(
            {
                "id": case["id"],
                "expected_status": case["expected_status"],
                "actual_status": proposal["status_proposal"],
                "incorrect_automatic_link": proposal["application_id"] is not None,
                "manual_review_required": proposal["review_status"] == "UNCERTAIN",
                "elapsed_ms": round((time.perf_counter() - started) * 1000),
            }
        )
    summaries = {
        mode: {
            "cases": len(documents),
            "blocked": sum(row[mode]["status"] == "BLOCKED" for row in documents),
            "numeric_omissions": sum(
                row[mode]["numeric_omissions"] for row in documents
            ),
            "missing_whole_fact_spans": sum(
                row[mode]["missing_whole_fact_spans"] for row in documents
            ),
            "median_ms": round(
                statistics.median(row[mode]["elapsed_ms"] for row in documents)
            ),
            "located_regions": sum(row[mode]["located_regions"] for row in documents),
        }
        for mode in ("existing_text", "docling_native")
    }
    report = {
        "verified_at": iso_utc(),
        "environment": {
            "python": platform.python_version(),
            "platform": platform.system(),
            "packages": {
                name: version(name)
                for name in (
                    "docling-slim",
                    "docling-parse",
                    "pypdf",
                    "pdfplumber",
                    "Pillow",
                )
            },
        },
        "synthetic_only": True,
        "model_calls": 0,
        "model_cost_usd": 0,
        "claim_attribution_evaluation": "NOT_RUN",
        "document_summary": summaries,
        "documents": documents,
        "mail_summary": {
            "cases": len(messages),
            "wrong_status_proposals": sum(
                x["expected_status"] != x["actual_status"] for x in messages
            ),
            "incorrect_automatic_links": sum(
                x["incorrect_automatic_link"] for x in messages
            ),
            "manual_review_required": sum(
                x["manual_review_required"] for x in messages
            ),
            "authenticated_connector_recall": "NOT_MEASURED_NO_MAILBOX_CONNECTOR",
        },
        "messages": messages,
    }
    destination = ROOT / "reports" / "workbench-evaluation.json"
    destination.parent.mkdir(exist_ok=True)
    destination.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "document_summary": summaries,
                "mail_summary": report["mail_summary"],
                "model_calls": 0,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
