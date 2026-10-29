from __future__ import annotations

import re
import base64
import json
import subprocess
import sys
import zipfile
from collections import Counter
from copy import deepcopy
from xml.etree import ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import JobOpsError
from .util import canonical_json, iso_utc, parse_iso, sha256_bytes, sha256_file


@dataclass(frozen=True)
class DocumentQAResult:
    status: str
    docx_name: str
    pdf_name: str
    docx_hash: str
    pdf_hash: str
    page_count: int
    page_limit: int
    ats_text_present: bool
    placeholders: tuple[str, ...]
    text_difference_ratio: float
    visual_record_hash: str
    visual_inspection: str
    reading_order_issue_count: int = 0

    def as_dict(self) -> dict[str, object]:
        return {**self.__dict__, "placeholders": list(self.placeholders)}


def extract_docx_text(path: Path) -> str:
    namespace = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    parts: list[str] = []
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        stories = sorted(name for name in names if name.startswith("word/header") and name.endswith(".xml"))
        stories += ["word/document.xml"]
        stories += sorted(name for name in names if name.startswith("word/footer") and name.endswith(".xml"))
        for name in stories:
            if name not in names:
                continue
            root = ET.fromstring(archive.read(name))
            for paragraph in root.findall(".//w:p", namespace):
                text = "".join(node.text or "" for node in paragraph.findall(".//w:t", namespace))
                if text.strip():
                    parts.append(text)
    return "\n".join(parts)


def expected_pdf_text(path: Path, page_count: int) -> tuple[str, list[str]]:
    """Account for uniform repeated headers/footers and simple page fields.

    Only the unambiguous single-section case is expanded. Other section/field
    arrangements retain the conservative original comparison. No body text is
    removed and no observed PDF value is used to invent an expected value.
    """
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    w = "{" + ns["w"] + "}"
    original = extract_docx_text(path)
    with zipfile.ZipFile(path) as archive:
        body = ET.fromstring(archive.read("word/document.xml"))
        headers = [name for name in archive.namelist() if re.fullmatch(r"word/header\d+\.xml", name)]
        footers = [name for name in archive.namelist() if re.fullmatch(r"word/footer\d+\.xml", name)]
        if len(body.findall(".//w:sectPr", ns)) != 1 or body.find(".//w:titlePg", ns) is not None or len(headers) > 1 or len(footers) > 1:
            return original, []
        if "word/settings.xml" in archive.namelist():
            settings = ET.fromstring(archive.read("word/settings.xml"))
            if settings.find("w:evenAndOddHeaders", ns) is not None:
                return original, []
        stories = [ET.fromstring(archive.read(name)) for name in headers + footers]
        for story in stories:
            if story.find(".//w:instrText", ns) is not None:
                return original, []
            if any(field.get(w + "instr", "").strip() not in {"PAGE", "NUMPAGES"} for field in story.findall(".//w:fldSimple", ns)):
                return original, []
        body_text = ["".join(node.text or "" for node in paragraph.findall(".//w:t", ns)) for paragraph in body.findall(".//w:p", ns)]
        expected_stories = []
        for page in range(1, page_count + 1):
            for original_story in stories:
                story = deepcopy(original_story)
                for parent in story.iter():
                    for field in list(parent):
                        if field.tag == w + "fldSimple":
                            value = str(page if field.get(w + "instr", "").strip() == "PAGE" else page_count)
                            replacement = ET.Element(w + "r")
                            ET.SubElement(replacement, w + "t").text = value
                            parent.insert(list(parent).index(field), replacement)
                            parent.remove(field)
                expected_stories.extend("".join(node.text or "" for node in p.findall(".//w:t", ns)) for p in story.findall(".//w:p", ns))
        return "\n".join(body_text + expected_stories), [text for text in expected_stories if text.strip()]


def repeated_story_issues(stories: list[str], pdf_text: str) -> int:
    separator = "\x1f"
    stream = separator + separator.join(normalized_tokens(pdf_text)) + separator
    needed = Counter(separator + separator.join(normalized_tokens(text)) + separator for text in stories)
    return sum(max(0, count - stream.count(story)) for story, count in needed.items())


