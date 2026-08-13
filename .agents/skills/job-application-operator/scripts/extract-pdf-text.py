from __future__ import annotations

import argparse
import base64
import json

import pdfplumber


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input")
    parser.add_argument("--layout", action="store_true")
    args = parser.parse_args()
    with pdfplumber.open(args.input) as pdf:
        text = "\n".join(page.extract_text(layout=args.layout) or "" for page in pdf.pages)
        result = {"text_base64": base64.b64encode(text.encode("utf-8")).decode("ascii"), "page_count": len(pdf.pages)}
    print(json.dumps(result, separators=(",", ":")))


if __name__ == "__main__":
    main()
