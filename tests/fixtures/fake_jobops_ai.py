from __future__ import annotations

import json
import sys


request = json.load(sys.stdin)
if request.get("task") == "JOBFLOW_FORM_SEMANTIC_REVIEW_V1":
    fields = []
    for item in request.get("page", {}).get("fields", []):
        classification = str(item.get("deterministic_classification", ""))
        role = "other"
        if classification == "file_upload_stop": role = "material_upload"
        elif classification == "final_submit_stop": role = "final_submit"
        elif classification == "navigation_control_stop": role = "navigation"
        elif classification == "work_authorization_stop": role = "work_authorization"
        elif classification in {"legal_declaration_stop", "signature_stop", "voluntary_disclosure_stop", "sensitive_review"}: role = "legal_or_sensitive"
        elif classification == "private_fixed": role = "identity"
        fields.append({
            "control_ref": item.get("control_ref"),
            "semantic_role": role,
            "reason": "The role is based only on the sanitized prompt and deterministic JobFlow classification.",
        })
    json.dump({
        "schema_version": 1,
        "selected_tool": "jobflow.inspect_application_form",
        "summary": "The current application controls were interpreted without receiving any entered values.",
        "fields": fields,
    }, sys.stdout)
    raise SystemExit(0)
if request.get("task") == "JOBFLOW_APPLICATION_MATERIAL_DECISION_V1":
    claim_ids = [
        str(item.get("claim_id")) for item in request.get("approved_claims", [])
        if isinstance(item, dict) and item.get("claim_id")
    ]
    json.dump({
        "schema_version": 1,
        "selected_tool": "jobflow.plan_resume_changes",
        "ranked_claim_ids": claim_ids,
        "summary": "The approved claims were ranked against the supplied job requirements.",
    }, sys.stdout)
    raise SystemExit(0)
if request.get("task") in {"JOBFLOW_APPLICATION_OPERATOR_TURN_V2", "JOBFLOW_NEW_JOB_OPERATOR_TURN_V2"}:
    new_job = request.get("task") == "JOBFLOW_NEW_JOB_OPERATOR_TURN_V2"
    discovery = new_job and request.get("current_task_state", {}).get("stage") == "JOB_DISCOVERY"
    json.dump({
        "schema_version": 1,
        "status": "READY",
        "summary": "The job is ready for a bounded AI-directed JobFlow run.",
        "steps": ([{
            "tool": "jobflow.search_official_jobs" if discovery else "jobflow.start_guided_intake",
            "reason": "Discover an official company role in the visible browser." if discovery else "Create the read-only browser lease for this company job.",
            "requires_user_approval": False if discovery else True,
            "expected_status": "AWAITING_JOB_DISCOVERY" if discovery else "GUIDED_INTAKE_PAIRING",
        }] if new_job else [
            {
                "tool": "jobflow.start_user_present_assist",
                "reason": "Start the host-validated Browser Companion lease.",
                "requires_user_approval": True,
                "expected_status": "BROWSER_COMPANION_PAIRING",
            },
        ]),
        "stop_condition": "AWAITING_USER_SUBMIT",
        "final_submit": "USER_ONLY",
        "automatic_retry": False,
    }, sys.stdout)
    raise SystemExit(0)
numbered = request.get("line_numbered_document", [])
line_count = len(numbered)
line_start = int(numbered[0].split("\t", 1)[0]) if numbered else 1
line_end = int(numbered[-1].split("\t", 1)[0]) if numbered else line_start
source_type = request.get("source", {}).get("source_type")
source_text = " ".join(item.split("\t", 1)[1] if "\t" in item else "" for item in numbered).strip()
statement = " ".join(source_text.split())
if "FORCE_FRAGMENT_CANDIDATE" in source_text:
    statement = "Project Lead at Synthetic."
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
        "line_start": line_start,
        "line_end": line_end,
    }] if entity_type else []),
    "candidates": [{
        "statement": statement,
        "category": entity_type or "summary",
        "claim_kind": "achievement" if entity_type else "summary",
        "entity_key": "synthetic-project-1" if entity_type else "",
        "confidence": "HIGH",
        "line_start": line_start,
        "line_end": line_end,
        "reason": "Complete source-grounded statement reconstructed as one entity.",
    }],
}, sys.stdout)
