from __future__ import annotations

import json
import threading
import unittest

from jobops.ai_operator import (
    analyze_application_form_semantics,
    application_operator_context,
    operator_execution_trace,
    operator_public_manifest,
    plan_application,
    plan_new_job,
    rank_application_claims,
    resolve_new_job_command,
)
from jobops.ai_runtime import AIAnalysisEngine
from jobops.errors import JobOpsError
from jobops.onboarding_center import OnboardingCenterService


class OperatorEngine(AIAnalysisEngine):
    ready = True

    def __init__(self, result: dict[str, object]) -> None:
        self.result = result
        self.request: dict[str, object] | None = None

    def public_status(self) -> dict[str, object]:
        return {
            "status": "READY", "structured_capability_status": "VERIFIED",
            "provider": "SYNTHETIC_AI", "display_name": "Synthetic AI",
        }

    def execute_structured_task(self, payload: dict[str, object]) -> object:
        self.request = payload
        return self.result


class WrappedOperatorEngine(OperatorEngine):
    """Match the safety envelope returned by Hermes and other Agent adapters."""

    def execute_structured_task(self, payload: dict[str, object]) -> object:
        self.request = payload
        return {
            "ok": True,
            "status": "ok",
            "toolSummary": {"calls": 0, "tools": []},
            "result": {"content": "```json\n" + json.dumps(self.result) + "\n```"},
        }


def displayed_packet() -> dict[str, object]:
    return {
        "status": "APPROVED", "application_status": "APPROVED",
        "application_id": "APP-ABCDEF123456", "stopped_fields": 2,
        "job_summary": {"company": "Example", "title": "Credit Analyst", "location": "New York"},
        "field_resolution": {
            "unresolved_count": 0, "resolved_count": 5, "separate_action_gate_count": 1,
            "private_value_for_test": "PRIVATE-EMAIL-VALUE-FOR-TEST",
        },
        "packet": {
            "source_route": {"provider": "company", "route_kind": "company_official", "current_url": "https://secret.invalid/path"},
            "execution_plan": {"actions": [{"value": "PRIVATE ANSWER MUST NOT LEAK"}]},
            "material_manifest": [{"purpose": "resume", "secure_ref": "secure-ref:PRIVATE"}],
        },
    }


def valid_result() -> dict[str, object]:
    return {
        "schema_version": 1, "status": "READY",
        "summary": "The reviewed application is ready for a bounded user-present run.",
        "steps": [
            {
                "tool": "jobflow.inspect_application_form",
                "reason": "Re-read the current form before filling approved values.",
                "requires_user_approval": False,
                "expected_status": "FORM_INSPECTED",
            },
            {
                "tool": "jobflow.prepare_fill_plan",
                "reason": "Bind only approved answers and materials to current controls.",
                "requires_user_approval": True,
                "expected_status": "AWAITING_USER_SUBMIT",
            },
            {
                "tool": "jobflow.start_user_present_assist",
                "reason": "Start the host-validated user-present browser lease.",
                "requires_user_approval": True,
                "expected_status": "BROWSER_COMPANION_PAIRING",
            },
        ],
        "stop_condition": "AWAITING_USER_SUBMIT", "final_submit": "USER_ONLY",
        "automatic_retry": False,
    }


