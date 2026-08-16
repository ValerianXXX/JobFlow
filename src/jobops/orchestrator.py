from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from .adapters import FakeBrowserPrefillAdapter
from .ai_operator import analyze_application_form_semantics, rank_application_claims
from .ai_runtime import AIAnalysisEngine
from .application_execution import build_application_execution_plan
from .application_field_resolution import (
    approval_unresolved_stop_ids,
    initial_application_field_status,
)
from .application_materials import build_material_plan, detect_material_requests
from .approvals import ApprovalContext, UploadBinding
from .ats_browser import analyze_local_ats_form, build_browser_action_plan
from .claim_registry import ClaimRegistry
from .claims import verify_claim_evidence
from .collector import JobCollector
from .db import JobOpsDB
from .document_builder import (
    build_cover_letter, discover_template_slots, export_docx_to_pdf,
    render_pdf_to_pngs, tailor_master_resume, tailor_master_resume_with_manifest,
)
from .document_qa import automated_visual_probe, extract_pdf_text, structural_qa
from .eligibility import check_eligibility
from .errors import JobOpsError
from .evidence import map_evidence
from .execution_bundle import build_application_execution_bundle
from .external_claims import (
    approved_external_claims, map_external_claim_evidence,
    validate_external_claim_set_integrity,
)
from .fit import compute_fit
from .forms import map_fields
from .jd_analyzer import analyze_jd
from .private_onboarding import PrivateOnboarding
from .queue_manager import QueueManager
from .research import OfflineResearchSource, build_offline_research_packet
from .resume_tailoring import (
    choose_tailoring_replacements, choose_template_replacements,
    validate_resume_tailoring_manifest_integrity,
)
from .runtime_schema import validate_named
from .security import assert_safe_path
from .sourcing import assess_job_freshness, verify_source_route
from .util import canonical_json, iso_utc, load_json, sha256_bytes, sha256_file, stable_id


