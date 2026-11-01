"""Read-only, current-packet access to locally encrypted application files."""
from __future__ import annotations

from dataclasses import dataclass
import json
import re
from pathlib import PurePosixPath

from .application_execution import validate_application_execution_plan_integrity
from .approvals import ApprovalContext
from .errors import JobOpsError
from .private_onboarding import MAX_PRIVATE_IMPORT_FILE_BYTES
from .runtime_schema import validate_named
from .security import validate_secure_reference
from .util import canonical_json, sha256_bytes


_MIME = {
    '.pdf': 'application/pdf',
    '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.txt': 'text/plain; charset=utf-8',
}
_PREVIEW = {'.pdf', '.png', '.jpg', '.jpeg'}
_HASH = re.compile(r'sha256:[0-9a-f]{64}\Z')


@dataclass(frozen=True)
class _Material:
    material_id: str
    purpose: str
    extension: str
    reference: str
    sha256: str
    registry_kind: str
    table_kind: str
    filename: str


class ApplicationMaterialReviewManager:
    def __init__(self, database, onboarding, schemas):
        self.database, self.onboarding, self.schemas = database, onboarding, schemas

    def _row(self, application_id):
        with self.database.connect() as connection:
            row = connection.execute(
                '''SELECT r.packet_id,r.packet_version,r.content_hash,r.relative_path,
                          b.context_hash,b.context_json
                   FROM review_packets r JOIN application_bindings b ON b.application_id=r.application_id
                   WHERE r.application_id=? ORDER BY r.packet_version DESC LIMIT 1''',
                (application_id,),
            ).fetchone()
        if row is None:
            raise JobOpsError('REVIEW_PACKET_NOT_FOUND', 'The selected application has no review packet.')
        return dict(row)

    def _snapshot(self, payload):
        application_id = payload.get('application_id')
        expected = payload.get('expected_packet_hash')
        if not isinstance(application_id, str) or not re.fullmatch(r'[A-Za-z0-9._:-]{1,200}', application_id):
            raise JobOpsError('APPLICATION_ID_INVALID', 'Select an application from the local queue.')
        if not isinstance(expected, str) or not _HASH.fullmatch(expected):
            raise JobOpsError('REVIEW_PACKET_HASH_INVALID', 'Open the current review packet first.')
        row = self._row(application_id)
        if row['content_hash'] != expected:
            raise JobOpsError('REVIEW_PACKET_STALE', 'The packet changed. Close this view and open the current packet.')
        metadata = self.onboarding.reference_metadata(row['relative_path'])
        if metadata['status'] != 'ACTIVE' or metadata['kind'] != 'review_packet':
            raise JobOpsError('REVIEW_PACKET_BINDING_INVALID', 'The retained review packet is unavailable.')
        raw = bytearray(self.onboarding.read_bytes(row['relative_path']))
        try:
            if not 0 < len(raw) <= 4 * 1024 * 1024:
                raise JobOpsError('REVIEW_PACKET_SIZE_INVALID', 'The review packet exceeds the local read limit.')
            packet = json.loads(raw.decode('utf-8'))
        except (ValueError, UnicodeError) as exc:
            raise JobOpsError('REVIEW_PACKET_INVALID', 'The retained review packet cannot be read.') from exc
        finally:
            raw[:] = b'\0' * len(raw)
        validate_named('review-packet', packet, self.schemas)
        validate_application_execution_plan_integrity(packet['execution_plan'])
        if (packet['application_id'] != application_id or packet['packet_id'] != row['packet_id']
                or packet['content_hash'] != expected
                or sha256_bytes(canonical_json({key: value for key, value in packet.items() if key != 'content_hash'})) != expected):
            raise JobOpsError('REVIEW_PACKET_HASH_INVALID', 'The saved packet no longer matches this application.')
        try:
            context = ApprovalContext.from_dict(json.loads(row['context_json'])).normalized()
        except (ValueError, TypeError, KeyError) as exc:
            raise JobOpsError('APPLICATION_BINDING_MISSING', 'The saved application binding is unavailable.') from exc
        if (context.application_id != application_id or context.context_hash != row['context_hash']
                or context.review_packet_hash != expected):
            raise JobOpsError('APPLICATION_BINDING_MISSING', 'The saved application binding has changed.')
        return {'application_id': application_id, 'row': row, 'metadata': metadata, 'packet': packet, 'context': context}

    def _assert_current(self, snapshot):
        if (self._row(snapshot['application_id']) != snapshot['row']
                or self.onboarding.reference_metadata(snapshot['row']['relative_path']) != snapshot['metadata']):
            raise JobOpsError('REVIEW_PACKET_STALE', 'The packet changed while it was being read. Open the current packet.')

    def _materials(self, snapshot):
        plan = snapshot['packet']['material_plan']
        items = []
        suffix = sha256_bytes(snapshot['application_id'].encode('utf-8'))[-12:]
        # Select only final job-specific files, never the original master, Profile,
        # arbitrary secure references, or paths supplied by the browser.
        for purpose in ('resume', 'cover_letter'):
            for extension in ('.pdf', '.docx'):
                kind = extension[1:]
                reference = plan[purpose].get(kind + '_secure_ref')
                if reference:
                    items.append(_Material(purpose + extension, purpose, extension, reference,
                        plan[purpose][kind + '_sha256'], 'generated_' + purpose + '_' + kind,
                        purpose + '_' + kind, 'jobflow-' + purpose.replace('_', '-') + '-' + suffix + extension))
        portfolio = plan['portfolio_file']
        if portfolio.get('binding_status') == 'BOUND_SECURE_FILE':
            extension = PurePosixPath(str(portfolio.get('safe_filename') or '')).suffix.casefold()
            items.append(_Material('portfolio.file', 'portfolio', extension, portfolio['secure_ref'],
                portfolio['sha256'], 'onboarding_source_document', 'portfolio_file', 'jobflow-portfolio-' + suffix + extension))
        return items

    def _check_material(self, snapshot, material):
        validate_secure_reference(material.reference)
        if material.extension not in _MIME or not isinstance(material.sha256, str) or not _HASH.fullmatch(material.sha256):
            raise JobOpsError('APPLICATION_MATERIAL_FORMAT_UNSUPPORTED', 'This saved file type is not supported by the local review.')
        with self.database.connect() as connection:
            row = connection.execute(
                'SELECT 1 FROM materials WHERE application_id=? AND kind=? AND path=? AND content_hash=? LIMIT 1',
                (snapshot['application_id'], material.table_kind, material.reference, material.sha256),
            ).fetchone()
        metadata = self.onboarding.reference_metadata(material.reference)
        if (row is None or metadata['status'] != 'ACTIVE' or metadata['kind'] != material.registry_kind
                or metadata['content_sha256'] != material.sha256):
            raise JobOpsError('APPLICATION_MATERIAL_UNAVAILABLE', 'The selected file is missing, changed or no longer available.')
        if material.extension == '.pdf' or material.purpose == 'portfolio':
            uploads = [item for item in snapshot['context'].uploads if item.purpose == material.purpose]
            # A prepared resume/letter may not be requested on this form page.
            # When an upload is bound, it must identify the same exact bytes.
            if any(item.sha256 != material.sha256 for item in uploads):
                raise JobOpsError('APPLICATION_MATERIAL_BINDING_INVALID', 'The saved file differs from this application upload.')

    @staticmethod
    def _descriptor(material, *, available=True, code=None, verified=False):
        return {'material_id': material.material_id, 'purpose': material.purpose,
                'format': material.extension[1:].upper(), 'filename': material.filename,
                'sha256': material.sha256, 'available': available, 'code': code,
                'preview_supported': material.extension in _PREVIEW,
                'content_type': _MIME.get(material.extension), 'content_verified': verified}

    def describe(self, payload):
        snapshot = self._snapshot(payload)
        materials = []
        for item in self._materials(snapshot):
            try:
                self._check_material(snapshot, item)
                materials.append(self._descriptor(item))
            except JobOpsError as exc:
                materials.append(self._descriptor(item, available=False, code=exc.code))
        self._assert_current(snapshot)
        return {'status': 'LOCAL_APPLICATION_MATERIALS', 'application_id': snapshot['application_id'],
                'packet_hash': snapshot['row']['content_hash'], 'materials': materials,
                'real_external_actions': 0, 'approval_changed': False}

    def _read(self, payload):
        snapshot = self._snapshot(payload)
        items = {item.material_id: item for item in self._materials(snapshot)}
        material_id = payload.get('material_id')
        if not isinstance(material_id, str) or material_id not in items:
            raise JobOpsError('APPLICATION_MATERIAL_NOT_FOUND', 'Select a final file listed in this application packet.')
        material = items[material_id]
        self._check_material(snapshot, material)
        raw = bytearray(self.onboarding.read_bytes(material.reference))
        try:
            if not 0 < len(raw) <= MAX_PRIVATE_IMPORT_FILE_BYTES:
                raise JobOpsError('APPLICATION_MATERIAL_SIZE_LIMIT', 'Local file review supports files up to 64 MiB.')
            if sha256_bytes(bytes(raw)) != material.sha256:
                raise JobOpsError('APPLICATION_MATERIAL_BINDING_INVALID', 'The selected file no longer matches the packet.')
            signatures = {'.pdf': b'%PDF-', '.docx': b'PK\x03\x04', '.png': b'\x89PNG\r\n\x1a\n', '.jpg': b'\xff\xd8', '.jpeg': b'\xff\xd8'}
            if material.extension in signatures and not raw.startswith(signatures[material.extension]):
                raise JobOpsError('APPLICATION_MATERIAL_FORMAT_INVALID', 'The saved file does not match its declared format.')
            self._check_material(snapshot, material)
            self._assert_current(snapshot)
            return raw, material, snapshot
        except Exception:
            raw[:] = b'\0' * len(raw)
            raise

    def file(self, payload):
        raw, material, _ = self._read(payload)
        return raw, self._descriptor(material, verified=True)

    def page(self, payload):
        from .source_review import source_page
        raw, material, snapshot = self._read(payload)
        try:
            if material.extension not in _PREVIEW:
                raise JobOpsError('APPLICATION_MATERIAL_PREVIEW_UNSUPPORTED', 'Download this format to inspect it locally; PDF and image pages can be previewed here.')
            page = source_page(bytes(raw), material.extension, expected_hash=material.sha256, page=payload.get('page', 1))
            self._check_material(snapshot, material)
            self._assert_current(snapshot)
            return {'application_id': snapshot['application_id'], 'packet_hash': snapshot['row']['content_hash'],
                    'material': self._descriptor(material, verified=True), 'page': page,
                    'real_external_actions': 0, 'approval_changed': False}
        finally:
            raw[:] = b'\0' * len(raw)
