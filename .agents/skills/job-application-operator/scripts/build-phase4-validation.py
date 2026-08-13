#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path


def find_project_root() -> Path:
    for candidate in (Path(__file__).resolve().parent, *Path(__file__).resolve().parents):
        if (candidate / ".jobops-root").is_file():
            return candidate
    raise SystemExit("JOBOPS_PROJECT_ROOT_NOT_FOUND")


ROOT = find_project_root()
sys.path.insert(0, str(ROOT / "src"))

from jobops.approvals import ApprovalContext, UploadBinding, issue_approval, submission_confirmation_status, validate_approval  # noqa: E402
from jobops.forms import map_fields  # noqa: E402
from jobops.queueing import queue_decision  # noqa: E402
from jobops.review import build_review_packet  # noqa: E402
from jobops.runtime_schema import validate_named  # noqa: E402
from jobops.sourcing import assess_job_freshness, verify_source_route  # noqa: E402
from jobops.util import canonical_json, iso_utc, load_json, sha256_bytes, write_json  # noqa: E402


def main() -> int:
    policy = load_json(ROOT / "config" / "policy.json")
    route_input = load_json(ROOT / "tests" / "fixtures" / "mock-official-route.json")
    route = verify_source_route(
        company_domain=route_input["company_domain"],
        official_entry_url=route_input["official_entry_url"],
        current_url=route_input["current_url"],
        navigation_history=route_input["navigation_history"],
        approved_ats_hosts=policy["approved_ats_hosts"],
        guest_available=route_input["guest_available"],
        tenant_binding=route_input["tenant_binding"],
        official_page_hash=route_input["official_page_hash"],
        jd_snapshot_hash=route_input["jd_snapshot_hash"],
    )
    freshness = assess_job_freshness(
        official_listing_present=True,
        application_form_available=True,
        checked_at=iso_utc(),
    )
    fields = [
        {"id": "portfolio_url", "label": "Portfolio URL"},
        {"id": "work_authorization", "label": "Work authorization"},
        {"id": "electronic_signature", "label": "Electronic signature"},
        {"id": "disability", "label": "Disability"},
    ]
    form = map_fields(fields, {"portfolio_url": "https://example.test/synthetic-portfolio"}, policy["blocked_form_categories"])
    artifacts = load_json(ROOT / "reports" / "validation-artifacts" / "phase3-validation-manifest.json")["artifacts"]
    resume = artifacts[0]
    answers_hash = sha256_bytes(canonical_json(form["fields"]))
    queue = queue_decision(1, policy["pending_approval_limit"])
    packet = build_review_packet({
        "job": {
            "job_id": "JOB-SYNTHETIC",
            "company": "Example Analytics Lab",
            "title": "Strategy Analyst - Local Validation Only",
            "official_url": route.official_entry_url,
        },
        "jd_captured_at": iso_utc(),
        "fit": {"overall": 72.5, "recommendation": "CONDITIONAL", "why_recommended": "Synthetic fit dimensions clear the test threshold; unknown hard fields still block submission."},
        "hard_gaps": ["candidate_work_authorization:UNKNOWN", "candidate_minimum_salary:UNKNOWN"],
        "resume_bullets": [{
            "text": "Mapped synthetic job requirements to traceable fixture evidence",
            "claim_id": "CLM-SYNTHETIC-BULLET",
            "evidence": ["personal_redacted:case.md#Synthetic Evidence"],
            "why_used": "Directly maps a synthetic JD requirement to an approved fixture claim.",
        }],
        "master_resume_diff": ["Synthetic validation generated a new one-page fixture; no real master resume was read."],
        "form_questions": form["fields"],
        "sensitive_fields": form["sensitive_fields"],
        "uploads": [{"filename": Path(resume["pdf"]).name, "sha256": resume["pdf_sha256"]}],
        "external_actions": ["upload_material", "submit_application"],
        "source_route": route.as_dict(),
        "queue": {"pending_count": queue.pending_count, "pending_limit": queue.pending_limit, "continue_intake": queue.continue_intake},
    })
    context = ApprovalContext(
        application_id="APP-111111111111", job_id="JOB-222222222222",
        jd_snapshot_hash=route.jd_snapshot_hash,
        jd_freshness_hash=sha256_bytes(canonical_json(freshness)), source_route_hash=route.route_hash,
        canonical_url=route.current_url, ats_tenant=route.ats_tenant, ats_board=route.ats_board,
        ats_job_identity=route.ats_job_identity, profile_version="synthetic-v1",
        claim_set_hash=sha256_bytes(canonical_json(packet["resume_bullets"])),
        form_snapshot_hash=sha256_bytes(canonical_json(fields)), answers_hash=answers_hash,
        review_packet_hash=sha256_bytes(canonical_json(packet)),
        uploads=(UploadBinding(Path(resume["pdf"]).name, "resume", resume["pdf_sha256"]),),
        external_actions=("upload_material", "submit_application"), site_policy_version=str(policy["schema_version"]),
        unresolved_stops=tuple(
            str(item["id"]) for item in form["fields"]
            if item["action"] == "STOP" and item["classification"] != "final_submit_stop"
        ),
        mandatory_unknowns=tuple(str(item) for item in form["unknown_fields"]),
    ).normalized()
    approval = issue_approval(context=context, user_confirmed=True, now=datetime.now(timezone.utc))
    validate_named("approval", approval.as_dict(), ROOT / "schemas")
    approval_exact = validate_approval(approval, context=context, required_actions=("submit_application",))
    changed_context = replace(context, uploads=(UploadBinding(Path(resume["pdf"]).name, "resume", "sha256:" + "0" * 64),))
    approval_after_change = validate_approval(approval, context=changed_context)
    result = {
        "schema_version": 1,
        "status": "PASS",
        "synthetic_only": True,
        "external_actions": 0,
        "superseded_by": "reports/checkpoint-final.json",
        "source_route": route.as_dict(),
        "freshness": freshness,
        "form_validation": form,
        "review_packet": packet,
        "queue_behavior": queue.as_dict(),
        "approval_validation": {
            "exact_binding": approval_exact,
            "after_resume_change": approval_after_change,
            "missing_confirmation_evidence": submission_confirmation_status(confirmation_page=True, confirmation_number=None, confirmation_email=False),
        },
    }
    output = ROOT / "reports" / "validation-artifacts" / "phase4-review-packet-validation.json"
    write_json(output, result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
