#!/usr/bin/env python3
from __future__ import annotations

import base64
import io
import json
import sys

from pypdf import PdfReader


def main() -> int:
    payload = sys.stdin.buffer.read()
    reader = PdfReader(io.BytesIO(payload))
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    metadata = json.dumps(dict(reader.metadata or {}), ensure_ascii=False, default=str)
    result = {
        "text_base64": base64.b64encode(text.encode("utf-8")).decode("ascii"),
        "metadata_base64": base64.b64encode(metadata.encode("utf-8")).decode("ascii"),
        "page_count": len(reader.pages),
    }
    sys.stdout.write(json.dumps(result, ensure_ascii=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
