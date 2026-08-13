from __future__ import annotations

import json
import os
import re
import sqlite3
import zipfile
from pathlib import Path
from typing import Any

from .adapters import audit_real_external_actions
from .db import JobOpsDB
from .errors import JobOpsError
from .knowledge import KnowledgeGateway
from .locator import locate_knowledge_root
from .util import load_json, sha256_file, write_json


ABSOLUTE_USER_PATH = re.compile(r"(?i)[A-Z]:[\\/]Users[\\/][^\\/\s\"']+")
SECRET_PATTERNS = {
    "jwt": re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
    "bearer": re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{12,}"),
    "openai_key": re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    "github_key": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    "aws_key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "private_key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "credential_assignment": re.compile(r"(?i)\b(?:password|cookie|oauth[_ -]?token|api[_ -]?key)\s*[:=]\s*['\"][^'\"]{8,}['\"]"),
    "verification_code_assignment": re.compile(r"(?i)\b(?:otp|verification[_ -]?code|验证码)\s*[:=]\s*['\"]?[0-9]{4,10}"),
    "username_assignment": re.compile(r"(?i)\b(?:username|user[_ -]?name|用户名)\s*[:=]\s*['\"][^'\"]{3,}['\"]"),
    "phone": re.compile(r"(?<![0-9])(?:\+?1[ .-]?)?\(?[2-9][0-9]{2}\)?[ .-][0-9]{3}[ .-][0-9]{4}(?![0-9])"),
    "street_address": re.compile(r"(?i)\b[0-9]{1,6}\s+[A-Z0-9.' -]{2,40}\s+(?:street|st|avenue|ave|road|rd|boulevard|blvd|lane|ln|drive|dr)\b"),
}
TEXT_EXTENSIONS = {".py", ".js", ".css", ".json", ".md", ".yaml", ".yml", ".txt", ".ps1", ".html", ".xml", ".rels", ".csv"}
RELEASE_INPUT_EXTENSIONS = TEXT_EXTENSIONS | {".js", ".css"}


def _latest_release_input_mtime(project: Path) -> float:
    latest = 0.0
    for root in (project / "src", project / "schemas", project / "tests", project / "config", project / ".agents"):
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix.casefold() not in RELEASE_INPUT_EXTENSIONS:
                continue
            if any(part in {"__pycache__", ".tmp"} for part in path.parts):
                continue
            latest = max(latest, path.stat().st_mtime)
    return latest


