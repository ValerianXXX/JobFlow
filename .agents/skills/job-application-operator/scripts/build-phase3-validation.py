#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


def find_project_root() -> Path:
    for candidate in (Path(__file__).resolve().parent, *Path(__file__).resolve().parents):
        if (candidate / ".jobops-root").is_file():
            return candidate
    raise SystemExit("JOBOPS_PROJECT_ROOT_NOT_FOUND")


ROOT = find_project_root()
sys.path.insert(0, str(ROOT / "src"))

from jobops.document_builder import build_cover_letter, build_resume, export_docx_to_pdf, render_pdf_to_pngs  # noqa: E402
from jobops.util import iso_utc, sha256_file, write_json  # noqa: E402


def claim(claim_id: str, wording: str, fact: str) -> dict[str, object]:
    now = datetime.now(timezone.utc)
    evidence_path = ROOT / "tests" / "fixtures" / "synthetic-knowledge" / "case.md"
    return {
        "claim_id": claim_id,
        "raw_fact": fact,
        "allowed_wording": [wording],
        "forbidden_wording": ["unverified real-world outcome"],
        "responsibility_boundary": {
            "candidate": "performed the synthetic fixture action",
            "team": "reviewed the synthetic fixture",
            "ai": "generated synthetic inputs and formatting",
        },
        "evidence": [{"kind": "synthetic_fixture", "value": 1}],
        "source_refs": [{
            "source_id": "personal_redacted",
            "relative_path": "case.md",
            "heading": "Synthetic Evidence",
            "excerpt": "Built a synthetic SQL and Python analysis with documented checks.",
            "fingerprint": sha256_file(evidence_path),
        }],
        "approved_for_external": True,
        "sensitivity": "personal-redacted",
        "last_verified_at": iso_utc(now),
        "expires_at": iso_utc(now + timedelta(days=7)),
    }


def run() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "reports" / "validation-artifacts")
    parser.add_argument("--privacy-scrub", type=Path, required=True)
    parser.add_argument("--pdftoppm", required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    raw = output / "raw"
    renders = output / "renders"
    output.mkdir(parents=True, exist_ok=True)
    raw.mkdir(parents=True, exist_ok=True)
    claims = [
        claim("CLM-SYNTHETIC-SUMMARY", "Completed evidence-gated analysis using synthetic data only", "Synthetic validation summary"),
        claim("CLM-SYNTHETIC-BULLET", "Mapped synthetic job requirements to traceable fixture evidence", "Synthetic evidence mapping fixture"),
        claim("CLM-SYNTHETIC-SKILL", "Synthetic skill: evidence analysis", "Synthetic skill fixture"),
        claim("CLM-SYNTHETIC-EDU", "Synthetic education fixture - not a real credential", "Synthetic education fixture"),
    ]
    raw_resume = raw / "Synthetic-Strategy-Analyst-Resume.docx"
    raw_letter = raw / "Synthetic-Strategy-Analyst-Cover-Letter.docx"
    resume = output / raw_resume.name
    letter = output / raw_letter.name
    build_resume(
        raw_resume,
        candidate_display_name="SYNTHETIC CANDIDATE",
        target_role="Strategy Analyst - Local Validation Only",
        summary=str(claims[0]["allowed_wording"][0]),
        claims=claims,
        skills=[str(claims[2]["allowed_wording"][0])],
        education=str(claims[3]["allowed_wording"][0]),
        bullet_claim_ids=[str(claims[1]["claim_id"])],
    )
    build_cover_letter(
        raw_letter,
        candidate_display_name="SYNTHETIC CANDIDATE",
        company="Example Analytics Lab",
        target_role="Strategy Analyst - Local Validation Only",
        why_company="Example Analytics Lab is a synthetic fixture company (https://example.com/careers, accessed 2026-08-12).",
        why_role=str(claims[1]["allowed_wording"][0]),
        claims=claims,
    )
    for source, destination in ((raw_resume, resume), (raw_letter, letter)):
        completed = subprocess.run([sys.executable, str(args.privacy_scrub), str(source), "--out", str(destination)], capture_output=True, text=True, timeout=60, check=False)
        if completed.returncode != 0:
            raise RuntimeError(completed.stderr)
    converter = ROOT / ".agents" / "skills" / "job-application-operator" / "scripts" / "export-docx-pdf.ps1"
    artifacts = []
    for docx_path in (resume, letter):
        pdf_path = docx_path.with_suffix(".pdf")
        export_docx_to_pdf(docx_path, pdf_path, converter)
        page_paths = render_pdf_to_pngs(pdf_path, renders / docx_path.stem, args.pdftoppm)
        artifacts.append({
            "docx": str(docx_path),
            "docx_sha256": sha256_file(docx_path),
            "pdf": str(pdf_path),
            "pdf_sha256": sha256_file(pdf_path),
            "rendered_pages": [str(path) for path in page_paths],
        })
    manifest = {
        "schema_version": 1,
        "status": "RENDERED_AWAITING_VISUAL_INSPECTION",
        "synthetic_only": True,
        "external_actions": 0,
        "claims": [{"claim_id": item["claim_id"], "wording": item["allowed_wording"][0]} for item in claims],
        "artifacts": artifacts,
    }
    write_json(output / "phase3-validation-manifest.json", manifest)
    shutil.rmtree(raw, ignore_errors=True)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