def _bounded_pdf_result(
    text: str,
    page_count: int,
    *,
    page_limit: int | None,
    character_limit: int | None,
) -> tuple[str, int]:
    if page_limit is not None and (page_count < 1 or page_count > page_limit):
        raise JobOpsError(
            "PDF_PAGE_LIMIT_EXCEEDED",
            "The PDF page count exceeds the bounded local extraction limit.",
            page_count=page_count,
            page_limit=page_limit,
        )
    if character_limit is not None and len(text) > character_limit:
        raise JobOpsError(
            "PDF_TEXT_LIMIT_EXCEEDED",
            "The extracted PDF text exceeds the bounded complete-analysis limit.",
            character_limit=character_limit,
        )
    return text, page_count


def extract_pdf_text(
    path: Path,
    *,
    layout: bool = False,
    page_limit: int | None = None,
    character_limit: int | None = None,
    require_complete_pages: bool = False,
) -> tuple[str, int]:
    if page_limit is not None and page_limit < 1:
        raise JobOpsError("PDF_PAGE_LIMIT_INVALID", "The PDF extraction page limit must be positive.")
    if character_limit is not None and character_limit < 1:
        raise JobOpsError("PDF_TEXT_LIMIT_INVALID", "The PDF extraction character limit must be positive.")
    try:
        import pdfplumber
        with pdfplumber.open(str(path)) as pdf:
            page_count = len(pdf.pages)
            if page_limit is not None and (page_count < 1 or page_count > page_limit):
                return _bounded_pdf_result(
                    "", page_count, page_limit=page_limit, character_limit=character_limit,
                )
            parts: list[str] = []
            used = 0
            for page in pdf.pages:
                part = page.extract_text(layout=layout) or ""
                used += len(part) + (1 if parts else 0)
                if character_limit is not None and used > character_limit:
                    raise JobOpsError(
                        "PDF_TEXT_LIMIT_EXCEEDED",
                        "The extracted PDF text exceeds the bounded complete-analysis limit.",
                        character_limit=character_limit,
                    )
                parts.append(part)
            if require_complete_pages:
                from .document_coverage import require_complete_page_text
                require_complete_page_text(parts)
            return "\n".join(parts), page_count
    except ModuleNotFoundError:
        from .util import project_root
        project = project_root()
        candidates = [
            Path(sys.executable),
            Path.home() / ".cache" / "codex-runtimes" / "codex-primary-runtime" / "dependencies" / "python" / "python.exe",
        ]
        helper = project / ".agents" / "skills" / "job-application-operator" / "scripts" / "extract-pdf-text.py"
        for interpreter in candidates:
            if not interpreter.is_file():
                continue
            command = [str(interpreter), str(helper), str(path)]
            if layout:
                command.append("--layout")
            if require_complete_pages:
                command.append("--page-parts")
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=45,
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if completed.returncode == 0:
                try:
                    result = json.loads(completed.stdout)
                    if require_complete_pages:
                        from .document_coverage import require_complete_page_text
                        parts = result.get("page_parts_base64")
                        if not isinstance(parts, list) or len(parts) != int(result["page_count"]):
                            raise JobOpsError("PDF_PAGE_COVERAGE_UNAVAILABLE", "The local PDF helper did not provide complete page coverage; no partial source was analyzed.")
                        decoded = [base64.b64decode(part, validate=True).decode("utf-8") for part in parts]
                        if "\n".join(decoded) != base64.b64decode(result["text_base64"]).decode("utf-8"):
                            raise JobOpsError("PDF_PAGE_COVERAGE_UNAVAILABLE", "The local PDF helper returned inconsistent page coverage.")
                        require_complete_page_text(decoded)
                    return _bounded_pdf_result(
                        base64.b64decode(result["text_base64"]).decode("utf-8"),
                        int(result["page_count"]),
                        page_limit=page_limit,
                        character_limit=character_limit,
                    )
                except JobOpsError:
                    raise
                except Exception:
                    continue
        raise JobOpsError(
            "PDF_TEXT_EXTRACTION_FAILED",
            "PDF text extraction requires the bundled local PDF runtime; interactive Office conversion is never attempted.",
        )
    except JobOpsError:
        raise
    except Exception as exc:
        raise JobOpsError(
            "PDF_TEXT_EXTRACTION_FAILED",
            "The PDF could not be parsed by the bounded local extraction runtime.",
        ) from exc


