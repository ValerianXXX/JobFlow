from __future__ import annotations

import argparse
import base64
import json

import pdfplumber


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
            result["page_parts_base64"] = [base64.b64encode(part.encode("utf-8")).decode("ascii") for part in parts]
    print(json.dumps(result, separators=(",", ":")))


if __name__ == "__main__":
    main()