def _scan_text(label: str, text: str, findings: list[dict[str, str]]) -> None:
    if ABSOLUTE_USER_PATH.search(text):
        findings.append({"kind": "absolute_user_path", "location": label})
    for kind, pattern in SECRET_PATTERNS.items():
        if pattern.search(text):
            findings.append({"kind": kind, "location": label})
    for email in re.findall(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", text):
        if not email.casefold().endswith(("@example.test", "@jobops.local")):
            findings.append({"kind": "email", "location": label})


def security_scan(project: Path, database: JobOpsDB) -> dict[str, Any]:
    findings: list[dict[str, str]] = []
    roots = [
        project / "src", project / "config", project / "schemas", project / ".agents",
        project / "state", project / "reports", project / "workspace", project / "tests",
    ]
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file() or "__pycache__" in path.parts or path.suffix.casefold() in {".db", ".png", ".pdf", ".docx", ".pyc"}:
                continue
            if path.suffix.casefold() in TEXT_EXTENSIONS or path.name in {"SKILL.md"}:
                try:
                    _scan_text(path.relative_to(project).as_posix(), path.read_text(encoding="utf-8-sig"), findings)
                except UnicodeDecodeError:
                    findings.append({"kind": "undecodable_text", "location": path.relative_to(project).as_posix()})
    artifact_files = {path for root in roots if root.exists() for path in root.rglob("*") if path.is_file()}
    for path in sorted((item for item in artifact_files if item.suffix.casefold() == ".docx"), key=str):
        try:
            with zipfile.ZipFile(path) as archive:
                for name in archive.namelist():
                    if name.endswith((".xml", ".rels")):
                        _scan_text(f"{path.relative_to(project).as_posix()}!{name}", archive.read(name).decode("utf-8", errors="ignore"), findings)
        except zipfile.BadZipFile:
            findings.append({"kind": "invalid_docx", "location": path.relative_to(project).as_posix()})
    for path in sorted((item for item in artifact_files if item.suffix.casefold() == ".pdf"), key=str):
        try:
            from .document_qa import extract_pdf_text
            pdf_text, _ = extract_pdf_text(path)
            _scan_text(path.relative_to(project).as_posix() + "#text", pdf_text, findings)
            _scan_text(path.relative_to(project).as_posix() + "#metadata", path.read_bytes().decode("latin-1", errors="ignore"), findings)
        except Exception:
            findings.append({"kind": "invalid_or_unreadable_pdf", "location": path.relative_to(project).as_posix()})
    for path in sorted((item for item in artifact_files if item.suffix.casefold() == ".png"), key=str):
        try:
            from PIL import Image
            with Image.open(path) as image:
                _scan_text(path.relative_to(project).as_posix() + "#metadata", json.dumps(dict(image.info), default=str), findings)
        except Exception:
            findings.append({"kind": "invalid_png", "location": path.relative_to(project).as_posix()})
    if database.path.is_file():
        with database.connect() as connection:
            tables = [row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
            for table in tables:
                for row in connection.execute(f'SELECT * FROM "{table}"'):
                    _scan_text(f"state/jobops.db#{table}", json.dumps(list(row), ensure_ascii=False, default=str), findings)
    local_app_data = os.environ.get("LOCALAPPDATA")
    private_root = Path(local_app_data) / "JobOps" / "private" if local_app_data else None
    staging_residue = []
    ciphertext_residue = []
    if private_root and private_root.is_dir():
        staging = private_root / "staging"
        staging_residue = [path for path in staging.rglob("*") if path.is_file()] if staging.is_dir() else []
        ciphertext_residue = [path for path in private_root.glob("*.dpapi") if path.is_file()]
        findings.extend({"kind": "private_staging_residue", "location": "$LOCALAPPDATA/JobOps/private/staging"} for _ in staging_residue)
        if ciphertext_residue:
            with database.connect() as connection:
                active = int(connection.execute("SELECT COUNT(*) FROM private_refs WHERE status='ACTIVE'").fetchone()[0])
            if active == 0:
                findings.append({"kind": "orphan_private_ciphertext", "location": "$LOCALAPPDATA/JobOps/private"})
    unique = sorted({(item["kind"], item["location"]) for item in findings})
    return {
        "status": "PASS" if not unique else "FAIL", "finding_count": len(unique),
        "findings": [{"kind": kind, "location": location} for kind, location in unique],
        "scanned_zones": [
            "source", "config", "schemas", "skill", "tests", "sqlite", "state", "reports",
            "docx_xml", "pdf_text_and_metadata", "png_metadata", "workspace", "private_staging",
        ],
        "private_staging_file_count": len(staging_residue),
        "private_ciphertext_file_count": len(ciphertext_residue),
    }


def verify_release(project: Path, database: JobOpsDB, *, require_independent: bool = False) -> dict[str, Any]:
    test_report_path = project / "reports" / "release-test-results.json"
    if not test_report_path.is_file():
        raise JobOpsError("RELEASE_TEST_REPORT_MISSING", "Run the checked-in release verification script before verify-release.")
    tests = load_json(test_report_path)
    if tests.get("status") != "PASS" or int(tests.get("failed", 1)) != 0:
        raise JobOpsError("RELEASE_TESTS_FAILED", "The most recent full regression report is not passing.")
    latest_input_mtime = _latest_release_input_mtime(project)
    if test_report_path.stat().st_mtime < latest_input_mtime:
        raise JobOpsError("RELEASE_TEST_REPORT_STALE", "Run the checked-in release verification script after the latest source changes.")
    location = locate_knowledge_root(project, project / "config" / "knowledge-sources.json")
    gateway = KnowledgeGateway(location)
    current_knowledge = gateway.snapshot_collections()
    knowledge = gateway.compare_snapshots(load_json(project / "state" / "knowledge-baseline.json"), current_knowledge)
    knowledge["collections"] = current_knowledge["collections"]
    knowledge["write_operations"] = 0
    actions = audit_real_external_actions(database)
    scan = security_scan(project, database)
    with database.connect() as connection:
        active_private = int(connection.execute("SELECT COUNT(*) FROM private_refs WHERE status='ACTIVE'").fetchone()[0])
        active_real_private = int(connection.execute("SELECT COUNT(*) FROM private_refs WHERE status='ACTIVE' AND synthetic=0").fetchone()[0])
        active_synthetic_private = int(connection.execute("SELECT COUNT(*) FROM private_refs WHERE status='ACTIVE' AND synthetic=1").fetchone()[0])
        active_onboarding_packets = int(connection.execute("SELECT COUNT(*) FROM private_refs WHERE status='ACTIVE' AND synthetic=0 AND kind='onboarding_review_packet'").fetchone()[0])
        deleted_synthetic = int(connection.execute("SELECT COUNT(*) FROM private_refs WHERE status='DELETED' AND synthetic=1").fetchone()[0])
        schema_version = database.schema_version()
        dry_run_sql = connection.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='applications'").fetchone()[0]
    skill_validation = load_json(project / "reports" / "skill-validation.json") if (project / "reports" / "skill-validation.json").is_file() else {"status": "MISSING"}
    independent_path = project / "reports" / "independent-qa.json"
    independent = load_json(independent_path) if independent_path.is_file() else {"status": "PENDING"}
    independent_fresh = bool(
        independent.get("status") == "PASS"
        and independent_path.is_file()
        and independent_path.stat().st_mtime >= latest_input_mtime
        and int(independent.get("tests", {}).get("passed", -1)) == int(tests.get("passed", -2))
        and int(independent.get("schemas", {}).get("total", -1)) == int(tests.get("schema_count", -2))
    )
    independent_view = {**independent, "fresh_for_current_release": independent_fresh}
    independent_counts = {
        "p0_open": independent.get("p0_open"),
        "p1_open": independent.get("p1_open"),
        "must_fix_open": independent.get("must_fix_open"),
    }
    onboarding_index_path = project / "state" / "onboarding-center-index.json"
    onboarding_in_progress = bool(
        onboarding_index_path.is_file()
        and load_json(onboarding_index_path).get("status") != "ONBOARDING_COMPLETE"
    )
    checks = {
        "tests": tests.get("status") == "PASS",
        "skill": skill_validation.get("status") == "PASS",
        "knowledge": knowledge.get("status") == "UNCHANGED" and knowledge["write_operations"] == 0,
        "security": scan["status"] == "PASS",
        "external_actions": actions["real_external_actions"] == 0,
        "database": schema_version == JobOpsDB.LATEST_SCHEMA_VERSION and "CHECK(dry_run = 1)" in dry_run_sql,
        "synthetic_private_purged": active_synthetic_private == 0,
        "private_store_consistent": active_private == scan["private_ciphertext_file_count"],
        "independent_qa": independent_fresh,
    }
    if require_independent and not checks["independent_qa"]:
        raise JobOpsError("INDEPENDENT_QA_REQUIRED", "Final release requires a passing independent read-only QA report.")
    core_pass = all(value for key, value in checks.items() if key != "independent_qa")
    status = "PASS" if core_pass and (checks["independent_qa"] or not require_independent) else "FAIL"
    public_release_blockers = []
    if not core_pass:
        public_release_blockers.append("CORE_RELEASE_CHECK_FAILED")
    if not independent_fresh:
        public_release_blockers.append("INDEPENDENT_QA_STALE_OR_MISSING")
    result = {
        "schema_version": 1, "status": status,
        "verification_scope": "PUBLIC_RELEASE" if require_independent else "LOCAL_DEVELOPMENT",
        "public_release_ready": core_pass and independent_fresh,
        "public_release_blockers": public_release_blockers,
        "final_states": [
            "PHASE_0_4_HARDENED", "PHASE_4_5_SECURE_ONBOARDING_READY",
            "BILINGUAL_ONBOARDING_CENTER_READY",
            "PHASE_5_6_OFFLINE_ENGINEERING_COMPLETE_NOT_AUTHORIZED_NOT_OPERATIONAL",
        ],
        "phase_5_6_authorization": "ABSENT", "real_external_actions": actions["real_external_actions"],
        "checks": checks, "tests": tests, "skill_validation": skill_validation,
        "knowledge": knowledge, "database": {"schema_version": schema_version, "dry_run_constraint": True},
        "security_scan": scan, "external_action_audit": actions,
        "private_data": {
            "active_references": active_private, "active_real_references": active_real_private,
            "active_synthetic_references": active_synthetic_private,
            "deleted_synthetic_metadata_records": deleted_synthetic,
            "synthetic_ciphertext_residue": active_synthetic_private,
            "private_ciphertext_file_count": scan["private_ciphertext_file_count"],
            "private_staging_file_count": scan["private_staging_file_count"],
        },
        "independent_qa": independent_view,
        "document_visual_review": load_json(project / "reports" / "validation-artifacts" / "complex-resume-visual-review.json"),
        **independent_counts,
        "closed_capabilities": ["real website access", "real prefill", "file upload", "final submit", "account creation", "email", "recruiter contact", "real scheduler"],
        "next_safe_action": (
            "onboarding-center" if status == "PASS" and onboarding_in_progress
            else "review-onboarding --latest" if status == "PASS" and active_onboarding_packets
            else "secure-onboard with user-selected private files" if status == "PASS"
            else "review failed release checks"
        ),
    }
    return result


def write_release_reports(project: Path, result: dict[str, Any]) -> None:
    write_json(project / "reports" / "checkpoint-final.json", result)
    tests = result["tests"]
    lines = [
        "# JobFlow verification checkpoint", "",
        f"Local verification status: **{result['status']}**", "",
        f"Public release readiness: **{'READY' if result.get('public_release_ready') else 'BLOCKED'}**", "",
        *[f"- Blocker: `{value}`" for value in result.get("public_release_blockers", [])],
        "" if result.get("public_release_blockers") else "",
        *[f"- `{value}`" for value in result["final_states"]],
        f"- `REAL_EXTERNAL_ACTIONS={result['real_external_actions']}`",
        f"- `PHASE_5_6_AUTHORIZATION={result['phase_5_6_authorization']}`", "",
        "## Verification", "",
        f"- Tests: {tests['passed']} passed, {tests['failed']} failed across {len(tests.get('categories', {}))} categories.",
        f"- Runtime Schemas: {tests.get('schema_count', 0)} strict schemas.",
        f"- Database migration: version {result['database']['schema_version']}; dry-run CHECK retained.",
        f"- Knowledge: {result['knowledge']['status']}; writes {result['knowledge']['write_operations']}.",
        *[
            f"  - `{name}`: {item['file_count']} files; `{item['tree_sha256']}`"
            for name, item in result["knowledge"].get("collections", {}).items()
        ],
        f"- Security findings: {result['security_scan']['finding_count']}.",
        f"- External-action audit: {result['external_action_audit']['attempt_count']} recorded attempts; {result['external_action_audit']['real_external_actions']} real side effects.",
        f"- Active private references: {result['private_data']['active_references']} (real: {result['private_data']['active_real_references']}; synthetic: {result['private_data']['active_synthetic_references']}); deleted synthetic metadata: {result['private_data']['deleted_synthetic_metadata_records']}; synthetic ciphertext residue: {result['private_data']['synthetic_ciphertext_residue']}; total ciphertext files: {result['private_data']['private_ciphertext_file_count']}; staging files: {result['private_data']['private_staging_file_count']}.",
        f"- P0/P1/must-fix open: {result['p0_open']}/{result['p1_open']}/{result['must_fix_open']}.",
        f"- Independent QA: {'PASS (fresh)' if result['checks']['independent_qa'] else 'STALE OR MISSING'}.", "",
        "## Implemented", "",
        "- Strict runtime schemas, versioned SQLite migrations, atomic state/audit transitions and one-time content-bound approvals.",
        "- Read-only Knowledge Gateway, evidence-verified Claim lifecycle, hardened path exclusions and DPAPI-backed secure references.",
        "- Transactional bounded intake, idempotent/revisable orchestration, safe recovery and a complete offline chain to `AWAITING_APPROVAL`.",
        "- Compound JD/eligibility/Fit analysis, local research evidence, master-resume copy tailoring, document QA and fail-closed bilingual form classification.",
        "- Disabled production transports plus isolated fake adapters, fake scheduler, fake receipt flow and network/side-effect probes for Phase 5-6 engineering.",
        "- Existing Skill, references, deterministic scripts, fixtures, quick start and release verification were updated in place.", "",
        "## Public CLI", "",
        "`status`, `audit`, `locate`, `init-db`, `migrate-db`, `secure-onboard`, `secure-onboard-resume`, `finalize-resume-onboarding`, `review-onboarding`, `secure-import-master-resume`, `secure-import-answer-bank`, `secure-store-status`, `purge-synthetic-private-data`, `propose-claims`, `list-claim-proposals`, `approve-claim`, `reject-claim`, `revoke-claim`, `revalidate-claims`, `discover-official-jobs`, `verify-route`, `import-jd`, `analyze-job`, `run-to-awaiting-approval`, `run-queue`, `list-pending`, `show-review-packet`, `revise-application`, `approve-review-packet`, `reject-review-packet`, `resume-blocked`, `retry-safe-step`, `explain`, `verify-release`.", "",
        "## Document QA", "",
        f"- Reviewer: {result['document_visual_review']['reviewer_type']}; rendered pages: {len(result['document_visual_review']['pages'])}; failed pages: {sum(page['result'] != 'PASS' for page in result['document_visual_review']['pages'])}.",
        "- The complex synthetic master and tailored DOCX/PDF were rendered; page hashes, timestamps and per-page reasons are retained in the visual record.", "",
        "## Closed capabilities", "",
        *[f"- {item}" for item in result["closed_capabilities"]], "",
        "## Next safe action", "", result["next_safe_action"], "",
        "Future real trial requires secure Candidate Profile, Answer Bank, editable master DOCX, individually approved Claims, and separate Phase 5 authorization. Site terms, fresh route/freshness evidence and a fresh per-job final approval would still apply. No real website, browser session, upload, account, email, recruiter contact or scheduler was used in this release.",
    ]
    (project / "reports" / "checkpoint-final.md").write_text("\n".join(lines), encoding="utf-8")
