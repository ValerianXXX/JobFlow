from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from .audit import audit_environment
from .adapters import audit_real_external_actions
from .approvals import ApprovalContext, issue_approval
from .claim_registry import ClaimRegistry
from .collector import JobCollector
from .db import JobOpsDB
from .errors import JobOpsError
from .external_actions import ExternalActionGateway, ExternalActionPolicy
from .forms import build_mock_ats_site
from .jd_analyzer import analyze_jd
from .knowledge import KnowledgeGateway
from .locator import locate_knowledge_root
from .orchestrator import JobOpsOrchestrator, _read_jd
from .onboarding_center import OnboardingCenterService
from .onboarding_server import run_server
from .private_onboarding import PrivateOnboarding
from .queue_manager import QueueManager
from .recovery import RecoveryManager
from .resume_onboarding import ResumeOnboardingManager
from .runtime_schema import validate_named
from .secure_store import WindowsDPAPIStore
from .security import assert_project_io_path
from .sourcing import verify_source_route
from .util import canonical_json, iso_utc, load_json, project_root, sha256_bytes, stable_id, write_json


def _display_path(path: Path, project: Path) -> str:
    absolute = path.absolute()
    try:
        return "$PROJECT_ROOT/" + absolute.relative_to(project).as_posix()
    except ValueError:
        return "$EXTERNAL_PATH/" + absolute.name


def _sanitize(value: Any, project: Path) -> Any:
    if isinstance(value, dict):
        return {str(key): _sanitize(item, project) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_sanitize(item, project) for item in value]
    if isinstance(value, str):
        project_text = str(project.absolute())
        if value.casefold().startswith(project_text.casefold()):
            suffix = value[len(project_text):].lstrip("\\/").replace("\\", "/")
            return "$PROJECT_ROOT" + ("/" + suffix if suffix else "")
        if re.match(r"^[A-Za-z]:[\\/]", value):
            return "$EXTERNAL_PATH/" + Path(value).name
    return value


def emit(value: object, project: Path) -> None:
    if isinstance(value, dict) and "next_safe_action" not in value:
        value = {**value, "next_safe_action": "NONE"}
    print(json.dumps(_sanitize(value, project), ensure_ascii=False, indent=2))


def resolve(project: Path, start: Path | None = None):
    return locate_knowledge_root(start or project, project / "config" / "knowledge-sources.json")


def _db_path(project: Path, raw: Path | None, *, operation: str) -> Path:
    candidate = raw or Path("state/jobops.db")
    candidate = candidate if candidate.is_absolute() else project / candidate
    return assert_project_io_path(candidate, project, operation=operation)


