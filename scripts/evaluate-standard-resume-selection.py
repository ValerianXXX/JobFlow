"""Render fixed, synthetic approved-fact formatting cases without model calls.

This is not material extraction or an applicant study. Use an explicitly
configured existing local PDF exporter. Every output directory must be new.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from jobops.document_builder import build_standard_resume, export_docx_to_pdf
from jobops.external_claims import build_external_claim_set, claim_review_hash
from jobops.util import canonical_json, sha256_bytes, sha256_file
from pypdf import PdfReader
import pypdfium2 as pdfium


INDICES = (0, 1, 2, 8, 9, 10, 16, 17, 18, 24, 25, 26)
CATEGORIES = ("work", "internship", "project", "education", "skill", "certification")


def fixture(locale):
    claims = []
    for index in range(48):
        number = index + 1
        if locale == "en":
            text = (f"In synthetic exercise {number:02d}, I checked {number + 10} monthly records and prepared "
                    f"{number + 2} traceable review notes for the fictional Harbor team. My responsibility "
                    "was limited to checking the supplied records and documenting discrepancies; the team "
                    "made the final decision. No customer adoption, revenue or independent business outcome "
                    "was measured in this classroom exercise.")
        else:
            text = (f"在合成练习 {number:02d} 中，我核验了 {number + 10} 份月度记录，为虚构的海港团队准备了 "
                    f"{number + 2} 份可追溯核对说明。我的责任仅限于检查提供的记录并记录差异，最终决定由团队作出。"
                    "这项课堂练习没有测量真实客户采用、收入、节省金额或独立业务成果，以上数字只用于验证简历排版是否保留批准原文。")
        claims.append({"claim_id": f"CLM-FORMAT-{number:02d}", "category": CATEGORIES[index // 8],
                       "claim_kind": "summary", "decision": "CONFIRMED", "statement": text})
    evidence_hash = sha256_bytes(canonical_json([c["statement"] for c in claims]))
    for claim in claims:
        claim["source_bindings"] = [{"kind": "MASTER_RESUME", "secure_ref": "secure-ref:SYNTHETIC_FORMAT_SOURCE",
                                    "content_sha256": evidence_hash}]
    approved = build_external_claim_set(onboarding_state_ref="secure-ref:SYNTHETIC_FORMAT_STATE",
        profile_ref="secure-ref:SYNTHETIC_FORMAT_PROFILE",
        master_resume={"secure_ref": "secure-ref:SYNTHETIC_FORMAT_SOURCE", "sha256": evidence_hash, "editable_docx": False},
        claims=claims, allowed_uses=["resume"], expected_review_hash=claim_review_hash(claims, evidence_hash))
    return approved, evidence_hash


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selection", choices=("all", "subset"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    report = {"protocol": "STANDARD_RESUME_SELECTION_V1", "synthetic_only": True,
              "seeded_approvals": True, "model_calls": 0, "real_business_actions": 0,
              "extraction_accuracy_measured": False, "human_review_completed": False,
              "selection": args.selection, "cases": []}
    for locale in ("en", "zh"):
        approved, evidence_hash = fixture(locale)
        chosen = [f"CLM-FORMAT-{index + 1:02d}" for index in INDICES]
        docx = args.output / f"synthetic-{locale}.docx"
        pdf = docx.with_suffix(".pdf")
        started = time.monotonic()
        kwargs = {"included_claim_ids": chosen} if args.selection == "subset" else {}
        preview = build_standard_resume(docx, profile={"candidate_display_name": "Synthetic Formatting Candidate",
            "email": "formatting@example.test"}, claim_set=approved, locale=locale, **kwargs)
        export_docx_to_pdf(docx, pdf, ROOT / ".agents/skills/job-application-operator/scripts/export-docx-pdf.ps1")
        reader = PdfReader(pdf)
        text = "".join("".join(page.extract_text().split()) for page in reader.pages)
        expected = chosen if args.selection == "subset" else [c["claim_id"] for c in approved["claims"]]
        matched = [c["claim_id"] for c in approved["claims"] if "".join(c["allowed_wording"][0].split()) in text]
        with pdfium.PdfDocument(str(pdf)) as document:
            renders = []
            for index in range(len(document)):
                path = args.output / f"synthetic-{locale}-page-{index + 1}.png"
                page = document[index]
                bitmap = page.render(scale=1.5)
                try:
                    bitmap.to_pil().save(path)
                finally:
                    bitmap.close()
                    page.close()
                renders.append({"file": path.name, "sha256": sha256_file(path)})
        result = {"locale": locale, "input_fact_count": len(approved["claims"]),
                  "selected_claim_ids": expected, "rendered_claim_ids": matched,
                  "exact_wording_pass": sorted(expected) == sorted(matched),
                  "source_statements_sha256": evidence_hash, "page_count": len(reader.pages),
                  "docx_sha256": sha256_file(docx), "pdf_sha256": sha256_file(pdf),
                  "preview_claim_count": preview["claim_count"], "rendered_pages": renders,
                  "elapsed_seconds": round(time.monotonic() - started, 3)}
        report["cases"].append(result)
        (args.output / "results.json").write_bytes(canonical_json(report))
        print(json.dumps({"locale": locale, "page_count": result["page_count"],
                          "matched": len(matched), "expected": len(expected), "pass": result["exact_wording_pass"]}), flush=True)
    return 0 if all(c["exact_wording_pass"] for c in report["cases"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
