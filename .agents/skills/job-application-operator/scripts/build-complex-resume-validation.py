from __future__ import annotations

import json
import sys
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(PROJECT / "src"))

from jobops.document_builder import tailor_master_resume  # noqa: E402


def main() -> None:
    master = PROJECT / "tests" / "fixtures" / "complex-master-resume.docx"
    output = PROJECT / "reports" / "validation-artifacts" / "Synthetic-Complex-Data-Analyst-Resume.docx"
    wordings = {
        "SUMMARY": "Analyzes synthetic datasets with reproducible methods.",
        "EXPERIENCE_BULLET": "Built a synthetic SQL and Python analysis with documented checks.",
        "PROJECT": "Created a local-only queue capacity simulation.",
        "SKILLS": "Python, SQL, and structured analysis.",
        "EDUCATION": "Completed a synthetic degree fixture.",
    }
    claims = []
    for index, wording in enumerate(wordings.values(), 1):
        claims.append({
            "claim_id": f"CLM-SYNTHETIC-{index}", "raw_fact": wording,
            "allowed_wording": [wording], "forbidden_wording": [],
            "responsibility_boundary": {"candidate": "synthetic fixture", "team": "none", "ai": "fixture generation"},
            "evidence": [{"kind": "fixture", "value": index}],
            "source_refs": [{"source_id": "personal_redacted", "relative_path": "tests/fixtures/synthetic-knowledge/case.md", "fingerprint": "sha256:" + "a" * 64}],
            "approved_for_external": True, "lifecycle_status": "approved", "sensitivity": "synthetic",
            "last_verified_at": "2026-08-12T00:00:00Z", "expires_at": "2099-01-01T00:00:00Z",
        })
    diff = tailor_master_resume(
        master, output,
        replacements={"CANDIDATE_NAME": "Synthetic Candidate", "TARGET_ROLE": "Data Analyst", **wordings},
        claims=claims, synthetic=True,
    )
    report = {
        "status": "TAILORED_COPY_CREATED",
        "master": "tests/fixtures/complex-master-resume.docx",
        "output": "reports/validation-artifacts/Synthetic-Complex-Data-Analyst-Resume.docx",
        "synthetic": True,
        "diff": diff,
        "real_external_actions": 0,
    }
    (PROJECT / "reports" / "validation-artifacts" / "complex-resume-diff.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
