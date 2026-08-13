from __future__ import annotations

import json
import sys


request = json.load(sys.stdin)
numbered = request.get("line_numbered_document", [])
line_count = len(numbered)
source_type = request.get("source", {}).get("source_type")
source_text = " ".join(item.split("\t", 1)[1] if "\t" in item else "" for item in numbered).strip()
statement = " ".join(source_text.split())
if len(statement) < 20:
    statement = f"The applicant provided this source-grounded statement: {statement}"
if statement[-1:] not in ".?!。！？":
    statement += "."
entity_type = "project" if source_type in {"resume", "project_case", "supporting_material"} else None
entity_role = " ".join(statement.strip(".?!。！？").split()[:4])
json.dump({
    "schema_version": 2,
    "entities": ([{
        "entity_key": "synthetic-project-1",
        "entity_type": entity_type,
        "organization": "",
        "role": entity_role,
        "start_date": "",
        "end_date": "",
        "line_start": 1,
        "line_end": max(1, line_count),
    }] if entity_type else []),
    "candidates": [{
        "statement": statement,
        "category": entity_type or "summary",
        "claim_kind": "achievement" if entity_type else "summary",
        "entity_key": "synthetic-project-1" if entity_type else "",
        "confidence": "HIGH",
        "line_start": 1,
        "line_end": max(1, line_count),
        "reason": "Complete source-grounded statement reconstructed as one entity.",
    }],
}, sys.stdout)
