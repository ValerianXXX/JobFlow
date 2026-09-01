from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[3]
SRC = PROJECT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from jobops.document_builder import (  # noqa: E402
    export_docx_to_pdf,
    render_pdf_to_pngs,
    tailor_master_resume,
)
from jobops.document_qa import automated_visual_probe  # noqa: E402
from jobops.util import iso_utc, write_json  # noqa: E402


OUTPUT = Path(__file__).resolve().parent
MASTER = PROJECT / "tests" / "fixtures" / "complex-master-resume.docx"
EXPORT = PROJECT / ".agents" / "skills" / "job-application-operator" / "scripts" / "export-docx-pdf.ps1"
PDFTOPPM = (
    Path.home()
    / ".cache"
    / "codex-runtimes"
    / "codex-primary-runtime"
    / "dependencies"
    / "native"
    / "poppler"
    / "Library"
    / "bin"
    / "pdftoppm.exe"
)


def _claim(index: int, wording: str) -> dict[str, object]:
    now = datetime.now(timezone.utc)
    return {
        "claim_id": f"CLM-SYNTHETIC-RELEASE-{index:02d}",
        "raw_fact": wording,
        "allowed_wording": [wording],
        "forbidden_wording": ["real applicant", "real employer outcome"],
        "responsibility_boundary": {
            "candidate": "synthetic fixture only",
            "team": "synthetic fixture review",
            "ai": "synthetic fixture generation",
        },
        "evidence": [{"kind": "synthetic_fixture", "value": 1}],
        "source_refs": [
            {
                "source_id": "personal_redacted",
                "relative_path": "synthetic/case.md",
                "fingerprint": "sha256:" + "a" * 64,
            }
        ],
        "approved_for_external": True,
        "sensitivity": "personal-redacted",
        "last_verified_at": iso_utc(now),
        "expires_at": iso_utc(now + timedelta(days=7)),
    }


def main() -> int:
    if not MASTER.is_file() or not EXPORT.is_file() or not PDFTOPPM.is_file():
        raise SystemExit("RELEASE_DOCUMENT_QA_TOOLCHAIN_MISSING")

    values = {
        "CANDIDATE_NAME": "SYNTHETIC CANDIDATE",
        "TARGET_ROLE": "Strategy Analyst - Local Validation Only",
        "SUMMARY": "Completed evidence-grounded analysis using synthetic data only.",
        "EXPERIENCE_BULLET": "Mapped synthetic job requirements to traceable fixture evidence.",
        "PROJECT": "Built a local-only validation workflow for structured resume tailoring.",
        "SKILLS": "Evidence analysis, Python, and SQL using synthetic inputs.",
        "EDUCATION": "Synthetic university fixture - not a real credential.",
    }
    claims = [
        _claim(index, values[key])
        for index, key in enumerate(
            ("SUMMARY", "EXPERIENCE_BULLET", "PROJECT", "SKILLS", "EDUCATION"),
            start=1,
        )
    ]
    docx = OUTPUT / "complex-master-resume.docx"
    pdf = OUTPUT / "complex-master-resume.pdf"
    render_dir = OUTPUT / ".render-work"
    render_dir.mkdir(exist_ok=True)
    tailor_master_resume(
        MASTER,
        docx,
        replacements=values,
        claims=claims,
        synthetic=True,
    )
    export_docx_to_pdf(docx, pdf, EXPORT)
    rendered = render_pdf_to_pngs(pdf, render_dir, str(PDFTOPPM))
    pages: list[Path] = []
    for index, source in enumerate(rendered, start=1):
        destination = OUTPUT / f"complex-master-resume-page-{index}.png"
        source.replace(destination)
        pages.append(destination)
    for stale in OUTPUT.glob("complex-master-resume-page-*.png"):
        if stale not in pages:
            stale.unlink()
    render_dir.rmdir()
    probe = automated_visual_probe(pages)
    write_json(OUTPUT / "automated-render-probe.json", probe)
    print(json.dumps(probe, ensure_ascii=False, indent=2))
    print("A human or Codex visual reviewer must inspect every tracked PNG and create visual-review.json.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
