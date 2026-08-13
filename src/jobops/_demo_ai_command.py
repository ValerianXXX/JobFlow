from __future__ import annotations

import json
import sys


def main() -> None:
    request = json.load(sys.stdin)
    numbered = request.get("line_numbered_document", [])
    line_start = int(numbered[0].split("\t", 1)[0]) if numbered else 1
    line_end = int(numbered[-1].split("\t", 1)[0]) if numbered else line_start
    source_text = " ".join(
        item.split("\t", 1)[1] if "\t" in item else ""
        for item in numbered
    ).strip()
    statement = " ".join(source_text.split())
    if statement[-1:] not in ".?!。！？":
        statement += "."
    json.dump(
        {
            "schema_version": 2,
            "entities": [
                {
                    "entity_key": "synthetic-demo-project",
                    "entity_type": "project",
                    "organization": "Synthetic Demo Studio",
                    "role": "Synthetic Workflow Contributor",
                    "start_date": "",
                    "end_date": "",
                    "line_start": line_start,
                    "line_end": line_end,
                }
            ],
            "candidates": [
                {
                    "statement": statement,
                    "category": "project",
                    "claim_kind": "achievement",
                    "entity_key": "synthetic-demo-project",
                    "confidence": "HIGH",
                    "line_start": line_start,
                    "line_end": line_end,
                    "reason": "Deterministic synthetic demo statement.",
                }
            ],
        },
        sys.stdout,
    )


if __name__ == "__main__":
    main()
