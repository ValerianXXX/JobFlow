"""Three synthetic material layouts; optionally one real existing-Hermes vision call."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import sys
import time
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from PIL import Image, ImageDraw, ImageFont
from jobops.document_qa import extract_pdf_text
from jobops.multimodal import read_visual
from jobops.ai_connections import WSLHermesCLIEngine

CASES = [
    {"id": "scanned_resume", "lines": ["SYNTHETIC RESUME", "North Star Lab | Analyst | 2023-2025", "Built a dashboard covering 12 stores.", "Reduced reporting time by 18%."], "facts": ["12 stores", "18%"], "owner": "North Star Lab"},
    {"id": "two_column_resume", "lines": ["SYNTHETIC TWO-COLUMN RESUME", "Cedar Studio | Analyst | 2024", "Processed 1,200 synthetic records.", "Bay Project | Team result | 2025", "Team managed a $12,500 budget."], "facts": ["1,200", "$12,500"], "owner": "Team"},
    {"id": "portfolio_screenshot", "lines": ["SYNTHETIC PORTFOLIO SCREENSHOT", "Harbor Project | Team | 2026", "Team tested 24 synthetic scenarios.", "Applicant prepared 6 review reports."], "facts": ["24", "6 review"], "owner": "Applicant"},
]


def main():
    parser = argparse.ArgumentParser(); parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--live-hermes", action="store_true"); parser.add_argument("--model"); parser.add_argument("--provider")
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Preserve prior results: choose a new output")
    font = ImageFont.truetype("arial.ttf", 24)
    images = []
    for case in CASES:
        image = Image.new("RGB", (1100, 650), "white"); draw = ImageDraw.Draw(image)
        for index, line in enumerate(case["lines"]):
            x, y = (55, 55 + index * 85)
            if case["id"] == "two_column_resume" and index >= 3:
                x, y = 570, 140 + (index - 3) * 85
            draw.text((x, y), line, fill="black", font=font)
        images.append(image)
    buffer = io.BytesIO(); images[0].save(buffer, format="PDF", save_all=True, append_images=images[1:])
    data = buffer.getvalue()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    fixture = args.output.with_suffix(".synthetic.pdf"); fixture.write_bytes(data)
    started = time.monotonic(); old, _ = extract_pdf_text(fixture)
    baseline_ms = round((time.monotonic() - started) * 1000)
    result = {"verified_on": datetime.now(timezone.utc).date().isoformat(), "fixture": "Original synthetic raster PDF; scanned, two-column and screenshot layouts", "cases": CASES,
              "baseline": {"method": "existing_local_text_extraction", "elapsed_ms": baseline_ms,
                           "omitted_numeric_facts": sum(fact not in old for case in CASES for fact in case["facts"]),
                           "misattribution": None, "numeric_errors": None, "cost_usd": 0},
              "candidate": {"status": "NOT_RUN"}, "claim_approval": False, "quality_scope": "transcription only; downstream claim approval remains separate"}
    if args.live_hermes:
        if not args.model or not args.provider:
            raise ValueError("Supply the already verified Hermes model/provider names")
        engine = WSLHermesCLIEngine("wsl.exe", "Ubuntu", model=args.model, provider_id=args.provider)
        try:
            text, meta = read_visual(data, ".pdf", engine, allow_configured_provider=True)
            result["candidate"] = {"status": "REAL_HERMES_SYNTHETIC_TRANSCRIPTION", "model": args.model, "provider": args.provider,
                "transcription": text, "evidence": meta, "omitted_numeric_facts": sum(fact not in text for case in CASES for fact in case["facts"]),
                "misattribution": None, "numeric_errors": None, "tool_calls": 0,
                "review_note": "Inspect the retained synthetic source and text before assigning attribution/number error counts."}
        except Exception as error:
            result["candidate"] = {"status": "FAILED", "error_type": type(error).__name__, "error_code": getattr(error, "code", None)}
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"baseline_omitted": result["baseline"]["omitted_numeric_facts"], "candidate_status": result["candidate"]["status"]}))


if __name__ == "__main__":
    main()
