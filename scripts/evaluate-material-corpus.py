"""Frozen synthetic extraction comparison; no model or applicant input.

This measures verbatim retention and entity neighborhood, not Claim accuracy.
Images/scans are expected to remain blocked without an OCR/vision route.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
from importlib.metadata import version
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from jobops.errors import JobOpsError
from jobops.onboarding_center import OnboardingCenterService
from jobops.source_quality import document_text_preflight
from jobops.util import iso_utc

CORPUS = ROOT / "tests/fixtures/material-layout-probes-v1"
MANIFEST_LF_SHA256 = "b7a4945984b49e5fc5e73e2ef8469bd07dbc51e92d41b7340675a165e1f36454"


def normalized(value: str) -> str:
    return re.sub(r"\s+", "", value).casefold()


def load_cases(split: str = "all") -> list[dict]:
    if hashlib.sha256((CORPUS / "manifest.json").read_bytes().replace(b"\r\n", b"\n")).hexdigest() != MANIFEST_LF_SHA256:
        raise ValueError("Frozen synthetic manifest changed")
    manifest = json.loads((CORPUS / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("synthetic_only") is not True or len(manifest["cases"]) != 36:
        raise ValueError("Synthetic corpus contract changed")
    for case in manifest["cases"]:
        filename = case["file"]
        if Path(filename).name != filename or Path(filename).suffix not in {".pdf", ".docx", ".png"}:
            raise ValueError("Corpus file is outside the fixed fixture root")
        if hashlib.sha256((CORPUS / filename).read_bytes()).hexdigest() != case["sha256"]:
            raise ValueError("Frozen synthetic input changed")
    return [case for case in manifest["cases"] if split == "all" or case["split"] == split]


@contextmanager
def extraction_mode(mode: str):
    updates = {"JOBFLOW_DOCUMENT_PARSER": "pdf_regions" if mode == "pdf_regions" else "",
               "JOBFLOW_VISUAL_ENABLE": "false", "JOBFLOW_VISUAL_ALLOW_CONFIGURED_PROVIDER": "false"}
    previous = {key: os.environ.get(key) for key in updates}
    os.environ.update(updates)
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


class PathStaging:
    @contextmanager
    def staging_directory(self):
        with tempfile.TemporaryDirectory(prefix="jobflow-synthetic-corpus-") as raw:
            yield Path(raw)


def measure(case: dict, text: str, metadata: dict) -> dict:
    stream = normalized(text)
    facts = [fact for entity in case["entities"] for fact in entity["facts"]]
    return {
        "expected_facts": len(facts),
        "verbatim_facts_retained": sum(normalized(fact["statement"]) in stream for fact in facts),
        "expected_entities": len(case["entities"]),
        "entity_contexts_retained": sum(normalized("\n".join(entity["lines"])) in stream for entity in case["entities"]),
        "regions": len(metadata.get("visual_regions") or []),
        "column_pages": metadata.get("local_parser_column_pages", 0),
        "table_pages": metadata.get("local_parser_table_pages", 0),
        "source_review_required": metadata.get("visual_verification_required", False),
        "conflict_detection": "NOT_MEASURED_REQUIRES_STRUCTURED_ANALYSIS" if case["conflicting_dates_require_review"] else "NOT_APPLICABLE",
    }


def evaluate(split: str) -> dict:
    service = object.__new__(OnboardingCenterService)
    service.onboarding = PathStaging()
    rows = []
    for case in load_cases(split):
        row = {key: case[key] for key in ("id", "language", "layout", "split", "sha256", "text_only_expected")}
        data = (CORPUS / case["file"]).read_bytes()
        for mode in ("existing_text", "pdf_regions"):
            started = time.monotonic()
            with extraction_mode(mode):
                try:
                    text, _, metadata = service._extract_text(data, Path(case["file"]).suffix, "resume")
                    quality = document_text_preflight(text, extension=Path(case["file"]).suffix, page_count=metadata.get("document_page_count"))
                    if quality["status"] == "FAIL":
                        raise JobOpsError("ONBOARDING_DOCUMENT_QUALITY_FAILED", "Synthetic extraction failed preflight.")
                    result = {"status": "EXTRACTED", **measure(case, text, metadata)}
                except JobOpsError as error:
                    result = {"status": "BLOCKED", "code": error.code, **measure(case, "", {})}
            result["elapsed_ms"] = round((time.monotonic() - started) * 1000)
            row[mode] = result
        rows.append(row)
    summaries = {}
    for mode in ("existing_text", "pdf_regions"):
        summaries[mode] = {"cases": len(rows), "extracted": sum(row[mode]["status"] == "EXTRACTED" for row in rows),
            "verbatim_facts_retained": sum(row[mode]["verbatim_facts_retained"] for row in rows),
            "expected_facts": sum(row[mode]["expected_facts"] for row in rows),
            "entity_contexts_retained": sum(row[mode]["entity_contexts_retained"] for row in rows),
            "expected_entities": sum(row[mode]["expected_entities"] for row in rows),
            "elapsed_ms": sum(row[mode]["elapsed_ms"] for row in rows)}
    return {"schema_version": 1, "observed_at": iso_utc(), "synthetic_only": True, "split": split,
            "base_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
            "working_tree_observation": True,
            "manifest_lf_sha256": hashlib.sha256((CORPUS / "manifest.json").read_bytes().replace(b"\r\n", b"\n")).hexdigest(),
            "source_hashes": {name: hashlib.sha256((ROOT / name).read_bytes().replace(b"\r\n", b"\n")).hexdigest()
                              for name in ("src/jobops/_pdf_region_worker.py", "src/jobops/local_document_parser.py", "src/jobops/onboarding_center.py", "scripts/evaluate-material-corpus.py")},
            "packages": {name: version(name) for name in ("pdfplumber", "pypdf", "python-docx", "Pillow")},
            "model_calls": 0, "model_call_cost_usd": 0,
            "claim_accuracy": "NOT_MEASURED", "regions_semantically_verified": False,
            "limitations": ["Local geometry cannot infer semantic table/column meaning.", "Raw extraction is not approved Claim accuracy.",
                            "No OCR in either compared path; image-only cases remain blocked.", "No real applicant or independent user acceptance."],
            "summary": summaries, "cases": rows}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--split", choices=("all", "development", "held_out"), default="development")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit("Refusing to overwrite an observation")
    report = evaluate(args.split)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report["summary"], indent=2))


if __name__ == "__main__":
    main()