def normalized_tokens(text: str) -> list[str]:
    return re.findall(r"[A-Za-z0-9+.#/-]+|[\u4e00-\u9fff]", text.casefold())


def compare_text(docx_text: str, pdf_text: str) -> float:
    left = normalized_tokens(docx_text)
    right = normalized_tokens(pdf_text)
    if not left and not right:
        return 0.0
    left_counts, right_counts = Counter(left), Counter(right)
    common = sum((left_counts & right_counts).values())
    denominator = max(sum(left_counts.values()), sum(right_counts.values()))
    return round(1.0 - common / denominator, 4)


def reading_order_issues(docx_text: str, pdf_text: str) -> int:
    """Count substantive paragraphs broken in the PDF's linear text stream.

    Word counts alone accept swapped ownership or interleaved columns. Require
    each paragraph of eight or more tokens to remain contiguous, ignoring line
    wrapping and the same punctuation differences as the existing token check.
    Short headings/contact labels still use the existing checks. Return counts
    only: private paragraphs must not become diagnostic text.
    """
    separator = "\x1f"
    stream = separator + separator.join(normalized_tokens(pdf_text)) + separator
    issues = 0
    for paragraph in docx_text.splitlines():
        tokens = normalized_tokens(paragraph)
        if len(tokens) >= 8 and separator + separator.join(tokens) + separator not in stream:
            issues += 1
    return issues


def find_placeholders(text: str) -> tuple[str, ...]:
    patterns = r"\{\{[^{}]+\}\}|\bUNKNOWN\b|secure-ref:[A-Za-z0-9_-]+|\[\[[^\]]+\]\]|JobOps|evidence-gated"
    return tuple(sorted(set(re.findall(patterns, text, flags=re.IGNORECASE))))


def build_visual_record(page_pngs: list[Path], page_results: list[dict[str, Any]], *, reviewer_type: str, rendered_at: str | None = None, reviewed_at: str | None = None) -> dict[str, Any]:
    if len(page_pngs) != len(page_results):
        raise JobOpsError("VISUAL_RECORD_INVALID", "Every rendered page needs exactly one inspection result.")
    pages = []
    for path, result in zip(page_pngs, page_results):
        pages.append({
            "page": len(pages) + 1, "filename": path.name, "render_sha256": sha256_file(path),
            "result": result.get("result"), "reasons": list(result.get("reasons", [])),
        })
    return {
        "schema_version": 1, "rendered_at": rendered_at or iso_utc(), "reviewed_at": reviewed_at or iso_utc(),
        "reviewer_type": reviewer_type, "pages": pages,
    }


def automated_visual_probe(page_pngs: list[Path], *, minimum_width: int = 600, minimum_height: int = 800) -> dict[str, Any]:
    """Create tamper-evident render evidence from conservative image checks.

    This is intentionally labeled an automated probe, not a human visual review.
    The release process still requires an actual reviewer to inspect every page.
    """
    from PIL import Image, ImageStat

    results: list[dict[str, Any]] = []
    for page in page_pngs:
        reasons: list[str] = []
        result = "PASS"
        try:
            with Image.open(page) as image:
                image.verify()
            with Image.open(page).convert("L") as gray:
                width, height = gray.size
                extrema = gray.getextrema()
                standard_deviation = float(ImageStat.Stat(gray).stddev[0])
                if width < minimum_width or height < minimum_height:
                    result, reasons = "FAIL", ["Rendered page resolution is below the QA threshold."]
                elif extrema == (255, 255) or standard_deviation < 2.0:
                    result, reasons = "FAIL", ["Rendered page appears blank or nearly blank."]
                else:
                    reasons = [f"Readable raster detected at {width}x{height}; blank-page and corruption probes passed."]
        except Exception:
            result, reasons = "FAIL", ["Rendered page cannot be decoded as an image."]
        results.append({"result": result, "reasons": reasons})
    return build_visual_record(page_pngs, results, reviewer_type="automated_render_probe")


