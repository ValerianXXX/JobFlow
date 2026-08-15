from __future__ import annotations

import json
import shutil
import sys
import tempfile
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Iterator
from unittest import mock


PROJECT = Path(__file__).resolve().parents[1]
SRC = PROJECT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def _synthetic_export_docx_to_pdf(docx_path: Path, pdf_path: Path, _powershell_script: Path) -> None:
    """Create a deterministic test artifact without requiring Microsoft Office."""
    from jobops.util import sha256_file

    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    fingerprint = sha256_file(docx_path)
    pdf_path.write_bytes(
        b"%PDF-1.4\n% JobFlow unit-test document " + fingerprint.encode("ascii") + b"\n%%EOF\n"
    )


def _synthetic_render_pdf_to_pngs(pdf_path: Path, output_dir: Path, _pdftoppm: str) -> list[Path]:
    """Produce a real, non-blank PNG so the visual probe still runs in tests."""
    from PIL import Image, ImageDraw

    output_dir.mkdir(parents=True, exist_ok=True)
    page = output_dir / f"{pdf_path.stem}-1.png"
    image = Image.new("RGB", (800, 1000), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((72, 72, 728, 132), fill="#183244")
    for index in range(8):
        top = 190 + index * 82
        draw.rectangle((92, top, 690 - (index % 3) * 55, top + 18), fill="#57717f")
    image.save(page, format="PNG")
    return [page]


def _synthetic_structural_qa(
    docx_path: Path,
    pdf_path: Path,
    page_pngs: list[Path],
    *,
    visual_record: dict[str, object],
    page_limit: int,
):
    """Stand in only for the Office/PDF integration already covered by focused tests."""
    from jobops.document_qa import DocumentQAResult
    from jobops.util import canonical_json, sha256_bytes, sha256_file

    return DocumentQAResult(
        status="PASS",
        docx_name=docx_path.name,
        pdf_name=pdf_path.name,
        docx_hash=sha256_file(docx_path),
        pdf_hash=sha256_file(pdf_path),
        page_count=len(page_pngs),
        page_limit=page_limit,
        ats_text_present=True,
        placeholders=(),
        text_difference_ratio=0.0,
        visual_record_hash=sha256_bytes(canonical_json(visual_record)),
        visual_inspection="PASS",
    )


@contextmanager
def project_temp() -> Iterator[Path]:
    base = PROJECT / "tests" / ".tmp"
    base.mkdir(parents=True, exist_ok=True)
    path = Path(tempfile.mkdtemp(prefix="jobops-test-", dir=base))
    try:
        # Orchestration tests verify state, encryption, and approval boundaries.
        # Dedicated document tests exercise the real Office subprocess contract.
        with ExitStack() as stack:
            stack.enter_context(mock.patch(
                "jobops.orchestrator.export_docx_to_pdf",
                side_effect=_synthetic_export_docx_to_pdf,
            ))
            stack.enter_context(mock.patch(
                "jobops.orchestrator.render_pdf_to_pngs",
                side_effect=_synthetic_render_pdf_to_pngs,
            ))
            stack.enter_context(mock.patch(
                "jobops.orchestrator._pdftoppm",
                return_value="synthetic-pdftoppm",
            ))
            stack.enter_context(mock.patch(
                "jobops.orchestrator.structural_qa",
                side_effect=_synthetic_structural_qa,
            ))
            yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def fixture_manifest(path: Path) -> Path:
    value = {
        "schema_version": 1,
        "candidate_root_markers": ["vault/marker.md"],
        "sources": [{
            "id": "personal_redacted",
            "classification": "personal-redacted",
            "root_subpath": "vault",
            "markers": ["marker.md"],
            "allowed_prefixes": ["."],
            "external_claim_policy": "approved_claim_only",
        }],
        "readable_extensions": [".md", ".txt", ".json"],
        "hard_excluded_segments": ["raw-attachments", "数据导入区", "cookies"],
        "hard_excluded_filenames": [".env", "credentials.json"],
    }
    write_json(path, value)
    return path


def make_knowledge_root(path: Path, text: str = "# Marker\n") -> Path:
    (path / "vault").mkdir(parents=True, exist_ok=True)
    (path / "vault" / "marker.md").write_text(text, encoding="utf-8")
    return path