class AIOperatorTests(unittest.TestCase):
    def test_one_line_command_extracts_one_company_url_without_sending_it_to_ai(self) -> None:
        command, url = resolve_new_job_command(
            "帮我处理这个岗位 https://careers.example.test/jobs/credit-analyst?source=operator"
        )
        self.assertEqual(command, "帮我处理这个岗位")
        self.assertEqual(url, "https://careers.example.test/jobs/credit-analyst?source=operator")
        with self.assertRaises(JobOpsError) as ambiguous:
            resolve_new_job_command(
                "Handle https://one.example.test/job and https://two.example.test/job"
            )
        self.assertEqual(ambiguous.exception.code, "AI_OPERATOR_JOB_URL_AMBIGUOUS")
        with self.assertRaises(JobOpsError) as conflict:
            resolve_new_job_command(
                "Handle https://one.example.test/job", "https://two.example.test/job"
            )
        self.assertEqual(conflict.exception.code, "AI_OPERATOR_JOB_URL_CONFLICT")

    def test_form_semantics_explain_exact_controls_without_answers_or_authority(self) -> None:
        engine = OperatorEngine({
            "schema_version": 1,
            "summary": "This page collects identity and ends at a user-only submit control.",
            "fields": [
                {"control_ref": "CTL-AAAAAAAAAAAA", "semantic_role": "identity", "reason": "The prompt asks for a first name."},
                {"control_ref": "CTL-BBBBBBBBBBBB", "semantic_role": "final_submit", "reason": "This is the final submit control."},
            ],
        })
        result = analyze_application_form_semantics(engine, form_analysis={
            "provider": "company", "step_kind": "MY_INFORMATION",
            "fields": [
                {"control_ref": "CTL-AAAAAAAAAAAA", "control_type": "text", "required": True,
                 "classification": "private_fixed", "answer_key": "first_name", "display_label": "First Name", "display_options": []},
                {"control_ref": "CTL-BBBBBBBBBBBB", "control_type": "button", "required": False,
                 "classification": "final_submit_stop", "answer_key": "UNKNOWN", "display_label": "Submit", "display_options": []},
            ],
        })
        self.assertEqual(result["classification_changes_accepted"], 0)
        self.assertEqual(result["answers_accepted"], 0)
        self.assertEqual(result["browser_actions"], 0)
        self.assertEqual(result["task_state_hash"], engine.request["current_task_state_hash"])
        self.assertFalse(engine.request["current_task_state"]["entered_values_exposed"])
        serialized = json.dumps(engine.request, ensure_ascii=False)
        self.assertNotIn("PRIVATE ANSWER", serialized)
        self.assertIn("Deterministic JobFlow classifications", serialized)

    def test_operator_receives_redacted_context_and_returns_allowlisted_plan(self) -> None:
        engine = OperatorEngine(valid_result())
        plan = plan_application(engine, displayed_packet())
        self.assertEqual(plan["status"], "READY")
        self.assertEqual(plan["final_submit"], "USER_ONLY")
        self.assertFalse(plan["automatic_retry"])
        self.assertEqual(plan["real_external_actions"], 0)
        serialized = json.dumps(engine.request, ensure_ascii=False)
        self.assertNotIn("PRIVATE ANSWER MUST NOT LEAK", serialized)
        self.assertNotIn("PRIVATE-EMAIL-VALUE-FOR-TEST", serialized)
        self.assertNotIn("secure-ref:", serialized)
        self.assertNotIn("https://secret.invalid/path", serialized)
        self.assertEqual(plan["task_state_hash"], engine.request["current_task_state_hash"])
        manifest = engine.request["jobflow_operating_manifest"]
        self.assertEqual(manifest["decision_loop"]["task_state_delivery"], "EVERY_AI_DECISION")
        self.assertEqual(manifest["decision_loop"]["continuation"], "EVENT_DRIVEN_UNTIL_USER_GATE")
        self.assertTrue(all(item["description"] and item["result"] for item in manifest["tools"]))
        self.assertTrue(all(item["execution_mode"] in {"HOST_COMMAND", "PIPELINE_STAGE", "USER_HANDOFF"} for item in manifest["tools"]))

    def test_operator_rejects_submit_tool_and_weakened_boundary(self) -> None:
        forbidden = valid_result()
        forbidden["steps"] = [{
            "tool": "jobflow.submit_application", "reason": "Submit it.",
            "requires_user_approval": False, "expected_status": "SUBMITTED",
        }]
        with self.assertRaises(JobOpsError) as tool_error:
            plan_application(OperatorEngine(forbidden), displayed_packet())
        self.assertEqual(tool_error.exception.code, "AI_OPERATOR_TOOL_FORBIDDEN")

        weakened = valid_result()
        weakened["final_submit"] = "AI_ALLOWED"
        with self.assertRaises(JobOpsError) as boundary_error:
            plan_application(OperatorEngine(weakened), displayed_packet())
        self.assertEqual(boundary_error.exception.code, "AI_OPERATOR_BOUNDARY_REJECTED")

    def test_ai_can_select_approved_page_write_only_inside_fresh_user_present_gate(self) -> None:
        result = valid_result()
        result["steps"].insert(2, {
            "tool": "jobflow.apply_approved_page",
            "reason": "Ask the host to apply only the current hash-bound fields and approved materials.",
            "requires_user_approval": True,
            "expected_status": "AWAITING_USER_SUBMIT",
        })
        with self.assertRaises(JobOpsError) as missing_gate:
            plan_application(OperatorEngine(result), displayed_packet())
        self.assertEqual(missing_gate.exception.code, "AI_OPERATOR_EXTERNAL_ACTION_UNAUTHORIZED")

        plan = plan_application(
            OperatorEngine(result), displayed_packet(), user_present_assist_confirmed=True,
        )
        selected = [item["tool"] for item in plan["steps"]]
        self.assertIn("jobflow.apply_approved_page", selected)
        self.assertEqual(plan["final_submit"], "USER_ONLY")
        self.assertEqual(plan["real_external_actions"], 0)

    def test_context_has_counts_and_purposes_but_no_answer_values(self) -> None:
        context = application_operator_context(displayed_packet())
        self.assertEqual(context["readiness"]["resolved_fields"], 5)
        self.assertEqual(context["materials"], ["resume"])
        self.assertEqual(context["private_answer_values_in_context"], 0)

    def test_onboarding_service_exposes_plan_without_external_action(self) -> None:
        service = object.__new__(OnboardingCenterService)
        service._lock = threading.RLock()
        service.ai_engine = OperatorEngine(valid_result())
        service.review_packet = lambda application_id: displayed_packet()
        result = service.plan_application_with_ai({"application_id": "APP-ABCDEF123456"})
        self.assertEqual(result["status"], "AI_OPERATOR_PLAN_READY")
        self.assertEqual(result["operator_plan"]["stop_condition"], "AWAITING_USER_SUBMIT")
        self.assertEqual(result["real_external_actions"], 0)

    def test_new_job_command_gives_ai_tools_and_requires_guided_intake(self) -> None:
        result = valid_result()
        result["steps"] = [{
            "tool": "jobflow.start_guided_intake",
            "reason": "Read the company role and application form through the bounded companion.",
            "requires_user_approval": True,
            "expected_status": "GUIDED_INTAKE_PAIRING",
        }]
        engine = OperatorEngine(result)
        plan = plan_new_job(
            engine,
            command="Handle this job for me",
            official_url="https://careers.example.test/jobs/credit-analyst?tracking=private",
            readiness={"status": "READY_FOR_OFFLINE_APPLICATION_PREPARATION", "blockers": []},
            guided_status={"status": "IDLE", "active": False},
        )
        self.assertEqual(plan["steps"][0]["tool"], "jobflow.start_guided_intake")
        request = json.dumps(engine.request, ensure_ascii=False)
        self.assertIn("AI_DECIDES_JOBFLOW_EXECUTES", json.dumps(operator_public_manifest()))
        self.assertIn("company_host", request)
        self.assertNotIn("tracking=private", request)
        self.assertEqual(plan["final_submit"], "USER_ONLY")

    def test_new_job_unwraps_real_agent_execution_envelope_without_weakening_contract(self) -> None:
        result = valid_result()
        result["steps"] = [{
            "tool": "jobflow.start_guided_intake",
            "reason": "Create the bounded company-page reading lease.",
            "requires_user_approval": True,
            "expected_status": "GUIDED_INTAKE_PAIRING",
        }]
        plan = plan_new_job(
            WrappedOperatorEngine(result),
            command="Handle this job for me",
            official_url="https://careers.example.test/jobs/credit-analyst",
            readiness={"status": "READY_FOR_OFFLINE_APPLICATION_PREPARATION", "blockers": []},
            guided_status={"status": "IDLE", "active": False},
        )
        self.assertEqual(plan["status"], "READY")
        self.assertEqual(plan["steps"][0]["tool"], "jobflow.start_guided_intake")
        self.assertEqual(plan["final_submit"], "USER_ONLY")
        self.assertFalse(plan["automatic_retry"])

    def test_recoverable_guided_failure_is_presented_as_restartable_after_confirmation(self) -> None:
        result = valid_result()
        result["steps"] = [{
            "tool": "jobflow.start_guided_intake",
            "reason": "Replace the recoverable failed read with a fresh bounded lease.",
            "requires_user_approval": False,
            "expected_status": "GUIDED_INTAKE_PAIRING",
        }]
        engine = OperatorEngine(result)
        plan = plan_new_job(
            engine,
            command="Handle this job for me",
            official_url="https://careers.example.test/jobs/credit-analyst",
            readiness={"status": "READY_FOR_OFFLINE_APPLICATION_PREPARATION", "blockers": []},
            guided_status={"status": "FORM_CAPTURE_FAILED", "active": True},
            read_only_intake_confirmed=True,
        )
        state = engine.request["current_task_state"]
        self.assertTrue(state["browser_task"]["restart_permitted"])
        self.assertTrue(state["authorization"]["read_only_guided_intake_confirmed"])
        self.assertEqual(plan["status"], "READY")

    def test_service_accepts_one_sentence_with_embedded_url_and_starts_host_lease(self) -> None:
        result = valid_result()
        result["steps"] = [{
            "tool": "jobflow.start_guided_intake",
            "reason": "Start the bounded read-only intake.",
            "requires_user_approval": True,
            "expected_status": "GUIDED_INTAKE_PAIRING",
        }]
        service = object.__new__(OnboardingCenterService)
        service._lock = threading.RLock()
        service.ai_engine = OperatorEngine(result)
        service.bootstrap = lambda: {
            "application_readiness": {
                "status": "READY_FOR_OFFLINE_APPLICATION_PREPARATION", "blockers": [],
            },
            "dashboard": {"guided_intake": {"status": "IDLE", "active": False}},
        }
        calls: list[dict[str, object]] = []
        service.start_guided_intake = lambda payload: calls.append(payload) or {
            "status": "GUIDED_INTAKE_PAIRING", "intake_id": "GIN-TEST",
        }
        response = service.start_job_with_ai({
            "command": "帮我处理这个岗位 https://careers.example.test/jobs/credit-analyst",
            "user_confirmed": True,
        })
        self.assertEqual(response["status"], "AI_OPERATOR_TASK_STARTED")
        self.assertEqual(calls, [{
            "official_url": "https://careers.example.test/jobs/credit-analyst",
            "user_confirmed": True,
        }])
        self.assertNotIn("https://", json.dumps(service.ai_engine.request, ensure_ascii=False))

    def test_application_operator_executes_only_host_owned_lease_action(self) -> None:
        service = object.__new__(OnboardingCenterService)
        service._lock = threading.RLock()
        service.ai_engine = OperatorEngine(valid_result())
        service.review_packet = lambda application_id: displayed_packet()
        calls: list[dict[str, object]] = []
        service.start_browser_assist = lambda payload: calls.append(payload) or {
            "status": "BROWSER_COMPANION_PAIRING", "assist_id": "BAS-TEST",
        }
        result = service.start_application_with_ai({
            "application_id": "APP-ABCDEF123456", "user_confirmed": True,
        })
        self.assertEqual(result["status"], "AI_OPERATOR_TASK_STARTED")
        self.assertEqual(result["host_executed_tools"], ["jobflow.start_user_present_assist"])
        self.assertEqual(result["operator_execution"]["host_executed_tools"], ["jobflow.start_user_present_assist"])
        self.assertIn("jobflow.inspect_application_form", result["operator_execution"]["pending_pipeline_tools"])
        self.assertFalse(result["operator_execution"]["all_selected_tools_executed"])
        self.assertEqual(calls, [{"application_id": "APP-ABCDEF123456", "user_confirmed": True}])
        self.assertEqual(result["real_external_actions"], 0)

    def test_execution_trace_never_claims_a_planned_stage_already_ran(self) -> None:
        plan = plan_application(OperatorEngine(valid_result()), displayed_packet())
        trace = operator_execution_trace(plan, executed_tools=["jobflow.start_user_present_assist"])
        self.assertEqual(trace["ai_selected_tools"], [step["tool"] for step in plan["steps"]])
        self.assertEqual(trace["host_executed_tools"], ["jobflow.start_user_present_assist"])
        self.assertEqual(
            trace["pending_pipeline_tools"],
            ["jobflow.inspect_application_form", "jobflow.prepare_fill_plan"],
        )
        self.assertEqual(
            trace["event_driven_pipeline_tools"],
            ["jobflow.inspect_application_form", "jobflow.prepare_fill_plan"],
        )
        self.assertEqual(trace["continuation_mode"], "EVENT_DRIVEN_UNTIL_USER_GATE")
        self.assertTrue(trace["initial_host_action_completed"])
        self.assertEqual(trace["final_submit"], "USER_ONLY")
        self.assertFalse(trace["automatic_retry"])

    def test_material_decision_can_rank_but_cannot_rewrite_or_invent_claims(self) -> None:
        claims = [
            {
                "claim_id": "CLM-ONE", "category": "work",
                "allowed_wording": ["Reviewed credit portfolios using documented risk controls."],
                "approved_for_external": True, "applicant_confirmed": True,
                "private_note": "MUST NOT BE SENT",
            },
            {
                "claim_id": "CLM-TWO", "category": "skill",
                "allowed_wording": ["Built financial models for scenario and variance analysis."],
                "approved_for_external": True, "applicant_confirmed": True,
            },
        ]
        engine = OperatorEngine({
            "schema_version": 1,
            "ranked_claim_ids": ["CLM-TWO", "CLM-ONE"],
            "summary": "Financial modeling is the closest match to the role.",
        })
        decision = rank_application_claims(
            engine,
            job_summary={
                "company": "Example", "title": "Credit Analyst", "location": "New York",
                "responsibilities": ["Analyze portfolio credit risk"],
                "requirements": [{"text": "Financial modeling"}],
                "keywords": ["credit", "modeling"],
            },
            claims=claims,
        )
        self.assertEqual(decision["ranked_claim_ids"], ["CLM-TWO", "CLM-ONE"])
        serialized = json.dumps(engine.request, ensure_ascii=False)
        self.assertNotIn("MUST NOT BE SENT", serialized)
        self.assertIn("jobflow_operating_manifest", engine.request)
        self.assertEqual(
            decision["task_state_hash"], engine.request["current_task_state_hash"],
        )
        self.assertEqual(engine.request["current_task_state"]["private_answer_values_in_context"], 0)
        self.assertEqual(decision["wording_changes_accepted"], 0)

        wrapped_decision = rank_application_claims(
            WrappedOperatorEngine({
                "schema_version": 1,
                "ranked_claim_ids": ["CLM-TWO", "CLM-ONE"],
                "summary": "Financial modeling is the closest match to the role.",
            }),
            job_summary={"title": "Credit Analyst", "requirements": ["Financial modeling"]},
            claims=claims,
        )
        self.assertEqual(wrapped_decision["ranked_claim_ids"], ["CLM-TWO", "CLM-ONE"])

        engine.result = {
            "schema_version": 1,
            "ranked_claim_ids": ["CLM-INVENTED"],
            "summary": "Invented selection.",
        }
        with self.assertRaises(JobOpsError) as rejected:
            rank_application_claims(engine, job_summary={"title": "Credit Analyst"}, claims=claims)
        self.assertEqual(rejected.exception.code, "AI_MATERIAL_CLAIM_FORBIDDEN")


if __name__ == "__main__":
    unittest.main()