def validate_visual_record(record: dict[str, Any], page_pngs: list[Path]) -> str:
    required = {"schema_version", "rendered_at", "reviewed_at", "reviewer_type", "pages"}
    if set(record) != required or record.get("schema_version") != 1:
        raise JobOpsError("VISUAL_RECORD_INVALID", "Visual evidence must use the complete versioned record, not a PASS string.")
    if record["reviewer_type"] not in {"codex_visual", "human", "independent_qa", "automated_render_probe"}:
        raise JobOpsError("VISUAL_RECORD_INVALID", "Visual reviewer type is not recognized.")
    try:
        if parse_iso(record["reviewed_at"]) < parse_iso(record["rendered_at"]):
            raise JobOpsError("VISUAL_RECORD_INVALID", "Visual review cannot predate rendering.")
    except JobOpsError:
        raise
    except Exception as exc:
        raise JobOpsError("VISUAL_RECORD_INVALID", "Visual timestamps are invalid.") from exc
    if len(record["pages"]) != len(page_pngs) or not page_pngs:
        raise JobOpsError("VISUAL_RECORD_INVALID", "Visual record page count does not match actual renders.")
    overall = "PASS"
    for index, (page, path) in enumerate(zip(record["pages"], page_pngs), 1):
        if set(page) != {"page", "filename", "render_sha256", "result", "reasons"}:
            raise JobOpsError("VISUAL_RECORD_INVALID", "Visual page record has missing or extra fields.")
        if page["page"] != index or page["filename"] != path.name or page["render_sha256"] != sha256_file(path):
            raise JobOpsError("VISUAL_RENDER_CHANGED", "Rendered page bytes do not match the reviewed page hash.")
        if page["result"] not in {"PASS", "FAIL"} or not isinstance(page["reasons"], list):
            raise JobOpsError("VISUAL_RECORD_INVALID", "Each page needs a structured PASS/FAIL and reason list.")
        if page["result"] == "FAIL":
            overall = "FAIL"
    return overall


def structural_qa(docx_path: Path, pdf_path: Path, page_pngs: list[Path], *, visual_record: dict[str, Any], page_limit: int) -> DocumentQAResult:
    if page_limit < 1:
        raise JobOpsError("PAGE_LIMIT_INVALID", "Document page limit must be at least one.")
    docx_text = extract_docx_text(docx_path)
    pdf_text, page_count = extract_pdf_text(pdf_path)
    placeholders = find_placeholders(docx_text + "\n" + pdf_text)
    expected_text, stories = expected_pdf_text(docx_path, page_count)
    difference = compare_text(expected_text, pdf_text)
    order_issues = reading_order_issues(docx_text, pdf_text)
    order_issues += repeated_story_issues(stories, pdf_text)
    visual = validate_visual_record(visual_record, page_pngs)
    status = "PASS" if docx_text.strip() and pdf_text.strip() and not placeholders and difference <= 0.08 and not order_issues and len(page_pngs) == page_count and page_count <= page_limit and visual == "PASS" else "FAIL"
    return DocumentQAResult(
        status=status, docx_name=docx_path.name, pdf_name=pdf_path.name,
        docx_hash=sha256_file(docx_path), pdf_hash=sha256_file(pdf_path), page_count=page_count,
        page_limit=page_limit, ats_text_present=bool(docx_text.strip() and pdf_text.strip()),
        placeholders=placeholders, text_difference_ratio=difference,
        visual_record_hash=sha256_bytes(canonical_json(visual_record)), visual_inspection=visual,
        reading_order_issue_count=order_issues,
    )
