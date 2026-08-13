from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from .adapters import FakeBrowserPrefillAdapter
from .approvals import ApprovalContext, UploadBinding
from .ats_browser import analyze_local_ats_form, build_browser_action_plan
from .claim_registry import ClaimRegistry
from .claims import verify_claim_evidence
from .collector import JobCollector
from .db import JobOpsDB
from .document_builder import export_docx_to_pdf, render_pdf_to_pngs, tailor_master_resume
from .document_qa import automated_visual_probe, extract_pdf_text, structural_qa
from .eligibility import check_eligibility
from .errors import JobOpsError
from .evidence import map_evidence
from .fit import compute_fit
from .forms import map_fields
from .jd_analyzer import analyze_jd
from .private_onboarding import PrivateOnboarding
from .queue_manager import QueueManager
from .research import OfflineResearchSource, build_offline_research_packet
from .runtime_schema import validate_named
from .security import assert_safe_path
from .sourcing import assess_job_freshness, verify_source_route
from .util import canonical_json, iso_utc, load_json, sha256_bytes, sha256_file, stable_id


MAX_JD_SOURCE_BYTES = 32 * 1024 * 1024
MAX_JD_TEXT_CHARACTERS = 4_000_000
MAX_JD_HTML_EVENTS = 300_000
MAX_JD_PDF_PAGES = 200


