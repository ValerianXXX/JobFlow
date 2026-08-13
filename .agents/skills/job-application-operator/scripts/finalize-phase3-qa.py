#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def find_project_root() -> Path:
    for candidate in (Path(__file__).resolve().parent, *Path(__file__).resolve().parents):
        if (candidate / ".jobops-root").is_file():
            return candidate
    raise SystemExit("JOBOPS_PROJECT_ROOT_NOT_FOUND")


ROOT = find_project_root()
sys.path.insert(0, str(ROOT / "src"))

from jobops.document_qa import structural_qa  # noqa: E402
from jobops.util import load_json, write_json  # noqa: E402


def bounded(value: str) -> Path:
    path = Path(value)
    path = path if path.is_absolute() else ROOT / path
    resolved = path.resolve(strict=True)
    try:
        resolved.relative_to(ROOT)
    except ValueError as exc:
        raise SystemExit("PROJECT_BOUNDED_QA_INPUT_REQUIRED") from exc
    return resolved


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", default="reports/validation-artifacts/phase3-validation-manifest.json")
    parser.add_argument("--visual-record", required=True, help="Structured reviewer evidence; a PASS string is never accepted.")
    parser.add_argument("--page-limit", type=int, default=2)
    args = parser.parse_args()
    manifest = load_json(bounded(args.manifest))
    visual = load_json(bounded(args.visual_record))
    results = []
    for artifact in manifest["artifacts"]:
        result = structural_qa(
            bounded(artifact["docx"]), bounded(artifact["pdf"]),
            [bounded(value) for value in artifact["rendered_pages"]],
            visual_record=visual, page_limit=args.page_limit,
        )
        results.append(result.as_dict())
    report = {
        "schema_version": 2, "status": "PASS" if all(item["status"] == "PASS" for item in results) else "FAIL",
        "synthetic_only": True, "external_actions": 0, "visual_evidence_source": Path(args.visual_record).name,
        "documents": results,
    }
    output = ROOT / "reports" / "validation-artifacts" / "phase3-document-qa.json"
    write_json(output, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
