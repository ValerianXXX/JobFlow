"""Render fixed synthetic fixtures for manual visual inspection; no private input."""
from __future__ import annotations
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from jobops.document_builder import export_docx_to_pdf, render_pdf_to_pngs
from jobops.orchestrator import _pdftoppm
from PIL import Image, ImageDraw

spec = importlib.util.spec_from_file_location("corpus", ROOT / "scripts/evaluate-material-corpus.py")
corpus = importlib.util.module_from_spec(spec)
spec.loader.exec_module(corpus)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=False)
    images, records = [], []
    for case in corpus.load_cases():
        source = corpus.CORPUS / case["file"]
        folder = args.output / case["id"]
        folder.mkdir()
        if source.suffix == ".docx":
            converted = folder / "source.pdf"
            export_docx_to_pdf(source, converted, ROOT / ".agents/skills/job-application-operator/scripts/export-docx-pdf.ps1")
            source = converted
        pages = [source] if source.suffix == ".png" else render_pdf_to_pngs(source, folder, _pdftoppm())
        for number, page in enumerate(pages, 1):
            images.append((f"{case['id']} / {number}", page))
            records.append({"case": case["id"], "page": number, "source_sha256": case["sha256"],
                            "render_sha256": hashlib.sha256(page.read_bytes()).hexdigest(),
                            "render": str(page.relative_to(ROOT)).replace("\\", "/")})
    sheets = []
    for index in range(0, len(images), 4):
        sheet = Image.new("RGB", (1680, 2240), "#d9e2eb")
        draw = ImageDraw.Draw(sheet)
        for slot, (label, path) in enumerate(images[index:index+4]):
            image = Image.open(path).convert("RGB")
            image.thumbnail((800, 1060))
            x, y = (slot % 2) * 840 + 20, (slot // 2) * 1120 + 40
            sheet.paste(image, (x, y))
            draw.text((x, y - 24), label, fill="#000000", font_size=18)
        target = args.output / f"inspection-{index//4+1:02d}.png"
        sheet.save(target)
        sheets.append(target.name)
    (args.output / "renders.json").write_text(json.dumps({"pages": records, "sheets": sheets, "visual_review": "PENDING"}, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"rendered_pages": len(images), "inspection_sheets": sheets}))


if __name__ == "__main__":
    main()