class _HTMLText(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.events = 0
        self.characters = 0

    def _tick(self) -> None:
        self.events += 1
        if self.events > MAX_JD_HTML_EVENTS:
            raise JobOpsError("JD_HTML_EVENT_LIMIT_EXCEEDED", "The local JD HTML exceeds the safe parser event limit.")

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._tick()

    def handle_endtag(self, tag: str) -> None:
        self._tick()

    def handle_data(self, data: str) -> None:
        self._tick()
        if data.strip():
            self.characters += len(data)
            if self.characters > MAX_JD_TEXT_CHARACTERS:
                raise JobOpsError("JD_TEXT_LIMIT_EXCEEDED", "The local JD text exceeds the safe analysis limit.")
            self.parts.append(data.strip())


class SyntheticKnowledgeGateway:
    """Narrow verifier for the checked-in synthetic evidence fixture only."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve(strict=True)
        self.definitions = {"personal_redacted": {"question_only_prefixes": []}}

    def safe_path(self, source_id: str, relative_path: str | Path) -> Path:
        if source_id != "personal_redacted":
            raise JobOpsError("NON_PERSONAL_SOURCE", "Synthetic evidence exposes only personal_redacted semantics.")
        return assert_safe_path(self.root / Path(relative_path), self.root, (), ())

    def read_text(self, source_id: str, relative_path: str | Path) -> str:
        return self.safe_path(source_id, relative_path).read_text(encoding="utf-8-sig")


def _pdftoppm() -> str:
    found = shutil.which("pdftoppm")
    if not found:
        raise JobOpsError("PDF_RENDERER_MISSING", "Poppler pdftoppm is required for document QA.")
    path = Path(found)
    if path.suffix.casefold() in {".cmd", ".bat"}:
        candidate = path.parents[2] / "native" / "poppler" / "Library" / "bin" / "pdftoppm.exe"
        if candidate.is_file():
            return str(candidate)
    return found


def _read_jd(path: Path, source_type: str | None = None) -> tuple[str, str, str | None]:
    kind = (source_type or path.suffix.lstrip(".")).casefold()
    try:
        with path.open("rb") as handle:
            raw = handle.read(MAX_JD_SOURCE_BYTES + 1)
    except OSError as exc:
        raise JobOpsError("JD_INPUT_UNREADABLE", "The selected local JD input could not be read.") from exc
    if len(raw) > MAX_JD_SOURCE_BYTES:
        raise JobOpsError("JD_INPUT_TOO_LARGE", "The local JD input exceeds the safe parser limit.", maximum_bytes=MAX_JD_SOURCE_BYTES)
    if kind in {"txt", "text"}:
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise JobOpsError("JD_INPUT_ENCODING_INVALID", "Text and HTML JD inputs must use UTF-8 encoding.") from exc
        if len(text) > MAX_JD_TEXT_CHARACTERS:
            raise JobOpsError("JD_TEXT_LIMIT_EXCEEDED", "The local JD text exceeds the safe analysis limit.")
        return text, "txt", None
    if kind in {"html", "htm"}:
        try:
            html = raw.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise JobOpsError("JD_INPUT_ENCODING_INVALID", "Text and HTML JD inputs must use UTF-8 encoding.") from exc
        parser = _HTMLText(); parser.feed(html)
        return "\n".join(parser.parts), "html", None
    if kind == "pdf":
        text, _ = extract_pdf_text(path, page_limit=MAX_JD_PDF_PAGES, character_limit=MAX_JD_TEXT_CHARACTERS)
        if not text.strip():
            raise JobOpsError("JD_PDF_TEXT_MISSING", "The local PDF job description contains no extractable text.")
        return text, "pdf", None
    if kind in {"snapshot", "page_snapshot", "json"}:
        try:
            value = json.loads(raw.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise JobOpsError("JD_SNAPSHOT_INVALID", "A saved page snapshot must be valid UTF-8 JSON.") from exc
        if not isinstance(value, dict):
            raise JobOpsError("JD_SNAPSHOT_INVALID", "A saved page snapshot must be a JSON object.")
        material = value.get("text") or value.get("html") or value.get("content")
        if not isinstance(material, str) or not material.strip():
            raise JobOpsError("JD_SNAPSHOT_CONTENT_MISSING", "A saved page snapshot needs local text or HTML content.")
        if len(material) > MAX_JD_TEXT_CHARACTERS:
            raise JobOpsError("JD_TEXT_LIMIT_EXCEEDED", "The local JD text exceeds the safe analysis limit.")
        if value.get("html"):
            parser = _HTMLText(); parser.feed(material); material = "\n".join(parser.parts)
        return material, "page_snapshot", str(value.get("source_url")) if value.get("source_url") else None
    raise JobOpsError("JD_FORMAT_UNSUPPORTED", "Offline JD input must be TXT, HTML, PDF, or a saved page snapshot.", source_type=kind)


class JobOpsOrchestrator:
    def __init__(self, project: Path, database: JobOpsDB, onboarding: PrivateOnboarding) -> None:
        self.project = project.resolve()
        self.database = database
        self.database.initialize()
        self.onboarding = onboarding
        self.onboarding.assert_outside_project(self.project)
        self.queue = QueueManager(database)
        self.schemas = self.project / "schemas"

    def secure_onboard_synthetic(self) -> dict[str, Any]:
        fixtures = self.project / "tests" / "fixtures"
        profile = self.onboarding.import_file("candidate_profile", fixtures / "synthetic-forward-profile.json", synthetic=True)
        answers = self.onboarding.import_file("answer_bank", fixtures / "synthetic-forward-answer-bank.json", synthetic=True)
        master = self.onboarding.import_file("master_resume_docx", fixtures / "complex-master-resume.docx", synthetic=True)
        return {
            "status": "SYNTHETIC_ONBOARDING_READY", "profile_ref": profile["secure_ref"],
            "answer_bank_ref": answers["secure_ref"], "master_resume_ref": master["secure_ref"],
            "private_values_emitted": 0, "synthetic": True,
        }

    def _load_json_ref(self, reference: str) -> dict[str, Any]:
        try:
            value = json.loads(self.onboarding.read_bytes(reference).decode("utf-8"))
        except JobOpsError:
            raise
        except Exception as exc:
            raise JobOpsError("SECURE_JSON_INVALID", "Encrypted private JSON could not be parsed.") from exc
        if not isinstance(value, dict):
            raise JobOpsError("SECURE_JSON_INVALID", "Encrypted private JSON must contain an object.")
        return value

    def _synthetic_claims(self) -> list[dict[str, Any]]:
        root = self.project / "tests" / "fixtures" / "synthetic-knowledge"
        gateway = SyntheticKnowledgeGateway(root)
        path = root / "case.md"
        file_hash = sha256_file(path)
        wordings = [
            "Analyzes synthetic datasets with reproducible methods.",
            "Built a synthetic SQL and Python analysis with documented checks.",
            "Created a local-only queue capacity simulation.",
            "Python, SQL, and structured analysis.",
            "Completed a synthetic degree fixture.",
        ]
        registry = ClaimRegistry(self.database, gateway)
        results: list[dict[str, Any]] = []
        for index, wording in enumerate(wordings, 1):
            claim = {
                "claim_id": f"CLM-SYNTHETIC-{index:02d}", "raw_fact": wording,
                "allowed_wording": [wording], "forbidden_wording": ["real client"],
                "responsibility_boundary": {"candidate": "performed only the synthetic fixture action", "team": "not applicable", "ai": "generated synthetic inputs"},
                "evidence": [{"kind": "synthetic_fixture", "value": wording}],
                "source_refs": [{
                    "source_id": "personal_redacted", "relative_path": "case.md", "heading": "Synthetic Evidence",
                    "excerpt": wording, "excerpt_fingerprint": sha256_bytes(wording.encode("utf-8")), "fingerprint": file_hash,
                }],
                "approved_for_external": False, "lifecycle_status": "proposed", "sensitivity": "synthetic",
                "last_verified_at": "2026-08-12T00:00:00Z", "expires_at": "2099-01-01T00:00:00Z",
            }
            stored = registry.propose(claim)
            if stored["lifecycle_status"] != "approved":
                stored = registry.approve(claim["claim_id"], allowed_uses=("resume", "cover_letter", "application_narrative"))
            verify_claim_evidence(stored, gateway)
            results.append(stored)
        return results

    @staticmethod
    def _crash(point: str | None, current: str) -> None:
        if point == current:
            raise JobOpsError("SYNTHETIC_CRASH_INJECTED", "A deterministic local crash was injected for recovery testing.", point=current)

    def run_to_awaiting(
        self,
        input_path: Path,
        *,
        profile_ref: str,
        master_resume_ref: str,
        answer_bank_ref: str,
        route_fixture: Path,
        form_fixture: Path,
        research_fixture: Path,
        source_type: str | None = None,
        synthetic: bool = False,
        crash_after_step: str | None = None,
    ) -> dict[str, Any]:
        if not synthetic:
            raise JobOpsError("REAL_PROFILE_FORWARD_TEST_REQUIRES_USER_REVIEW", "This build runs the unattended forward chain only with explicitly synthetic fixtures.")
        input_path = input_path.resolve(strict=True)
        content, source_format, snapshot_url = _read_jd(input_path, source_type)
        normalized = content.replace("\r\n", "\n").replace("\r", "\n").strip() + "\n"
        intake_key = sha256_bytes(normalized.encode("utf-8"))
        locator = input_path.name
        admission = self.queue.enqueue(intake_key, source_type=source_format, source_locator=locator)
        if admission.status == "DEFERRED":
            return {**admission.as_dict(), "real_external_actions": 0}
        if admission.status == "ACCEPTED":
            with self.database.connect() as connection:
                row = connection.execute("SELECT application_id,status FROM applications WHERE job_id=(SELECT job_id FROM jd_snapshots WHERE content_hash=?)", (intake_key,)).fetchone()
            if row is None:
                raise JobOpsError("INTAKE_APPLICATION_MISSING", "Accepted intake has no associated application.")
            if str(row["status"]) in {"NEEDS_USER_INPUT", "MATERIALS_NEEDS_CORRECTION", "SITE_CHANGED", "APPROVAL_EXPIRED"}:
                admission = self.queue.reserve_reprocess(intake_key, str(row["application_id"]))
                if admission.status == "DEFERRED":
                    return {**admission.as_dict(), "real_external_actions": 0}
            else:
                return {"status": str(row["status"]), "application_id": str(row["application_id"]), "deduplicated": True, "real_external_actions": 0}
        self._crash(crash_after_step, "after_reservation")

        route_value = load_json(route_fixture)
        official_path = self.project / str(route_value["official_snapshot"])
        official_hash = sha256_file(official_path)
        binding = dict(route_value["tenant_binding"])
        binding.update({"official_page_hash": official_hash, "jd_snapshot_hash": intake_key})
        policy = load_json(self.project / "config" / "policy.json")
        route = verify_source_route(
            official_entry_url=route_value["official_entry_url"], current_url=route_value["current_url"],
            navigation_history=route_value["navigation_history"], approved_ats_hosts=policy["approved_ats_hosts"],
            guest_available=route_value.get("guest_available"), tenant_binding=binding,
            official_page_hash=official_hash, jd_snapshot_hash=intake_key,
        )
        validate_named("source-route", route.as_dict(), self.schemas)
        collector = JobCollector(self.database, self.project / "workspace" / "jobs", self.project)
        collected = collector.collect_text(
            normalized, source_type=source_format, source_locator=locator,
            company="Synthetic", title="Synthetic", official_url=route.official_entry_url if not snapshot_url else snapshot_url,
        )
        self._crash(crash_after_step, "after_snapshot")
        job_id = str(collected["job_id"])
        application_id = stable_id("APP", job_id)
        jd = analyze_jd(normalized)
        if jd.snapshot_hash != intake_key:
            raise JobOpsError("JD_HASH_DRIFT", "JD analysis did not bind to the stored normalized snapshot.")
        discovered_at = iso_utc()
        validate_named("job", {
            "job_id": job_id, "source_type": source_format, "source_locator": locator, "official_url": route.official_entry_url,
            "company": jd.company, "title": jd.title, "location": jd.location, "status": "SNAPSHOTTED", "discovered_at": discovered_at,
        }, self.schemas)
        validate_named("jd-snapshot", {
            "snapshot_id": stable_id("JDS", intake_key), "job_id": job_id, "source_format": source_format,
            "content_hash": intake_key, "relative_path": str(collected["snapshot_path"]), "captured_at": discovered_at,
            "source_url": snapshot_url,
        }, self.schemas)
        profile = self._load_json_ref(profile_ref)
        profile["profile_ref"] = profile_ref
        validate_named("candidate-profile", profile, self.schemas)
        eligibility = check_eligibility(jd, profile)
        if eligibility.status != "ELIGIBLE":
            raise JobOpsError(eligibility.status, "The job cannot proceed to materials until every hard condition is confirmed.", hard_gaps=list(eligibility.hard_gaps), unknowns=list(eligibility.unknowns))
        claims = self._synthetic_claims()
        mappings = map_evidence(jd.hard_requirements, claims)
        fit = compute_fit(jd, profile, eligibility, evidence_mappings=mappings)
        validate_named("fit-result", fit.as_dict(), self.schemas)
        for requirement in jd.requirements:
            validate_named("requirement", requirement.as_dict(), self.schemas)
        analysis_id = stable_id("JDA", job_id, intake_key)
        analysis = {
            "analysis_id": analysis_id, "job_id": job_id, "snapshot_hash": intake_key,
            "company": jd.company, "title": jd.title, "location": jd.location, "level": jd.level,
            "responsibilities": list(jd.responsibilities), "requirements": [item.as_dict() for item in jd.requirements],
            "preferred_qualifications": list(jd.preferred_qualifications), "keywords": list(jd.keywords),
            "untrusted_instruction_signals": list(jd.untrusted_instruction_signals), "created_at": iso_utc(),
        }
        validate_named("jd-analysis", analysis, self.schemas)
        analysis_hash = sha256_bytes(canonical_json(analysis))
        self._crash(crash_after_step, "after_analysis")

        research_text = research_fixture.read_text(encoding="utf-8-sig")
        excerpt = "Example Analytics Lab uses documented checks for synthetic dataset analysis."
        research_source = OfflineResearchSource(
            title="Synthetic Company Update", url="https://example.com/news/synthetic-update", source_type="official_company",
            snapshot_path=research_fixture, snapshot_hash=sha256_file(research_fixture), published_at="2026-08-12T00:00:00Z",
            accessed_at=iso_utc(), evidence_excerpt=excerpt, evidence_fingerprint=sha256_bytes(excerpt.encode("utf-8")), official=True,
        )
        research = build_offline_research_packet(company=jd.company, findings=[{"claim": excerpt}], sources=[research_source])
        finding = {
            "finding_id": stable_id("RFN", research_source.snapshot_hash, excerpt), "claim": excerpt,
            "source_url": research_source.url, "source_type": research_source.source_type, "snapshot_hash": research_source.snapshot_hash,
            "published_at": research_source.published_at, "accessed_at": research_source.accessed_at, "evidence_excerpt": excerpt,
            "evidence_sha256": research_source.evidence_fingerprint, "freshness": "CURRENT", "official": True,
        }
        validate_named("research-finding", finding, self.schemas)

        answers = self._load_json_ref(answer_bank_ref)
        answers["full_name"] = profile_ref
        ats_safe_prefill: dict[str, Any] | None = None
        if form_fixture.suffix.casefold() in {".html", ".htm"}:
            form_analysis = analyze_local_ats_form(
                form_fixture.read_bytes(), route=route.as_dict(), blocked_categories=policy["blocked_form_categories"]
            )
            bindings: dict[str, dict[str, str]] = {}
            for item in form_analysis["fields"]:
                answer_key = str(item["answer_key"])
                if item["classification"] == "private_fixed" and answer_key == "full_name" and profile.get("candidate_display_name"):
                    bindings[str(item["control_ref"])] = {"kind": "secure_ref", "value": profile_ref}
                elif item["classification"] == "ordinary_fixed" and answer_key in answers:
                    candidate = str(answers[answer_key])
                    if candidate not in {"", "UNKNOWN", "UNANSWERED"}:
                        bindings[str(item["control_ref"])] = {"kind": "public_value", "value": candidate}
            browser_plan = build_browser_action_plan(form_analysis, bindings)
            fake_browser = FakeBrowserPrefillAdapter().prefill({
                "plan": browser_plan, "current_form_snapshot_hash": form_analysis["form_snapshot_hash"],
                "isolation_policy": "ISOLATED_FAKE_ONLY",
            })
            action_by_ref = {str(item["control_ref"]): item for item in browser_plan["actions"]}
            safe_questions = []
            for item in form_analysis["fields"]:
                action = action_by_ref[str(item["control_ref"])]
                safe_questions.append({
                    "id": item["control_ref"], "label": item["answer_key"], "answer_key": item["answer_key"],
                    "prompt_hash": item["prompt_hash"], "control_type": item["control_type"],
                    "classification": item["classification"], "reason": item["reason_code"],
                    "gate": "PREFILL_ALLOWED" if action["action"] == "PROPOSE_PREFILL" else "STOP_REQUIRED",
                    "action": "PREFILL_FROM_SECURE_STORE" if action["binding_kind"] == "SECURE_REF" else ("PREFILL" if action["action"] == "PROPOSE_PREFILL" else "STOP"),
                    "status": "READY" if action["action"] == "PROPOSE_PREFILL" else "STOPPED",
                    "secure_ref": action["binding_ref"] if action["binding_kind"] == "SECURE_REF" else None,
                    "redacted_summary": "PRIVATE_VALUE_PRESENT" if action["binding_kind"] == "SECURE_REF" else ("PUBLIC_VALUE_HASH_PRESENT" if action["binding_kind"] == "PUBLIC_VALUE_HASH" else "UNANSWERED"),
                })
            fields = {
                "fields": safe_questions,
                "sensitive_fields": [item["id"] for item in safe_questions if item["action"] == "STOP"],
                "unknown_fields": [item["id"] for item in safe_questions if item["classification"] == "unknown_stop"],
                "submit_blocked": True,
            }
            form_snapshot_hash = str(form_analysis["form_snapshot_hash"])
            ats_safe_prefill = {
                "schema_version": 1, "status": "LOCAL_ATS_PLAN_VALIDATED", "provider": form_analysis["provider"],
                "source_route_hash": route.route_hash, "form_snapshot_hash": form_snapshot_hash,
                "browser_plan_hash": browser_plan["plan_hash"], "fields_discovered": form_analysis["field_count"],
                "fields_proposed": browser_plan["fillable_count"], "fields_stopped": browser_plan["stopped_count"],
                "browser_adapter_status": fake_browser["status"], "fields_modified": fake_browser["fields_modified"],
                "submit_blocked": True, "upload_blocked": True, "account_creation_blocked": True,
                "browser_actions": fake_browser["browser_actions"], "network_actions": fake_browser["network_actions"],
                "real_external_actions": fake_browser["real_side_effects"],
            }
            validate_named("ats-vertical-evidence", ats_safe_prefill, self.schemas)
        else:
            form_value = load_json(form_fixture)
            fields = map_fields(form_value["fields"], {str(k): str(v) for k, v in answers.items()}, policy["blocked_form_categories"], page_context=str(form_value.get("page_context", "")))
            form_snapshot_hash = sha256_file(form_fixture)
        if fields["unknown_fields"]:
            raise JobOpsError("UNKNOWN_FORM_FIELDS", "Unrecognized form fields must be reviewed before a packet can be prepared.", fields=fields["unknown_fields"])
        field_records = []
        for field in fields["fields"]:
            status = "READY" if str(field["action"]).startswith("PREFILL") else "STOP_REQUIRED"
            record = {
                "field_id": stable_id("FLD", application_id, str(field["id"])), "application_id": application_id,
                "classification": field["classification"], "action": "PREFILL" if str(field["action"]).startswith("PREFILL") else "STOP",
                "status": status, "secure_ref": field.get("secure_ref"), "redacted_summary": field.get("redacted_summary"),
                "field_hash": sha256_bytes(canonical_json(field)),
            }
            validate_named("application-field", record, self.schemas)
            field_records.append(record)

        replacements = {
            "CANDIDATE_NAME": str(profile["candidate_display_name"]), "TARGET_ROLE": jd.title,
            "SUMMARY": claims[0]["allowed_wording"][0], "EXPERIENCE_BULLET": claims[1]["allowed_wording"][0],
            "PROJECT": claims[2]["allowed_wording"][0], "SKILLS": claims[3]["allowed_wording"][0],
            "EDUCATION": claims[4]["allowed_wording"][0],
        }
        with self.onboarding.staging_directory() as staging:
            master_path = staging / "master.docx"
            master_path.write_bytes(self.onboarding.read_bytes(master_resume_ref))
            resume_docx = staging / "resume.docx"
            resume_pdf = staging / "resume.pdf"
            diff = tailor_master_resume(master_path, resume_docx, replacements=replacements, claims=claims, synthetic=True)
            export_docx_to_pdf(resume_docx, resume_pdf, self.project / ".agents" / "skills" / "job-application-operator" / "scripts" / "export-docx-pdf.ps1")
            pages = render_pdf_to_pngs(resume_pdf, staging / "renders", _pdftoppm())
            visual = automated_visual_probe(pages)
            qa = structural_qa(resume_docx, resume_pdf, pages, visual_record=visual, page_limit=2)
            if qa.status != "PASS":
                raise JobOpsError("MATERIALS_NEEDS_CORRECTION", "Tailored resume failed structural or render QA.", qa=qa.as_dict())
            docx_ref = self.onboarding.import_bytes("generated_resume_docx", resume_docx.read_bytes(), synthetic=True)
            pdf_ref = self.onboarding.import_bytes("generated_resume_pdf", resume_pdf.read_bytes(), synthetic=True)
            visual_ref = self.onboarding.import_bytes("visual_evidence", canonical_json(visual), synthetic=True)
        self._crash(crash_after_step, "after_materials")

        freshness = assess_job_freshness(official_listing_present=True, application_form_available=True, checked_at=iso_utc())
        if freshness["status"] != "CURRENT":
            raise JobOpsError("JD_FRESHNESS_REQUIRED", "Official listing freshness must be current before review packet generation.")
        claim_set_hash = sha256_bytes(canonical_json([{"claim_id": item["claim_id"], "content_hash": item["content_hash"], "version": item["version"]} for item in claims]))
        uploads = [
            {"filename": "resume.pdf", "purpose": "resume", "sha256": pdf_ref["content_sha256"]},
        ]
        answers_hash = sha256_bytes(canonical_json(answers))
        packet_id = stable_id(
            "RPK", application_id, intake_key, str(profile["profile_version"]), claim_set_hash,
            str(pdf_ref["content_sha256"]), route.route_hash, form_snapshot_hash, answers_hash,
        )
        packet: dict[str, Any] = {
            "schema_version": 1, "status": "AWAITING_APPROVAL", "packet_id": packet_id, "application_id": application_id,
            "job": {"job_id": job_id, "company": jd.company, "title": jd.title, "official_url": route.official_entry_url},
            "jd_captured_at": analysis["created_at"], "fit": fit.as_dict(), "hard_gaps": list(eligibility.hard_gaps),
            "resume_bullets": [{"text": item["allowed_wording"][0], "claim_id": item["claim_id"], "evidence": item["source_refs"]} for item in claims],
            "master_resume_diff": diff, "form_questions": fields["fields"],
            "sensitive_fields": [item for item in fields["fields"] if item["action"] == "STOP"],
            "uploads": uploads, "external_actions": ["upload_material", "submit_application"],
            "source_route": route.as_dict(), "queue": self.queue.status(),
        }
        packet["content_hash"] = sha256_bytes(canonical_json(packet))
        validate_named("review-packet", packet, self.schemas)
        packet_ref = self.onboarding.import_bytes("review_packet", canonical_json(packet), synthetic=True)
        context = ApprovalContext(
            application_id=application_id, job_id=job_id, jd_snapshot_hash=intake_key,
            jd_freshness_hash=sha256_bytes(canonical_json(freshness)), source_route_hash=route.route_hash,
            canonical_url=route.current_url, ats_tenant=route.ats_tenant, ats_board=route.ats_board,
            ats_job_identity=route.ats_job_identity, profile_version=str(profile["profile_version"]),
            claim_set_hash=claim_set_hash, form_snapshot_hash=form_snapshot_hash, answers_hash=answers_hash,
            review_packet_hash=packet["content_hash"], uploads=tuple(UploadBinding(item["filename"], item["purpose"], item["sha256"]) for item in uploads),
            external_actions=("upload_material", "submit_application"), site_policy_version=str(policy["schema_version"]),
            unresolved_stops=tuple(
                str(item["id"]) for item in fields["fields"]
                if item["action"] == "STOP" and item["classification"] != "final_submit_stop"
            ),
            mandatory_unknowns=tuple(str(item) for item in fields["unknown_fields"]),
        ).normalized()
        self._crash(crash_after_step, "before_admission")
        materials = [
            {"material_id": stable_id("MAT", application_id, "resume_docx", str(docx_ref["content_sha256"])), "kind": "resume_docx", "path": docx_ref["secure_ref"], "content_hash": docx_ref["content_sha256"], "claim_ids": [item["claim_id"] for item in claims]},
            {"material_id": stable_id("MAT", application_id, "resume_pdf", str(pdf_ref["content_sha256"])), "kind": "resume_pdf", "path": pdf_ref["secure_ref"], "content_hash": pdf_ref["content_sha256"], "claim_ids": [item["claim_id"] for item in claims]},
            {"material_id": stable_id("MAT", application_id, "visual_evidence", str(visual_ref["content_sha256"])), "kind": "visual_evidence", "path": visual_ref["secure_ref"], "content_hash": visual_ref["content_sha256"], "claim_ids": []},
        ]
        admitted = self.queue.admit_awaiting(
            admission.reservation_id, context, snapshot_relative_path=str(collected["snapshot_path"]),
            job_details={"source_type": source_format, "source_locator": locator, "official_url": route.official_entry_url, "company": jd.company, "title": jd.title, "location": jd.location},
            secure_profile_ref=profile_ref, review_packet={"packet_id": packet_id, "content_hash": packet["content_hash"], "secure_ref": packet_ref["secure_ref"], "status": "AWAITING_APPROVAL"},
            material_records=materials, analysis_record={"analysis_id": analysis_id, "analysis": analysis, "analysis_hash": analysis_hash},
            research_records=[finding], field_records=field_records, source_route=route.as_dict(),
        )
        return {
            **admitted, "job_id": job_id, "review_packet_id": packet_id, "review_packet_ref": packet_ref["secure_ref"],
            "fit_recommendation": fit.recommendation, "document_qa": qa.as_dict(), "queue": self.queue.status(),
            "ats_safe_prefill": ats_safe_prefill, "real_external_actions": 0, "next_safe_action": "show-review-packet",
        }