MAX_JD_SOURCE_BYTES = 32 * 1024 * 1024
MAX_JD_TEXT_CHARACTERS = 4_000_000
MAX_JD_HTML_EVENTS = 300_000
MAX_JD_PDF_PAGES = 200
ROLLBACK_PRIVATE_KINDS = {
    "generated_resume_docx", "generated_resume_pdf",
    "generated_cover_letter_docx", "generated_cover_letter_pdf",
    "visual_evidence", "review_packet", "application_execution_bundle",
}


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
    def __init__(
        self,
        project: Path,
        database: JobOpsDB,
        onboarding: PrivateOnboarding,
        *,
        ai_engine: AIAnalysisEngine | None = None,
    ) -> None:
        self.project = project.resolve()
        self.database = database
        self.database.initialize()
        self.onboarding = onboarding
        self.onboarding.assert_outside_project(self.project)
        self.queue = QueueManager(database)
        self.schemas = self.project / "schemas"
        self.ai_engine = ai_engine

    @staticmethod
    def _remember_created_reference(record: dict[str, Any], created: set[str]) -> None:
        if record.get("deduplicated") is not True:
            created.add(str(record["secure_ref"]))

    def _rollback_generated_references(self, created: set[str]) -> None:
        for reference in sorted(created):
            metadata = self.onboarding.reference_metadata(reference)
            if metadata["kind"] not in ROLLBACK_PRIVATE_KINDS:
                raise JobOpsError("APPLICATION_PREPARATION_ROLLBACK_SCOPE_INVALID", "Rollback encountered an unexpected private material kind.")
            self.onboarding.delete(reference, user_confirmed=True)

    def secure_onboard_synthetic(self) -> dict[str, Any]:
        fixtures = self.project / "tests" / "fixtures"
        portfolio = self.onboarding.import_file(
            "onboarding_source_document", fixtures / "synthetic-forward-jd.pdf", synthetic=True,
        )
        profile_value = load_json(fixtures / "synthetic-forward-profile.json")
        profile_value.update({
            "github_url": "https://github.com/synthetic-candidate",
            "portfolio_url": "https://portfolio.example.test/synthetic-candidate",
            "portfolio_file_ref": portfolio["secure_ref"],
            "portfolio_file_sha256": portfolio["content_sha256"],
            "portfolio_file_display_name": "synthetic-portfolio.pdf",
        })
        profile = self.onboarding.import_bytes("candidate_profile", canonical_json(profile_value), synthetic=True)
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

    def _assert_private_reference(
        self, reference: str, *, kinds: set[str], synthetic: bool,
    ) -> dict[str, object]:
        metadata = self.onboarding.reference_metadata(reference)
        if metadata["status"] != "ACTIVE" or metadata["kind"] not in kinds or metadata["synthetic"] is not synthetic:
            raise JobOpsError(
                "APPLICATION_PRIVATE_REFERENCE_INVALID",
                "An application input reference has the wrong kind, state, or synthetic boundary.",
                expected_kinds=sorted(kinds),
            )
        value = self.onboarding.read_bytes(reference)
        if sha256_bytes(value) != metadata["content_sha256"]:
            raise JobOpsError("APPLICATION_PRIVATE_REFERENCE_HASH_INVALID", "An encrypted application input failed its content binding.")
        return metadata

    def _assert_real_master_reference(
        self,
        reference: str,
        *,
        claim_set: dict[str, Any],
    ) -> dict[str, object]:
        """Accept the dedicated master kind or an exactly bound UI-designated DOCX source.

        The onboarding center retains uploaded documents as onboarding sources and
        records the chosen editable master in its encrypted state.  That path is
        as strongly bound as the older dedicated master kind, but only when the
        state descriptor, applicant-approved Claim set, and content hash all agree.
        """

        metadata = self._assert_private_reference(
            reference,
            kinds={"master_resume_docx", "onboarding_source_document"},
            synthetic=False,
        )
        if metadata["kind"] == "master_resume_docx":
            return metadata

        approved_master = claim_set.get("master_resume")
        state_ref = str(claim_set.get("onboarding_state_ref", ""))
        if not isinstance(approved_master, dict) or not state_ref:
            raise JobOpsError(
                "APPLICATION_MASTER_SOURCE_BINDING_INVALID",
                "The editable Master Resume selected in JobFlow is missing its approved state binding.",
            )
        self._assert_private_reference(
            state_ref,
            kinds={"onboarding_center_state"},
            synthetic=False,
        )
        state = self._load_json_ref(state_ref)
        descriptor = state.get("master_resume")
        if (
            not isinstance(descriptor, dict)
            or descriptor.get("secure_ref") != reference
            or descriptor.get("sha256") != metadata["content_sha256"]
            or str(descriptor.get("extension", "")).casefold() != ".docx"
            or descriptor.get("editable_docx") is not True
            or approved_master.get("secure_ref") != reference
            or approved_master.get("sha256") != metadata["content_sha256"]
            or approved_master.get("editable_docx") is not True
        ):
            raise JobOpsError(
                "APPLICATION_MASTER_SOURCE_BINDING_INVALID",
                "The editable Master Resume selected in JobFlow no longer matches its approved encrypted state.",
            )
        return metadata

    def _real_application_context(
        self,
        *,
        profile_ref: str | None,
        master_resume_ref: str | None,
        answer_bank_ref: str | None,
        external_claim_set_ref: str | None,
        tailoring_manifest_ref: str | None,
    ) -> dict[str, Any]:
        claim_reference = external_claim_set_ref or self.onboarding.latest_active_reference("external_claim_set", synthetic=False)
        completion_reference = self.onboarding.latest_active_reference("onboarding_completion_packet", synthetic=False)
        if not claim_reference or not completion_reference:
            raise JobOpsError(
                "APPLICATION_ONBOARDING_APPROVAL_REQUIRED",
                "Complete onboarding and approve exact Claim use before preparing a real-profile offline application.",
            )
        self._assert_private_reference(claim_reference, kinds={"external_claim_set"}, synthetic=False)
        self._assert_private_reference(completion_reference, kinds={"onboarding_completion_packet"}, synthetic=False)
        claim_set = self._load_json_ref(claim_reference)
        completion = self._load_json_ref(completion_reference)
        validate_named("external-claim-set", claim_set, self.schemas)
        validate_external_claim_set_integrity(claim_set)
        validate_named("onboarding-completion", completion, self.schemas)

        resolved_profile = profile_ref or str(completion["profile_ref"])
        resolved_answers = answer_bank_ref or str(completion["answer_bank_ref"])
        resolved_master = master_resume_ref or str(claim_set["master_resume"]["secure_ref"])
        if (
            resolved_profile != completion["profile_ref"]
            or resolved_profile != claim_set["profile_ref"]
            or resolved_answers != completion["answer_bank_ref"]
            or resolved_master != claim_set["master_resume"]["secure_ref"]
        ):
            raise JobOpsError(
                "APPLICATION_ONBOARDING_BINDING_MISMATCH",
                "Profile, Answer Bank, Master Resume and Claim approval must come from the same completed onboarding state.",
            )
        self._assert_private_reference(resolved_profile, kinds={"candidate_profile"}, synthetic=False)
        self._assert_private_reference(resolved_answers, kinds={"answer_bank"}, synthetic=False)
        master_metadata = self._assert_real_master_reference(
            resolved_master,
            claim_set=claim_set,
        )
        if master_metadata["content_sha256"] != claim_set["master_resume"]["sha256"]:
            raise JobOpsError("APPLICATION_MASTER_BINDING_MISMATCH", "The Master Resume no longer matches the approved Claim set.")

        claims_to_validate: dict[str, dict[str, Any]] = {}
        for approved_use in claim_set.get("allowed_uses", []):
            for claim in approved_external_claims(claim_set, use=str(approved_use)):
                claims_to_validate[str(claim["claim_id"])] = claim
        for claim in claims_to_validate.values():
            for binding in claim.get("source_bindings", []):
                reference = str(binding["secure_ref"])
                if binding["kind"] == "MASTER_RESUME":
                    if reference != resolved_master:
                        raise JobOpsError(
                            "APPLICATION_CLAIM_MASTER_BINDING_MISMATCH",
                            "An approved Claim points to a different Master Resume than the current application.",
                        )
                    source_metadata = master_metadata
                else:
                    source_metadata = self._assert_private_reference(
                        reference,
                        kinds={"onboarding_source_document", "onboarding_ai_derived"},
                        synthetic=False,
                    )
                if source_metadata["content_sha256"] != binding["content_sha256"]:
                    raise JobOpsError("APPLICATION_CLAIM_SOURCE_CHANGED", "An approved Claim source no longer matches its encrypted hash.")

        manifest_reference = tailoring_manifest_ref or self.onboarding.latest_active_reference("resume_tailoring_manifest", synthetic=False)
        manifest: dict[str, Any] | None = None
        if manifest_reference:
            self._assert_private_reference(manifest_reference, kinds={"resume_tailoring_manifest"}, synthetic=False)
            manifest = self._load_json_ref(manifest_reference)
            validate_named("resume-tailoring-manifest", manifest, self.schemas)
            validate_resume_tailoring_manifest_integrity(manifest)
            if (
                manifest.get("onboarding_state_ref") != claim_set.get("onboarding_state_ref")
                or manifest.get("master_resume_ref") != resolved_master
                or manifest.get("master_resume_sha256") != master_metadata["content_sha256"]
            ):
                raise JobOpsError("TAILORING_MANIFEST_STALE", "The safe tailoring positions do not belong to the current approved onboarding state.")
        return {
            "profile_ref": resolved_profile, "answer_bank_ref": resolved_answers,
            "master_resume_ref": resolved_master, "external_claim_set_ref": claim_reference,
            "tailoring_manifest_ref": manifest_reference, "external_claim_set": claim_set,
            "tailoring_manifest": manifest,
        }

    def current_real_application_references(self) -> dict[str, str]:
        """Return only the opaque bindings for the current completed onboarding state."""

        context = self._real_application_context(
            profile_ref=None, master_resume_ref=None, answer_bank_ref=None,
            external_claim_set_ref=None, tailoring_manifest_ref=None,
        )
        return {
            key: str(context[key])
            for key in (
                "profile_ref", "master_resume_ref", "answer_bank_ref",
                "external_claim_set_ref", "tailoring_manifest_ref",
            )
            if context.get(key)
        }

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
        profile_ref: str | None,
        master_resume_ref: str | None,
        answer_bank_ref: str | None,
        route_fixture: Path,
        form_fixture: Path,
        research_fixture: Path,
        official_snapshot_fixture: Path | None = None,
        external_claim_set_ref: str | None = None,
        tailoring_manifest_ref: str | None = None,
        source_type: str | None = None,
        synthetic: bool = False,
        crash_after_step: str | None = None,
    ) -> dict[str, Any]:
        real_context: dict[str, Any] | None = None
        if synthetic:
            if not profile_ref or not master_resume_ref or not answer_bank_ref:
                raise JobOpsError("SYNTHETIC_ONBOARDING_REFERENCES_REQUIRED", "Synthetic orchestration requires all three synthetic onboarding references.")
            for reference, kinds in (
                (profile_ref, {"candidate_profile"}),
                (master_resume_ref, {"master_resume_docx"}),
                (answer_bank_ref, {"answer_bank"}),
            ):
                self._assert_private_reference(reference, kinds=kinds, synthetic=True)
        else:
            real_context = self._real_application_context(
                profile_ref=profile_ref, master_resume_ref=master_resume_ref,
                answer_bank_ref=answer_bank_ref, external_claim_set_ref=external_claim_set_ref,
                tailoring_manifest_ref=tailoring_manifest_ref,
            )
            profile_ref = str(real_context["profile_ref"])
            master_resume_ref = str(real_context["master_resume_ref"])
            answer_bank_ref = str(real_context["answer_bank_ref"])
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
        return self._run_reserved_to_awaiting(
            input_path=input_path, normalized=normalized, source_format=source_format,
            snapshot_url=snapshot_url, intake_key=intake_key, locator=locator,
            admission=admission, profile_ref=profile_ref,
            master_resume_ref=master_resume_ref, answer_bank_ref=answer_bank_ref,
            route_fixture=route_fixture, form_fixture=form_fixture,
            research_fixture=research_fixture, official_snapshot_fixture=official_snapshot_fixture,
            real_context=real_context,
            synthetic=synthetic, crash_after_step=crash_after_step,
        )

    def _run_reserved_to_awaiting(
        self,
        *,
        input_path: Path,
        normalized: str,
        source_format: str,
        snapshot_url: str | None,
        intake_key: str,
        locator: str,
        admission: Any,
        profile_ref: str,
        master_resume_ref: str,
        answer_bank_ref: str,
        route_fixture: Path,
        form_fixture: Path,
        research_fixture: Path,
        official_snapshot_fixture: Path | None,
        real_context: dict[str, Any] | None,
        synthetic: bool,
        crash_after_step: str | None,
    ) -> dict[str, Any]:
        created_references: set[str] = set()
        try:
            return self._prepare_reserved_application(
                input_path=input_path, normalized=normalized, source_format=source_format,
                snapshot_url=snapshot_url, intake_key=intake_key, locator=locator,
                admission=admission, profile_ref=profile_ref,
                master_resume_ref=master_resume_ref, answer_bank_ref=answer_bank_ref,
                route_fixture=route_fixture, form_fixture=form_fixture,
                research_fixture=research_fixture, official_snapshot_fixture=official_snapshot_fixture,
                real_context=real_context,
                synthetic=synthetic, crash_after_step=crash_after_step,
                created_references=created_references,
            )
        except Exception as exc:
            # A failed offline preparation must not consume the user's approval
            # capacity. Crash-injection tests intentionally preserve the slot to
            # exercise recovery semantics.
            if not (isinstance(exc, JobOpsError) and exc.code == "SYNTHETIC_CRASH_INJECTED"):
                try:
                    self._rollback_generated_references(created_references)
                    self.queue.release_reservation(
                        admission.reservation_id,
                        reason=exc.code if isinstance(exc, JobOpsError) else "LOCAL_PREPARATION_FAILED",
                    )
                except Exception as rollback_exc:
                    raise JobOpsError(
                        "APPLICATION_PREPARATION_ROLLBACK_FAILED",
                        "A failed local preparation could not fully remove its newly generated encrypted materials.",
                    ) from rollback_exc
            raise

    def _prepare_reserved_application(
        self,
        *,
        input_path: Path,
        normalized: str,
        source_format: str,
        snapshot_url: str | None,
        intake_key: str,
        locator: str,
        admission: Any,
        profile_ref: str,
        master_resume_ref: str,
        answer_bank_ref: str,
        route_fixture: Path,
        form_fixture: Path,
        research_fixture: Path,
        official_snapshot_fixture: Path | None,
        real_context: dict[str, Any] | None,
        synthetic: bool,
        crash_after_step: str | None,
        created_references: set[str],
    ) -> dict[str, Any]:

        route_value = load_json(route_fixture)
        official_path = (
            official_snapshot_fixture.resolve(strict=True)
            if official_snapshot_fixture is not None
            else assert_safe_path(self.project / str(route_value["official_snapshot"]), self.project, (), ())
        )
        if not official_path.is_file():
            raise JobOpsError("OFFICIAL_SNAPSHOT_MISSING", "The saved official-company snapshot is missing.")
        official_hash = sha256_file(official_path)
        binding = dict(route_value.get("tenant_binding") or {})
        if binding:
            binding.update({"official_page_hash": official_hash, "jd_snapshot_hash": intake_key})
        policy = load_json(self.project / "config" / "policy.json")
        route = verify_source_route(
            official_entry_url=route_value["official_entry_url"], current_url=route_value["current_url"],
            navigation_history=route_value["navigation_history"], approved_ats_hosts=policy["approved_ats_hosts"],
            guest_available=route_value.get("guest_available"), tenant_binding=binding or None,
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
        if eligibility.status == "INELIGIBLE":
            raise JobOpsError(
                "INELIGIBLE", "A confirmed hard condition conflicts with this saved job; no application material was generated.",
                hard_gaps=list(eligibility.hard_gaps), unknowns=list(eligibility.unknowns),
            )
        if synthetic:
            claims = self._synthetic_claims()
            external_claim_set = None
            mappings = map_evidence(jd.hard_requirements, claims)
        else:
            external_claim_set = dict(real_context["external_claim_set"] if real_context else {})
            claims = approved_external_claims(external_claim_set, use="resume")
            mappings = map_external_claim_evidence(jd.hard_requirements, external_claim_set)
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
        preferred_claim_ids: list[str] | None = None
        if not synthetic and self.ai_engine is not None:
            material_decision = rank_application_claims(
                self.ai_engine,
                job_summary=analysis,
                claims=claims,
            )
            preferred_claim_ids = list(material_decision["ranked_claim_ids"])
        self._crash(crash_after_step, "after_analysis")

        if synthetic:
            excerpt = "Example Analytics Lab uses documented checks for synthetic dataset analysis."
            research_metadata = {
                "title": "Synthetic Company Update", "url": "https://example.com/news/synthetic-update",
                "source_type": "official_company", "published_at": "2026-08-12T00:00:00Z",
                "accessed_at": iso_utc(), "official": True,
            }
        else:
            research_metadata = route_value.get("research")
            if not isinstance(research_metadata, dict):
                raise JobOpsError(
                    "OFFLINE_RESEARCH_METADATA_REQUIRED",
                    "Real-profile offline preparation needs exact metadata for the user-saved official research snapshot.",
                )
            excerpt = str(research_metadata.get("evidence_excerpt", "")).strip()
            if not excerpt:
                raise JobOpsError("OFFLINE_RESEARCH_EXCERPT_REQUIRED", "Select one exact excerpt from the saved official research snapshot.")
        research_source = OfflineResearchSource(
            title=str(research_metadata.get("title", "")).strip(),
            url=str(research_metadata.get("url", "")).strip(),
            source_type=str(research_metadata.get("source_type", "official_company")),
            snapshot_path=research_fixture, snapshot_hash=sha256_file(research_fixture),
            published_at=str(research_metadata.get("published_at")) if research_metadata.get("published_at") else None,
            accessed_at=str(research_metadata.get("accessed_at", iso_utc())),
            evidence_excerpt=excerpt, evidence_fingerprint=sha256_bytes(excerpt.encode("utf-8")),
            official=research_metadata.get("official") is True,
        )
        research = build_offline_research_packet(company=jd.company, findings=[{"claim": excerpt}], sources=[research_source])
        finding = {
            "finding_id": stable_id("RFN", research_source.snapshot_hash, excerpt), "claim": excerpt,
            "source_url": research_source.url, "source_type": research_source.source_type, "snapshot_hash": research_source.snapshot_hash,
            "published_at": research_source.published_at, "accessed_at": research_source.accessed_at, "evidence_excerpt": excerpt,
            "evidence_sha256": research_source.evidence_fingerprint, "freshness": "CURRENT", "official": True,
        }
        validate_named("research-finding", finding, self.schemas)

        answer_record = self._load_json_ref(answer_bank_ref)
        if isinstance(answer_record.get("answers"), dict):
            answers = {
                str(key): item.get("value")
                for key, item in answer_record["answers"].items()
                if isinstance(item, dict) and item.get("status") in {"CONFIRMED", "NOT_APPLICABLE"}
            }
        else:
            answers = dict(answer_record)
        answers["full_name"] = profile_ref
        public_answers = dict(answers)
        public_answers.update({
            "github": profile.get("github_url") or answers.get("github"),
            "portfolio": profile.get("portfolio_url") or answers.get("portfolio"),
            "website": answers.get("website"),
        })
        ats_safe_prefill: dict[str, Any] | None = None
        form_analysis: dict[str, Any] | None = None
        browser_plan: dict[str, Any] | None = None
        public_values_by_control: dict[str, str] = {}
        browser_plan_hash: str
        if form_fixture.suffix.casefold() in {".html", ".htm"}:
            form_analysis = analyze_local_ats_form(
                form_fixture.read_bytes(), route=route.as_dict(), blocked_categories=policy["blocked_form_categories"]
            )
            ai_form_semantics: dict[str, Any] | None = None
            if not synthetic and self.ai_engine is not None:
                ai_form_semantics = analyze_application_form_semantics(
                    self.ai_engine, form_analysis=form_analysis,
                )
            semantic_by_ref = {
                str(item["control_ref"]): item
                for item in (ai_form_semantics or {}).get("fields", [])
            }
            bindings: dict[str, dict[str, str]] = {}
            for item in form_analysis["fields"]:
                answer_key = str(item["answer_key"])
                if item["classification"] == "private_fixed" and answer_key == "full_name" and profile.get("candidate_display_name"):
                    bindings[str(item["control_ref"])] = {"kind": "secure_ref", "value": profile_ref}
                elif (
                    item["classification"] == "private_fixed"
                    and answer_key in profile
                    and profile[answer_key] not in (None, "", "UNKNOWN", "UNANSWERED")
                ):
                    # Resume-provided contact values live only inside the encrypted
                    # Candidate Profile.  Binding the profile reference here avoids
                    # asking the applicant to type the same name, email, or phone on
                    # every application while keeping the plaintext out of the packet.
                    bindings[str(item["control_ref"])] = {"kind": "secure_ref", "value": profile_ref}
                elif item["classification"] == "private_fixed" and answer_key in answers and answers[answer_key] not in (None, "", "UNKNOWN", "UNANSWERED"):
                    bindings[str(item["control_ref"])] = {"kind": "secure_ref", "value": answer_bank_ref}
                elif item["classification"] == "ordinary_fixed" and answer_key in public_answers:
                    candidate = str(public_answers[answer_key])
                    if candidate not in {"", "UNKNOWN", "UNANSWERED"}:
                        bindings[str(item["control_ref"])] = {"kind": "public_value", "value": candidate}
                        public_values_by_control[str(item["control_ref"])] = candidate
            browser_plan = build_browser_action_plan(form_analysis, bindings)
            browser_plan_hash = str(browser_plan["plan_hash"])
            fake_browser = FakeBrowserPrefillAdapter().prefill({
                "plan": browser_plan, "current_form_snapshot_hash": form_analysis["form_snapshot_hash"],
                "isolation_policy": "ISOLATED_FAKE_ONLY",
            })
            action_by_ref = {str(item["control_ref"]): item for item in browser_plan["actions"]}
            safe_questions = []
            for item in form_analysis["fields"]:
                action = action_by_ref[str(item["control_ref"])]
                safe_questions.append({
                    "id": item["control_ref"],
                    "label": item.get("display_label") or item["answer_key"],
                    "options": list(item.get("display_options", [])),
                    "untrusted_prompt_display_only": True,
                    "answer_key": item["answer_key"],
                    "prompt_hash": item["prompt_hash"], "control_type": item["control_type"],
                    "required": bool(item.get("required", False)),
                    "classification": item["classification"], "reason": item["reason_code"],
                    "gate": "PREFILL_ALLOWED" if action["action"] == "PROPOSE_PREFILL" else "STOP_REQUIRED",
                    "action": "PREFILL_FROM_SECURE_STORE" if action["binding_kind"] == "SECURE_REF" else ("PREFILL" if action["action"] == "PROPOSE_PREFILL" else "STOP"),
                    "status": "READY" if action["action"] == "PROPOSE_PREFILL" else "STOPPED",
                    "secure_ref": action["binding_ref"] if action["binding_kind"] == "SECURE_REF" else None,
                    "redacted_summary": "PRIVATE_VALUE_PRESENT" if action["binding_kind"] == "SECURE_REF" else ("PUBLIC_VALUE_HASH_PRESENT" if action["binding_kind"] == "PUBLIC_VALUE_HASH" else "UNANSWERED"),
                    "ai_semantic_role": semantic_by_ref.get(str(item["control_ref"]), {}).get("semantic_role"),
                    "ai_semantic_reason": semantic_by_ref.get(str(item["control_ref"]), {}).get("reason"),
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
            browser_plan_hash = sha256_bytes(canonical_json({
                "mode": "LEGACY_LOCAL_FORM_MAPPING",
                "form_snapshot_hash": form_snapshot_hash,
                "fields": fields,
            }))
        field_records = []
        for field in fields["fields"]:
            classification = str(field["classification"])
            status = initial_application_field_status(classification, str(field["action"]))
            record = {
                "field_id": stable_id("FLD", application_id, str(field["id"])), "application_id": application_id,
                "classification": field["classification"], "action": "PREFILL" if str(field["action"]).startswith("PREFILL") else "STOP",
                "status": status, "secure_ref": field.get("secure_ref"), "redacted_summary": field.get("redacted_summary"),
                "field_hash": sha256_bytes(canonical_json(field)),
            }
            validate_named("application-field", record, self.schemas)
            field_records.append(record)

        replacements = ({
            "CANDIDATE_NAME": str(profile["candidate_display_name"]), "TARGET_ROLE": jd.title,
            "SUMMARY": claims[0]["allowed_wording"][0], "EXPERIENCE_BULLET": claims[1]["allowed_wording"][0],
            "PROJECT": claims[2]["allowed_wording"][0], "SKILLS": claims[3]["allowed_wording"][0],
            "EDUCATION": claims[4]["allowed_wording"][0],
        } if synthetic else {})
        material_requests = detect_material_requests(fields["fields"])
        anticipate_later_materials = bool(
            form_analysis is not None
            and (
                route.provider in {"greenhouse", "lever", "workday"}
                or "NAVIGATION_ACTION_STOP" in form_analysis.get("blockers", [])
            )
        )
        cover_requested = (
            any(item["purpose"] == "cover_letter" for item in material_requests["uploads"])
            or anticipate_later_materials
        )
        cover_docx_ref: dict[str, Any] | None = None
        cover_pdf_ref: dict[str, Any] | None = None
        cover_visual_ref: dict[str, Any] | None = None
        cover_qa: dict[str, Any] | None = None
        with self.onboarding.staging_directory() as staging:
            master_path = staging / "master.docx"
            master_bytes = self.onboarding.read_bytes(master_resume_ref)
            master_sha256 = sha256_bytes(master_bytes)
            master_path.write_bytes(master_bytes)
            resume_docx = staging / "resume.docx"
            resume_pdf = staging / "resume.pdf"
            selected_claim_ids: list[str]
            if synthetic:
                diff = tailor_master_resume(
                    master_path, resume_docx, replacements=replacements, claims=claims, synthetic=True,
                )
                selected_claim_ids = [str(item["claim_id"]) for item in claims]
            else:
                manifest = real_context.get("tailoring_manifest") if real_context else None
                if manifest is not None:
                    replacement_plan = choose_tailoring_replacements(
                        manifest=manifest, external_claim_set=external_claim_set,
                        job_text=normalized,
                        preferred_claim_ids=preferred_claim_ids,
                    )
                    diff = tailor_master_resume_with_manifest(
                        master_path, resume_docx, manifest=manifest,
                        replacements=replacement_plan, external_claim_set=external_claim_set,
                        synthetic=False,
                    )
                    selected_claim_ids = [item["claim_id"] for item in replacement_plan]
                else:
                    slots = discover_template_slots(master_path)
                    if not slots:
                        raise JobOpsError(
                            "TAILORING_MANIFEST_REQUIRED",
                            "An ordinary DOCX needs applicant-approved safe tailoring positions before material generation.",
                        )
                    replacements = choose_template_replacements(
                        template_slots=slots, external_claim_set=external_claim_set,
                        job_text=normalized, candidate_display_name=str(profile["candidate_display_name"]),
                        target_role=jd.title,
                        preferred_claim_ids=preferred_claim_ids,
                    )
                    diff = tailor_master_resume(
                        master_path, resume_docx, replacements=replacements,
                        external_claim_set=external_claim_set, synthetic=False,
                    )
                    selected_wordings = set(replacements.values())
                    selected_claim_ids = [
                        str(item["claim_id"]) for item in claims
                        if str(item["allowed_wording"][0]) in selected_wordings
                    ]
            export_docx_to_pdf(resume_docx, resume_pdf, self.project / ".agents" / "skills" / "job-application-operator" / "scripts" / "export-docx-pdf.ps1")
            pages = render_pdf_to_pngs(resume_pdf, staging / "renders", _pdftoppm())
            visual = automated_visual_probe(pages)
            qa = structural_qa(resume_docx, resume_pdf, pages, visual_record=visual, page_limit=2)
            if qa.status != "PASS":
                raise JobOpsError("MATERIALS_NEEDS_CORRECTION", "Tailored resume failed structural or render QA.", qa=qa.as_dict())
            docx_ref = self.onboarding.import_bytes("generated_resume_docx", resume_docx.read_bytes(), synthetic=synthetic)
            self._remember_created_reference(docx_ref, created_references)
            pdf_ref = self.onboarding.import_bytes("generated_resume_pdf", resume_pdf.read_bytes(), synthetic=synthetic)
            self._remember_created_reference(pdf_ref, created_references)
            visual_ref = self.onboarding.import_bytes("visual_evidence", canonical_json(visual), synthetic=synthetic)
            self._remember_created_reference(visual_ref, created_references)
            if cover_requested:
                cover_docx = staging / "cover-letter.docx"
                cover_pdf = staging / "cover-letter.pdf"
                cover_claims = None if not synthetic else claims[:2]
                cover_claim_set = external_claim_set if not synthetic else None
                if synthetic:
                    why_role = str(claims[0]["allowed_wording"][0])
                else:
                    cover_candidates = approved_external_claims(external_claim_set, use="cover_letter")
                    selected = next((item for item in cover_candidates if item["claim_id"] in selected_claim_ids), cover_candidates[0])
                    why_role = str(selected["allowed_wording"][0])
                build_cover_letter(
                    cover_docx,
                    candidate_display_name=str(profile["candidate_display_name"]),
                    company=jd.company,
                    target_role=jd.title,
                    why_company=f"{excerpt} ({research_source.url}, accessed {research_source.accessed_at[:10]}).",
                    why_role=why_role, claims=cover_claims, external_claim_set=cover_claim_set,
                )
                export_docx_to_pdf(
                    cover_docx,
                    cover_pdf,
                    self.project / ".agents" / "skills" / "job-application-operator" / "scripts" / "export-docx-pdf.ps1",
                )
                cover_pages = render_pdf_to_pngs(cover_pdf, staging / "cover-renders", _pdftoppm())
                cover_visual = automated_visual_probe(cover_pages)
                cover_qa_result = structural_qa(
                    cover_docx, cover_pdf, cover_pages, visual_record=cover_visual, page_limit=2,
                )
                if cover_qa_result.status != "PASS":
                    raise JobOpsError(
                        "MATERIALS_NEEDS_CORRECTION",
                        "The on-demand Cover Letter failed structural or render QA.",
                        qa=cover_qa_result.as_dict(),
                    )
                cover_qa = cover_qa_result.as_dict()
                cover_docx_ref = self.onboarding.import_bytes(
                    "generated_cover_letter_docx", cover_docx.read_bytes(), synthetic=synthetic,
                )
                self._remember_created_reference(cover_docx_ref, created_references)
                cover_pdf_ref = self.onboarding.import_bytes(
                    "generated_cover_letter_pdf", cover_pdf.read_bytes(), synthetic=synthetic,
                )
                self._remember_created_reference(cover_pdf_ref, created_references)
                cover_visual_ref = self.onboarding.import_bytes(
                    "visual_evidence", canonical_json(cover_visual), synthetic=synthetic,
                )
                self._remember_created_reference(cover_visual_ref, created_references)
        self._crash(crash_after_step, "after_materials")

        freshness = assess_job_freshness(official_listing_present=True, application_form_available=True, checked_at=iso_utc())
        if freshness["status"] != "CURRENT":
            raise JobOpsError("JD_FRESHNESS_REQUIRED", "Official listing freshness must be current before review packet generation.")
        claim_set_hash = (
            sha256_bytes(canonical_json([{
                "claim_id": item["claim_id"], "content_hash": item["content_hash"], "version": item["version"],
            } for item in claims]))
            if synthetic else str(external_claim_set["content_hash"])
        )
        public_values = {key: public_answers.get(key) for key in ("github", "portfolio", "website")}
        cover_binding = (
            {
                "docx_secure_ref": str(cover_docx_ref["secure_ref"]),
                "docx_sha256": str(cover_docx_ref["content_sha256"]),
                "pdf_secure_ref": str(cover_pdf_ref["secure_ref"]),
                "pdf_sha256": str(cover_pdf_ref["content_sha256"]),
            }
            if cover_docx_ref and cover_pdf_ref else None
        )
        portfolio_binding = (
            {
                "secure_ref": str(profile["portfolio_file_ref"]),
                "sha256": str(profile["portfolio_file_sha256"]),
                "safe_filename": str(profile["portfolio_file_display_name"]),
            }
            if all(profile.get(key) for key in ("portfolio_file_ref", "portfolio_file_sha256", "portfolio_file_display_name"))
            else None
        )
        if portfolio_binding and not synthetic:
            portfolio_metadata = self._assert_private_reference(
                str(portfolio_binding["secure_ref"]),
                kinds={"onboarding_source_document"}, synthetic=False,
            )
            if portfolio_metadata["content_sha256"] != portfolio_binding["sha256"]:
                raise JobOpsError("APPLICATION_PORTFOLIO_BINDING_MISMATCH", "The encrypted portfolio file no longer matches the approved profile.")
        material_plan = build_material_plan(
            master_resume_ref=master_resume_ref,
            master_resume_sha256=master_sha256,
            tailored_docx_ref=str(docx_ref["secure_ref"]),
            tailored_docx_sha256=str(docx_ref["content_sha256"]),
            tailored_pdf_ref=str(pdf_ref["secure_ref"]),
            tailored_pdf_sha256=str(pdf_ref["content_sha256"]),
            fields=fields["fields"],
            public_values=public_values,
            cover_letter=cover_binding,
            portfolio_file=portfolio_binding,
            anticipate_later_pages=anticipate_later_materials,
        )
        validate_named("material-plan", material_plan, self.schemas)
        execution_plan = build_application_execution_plan(
            application_id=application_id,
            source_route=route.as_dict(),
            form_snapshot_hash=form_snapshot_hash,
            browser_plan_hash=browser_plan_hash,
            form_fields=fields["fields"],
            material_plan=material_plan,
            pending_limit=int(self.queue.status()["pending_limit"]),
            form_blockers=form_analysis["blockers"] if form_analysis is not None else (),
        )
        safe_suffix = application_id.rsplit("-", 1)[-1].casefold()
        uploads = [{
            "filename": f"jobflow-resume-{safe_suffix}.pdf",
            "purpose": "resume",
            "sha256": pdf_ref["content_sha256"],
        }]
        if cover_pdf_ref:
            uploads.append({
                "filename": f"jobflow-cover-letter-{safe_suffix}.pdf",
                "purpose": "cover_letter",
                "sha256": cover_pdf_ref["content_sha256"],
            })
        if material_plan["portfolio_file"]["binding_status"] == "BOUND_SECURE_FILE":
            uploads.append({
                "filename": material_plan["portfolio_file"]["safe_filename"],
                "purpose": "portfolio",
                "sha256": material_plan["portfolio_file"]["sha256"],
            })
        execution_bundle_ref: dict[str, Any] | None = None
        if form_analysis is not None and browser_plan is not None:
            upload_reference_by_purpose = {
                "resume": str(pdf_ref["secure_ref"]),
                **({"cover_letter": str(cover_pdf_ref["secure_ref"])} if cover_pdf_ref else {}),
                **(
                    {"portfolio": str(material_plan["portfolio_file"]["secure_ref"])}
                    if material_plan["portfolio_file"]["binding_status"] == "BOUND_SECURE_FILE"
                    else {}
                ),
            }
            execution_bundle = build_application_execution_bundle(
                application_id=application_id,
                form_snapshot=form_analysis,
                browser_plan=browser_plan,
                execution_plan=execution_plan,
                public_values=public_values_by_control,
                material_references=[{
                    **item,
                    "secure_ref": upload_reference_by_purpose[str(item["purpose"])],
                } for item in uploads],
            )
            execution_bundle_ref = self.onboarding.import_bytes(
                "application_execution_bundle", canonical_json(execution_bundle), synthetic=synthetic,
            )
            self._remember_created_reference(execution_bundle_ref, created_references)
        answers_hash = sha256_bytes(canonical_json(answers))
        packet_id = stable_id(
            "RPK", application_id, intake_key, str(profile["profile_version"]), claim_set_hash,
            str(pdf_ref["content_sha256"]), sha256_bytes(canonical_json(material_plan)),
            route.route_hash, form_snapshot_hash, answers_hash,
        )
        packet: dict[str, Any] = {
            "schema_version": 1, "status": "AWAITING_APPROVAL", "packet_id": packet_id, "application_id": application_id,
            "job": {"job_id": job_id, "company": jd.company, "title": jd.title, "official_url": route.official_entry_url},
            "jd_captured_at": analysis["created_at"], "fit": fit.as_dict(), "hard_gaps": list(eligibility.hard_gaps),
            "resume_bullets": [{
                "text": item["allowed_wording"][0], "claim_id": item["claim_id"],
                "evidence": item["source_refs"] if synthetic else item["source_bindings"],
            } for item in claims if synthetic or item["claim_id"] in selected_claim_ids],
            "master_resume_diff": diff, "form_questions": fields["fields"],
            "sensitive_fields": [item for item in fields["fields"] if item["action"] == "STOP"],
            "uploads": uploads, "material_plan": material_plan, "execution_plan": execution_plan,
            "external_actions": ["upload_material", "submit_application"],
            "source_route": route.as_dict(), "queue": self.queue.status(),
        }
        if execution_bundle_ref is not None:
            packet["execution_bundle_content_hash"] = execution_bundle_ref["content_sha256"]
        packet["content_hash"] = sha256_bytes(canonical_json(packet))
        validate_named("review-packet", packet, self.schemas)
        packet_ref = self.onboarding.import_bytes("review_packet", canonical_json(packet), synthetic=synthetic)
        self._remember_created_reference(packet_ref, created_references)
        context = ApprovalContext(
            application_id=application_id, job_id=job_id, jd_snapshot_hash=intake_key,
            jd_freshness_hash=sha256_bytes(canonical_json(freshness)), source_route_hash=route.route_hash,
            canonical_url=route.current_url, ats_tenant=route.ats_tenant, ats_board=route.ats_board,
            ats_job_identity=route.ats_job_identity, profile_version=str(profile["profile_version"]),
            claim_set_hash=claim_set_hash, form_snapshot_hash=form_snapshot_hash, answers_hash=answers_hash,
            review_packet_hash=packet["content_hash"], uploads=tuple(UploadBinding(item["filename"], item["purpose"], item["sha256"]) for item in uploads),
            external_actions=("upload_material", "submit_application"), site_policy_version=str(policy["schema_version"]),
            unresolved_stops=approval_unresolved_stop_ids(fields["fields"]),
            mandatory_unknowns=tuple(
                [str(item) for item in fields["unknown_fields"]]
                + [str(item) for item in eligibility.unknowns]
            ),
        ).normalized()
        self._crash(crash_after_step, "before_admission")
        materials = [
            {"material_id": stable_id("MAT", application_id, "resume_docx", str(docx_ref["content_sha256"])), "kind": "resume_docx", "path": docx_ref["secure_ref"], "content_hash": docx_ref["content_sha256"], "claim_ids": selected_claim_ids},
            {"material_id": stable_id("MAT", application_id, "resume_pdf", str(pdf_ref["content_sha256"])), "kind": "resume_pdf", "path": pdf_ref["secure_ref"], "content_hash": pdf_ref["content_sha256"], "claim_ids": selected_claim_ids},
            {"material_id": stable_id("MAT", application_id, "visual_evidence", str(visual_ref["content_sha256"])), "kind": "visual_evidence", "path": visual_ref["secure_ref"], "content_hash": visual_ref["content_sha256"], "claim_ids": []},
        ]
        if cover_docx_ref and cover_pdf_ref and cover_visual_ref:
            materials.extend([
                {"material_id": stable_id("MAT", application_id, "cover_letter_docx", str(cover_docx_ref["content_sha256"])), "kind": "cover_letter_docx", "path": cover_docx_ref["secure_ref"], "content_hash": cover_docx_ref["content_sha256"], "claim_ids": selected_claim_ids[:2]},
                {"material_id": stable_id("MAT", application_id, "cover_letter_pdf", str(cover_pdf_ref["content_sha256"])), "kind": "cover_letter_pdf", "path": cover_pdf_ref["secure_ref"], "content_hash": cover_pdf_ref["content_sha256"], "claim_ids": selected_claim_ids[:2]},
                {"material_id": stable_id("MAT", application_id, "cover_visual_evidence", str(cover_visual_ref["content_sha256"])), "kind": "visual_evidence", "path": cover_visual_ref["secure_ref"], "content_hash": cover_visual_ref["content_sha256"], "claim_ids": []},
            ])
        if material_plan["portfolio_file"]["binding_status"] == "BOUND_SECURE_FILE":
            materials.append({
                "material_id": stable_id("MAT", application_id, "portfolio_file", str(material_plan["portfolio_file"]["sha256"])),
                "kind": "portfolio_file", "path": material_plan["portfolio_file"]["secure_ref"],
                "content_hash": material_plan["portfolio_file"]["sha256"], "claim_ids": [],
            })
        if execution_bundle_ref is not None:
            materials.append({
                "material_id": stable_id(
                    "MAT", application_id, "execution_bundle", str(execution_bundle_ref["content_sha256"]),
                ),
                "kind": "execution_bundle", "path": execution_bundle_ref["secure_ref"],
                "content_hash": execution_bundle_ref["content_sha256"], "claim_ids": [],
            })
        admitted = self.queue.admit_awaiting(
            admission.reservation_id, context, snapshot_relative_path=str(collected["snapshot_path"]),
            job_details={"source_type": source_format, "source_locator": locator, "official_url": route.official_entry_url, "company": jd.company, "title": jd.title, "location": jd.location},
            secure_profile_ref=profile_ref, review_packet={"packet_id": packet_id, "content_hash": packet["content_hash"], "secure_ref": packet_ref["secure_ref"], "status": "AWAITING_APPROVAL"},
            material_records=materials, analysis_record={"analysis_id": analysis_id, "analysis": analysis, "analysis_hash": analysis_hash},
            research_records=[finding], field_records=field_records, source_route=route.as_dict(),
        )
        return {
            **admitted, "job_id": job_id, "review_packet_id": packet_id, "review_packet_ref": packet_ref["secure_ref"],
            "fit_recommendation": fit.recommendation, "document_qa": qa.as_dict(), "cover_letter_qa": cover_qa,
            "material_plan": material_plan, "execution_plan": execution_plan, "queue": self.queue.status(),
            "ats_safe_prefill": ats_safe_prefill, "real_external_actions": 0, "next_safe_action": "show-review-packet",
        }