def _add_path_argument(command: argparse.ArgumentParser) -> None:
    command.add_argument("--path", type=Path)


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="jobflow", description="Local evidence-gated JobFlow controller")
    sub = root.add_subparsers(dest="command", required=True)
    _add_path_argument(sub.add_parser("status"))
    sub.add_parser("audit")
    locate = sub.add_parser("locate")
    locate.add_argument("--start", type=Path)
    locate.add_argument("--write-state", action="store_true")
    _add_path_argument(sub.add_parser("init-db"))
    _add_path_argument(sub.add_parser("migrate-db"))
    snapshot = sub.add_parser("snapshot")
    snapshot.add_argument("--output", type=Path, required=True)
    verify = sub.add_parser("verify-readonly")
    verify.add_argument("--baseline", type=Path, required=True)
    search = sub.add_parser("search")
    search.add_argument("query")
    search.add_argument("--source", action="append", dest="sources")
    search.add_argument("--limit", type=int, default=10)
    mocks = sub.add_parser("build-mock-sites")
    mocks.add_argument("--output", type=Path, default=Path("workspace/mock-sites"))
    queue = sub.add_parser("queue")
    queue.add_argument("--set-limit", type=int)
    route = sub.add_parser("verify-route")
    route.add_argument("--input", type=Path, required=True)

    onboard = sub.add_parser("secure-onboard")
    onboard.add_argument("--input-file", type=Path)
    onboard.add_argument("--synthetic", action="store_true")
    master = sub.add_parser("secure-import-master-resume")
    master.add_argument("--input-file", type=Path, required=True)
    master.add_argument("--synthetic", action="store_true")
    answers = sub.add_parser("secure-import-answer-bank")
    answers.add_argument("--input-file", type=Path)
    answers.add_argument("--synthetic", action="store_true")
    secure_status = sub.add_parser("secure-store-status")
    secure_status.add_argument("--ref")
    sub.add_parser("purge-synthetic-private-data")

    sub.add_parser("secure-onboard-resume")
    finalize_resume = sub.add_parser("finalize-resume-onboarding")
    finalize_resume.add_argument("--session", required=True)
    finalize_resume.add_argument("--page-result", action="append", dest="page_results", required=True, choices=("PASS", "FAIL"))
    review_onboarding = sub.add_parser("review-onboarding")
    review_onboarding.add_argument("--packet-ref")
    review_onboarding.add_argument("--latest", action="store_true")
    center = sub.add_parser("onboarding-center")
    center.add_argument("--port", type=int, default=0)
    center.add_argument("--no-browser", action="store_true")
    sub.add_parser("onboarding-status")

    propose = sub.add_parser("propose-claims")
    propose.add_argument("--input", type=Path)
    sub.add_parser("list-claim-proposals")
    for name in ("approve-claim", "reject-claim", "revoke-claim", "revalidate-claims"):
        command = sub.add_parser(name)
        command.add_argument("--claim-id")

    import_jd = sub.add_parser("import-jd")
    import_jd.add_argument("--input", type=Path)
    import_jd.add_argument("--source-type", choices=("txt", "html", "pdf", "snapshot"))
    analyze = sub.add_parser("analyze-job")
    analyze.add_argument("--job-id")
    analyze.add_argument("--profile-ref")
    run = sub.add_parser("run-to-awaiting-approval")
    run.add_argument("--input", type=Path, required=True)
    run.add_argument("--profile-ref", required=True)
    run.add_argument("--master-resume-ref", required=True)
    run.add_argument("--answer-bank-ref", required=True)
    run.add_argument("--route", type=Path)
    run.add_argument("--form", type=Path)
    run.add_argument("--research", type=Path)
    run.add_argument("--source-type", choices=("txt", "html", "pdf", "snapshot"))
    run.add_argument("--synthetic", action="store_true")
    run_queue = sub.add_parser("run-queue")
    run_queue.add_argument("--manifest", type=Path)
    sub.add_parser("list-pending")
    show = sub.add_parser("show-review-packet")
    show.add_argument("--application-id")
    for name in ("revise-application", "approve-review-packet", "reject-review-packet", "resume-blocked", "retry-safe-step", "explain"):
        command = sub.add_parser(name)
        command.add_argument("--application-id")
        if name in {"resume-blocked", "retry-safe-step"}:
            command.add_argument("--override-ineligible", action="store_true")
    verify_release_command = sub.add_parser("verify-release")
    verify_release_command.add_argument("--require-independent", action="store_true")
    return root


def _onboarding(project: Path, database: JobOpsDB) -> PrivateOnboarding:
    script = project / ".agents" / "skills" / "job-application-operator" / "scripts" / "secure-store.ps1"
    return PrivateOnboarding(database, WindowsDPAPIStore(script))


def _database(project: Path) -> JobOpsDB:
    database = JobOpsDB(project / "state" / "jobops.db")
    database.initialize()
    return database


def _project_input(project: Path, value: Path, *, operation: str = "read") -> Path:
    path = value if value.is_absolute() else project / value
    return assert_project_io_path(path, project, operation=operation)


