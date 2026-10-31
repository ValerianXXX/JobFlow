from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path
import sys

import pdfplumber

# The fallback interpreter may have PDF dependencies without an installed
# JobFlow package. Load the same reviewed local guard, never a copied policy.
sys.path.insert(0, str(Path(__file__).resolve().parents[4] / 'src'))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input")
    parser.add_argument("--layout", action="store_true")
    parser.add_argument("--page-parts", action="store_true")
    args = parser.parse_args()
    with pdfplumber.open(args.input) as pdf:
        parts = [page.extract_text(layout=args.layout) or "" for page in pdf.pages]
        text = "\n".join(parts)
        result = {"text_base64": base64.b64encode(text.encode("utf-8")).decode("ascii"), "page_count": len(pdf.pages)}
        if args.page_parts:
            from jobops.pdf_content_coverage import inspect_pdf_page_content
            result["page_parts_base64"] = [base64.b64encode(part.encode("utf-8")).decode("ascii") for part in parts]
            result['page_content_reports'] = [inspect_pdf_page_content(page, part) for page, part in zip(pdf.pages, parts)]
    print(json.dumps(result, separators=(",", ":")))


if __name__ == "__main__":
    main()
