"""Evaluation-only local OCR adapter; one PNG on stdin, never an input path.

Requires an existing trusted Tesseract binary and language data. Does not install
anything, contact a model, generate Claims, or change the application OCR route.
"""
from __future__ import annotations
import base64
import csv
import hashlib
import io
import json
import os
import subprocess
import sys
import time


def run(request, *, execute=subprocess.run):
    if not isinstance(request, dict) or set(request) != {"png_base64", "dpi", "language"}:
        raise ValueError("Invalid synthetic OCR request")
    if type(request["dpi"]) is not int or not 110 <= request["dpi"] <= 300 or request["language"] not in {"eng", "chi_sim+eng"}:
        raise ValueError("Invalid OCR parameters")
    image = base64.b64decode(request["png_base64"], validate=True)
    if not image.startswith(b"\x89PNG\r\n\x1a\n") or len(image) > 6 * 1024 * 1024:
        raise ValueError("Only bounded synthetic PNG input is supported")
    binary = os.getenv("JOBFLOW_OCR_TEST_BINARY", "tesseract")
    started = time.monotonic()
    result = execute([binary, "stdin", "stdout", "-l", request["language"], "--oem", "1", "--psm", "3",
                      "--dpi", str(request["dpi"]), "-c", "tessedit_create_tsv=1"], input=image, capture_output=True, timeout=60)
    if result.returncode:
        # Do not reflect file paths, stderr, or supplied content into diagnostics.
        raise ValueError("The configured local OCR runtime failed")
    if len(result.stdout) > 2 * 1024 * 1024:
        raise ValueError("OCR output exceeded the evaluation limit")
    reader = csv.DictReader(io.StringIO(result.stdout.decode("utf-8")), delimiter="\t")
    if reader.fieldnames != ["level", "page_num", "block_num", "par_num", "line_num", "word_num",
                            "left", "top", "width", "height", "conf", "text"]:
        raise ValueError("Local OCR did not return the requested TSV contract")
    groups = {}
    for word in reader:
        if word.get("level") != "5" or not word.get("text", "").strip():
            continue
        key = tuple(word[field] for field in ("page_num", "block_num", "par_num", "line_num"))
        x, y, w, h = (int(word[field]) for field in ("left", "top", "width", "height"))
        if min(x, y, w, h) < 0:
            raise ValueError("Invalid OCR coordinates")
        row = groups.setdefault(key, {"words": [], "bbox": [x, y, x + w, y + h]})
        row["words"].append(word["text"])
        row["bbox"] = [min(row["bbox"][0], x), min(row["bbox"][1], y),
                       max(row["bbox"][2], x + w), max(row["bbox"][3], y + h)]
    lines = [{"text": " ".join(row["words"]), "bbox_pixels": row["bbox"]} for row in groups.values()]
    return {"status": "TRANSCRIBED_UNVERIFIED", "text": "\n".join(row["text"] for row in lines), "lines": lines,
            "image_sha256": hashlib.sha256(image).hexdigest(), "elapsed_ms": round((time.monotonic() - started) * 1000),
            "engine": "Tesseract", "language": request["language"], "oem": 1, "psm": 3,
            "cloud_calls": 0, "factual_accuracy_verified": False}


def main():
    try:
        data = sys.stdin.buffer.read(9 * 1024 * 1024 + 1)
        if len(data) > 9 * 1024 * 1024:
            raise ValueError("OCR request exceeded the evaluation limit")
        print(json.dumps(run(json.loads(data)), ensure_ascii=False))
    except Exception as error:
        print(json.dumps({"status": "FAILED", "error_type": type(error).__name__}))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
