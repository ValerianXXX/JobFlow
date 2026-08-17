from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path

from jobops.ai_operator import (
    analyze_application_form_semantics,
    application_operator_context,
    operator_execution_trace,
    operator_public_manifest,
    plan_application,
    plan_new_job,
    rank_application_claims,
    record_operator_turn_event,
    resolve_new_job_command,
)
from jobops.ai_runtime import AIAnalysisEngine
from jobops.db import JobOpsDB
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

    def test_one_line_search_intent_is_valid_without_a_pasted_url(self) -> None:
        command, url = resolve_new_job_command(
            "Find matching credit risk analyst roles in New York on official company career sites"
        )
        self.assertEqual(
            command,
            "Find matching credit risk analyst roles in New York on official company career sites",
        )
        self.assertEqual(url, "")
        with self.assertRaises(JobOpsError) as missing:
            resolve_new_job_command("   ")
        self.assertEqual(missing.exception.code, "AI_OPERATOR_SEARCH_INTENT_REQUIRED")

    def test_form_semantics_explain_exact_controls_without_answers_or_authority(self) -> None:
        engine = OperatorEngine({
            "schema_version": 1,
            "selected_tool": "jobflow.inspect_application_form",
            "summary": "This page collects identity and ends at a user-only submit control.",
            "fields": [
                {"control_ref": "CTL-AAAAAAAAAAAA", "semantic_role": "identity", "reason": "The prompt asks for a first name."},
                {"control_ref": "CTL-BBBBBBBBBBBB", "semantic_role": "final_submit", "reason": "This is the final submit control."},
            ],
        })
        result = analyze_application_form_semantics(engine, form_analysis={
            "provider": "company", "step_kind": "MY_INFORMATION",
            "page_content_hash": "sha256:" + "1" * 64,
            "form_snapshot_hash": "sha256:" + "2" * 64,
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
        self.assertEqual(result["operator_turn"]["selected_tool"], "jobflow.inspect_application_form")
        self.assertEqual(result["operator_turn"]["decision_point"], "CURRENT_FORM_SEMANTIC_REVIEW")
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
        self.assertEqual(plan["operator_turn"]["selected_tool"], "jobflow.start_user_present_assist")
        self.assertEqual(plan["operator_turn"]["decision_point"], "START_OR_RESUME_TASK")
        manifest = engine.request["jobflow_operating_manifest"]
        self.assertEqual(manifest["decision_loop"]["task_state_delivery"], "EVERY_AI_DECISION")
        self.assertEqual(manifest["decision_loop"]["continuation"], "EVENT_DRIVEN_UNTIL_USER_GATE")
        self.assertTrue(manifest["decision_loop"]["one_immediate_tool_per_decision"])
        self.assertTrue(manifest["decision_loop"]["future_tools_require_fresh_state"])
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

    def test_ai_cannot_select_a_future_page_write_during_the_start_turn(self) -> None:
        result = valid_result()
        result["steps"] = [{
            "tool": "jobflow.apply_approved_page",
            "reason": "Ask the host to apply only the current hash-bound fields and approved materials.",
            "requires_user_approval": True,
            "expected_status": "AWAITING_USER_SUBMIT",
        }]
        with self.assertRaises(JobOpsError) as missing_gate:
            plan_application(OperatorEngine(result), displayed_packet())
        self.assertEqual(missing_gate.exception.code, "AI_OPERATOR_EXTERNAL_ACTION_UNAUTHORIZED")

        with self.assertRaises(JobOpsError) as wrong_stage:
            plan_application(
                OperatorEngine(result), displayed_packet(), user_present_assist_confirmed=True,
            )
        self.assertEqual(wrong_stage.exception.code, "AI_OPERATOR_REQUIRED_TOOL_MISSING")

        multiple = valid_result()
        multiple["steps"].append({
            "tool": "jobflow.inspect_application_form",
            "reason": "A future step must wait for fresh page state.",
            "requires_user_approval": False,
            "expected_status": "FORM_INSPECTED",
        })
        with self.assertRaises(JobOpsError) as future_plan:
            plan_application(OperatorEngine(multiple), displayed_packet())
        self.assertEqual(future_plan.exception.code, "AI_OPERATOR_RESPONSE_INVALID")

    def test_context_has_counts_and_purposes_but_no_answer_values(self) -> None:
        context = application_operator_context(displayed_packet())
        self.assertEqual(context["readiness"]["resolved_fields"], 5)
        self.assertEqual(context["materials"], ["resume"])
        self.assertEqual(context["private_answer_values_in_context"], 0)

    def test_operator_turn_ledger_is_append_only_redacted_and_restart_readable(self) -> None:
        plan = plan_application(OperatorEngine(valid_result()), displayed_packet())
        turn = plan["operator_turn"]
        with tempfile.TemporaryDirectory() as temporary:
            database = JobOpsDB(Path(temporary) / "jobops.db")
            database.initialize()
            record_operator_turn_event(database, turn, status="AI_SELECTED")
            record_operator_turn_event(database, turn, status="HOST_EXECUTED")
            service = object.__new__(OnboardingCenterService)
            service.database = database
            activity = service._operator_activity()
            self.assertEqual(activity["turn_count"], 1)
            self.assertEqual(activity["completed_turn_count"], 1)
            self.assertEqual(activity["recent_turns"][0]["status"], "HOST_EXECUTED")
            self.assertEqual(
                activity["recent_turns"][0]["selected_tool"],
                "jobflow.start_user_present_assist",
            )
            with database.connect() as connection:
                payloads = [str(row[0]) for row in connection.execute(
                    "SELECT payload_json FROM events WHERE event_type='AI_OPERATOR_TURN' ORDER BY event_id"
                )]
            serialized = "\n".join(payloads)
            self.assertNotIn("PRIVATE ANSWER MUST NOT LEAK", serialized)
            self.assertNotIn("PRIVATE-EMAIL-VALUE-FOR-TEST", serialized)
            self.assertNotIn("secure-ref:", serialized)
            self.assertNotIn("https://secret.invalid", serialized)

    def test_onboarding_service_exposes_plan_without_external_action(self) -> None:
        service = object.__new__(OnboardingCenterService)
        service._lock = threading.RLock()
        service.ai_engine = OperatorEngine(valid_result())
        service._record_operator_turn = lambda *_args, **_kwargs: None
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

    def test_new_job_search_intent_requires_official_job_discovery(self) -> None:
        result = valid_result()
        result["steps"] = [{
            "tool": "jobflow.search_official_jobs",
            "reason": "Search visibly through the user's browser and return only official company role candidates.",
            "requires_user_approval": False,
            "expected_status": "AWAITING_JOB_DISCOVERY",
        }]
        engine = OperatorEngine(result)
        plan = plan_new_job(
            engine,
            command="Find matching credit risk analyst roles in New York",
            official_url="",
            readiness={"status": "READY_FOR_OFFLINE_APPLICATION_PREPARATION", "blockers": []},
            guided_status={"status": "IDLE", "active": False},
            read_only_intake_confirmed=True,
        )
        self.assertEqual(plan["steps"][0]["tool"], "jobflow.search_official_jobs")
        current = engine.request["current_task_state"]
        self.assertEqual(current["stage"], "JOB_DISCOVERY")
        self.assertEqual(current["job_source"]["mode"], "OFFICIAL_COMPANY_SEARCH")
        self.assertNotIn("company_host", current["job_source"])
        self.assertEqual(current["private_answer_values_in_context"], 0)

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
        service._record_operator_turn = lambda *_args, **_kwargs: None
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
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["official_url"], "https://careers.example.test/jobs/credit-analyst")
        self.assertTrue(calls[0]["user_confirmed"])
        self.assertRegex(str(calls[0]["operator_task_id"]), r"^AIT-[A-F0-9]{12}$")
        self.assertNotIn("https://", json.dumps(service.ai_engine.request, ensure_ascii=False))

    def test_service_starts_browser_discovery_when_the_command_has_no_url(self) -> None:
        result = valid_result()
        result["steps"] = [{
            "tool": "jobflow.search_official_jobs",
            "reason": "Use the visible browser search surface to discover official company role pages.",
            "requires_user_approval": False,
            "expected_status": "AWAITING_JOB_DISCOVERY",
        }]
        service = object.__new__(OnboardingCenterService)
        service._lock = threading.RLock()
        service.ai_engine = OperatorEngine(result)
        service._record_operator_turn = lambda *_args, **_kwargs: None
        service.bootstrap = lambda: {
            "application_readiness": {
                "status": "READY_FOR_OFFLINE_APPLICATION_PREPARATION", "blockers": [],
            },
            "dashboard": {"guided_intake": {"status": "IDLE", "active": False}},
        }
        calls: list[dict[str, object]] = []
        service.start_guided_intake = lambda payload: calls.append(payload) or {
            "status": "GUIDED_INTAKE_PAIRING", "intake_id": "GIN-DISCOVERY",
        }
        response = service.start_job_with_ai({
            "command": "Find matching credit risk analyst roles in New York",
            "user_confirmed": True,
        })
        self.assertEqual(response["status"], "AI_OPERATOR_TASK_STARTED")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["search_intent"], "Find matching credit risk analyst roles in New York")
        self.assertTrue(calls[0]["user_confirmed"])
        self.assertRegex(str(calls[0]["operator_task_id"]), r"^AIT-[A-F0-9]{12}$")
        self.assertEqual(
            response["operator_execution"]["host_executed_tools"],
            ["jobflow.search_official_jobs"],
        )

    def test_application_operator_executes_only_host_owned_lease_action(self) -> None:
        service = object.__new__(OnboardingCenterService)
        service._lock = threading.RLock()
        service.ai_engine = OperatorEngine(valid_result())
        service._record_operator_turn = lambda *_args, **_kwargs: None
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
        self.assertEqual(result["operator_execution"]["pending_pipeline_tools"], [])
        self.assertTrue(result["operator_execution"]["all_selected_tools_executed"])
        self.assertEqual(result["operator_execution"]["current_turn"]["status"], "HOST_EXECUTED")
        self.assertEqual(calls, [{"application_id": "APP-ABCDEF123456", "user_confirmed": True}])
        self.assertEqual(result["real_external_actions"], 0)

    def test_one_confirmation_resolves_fields_approves_rebound_packet_and_starts(self) -> None:
        service = object.__new__(OnboardingCenterService)
        service._lock = threading.RLock()
        calls: list[tuple[str, dict[str, object]]] = []

        def resolve(payload: dict[str, object]) -> dict[str, object]:
            calls.append(("resolve", dict(payload)))
            return {
                "status": "JOB_SPECIFIC_ANSWERS_ENCRYPTED",
                "packet_hash": "sha256:" + "2" * 64,
                "remaining_unresolved_count": 0,
            }

        def decide(payload: dict[str, object]) -> dict[str, object]:
            calls.append(("decide", dict(payload)))
            self.assertEqual(payload["expected_packet_hash"], "sha256:" + "2" * 64)
            return {"status": "APPROVED", "application_id": "APP-ONE-CONFIRMATION"}

        def start(payload: dict[str, object]) -> dict[str, object]:
            calls.append(("start", dict(payload)))
            return {
                "status": "AI_OPERATOR_TASK_STARTED",
                "operator_plan": {"status": "AI_OPERATOR_PLAN_READY"},
                "operator_execution": {"host_executed_tools": ["jobflow.start_user_present_assist"]},
                "browser_assist": {"status": "BROWSER_COMPANION_PAIRING"},
            }

        service.resolve_application_fields = resolve
        service.decide_review_packet = decide
        service.start_application_with_ai = start
        result = service.approve_and_start_application({
            "application_id": "APP-ONE-CONFIRMATION",
            "expected_packet_hash": "sha256:" + "1" * 64,
            "resolutions": [{
                "control_ref": "FLD-SYNTHETIC",
                "decision": "CONFIRMED_VALUE",
                "value": "Synthetic confirmed value",
            }],
            "non_form_resolutions": [],
            "user_confirmed": True,
        })

        self.assertEqual([name for name, _payload in calls], ["resolve", "decide", "start"])
        self.assertEqual(calls[0][1]["expected_packet_hash"], "sha256:" + "1" * 64)
        self.assertEqual(calls[1][1]["decision"], "APPROVE")
        self.assertEqual(calls[2][1], {
            "application_id": "APP-ONE-CONFIRMATION", "user_confirmed": True,
        })
        self.assertEqual(result["status"], "APPROVED_APPLICATION_AUTOPILOT_STARTED")
        self.assertEqual(result["final_submit"], "USER_ONLY")
        self.assertFalse(result["automatic_retry"])
        self.assertEqual(result["real_external_actions"], 0)

    def test_generic_search_command_uses_only_approved_job_preferences(self) -> None:
        service = object.__new__(OnboardingCenterService)
        service._base_profile = lambda: {
            "candidate_display_name": "MUST NOT LEAK",
            "email": "must-not-leak@example.test",
            "work_authorization": "MUST NOT LEAK",
            "minimum_salary": 987654,
            "target_functions": ["Credit Risk Analyst", "Portfolio Risk"],
            "target_levels": ["Entry level"],
            "locations": ["New York, NY"],
            "remote_preference": "Hybrid",
        }
        expanded = service._effective_job_search_command("Find and handle the best matching role for me")
        self.assertIn("Credit Risk Analyst", expanded)
        self.assertIn("New York, NY", expanded)
        self.assertIn("Hybrid", expanded)
        self.assertNotIn("MUST NOT LEAK", expanded)
        self.assertNotIn("987654", expanded)
        explicit = "Find a treasury analyst role in Boston"
        self.assertEqual(service._effective_job_search_command(explicit), explicit)

    def test_execution_trace_never_claims_a_planned_stage_already_ran(self) -> None:
        plan = plan_application(OperatorEngine(valid_result()), displayed_packet())
        trace = operator_execution_trace(plan, executed_tools=["jobflow.start_user_present_assist"])
        self.assertEqual(trace["ai_selected_tools"], [step["tool"] for step in plan["steps"]])
        self.assertEqual(trace["host_executed_tools"], ["jobflow.start_user_present_assist"])
        self.assertEqual(trace["pending_pipeline_tools"], [])
        self.assertEqual(trace["event_driven_pipeline_tools"], [])
        self.assertEqual(trace["continuation_mode"], "EVENT_DRIVEN_UNTIL_USER_GATE")
        self.assertTrue(trace["initial_host_action_completed"])
        self.assertEqual(trace["current_turn"]["selected_tool"], "jobflow.start_user_present_assist")
        self.assertEqual(trace["current_turn"]["status"], "HOST_EXECUTED")
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
            "selected_tool": "jobflow.plan_resume_changes",
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
        self.assertEqual(decision["operator_turn"]["selected_tool"], "jobflow.plan_resume_changes")
        self.assertEqual(decision["operator_turn"]["decision_point"], "JOB_AND_MATERIAL_DECISION")

        wrapped_decision = rank_application_claims(
            WrappedOperatorEngine({
                "schema_version": 1,
                "selected_tool": "jobflow.plan_resume_changes",
                "ranked_claim_ids": ["CLM-TWO", "CLM-ONE"],
                "summary": "Financial modeling is the closest match to the role.",
            }),
            job_summary={"title": "Credit Analyst", "requirements": ["Financial modeling"]},
            claims=claims,
        )
        self.assertEqual(wrapped_decision["ranked_claim_ids"], ["CLM-TWO", "CLM-ONE"])

        engine.result = {
            "schema_version": 1,
            "selected_tool": "jobflow.plan_resume_changes",
            "ranked_claim_ids": ["CLM-INVENTED"],
            "summary": "Invented selection.",
        }
        with self.assertRaises(JobOpsError) as rejected:
            rank_application_claims(engine, job_summary={"title": "Credit Analyst"}, claims=claims)
        self.assertEqual(rejected.exception.code, "AI_MATERIAL_CLAIM_FORBIDDEN")


if __name__ == "__main__":
    unittest.main()
