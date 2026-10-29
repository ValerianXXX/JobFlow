"""Fixed synthetic scan comparison, at most four real adapter requests.

No arbitrary material path. Existing provider only; no retry or Claim creation.
The optional local OCR adapter is a trusted caller-supplied argv JSON, receives
synthetic PNG only on stdin, and has a separate bounded timeout. All observations
are exclusive-create and earlier failures are never overwritten.
"""
from __future__ import annotations
import argparse
import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from jobops.ai_connections import WSLHermesCLIEngine
from jobops.errors import JobOpsError
from jobops.multimodal import read_visual, render_pages
from jobops.util import iso_utc


def load_script(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


corpus = load_script("scan_corpus", "evaluate-material-corpus.py")
previous = load_script("scan_previous", "evaluate-material-preview.py")
ARMS = (("zh-scan", 110), ("zh-scan", 220), ("en-scanned_columns", 220), ("en-scanned_columns", 110))
CALL_LIMIT = 4


class ObservedVision(WSLHermesCLIEngine):
    def __init__(self, route):
        super().__init__("wsl.exe", "Ubuntu", model=route["model"], provider_id=route["provider"])
        self.calls = []

    def _invoke(self, request):
        if len(self.calls) >= CALL_LIMIT:
            raise JobOpsError("EVALUATION_CALL_LIMIT", "The fixed four-request scan budget is exhausted.")
        row = {"task": request.get("task"), "status": "FAILED", "tool_calls": None}
        self.calls.append(row)
        started = time.monotonic()
        try:
            result = super()._invoke(request)
            row.update(status="RETURNED", tool_calls=result["toolSummary"]["calls"])
            return result
        finally:
            row["elapsed_ms"] = round((time.monotonic() - started) * 1000)


def assess(case, text):
    stream = corpus.normalized(text)
    expected = [line for entity in case["entities"] for line in entity["lines"]]
    measurement = corpus.measure(case, text, {})
    result = {key: measurement[key] for key in ("expected_facts", "verbatim_facts_retained",
              "expected_entities", "entity_contexts_retained")}
    result.update(expected_source_lines=len(expected),
                  exact_normalized_lines_retained=sum(corpus.normalized(line) in stream for line in expected),
                  missing_or_changed_lines=[line for line in expected if corpus.normalized(line) not in stream],
                  scope="Verbatim text/adjacent-context retention; not full Claim accuracy or calibrated visual geometry",
                  manual_adjudication_required=True)
    return result


def local_ocr(command, page, language, dpi):
    request = {"png_base64": page["data_url"].split(",", 1)[1], "dpi": dpi, "language": language}
    result = subprocess.run(command, input=json.dumps(request).encode("utf-8"), capture_output=True, timeout=90,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if result.returncode or len(result.stdout) > 2 * 1024 * 1024:
        raise ValueError("Local OCR failed or exceeded its limit")
    value = json.loads(result.stdout)
    if not isinstance(value, dict) or value.get("status") != "TRANSCRIBED_UNVERIFIED" or not isinstance(value.get("text"), str):
        raise ValueError("Invalid local OCR response")
    digest = hashlib.sha256(base64.b64decode(request["png_base64"])).hexdigest()
    if value.get("image_sha256") != digest:
        raise ValueError("OCR response does not match the supplied page")
    if any(len(row["bbox_pixels"]) != 4 or not (0 <= row["bbox_pixels"][0] <= row["bbox_pixels"][2] <= page["width"]
           and 0 <= row["bbox_pixels"][1] <= row["bbox_pixels"][3] <= page["height"]) for row in value.get("lines", [])):
        raise ValueError("OCR boxes extend outside the rendered page")
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--live-hermes", action="store_true")
    parser.add_argument("--local-ocr-command", type=Path)
    args = parser.parse_args()
    checkpoint = args.output.with_suffix(".jsonl")
    if args.output.exists() or checkpoint.exists():
        parser.error("Choose a new output; prior observations are immutable")
    command = None
    if args.local_ocr_command:
        command = json.loads(args.local_ocr_command.read_text(encoding="utf-8-sig"))
        if not isinstance(command, list) or not command or any(not isinstance(s, str) or not s for s in command):
            parser.error("Use a trusted argv array; shell commands are unsupported")
    cases = {case["id"]: case for case in corpus.load_cases()}
    route = previous.public_hermes_route() if args.live_hermes else None
    engine = ObservedVision(route) if route else None
    report = {"observed_at": iso_utc(), "synthetic_only": True, "manifest_lf_sha256": corpus.MANIFEST_LF_SHA256,
              "base_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
              "working_tree_observation": True, "model_route_before": route, "adapter_request_limit": CALL_LIMIT,
              "source_lf_sha256": {name: hashlib.sha256((ROOT / name).read_bytes().replace(b"\r\n", b"\n")).hexdigest()
                                    for name in ("src/jobops/multimodal.py", "scripts/evaluate-scan-resolution.py", "scripts/tesseract-probe-adapter.py")},
              "cost_usd": None, "usage_tokens": None, "provider_api_calls": None,
              "business_completion": "NOT_ATTEMPTED", "original_bytes_unchanged": True, "arms": []}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with checkpoint.open("x", encoding="utf-8") as stream:
        for case_id, dpi in ARMS:
            case = cases[case_id]
            data = (corpus.CORPUS / case["file"]).read_bytes()
            row = {"case": case_id, "pdf_dpi": dpi, "source_sha256": case["sha256"]}
            with patch.dict(os.environ, {"JOBFLOW_VISUAL_PDF_DPI": str(dpi)}):
                started = time.monotonic()
                pages = render_pages(data, ".pdf")
                row["render"] = {"elapsed_ms": round((time.monotonic() - started) * 1000),
                                 "payload_characters": sum(len(page["data_url"]) for page in pages),
                                 "pages": [{key: p[key] for key in ("page", "width", "height", "sha256")} for p in pages]}
                if len(pages) != 1:
                    raise ValueError("The fixed scan probe must have one page")
                if command:
                    try:
                        ocr = local_ocr(command, pages[0], "eng" if case["language"] == "en" else "chi_sim+eng", dpi)
                        row["local_ocr"] = {**ocr, "assessment": assess(case, ocr["text"])}
                    except Exception as error:
                        row["local_ocr"] = {"status": "FAILED", "error_type": type(error).__name__}
                if engine:
                    try:
                        text, meta = read_visual(data, ".pdf", engine, allow_configured_provider=True)
                        if meta["visual_render"]["pages"] != row["render"]["pages"]:
                            raise ValueError("OCR and model did not see the same rendered image")
                        row["vision"] = {"status": "TRANSCRIBED_UNVERIFIED", "text": text, "metadata": meta,
                                         "assessment": assess(case, text)}
                    except JobOpsError as error:
                        row["vision"] = {"status": "BLOCKED", "code": error.code}
                    except Exception as error:
                        row["vision"] = {"status": "FAILED", "error_type": type(error).__name__}
            report["arms"].append(row)
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
            stream.flush()
            print(json.dumps({"case": case_id, "dpi": dpi,
                              "local_ocr": row.get("local_ocr", {}).get("status", "NOT_RUN"),
                              "vision": row.get("vision", {}).get("status", "NOT_RUN")}), flush=True)
    report["adapter_requests"] = engine.calls if engine else []
    if engine:
        try:
            report["model_route_after"] = previous.public_hermes_route()
        except Exception:
            report["model_route_after"] = {"status": "UNAVAILABLE"}
        report["model_route_unchanged"] = report["model_route_after"] == route
    with args.output.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"observations": len(report["arms"]), "model_requests": len(report["adapter_requests"]), "cost_usd": None}))


if __name__ == "__main__":
    main()
