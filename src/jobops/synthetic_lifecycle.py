from __future__ import annotations

import json
import re
from typing import Any

from .adapters import audit_real_external_actions
from .db import JobOpsDB
from .ephemeral_payload import EphemeralATSPayloadBroker
from .errors import JobOpsError
from .execution_bundle import ApplicationExecutionBundleManager
from .execution_controller import IsolatedApplicationExecutionController
from .external_action_sessions import ExternalActionSessionManager, ExternalActionSessionPolicy
from .private_onboarding import PrivateOnboarding
from .util import canonical_json, iso_utc, sha256_bytes


class SyntheticApplicationLifecycle:
    """Run one fully bound application through fake adapters only.

    This is acceptance evidence, not a production transport.  It intentionally has
    no live provider adapter and refuses every non-synthetic encrypted reference.
    """

    def __init__(self, database: JobOpsDB, onboarding: PrivateOnboarding) -> None:
        self.database = database
        self.onboarding = onboarding
        self.bundles = ApplicationExecutionBundleManager(database, onboarding)

    @staticmethod
    def _freshness_hash(application_id: str, context_hash: str, route_hash: str, form_hash: str) -> str:
        return sha256_bytes(canonical_json({
            "mode": "LOCAL_SYNTHETIC_RECHECK",
            "application_id": application_id,
            "application_context_hash": context_hash,
            "source_route_hash": route_hash,
            "form_snapshot_hash": form_hash,
            "network_actions": 0,
            "real_external_actions": 0,
        }))

    def _assert_synthetic_bundle(self, application_id: str, bundle: dict[str, Any], answer_refs: list[str]) -> None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT path FROM materials WHERE application_id=? AND kind='execution_bundle' ORDER BY created_at DESC LIMIT 1",
                (application_id,),
            ).fetchone()
        if row is None or self.onboarding.reference_metadata(str(row["path"]))["synthetic"] is not True:
            raise JobOpsError("SYNTHETIC_LIFECYCLE_ONLY", "The isolated lifecycle accepts only synthetic execution bundles.")
        references = [
            str(item["secure_ref"]) for item in bundle["material_references"]
        ] + list(answer_refs)
        references.extend(
            str(item["binding_ref"])
            for item in bundle["browser_plan"]["actions"]
            if item.get("binding_kind") == "SECURE_REF"
        )
        for reference in sorted(set(references)):
            if self.onboarding.reference_metadata(reference)["synthetic"] is not True:
                raise JobOpsError("SYNTHETIC_LIFECYCLE_ONLY", "The isolated lifecycle cannot decrypt a real applicant reference.")

    def prepare_until_final_authorization(
        self,
        *,
        application_id: str,
        user_confirmed: bool,
    ) -> dict[str, Any]:
        if not user_confirmed:
            raise JobOpsError(
                "SYNTHETIC_EXECUTION_CONFIRMATION_REQUIRED",
                "Running the complete synthetic lifecycle requires an explicit local confirmation.",
            )
        bundle, context, answer_refs = self.bundles.load_current(application_id)
        self._assert_synthetic_bundle(application_id, bundle, answer_refs)
        public_values = {
            str(item["control_ref"]): str(item["value"])
            for item in bundle["public_values"]
        }
        material_references = {
            str(item["sha256"]): str(item["secure_ref"])
            for item in bundle["material_references"]
        }
        required_actions = ["read_official_job", "inspect_application_form", "upload_materials"]
        if int(bundle["browser_plan"].get("fillable_count", 0)):
            required_actions.append("prefill_application_form")
        sessions = ExternalActionSessionManager(self.database, ExternalActionSessionPolicy.isolated_fake())
        sessions.enable(user_confirmed=True)
        try:
            session = sessions.issue(
                context=context,
                allowed_actions=required_actions,
                user_confirmed=True,
            )
            sessions.persist(session, context=context)
            sessions.validate_scope(
                session_id=session.session_id,
                context=context,
                required_actions=required_actions,
            )
            payload_evidence = EphemeralATSPayloadBroker(
                self.onboarding, isolated_test_mode=True,
            ).run_isolated_probe(
                context=context,
                form_snapshot=bundle["form_snapshot"],
                browser_plan=bundle["browser_plan"],
                public_values=public_values,
                material_references=material_references,
                application_answer_bundle_references=answer_refs,
            )
            payload_evidence_hash = sha256_bytes(canonical_json(payload_evidence))
            now = iso_utc()
            with self.database.connect() as connection:
                connection.execute(
                    "INSERT INTO events(application_id,event_type,from_state,to_state,payload_json,created_at) VALUES(?,?,?,?,?,?)",
                    (
                        application_id,
                        "SYNTHETIC_EPHEMERAL_PAYLOAD_VALIDATED",
                        "APPROVED",
                        "APPROVED",
                        json.dumps({
                            "payload_evidence_hash": payload_evidence_hash,
                            "field_count": payload_evidence["field_count"],
                            "file_count": payload_evidence["file_count"],
                        }, sort_keys=True),
                        now,
                    ),
                )
            freshness_hash = self._freshness_hash(
                application_id,
                context.context_hash,
                context.source_route_hash,
                context.form_snapshot_hash,
            )
            prepared = IsolatedApplicationExecutionController(
                self.database,
            ).prepare_until_final_authorization(
                context=context,
                execution_plan=bundle["execution_plan"],
                browser_plan=bundle["browser_plan"],
                current_form_snapshot_hash=context.form_snapshot_hash,
                freshness_evidence_hash=freshness_hash,
                action_session_id=session.session_id,
            )
        finally:
            sessions.disable(reason="SYNTHETIC_LIFECYCLE_SCOPE_COMPLETE")
        audit = audit_real_external_actions(self.database)
        if audit["real_external_actions"] != 0:
            raise JobOpsError("SYNTHETIC_EXTERNAL_ACTION_DETECTED", "The isolated lifecycle recorded a real external action.")
        return {
            **prepared,
            "payload_evidence_hash": payload_evidence_hash,
            "ephemeral_field_count": payload_evidence["field_count"],
            "ephemeral_file_count": payload_evidence["file_count"],
            "confirmed_stop_field_count": payload_evidence["confirmed_stop_field_count"],
            "skipped_optional_field_count": payload_evidence["skipped_optional_field_count"],
            "temporary_files_removed": payload_evidence["temporary_files_removed"],
            "production_activation": False,
            "network_actions": 0,
            "real_external_actions": 0,
        }

    def complete_with_fresh_authorization(
        self,
        *,
        application_id: str,
        run_id: str,
        user_confirmed: bool,
        fake_confirmation_number: str | None,
    ) -> dict[str, Any]:
        if not re.fullmatch(r"RUN-[A-F0-9]{12}", run_id):
            raise JobOpsError("EXECUTION_RUN_ID_INVALID", "The isolated execution run identifier is invalid.")
        bundle, context, answer_refs = self.bundles.load_current(application_id)
        self._assert_synthetic_bundle(application_id, bundle, answer_refs)
        freshness_hash = self._freshness_hash(
            application_id,
            context.context_hash,
            context.source_route_hash,
            context.form_snapshot_hash,
        )
        completed = IsolatedApplicationExecutionController(
            self.database,
        ).complete_with_fresh_authorization(
            run_id=run_id,
            context=context,
            execution_plan=bundle["execution_plan"],
            browser_plan=bundle["browser_plan"],
            current_form_snapshot_hash=context.form_snapshot_hash,
            freshness_evidence_hash=freshness_hash,
            user_confirmed=user_confirmed,
            fake_confirmation_number=fake_confirmation_number,
        )
        audit = audit_real_external_actions(self.database)
        if audit["real_external_actions"] != 0:
            raise JobOpsError("SYNTHETIC_EXTERNAL_ACTION_DETECTED", "The isolated lifecycle recorded a real external action.")
        return {
            **completed,
            "production_activation": False,
            "network_actions": 0,
            "real_external_actions": 0,
        }