def _latest_ref(database: JobOpsDB, kind: str) -> str:
    with database.connect() as connection:
        row = connection.execute("SELECT secure_ref FROM private_refs WHERE kind=? AND status='ACTIVE' ORDER BY updated_at DESC LIMIT 1", (kind,)).fetchone()
    if row is None:
        raise JobOpsError("SECURE_REFERENCE_MISSING", "Required private onboarding reference is not available.", kind=kind)
    return str(row[0])


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    project = project_root()
    try:
        if args.command == "migrate-db":
            path = _db_path(project, args.path, operation="write")
            database = JobOpsDB(path)
            applied = database.migrate()
            emit({"status": "MIGRATED", "database": _display_path(path, project), "schema_version": database.schema_version(), "applied_versions": applied, "next_safe_action": "status"}, project)
        elif args.command == "status":
            path = _db_path(project, args.path, operation="read")
            if not path.is_file():
                raise JobOpsError("DATABASE_NOT_FOUND", "JobOps database does not exist; run migrate-db first.")
            database = JobOpsDB(path)
            emit({"status": "READY", "database": _display_path(path, project), "schema_version": database.schema_version(), "queue": QueueManager(database).status(), "table_counts": database.table_counts(), "next_safe_action": "run-to-awaiting-approval"}, project)
        elif args.command == "audit":
            result = _sanitize(audit_environment(), project)
            database = _database(project)
            result["external_action_audit"] = audit_real_external_actions(database)
            output = project / "state" / "environment-audit.json"
            write_json(output, result)
            emit({**result, "state_path": _display_path(output, project), "next_safe_action": "locate"}, project)
        elif args.command == "locate":
            location = resolve(project, args.start)
            result = {**location.as_dict(), "resolved_at": iso_utc(), "knowledge_write_operations": 0, "next_safe_action": "init-db"}
            if args.write_state:
                output = project / "state" / "knowledge-resolution.json"
                write_json(output, _sanitize(result, project))
                result["state_path"] = _display_path(output, project)
            emit(result, project)
        elif args.command == "init-db":
            path = _db_path(project, args.path, operation="write")
            database = JobOpsDB(path)
            database.initialize()
            location = resolve(project)
            collections = KnowledgeGateway(location).snapshot_collections()["collections"]
            source_to_collection = {"ai_public_core": "ai-public", "business_public_core": "business-public", "joint_navigation": "joint-navigation", "personal_redacted": "personal-redacted"}
            fingerprints = {source_id: str(collections[collection_id]["tree_sha256"]) for source_id, collection_id in source_to_collection.items()}
            database.sync_knowledge_sources(location.sources, fingerprints)
            emit({"status": "INITIALIZED", "database": _display_path(path, project), "schema_version": database.schema_version(), "table_counts": database.table_counts(), "real_external_actions": 0, "next_safe_action": "secure-onboard"}, project)
        elif args.command == "snapshot":
            output = args.output if args.output.is_absolute() else project / args.output
            output = assert_project_io_path(output, project, operation="write")
            result = KnowledgeGateway(resolve(project)).snapshot_collections()
            result["captured_at"] = iso_utc()
            write_json(output, _sanitize(result, project))
            emit({"status": "SNAPSHOT_WRITTEN", "path": _display_path(output, project), **result, "next_safe_action": "verify-readonly"}, project)
        elif args.command == "verify-readonly":
            baseline_path = args.baseline if args.baseline.is_absolute() else project / args.baseline
            baseline_path = assert_project_io_path(baseline_path, project, operation="read")
            gateway = KnowledgeGateway(resolve(project))
            current = gateway.snapshot_collections()
            comparison = gateway.compare_snapshots(load_json(baseline_path), current)
            result = {**comparison, "verified_at": iso_utc(), "baseline": _display_path(baseline_path, project), "knowledge_write_operations": 0, "current": current, "next_safe_action": "NONE" if comparison["status"] == "UNCHANGED" else "STOP_AND_REVIEW_KNOWLEDGE_CHANGES"}
            write_json(project / "state" / "knowledge-readonly-verification.json", _sanitize(result, project))
            emit(result, project)
            return 0 if comparison["status"] == "UNCHANGED" else 2
        elif args.command == "search":
            records = KnowledgeGateway(resolve(project)).search(args.query, source_ids=args.sources, limit=args.limit)
            emit({"status": "SEARCHED", "query": args.query, "count": len(records), "records": [record.as_dict() for record in records], "next_safe_action": "propose-claims"}, project)
        elif args.command == "build-mock-sites":
            output = args.output if args.output.is_absolute() else project / args.output
            output = assert_project_io_path(output, project, operation="write")
            fields = [{"id": "portfolio_url", "label": "Portfolio URL"}, {"id": "work_authorization", "label": "Work authorization"}, {"id": "electronic_signature", "label": "Electronic signature"}, {"id": "disability", "label": "Disability"}]
            manifests = [build_mock_ats_site(output, provider, fields) for provider in ("greenhouse", "lever", "workday")]
            emit({"status": "MOCK_SITES_BUILT", "sites": manifests, "real_external_actions": 0, "next_safe_action": "run-to-awaiting-approval"}, project)
        elif args.command == "queue":
            database = JobOpsDB(project / "state" / "jobops.db")
            database.initialize()
            if args.set_limit is not None:
                database.set_pending_limit(args.set_limit)
            emit({"status": "QUEUE_READY", **database.pending_queue_decision().as_dict(), "next_safe_action": "run-queue"}, project)
        elif args.command == "verify-route":
            input_path = args.input if args.input.is_absolute() else project / args.input
            input_path = assert_project_io_path(input_path, project, operation="read")
            value = load_json(input_path)
            policy = load_json(project / "config" / "policy.json")
            result = verify_source_route(
                company_domain=value.get("company_domain"), official_entry_url=value["official_entry_url"], current_url=value["current_url"],
                navigation_history=value["navigation_history"], approved_ats_hosts=policy["approved_ats_hosts"], guest_available=value.get("guest_available"),
                tenant_binding=value.get("tenant_binding"), official_page_hash=value.get("official_page_hash"),
                jd_snapshot_hash=value.get("jd_snapshot_hash"), approved_intermediary_hosts=value.get("approved_intermediary_hosts"),
            )
            emit({**result.as_dict(), "next_safe_action": "run-to-awaiting-approval"}, project)
        elif args.command == "secure-onboard":
            database = _database(project); onboarding = _onboarding(project, database)
            if args.synthetic:
                result = JobOpsOrchestrator(project, database, onboarding).secure_onboard_synthetic()
            elif args.input_file:
                profile = load_json(args.input_file)
                if not isinstance(profile, dict):
                    raise JobOpsError("CANDIDATE_PROFILE_INVALID", "Candidate Profile must be a JSON object.")
                profile["profile_ref"] = "secure-ref:IMPORT_PENDING"
                validate_named("candidate-profile", profile, project / "schemas")
                result = {"status": "SECURE_ONBOARDING_READY", **onboarding.import_file("candidate_profile", args.input_file, synthetic=False)}
            else:
                raise JobOpsError("PRIVATE_INPUT_REQUIRED", "Select a profile file or use the explicit synthetic fixture mode.")
            emit({**result, "next_safe_action": "secure-import-master-resume"}, project)
        elif args.command == "secure-import-master-resume":
            database = _database(project); onboarding = _onboarding(project, database)
            suffix = args.input_file.suffix.casefold()
            if suffix not in {".docx", ".pdf"}:
                raise JobOpsError("MASTER_RESUME_FORMAT_INVALID", "Master resume import accepts DOCX or PDF only.")
            result = onboarding.import_file("master_resume_docx" if suffix == ".docx" else "master_resume_pdf", args.input_file, synthetic=args.synthetic)
            emit({"status": "MASTER_RESUME_SECURED", **result, "next_safe_action": "secure-import-answer-bank"}, project)
        elif args.command == "secure-import-answer-bank":
            database = _database(project); onboarding = _onboarding(project, database)
            source = project / "tests" / "fixtures" / "synthetic-forward-answer-bank.json" if args.synthetic else args.input_file
            if source is None:
                raise JobOpsError("PRIVATE_INPUT_REQUIRED", "Select an answer-bank file or use the explicit synthetic fixture mode.")
            answer_bank = load_json(source)
            if not isinstance(answer_bank, dict):
                raise JobOpsError("ANSWER_BANK_INVALID", "Answer Bank must be a JSON object.")
            result = onboarding.import_file("answer_bank", source, synthetic=args.synthetic)
            emit({"status": "ANSWER_BANK_SECURED", **result, "next_safe_action": "run-to-awaiting-approval"}, project)
        elif args.command == "secure-store-status":
            database = _database(project)
            with database.connect() as connection:
                if args.ref:
                    rows = connection.execute("SELECT secure_ref,kind,display_name,version,status,synthetic,updated_at FROM private_refs WHERE secure_ref=?", (args.ref,)).fetchall()
                else:
                    rows = connection.execute("SELECT secure_ref,kind,display_name,version,status,synthetic,updated_at FROM private_refs ORDER BY kind,updated_at").fetchall()
            emit({"status": "SECURE_STORE_METADATA", "references": [dict(row) for row in rows], "private_values_emitted": 0, "next_safe_action": "NONE"}, project)
        elif args.command == "purge-synthetic-private-data":
            database = _database(project)
            result = _onboarding(project, database).purge_synthetic()
            emit({**result, "next_safe_action": "secure-store-status"}, project)
        elif args.command == "secure-onboard-resume":
            database = _database(project); onboarding = _onboarding(project, database)
            result = ResumeOnboardingManager(project, database, onboarding).prepare()
            emit(result, project)
        elif args.command == "finalize-resume-onboarding":
            database = _database(project); onboarding = _onboarding(project, database)
            result = ResumeOnboardingManager(project, database, onboarding).finalize(args.session, args.page_results)
            emit(result, project)
        elif args.command == "review-onboarding":
            if args.packet_ref and args.latest:
                raise JobOpsError("ONBOARDING_REVIEW_SELECTOR_INVALID", "Choose either the latest packet or one secure reference, not both.")
            database = _database(project); onboarding = _onboarding(project, database)
            result = ResumeOnboardingManager(project, database, onboarding).show_review(None if args.latest or not args.packet_ref else args.packet_ref)
            emit(result, project)
        elif args.command == "onboarding-center":
            database = _database(project); onboarding = _onboarding(project, database)
            run_server(OnboardingCenterService(project, database, onboarding), port=args.port, open_browser=not args.no_browser)
        elif args.command == "onboarding-status":
            database = _database(project); onboarding = _onboarding(project, database)
            emit(OnboardingCenterService(project, database, onboarding).redacted_status(), project)
        elif args.command == "propose-claims":
            if not args.input:
                raise JobOpsError("CLAIM_INPUT_REQUIRED", "Claim proposal requires a project-bounded JSON input.")
            database = _database(project); gateway = KnowledgeGateway(resolve(project))
            value = load_json(_project_input(project, args.input))
            result = ClaimRegistry(database, gateway).propose(value)
            emit({"status": "CLAIM_PROPOSED", "claim_id": result["claim_id"], "lifecycle_status": result["lifecycle_status"], "approved_for_external": False, "next_safe_action": "approve-claim"}, project)
        elif args.command == "list-claim-proposals":
            database = _database(project)
            with database.connect() as connection:
                rows = connection.execute("SELECT claim_id,lifecycle_status,approved_for_external,sensitivity,version,expires_at FROM claims ORDER BY claim_id").fetchall()
            emit({"status": "CLAIM_METADATA", "claims": [dict(row) for row in rows], "personal_values_emitted": 0, "next_safe_action": "approve-claim"}, project)
        elif args.command in {"approve-claim", "reject-claim", "revoke-claim", "revalidate-claims"}:
            database = _database(project); registry = ClaimRegistry(database, KnowledgeGateway(resolve(project)))
            if args.command != "revalidate-claims" and not args.claim_id:
                raise JobOpsError("CLAIM_ID_REQUIRED", "This operation requires a claim ID.")
            if args.command == "approve-claim": result = registry.approve(args.claim_id, allowed_uses=("resume", "cover_letter", "application_narrative"))
            elif args.command == "reject-claim": result = registry.reject(args.claim_id)
            elif args.command == "revoke-claim": result = registry.revoke(args.claim_id)
            elif args.claim_id: result = registry.revalidate(args.claim_id)
            else:
                with database.connect() as connection:
                    ids = [str(row[0]) for row in connection.execute("SELECT claim_id FROM claims")]
                result = {"claims": [{"claim_id": claim_id, "lifecycle_status": registry.revalidate(claim_id)["lifecycle_status"]} for claim_id in ids]}
            safe = {key: value for key, value in result.items() if key in {"claim_id", "lifecycle_status", "approved_for_external", "version", "claims"}}
            emit({"status": "CLAIM_UPDATED", **safe, "next_safe_action": "revalidate-claims"}, project)
        elif args.command == "import-jd":
            if not args.input:
                raise JobOpsError("JD_INPUT_REQUIRED", "Select a local TXT, HTML, PDF, or saved page snapshot.")
            path = _project_input(project, args.input)
            content, source_format, source_url = _read_jd(path, args.source_type)
            intake_key = sha256_bytes((content.replace("\r\n", "\n").replace("\r", "\n").strip() + "\n").encode("utf-8"))
            database = _database(project); queue_result = QueueManager(database).enqueue(intake_key, source_type=source_format, source_locator=path.name)
            if queue_result.status == "DEFERRED":
                emit({**queue_result.as_dict(), "job_created": False, "real_external_actions": 0}, project)
            else:
                collected = JobCollector(database, project / "workspace" / "jobs", project).collect_text(content, source_type=source_format, source_locator=path.name, official_url=source_url)
                emit({**collected, "reservation_id": queue_result.reservation_id, "source_format": source_format, "next_safe_action": "analyze-job"}, project)
        elif args.command == "analyze-job":
            if not args.job_id or not args.profile_ref:
                raise JobOpsError("ANALYSIS_INPUT_REQUIRED", "analyze-job requires a job ID and secure profile reference.")
            database = _database(project)
            with database.connect() as connection:
                row = connection.execute("SELECT snapshot_path FROM jd_snapshots WHERE job_id=?", (args.job_id,)).fetchone()
            if row is None:
                raise JobOpsError("JOB_NOT_FOUND", "No JD snapshot exists for this job.")
            snapshot = _project_input(project, Path(str(row[0])))
            jd = analyze_jd(snapshot.read_text(encoding="utf-8-sig"))
            profile = json.loads(_onboarding(project, database).read_bytes(args.profile_ref).decode("utf-8")); profile["profile_ref"] = args.profile_ref
            from .eligibility import check_eligibility
            from .fit import compute_fit
            eligibility = check_eligibility(jd, profile); fit = compute_fit(jd, profile, eligibility, evidence_mappings=[])
            validate_named("fit-result", fit.as_dict(), project / "schemas")
            emit({"status": "ANALYZED", "job_id": args.job_id, "jd": jd.as_dict(), "eligibility": eligibility.as_dict(), "fit": fit.as_dict(), "next_safe_action": "run-to-awaiting-approval"}, project)
        elif args.command == "run-to-awaiting-approval":
            database = _database(project); onboarding = _onboarding(project, database); orchestrator = JobOpsOrchestrator(project, database, onboarding)
            fixtures = project / "tests" / "fixtures"
            route = _project_input(project, args.route or Path("tests/fixtures/synthetic-forward-route.json"))
            form = _project_input(project, args.form or Path("tests/fixtures/synthetic-forward-form.json"))
            research = _project_input(project, args.research or Path("tests/fixtures/synthetic-research.html"))
            result = orchestrator.run_to_awaiting(
                _project_input(project, args.input), profile_ref=args.profile_ref, master_resume_ref=args.master_resume_ref,
                answer_bank_ref=args.answer_bank_ref, route_fixture=route, form_fixture=form, research_fixture=research,
                source_type=args.source_type, synthetic=args.synthetic,
            )
            emit(result, project)
        elif args.command == "run-queue":
            if not args.manifest:
                raise JobOpsError("QUEUE_MANIFEST_REQUIRED", "run-queue requires a project-bounded JSON manifest.")
            manifest = load_json(_project_input(project, args.manifest)); jobs = manifest.get("jobs", [])
            if not isinstance(jobs, list):
                raise JobOpsError("QUEUE_MANIFEST_INVALID", "Queue manifest jobs must be a list.")
            database = _database(project); onboarding = _onboarding(project, database); orchestrator = JobOpsOrchestrator(project, database, onboarding)
            results = []
            for item in jobs:
                try:
                    fixtures = project / "tests" / "fixtures"
                    results.append(orchestrator.run_to_awaiting(
                        _project_input(project, Path(item["input"])), profile_ref=item["profile_ref"], master_resume_ref=item["master_resume_ref"], answer_bank_ref=item["answer_bank_ref"],
                        route_fixture=_project_input(project, Path(item.get("route", "tests/fixtures/synthetic-forward-route.json"))),
                        form_fixture=_project_input(project, Path(item.get("form", "tests/fixtures/synthetic-forward-form.json"))),
                        research_fixture=_project_input(project, Path(item.get("research", "tests/fixtures/synthetic-research.html"))),
                        source_type=item.get("source_type"), synthetic=bool(item.get("synthetic", False)),
                    ))
                except JobOpsError as exc:
                    results.append(exc.as_dict())
            emit({"status": "QUEUE_RUN_COMPLETE", "results": results, "queue": QueueManager(database).status(), "real_external_actions": 0, "next_safe_action": "list-pending"}, project)
        elif args.command == "list-pending":
            database = _database(project)
            with database.connect() as connection:
                rows = connection.execute("SELECT a.application_id,a.job_id,a.status,r.packet_id,r.content_hash FROM applications a JOIN review_packets r ON r.application_id=a.application_id WHERE a.status='AWAITING_APPROVAL' ORDER BY a.updated_at").fetchall()
            emit({"status": "PENDING_LIST", "applications": [dict(row) for row in rows], "queue": QueueManager(database).status(), "next_safe_action": "show-review-packet"}, project)
        elif args.command == "show-review-packet":
            if not args.application_id:
                raise JobOpsError("APPLICATION_ID_REQUIRED", "Select an application review packet.")
            database = _database(project)
            with database.connect() as connection:
                row = connection.execute("SELECT packet_id,content_hash,status,relative_path,created_at FROM review_packets WHERE application_id=? ORDER BY created_at DESC, rowid DESC LIMIT 1", (args.application_id,)).fetchone()
                job = connection.execute("SELECT company,title,official_url FROM jobs WHERE job_id=(SELECT job_id FROM applications WHERE application_id=?)", (args.application_id,)).fetchone()
                counts = {"materials": connection.execute("SELECT COUNT(*) FROM materials WHERE application_id=?", (args.application_id,)).fetchone()[0], "stopped_fields": connection.execute("SELECT COUNT(*) FROM application_fields WHERE application_id=? AND status='STOP_REQUIRED'", (args.application_id,)).fetchone()[0]}
            if row is None:
                raise JobOpsError("REVIEW_PACKET_NOT_FOUND", "Review packet does not exist.")
            emit({"status": row["status"], "application_id": args.application_id, "packet_id": row["packet_id"], "content_hash": row["content_hash"], "secure_ref": row["relative_path"], "job": dict(job) if job else None, **counts, "private_values_emitted": 0, "next_safe_action": "approve-review-packet"}, project)
        elif args.command == "approve-review-packet":
            if not args.application_id:
                raise JobOpsError("APPLICATION_ID_REQUIRED", "Select the reviewed application.")
            database = _database(project)
            with database.connect() as connection:
                row = connection.execute("SELECT context_json FROM application_bindings WHERE application_id=?", (args.application_id,)).fetchone()
            if row is None:
                raise JobOpsError("APPLICATION_BINDING_MISSING", "The current approval binding is missing.")
            context = ApprovalContext.from_dict(json.loads(row[0])); approval = issue_approval(context=context, user_confirmed=True)
            result = ExternalActionGateway(database, ExternalActionPolicy.production_disabled()).persist_approval(approval, context)
            promoted = QueueManager(database).promote_next_deferred()
            emit({**result, "promoted": promoted.as_dict(), "phase5_authorization": "ABSENT", "next_safe_action": "NONE_EXTERNAL_ACTIONS_REMAIN_DISABLED"}, project)
        elif args.command == "reject-review-packet":
            if not args.application_id:
                raise JobOpsError("APPLICATION_ID_REQUIRED", "Select the review packet to reject.")
            database = _database(project); manager = QueueManager(database)
            result = manager.release_application(args.application_id, reason="USER_REJECTED_REVIEW_PACKET")
            promoted = manager.promote_next_deferred()
            emit({**result, "promoted": promoted.as_dict(), "next_safe_action": "run-queue"}, project)
        elif args.command == "revise-application":
            if not args.application_id:
                raise JobOpsError("APPLICATION_ID_REQUIRED", "Select the application to revise.")
            database = _database(project); manager = QueueManager(database)
            result = manager.request_revision(args.application_id, reason="USER_REQUESTED_REVIEW_PACKET_REVISION")
            promoted = manager.promote_next_deferred()
            emit({**result, "promoted": promoted.as_dict(), "next_safe_action": "run-to-awaiting-approval"}, project)
        elif args.command in {"resume-blocked", "retry-safe-step", "explain"}:
            if not args.application_id:
                raise JobOpsError("APPLICATION_ID_REQUIRED", "Select an application.")
            database = _database(project); recovery = RecoveryManager(database)
            if args.command == "explain": result = recovery.explain(args.application_id)
            else:
                with database.connect() as connection:
                    row = connection.execute("SELECT context_hash FROM application_bindings WHERE application_id=?", (args.application_id,)).fetchone()
                result = recovery.resume_safe_step(
                    args.application_id,
                    validation_material={"context_hash": row[0] if row else None},
                    explicit_ineligible_override=bool(args.override_ineligible),
                )
            emit({**result, "next_safe_action": "run-to-awaiting-approval"}, project)
        elif args.command == "verify-release":
            from .release import verify_release, write_release_reports
            database = _database(project)
            result = verify_release(project, database, require_independent=bool(args.require_independent))
            write_release_reports(project, result)
            emit(result, project)
            return 0 if result["status"] == "PASS" else 2
        else:
            raise JobOpsError("UNKNOWN_COMMAND", "The requested command is not registered in this build.", command=args.command)
        return 0
    except JobOpsError as exc:
        emit({**exc.as_dict(), "next_safe_action": "REVIEW_BLOCKING_CODE"}, project)
        return 2
    except Exception as exc:
        emit({"status": "FAILED", "code": type(exc).__name__, "message": "Operation failed without exposing private values.", "next_safe_action": "REVIEW_LOCAL_LOGS"}, project)
        return 1


if __name__ == "__main__":
    sys.exit(main())
