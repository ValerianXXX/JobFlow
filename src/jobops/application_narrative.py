from __future__ import annotations

import re
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator

from .db import JobOpsDB
from .errors import JobOpsError
from .private_onboarding import PrivateOnboarding
from .security import validate_secure_reference
from .util import sha256_bytes


MAX_APPLICATION_NARRATIVE_CHARACTERS = 4_000


@dataclass(frozen=True)
class ApplicationNarrativeMaterial:
    secure_ref: str
    content_hash: str
    text: str


def validate_application_narrative_text(value: object) -> str:
    if not isinstance(value, str):
        raise JobOpsError(
            "APPLICATION_NARRATIVE_INVALID",
            "The generated application narrative must be text.",
        )
    if (
        not value
        or value != value.strip()
        or len(value) > MAX_APPLICATION_NARRATIVE_CHARACTERS
        or re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", value)
        or re.search(r"[\u202a-\u202e\u2066-\u2069]", value)
    ):
        raise JobOpsError(
            "APPLICATION_NARRATIVE_INVALID",
            "The generated application narrative is empty, unsafe, or exceeds 4,000 characters.",
        )
    return value


def application_narrative_metadata(
    database: JobOpsDB,
    onboarding: PrivateOnboarding,
    application_id: str,
    *,
    expected_content_hash: str | None = None,
) -> dict[str, str] | None:
    with database.connect() as connection:
        rows = connection.execute(
            """SELECT path,content_hash FROM materials
               WHERE application_id=? AND kind='application_narrative'
               ORDER BY created_at DESC,material_id DESC""",
            (application_id,),
        ).fetchall()
    candidates = [
        row for row in rows
        if expected_content_hash is None or str(row["content_hash"]) == expected_content_hash
    ]
    if not candidates:
        return None
    active: dict[tuple[str, str], dict[str, str]] = {}
    for row in candidates:
        secure_ref = str(row["path"])
        validate_secure_reference(secure_ref)
        metadata = onboarding.reference_metadata(secure_ref)
        if metadata["status"] != "ACTIVE":
            continue
        content_hash = str(row["content_hash"])
        if metadata["kind"] != "application_narrative" or str(metadata["content_sha256"]) != content_hash:
            raise JobOpsError(
                "APPLICATION_NARRATIVE_BINDING_INVALID",
                "The generated narrative no longer matches this application.",
            )
        active[(secure_ref, content_hash)] = {
            "secure_ref": secure_ref,
            "content_hash": content_hash,
        }
    if not active:
        return None
    if len(active) != 1:
        raise JobOpsError(
            "APPLICATION_NARRATIVE_COUNT_INVALID",
            "The current application must have exactly one active, review-bound generated narrative.",
        )
    return next(iter(active.values()))


@contextmanager
def decrypted_application_narrative(
    database: JobOpsDB,
    onboarding: PrivateOnboarding,
    application_id: str,
    *,
    expected_content_hash: str | None = None,
) -> Iterator[ApplicationNarrativeMaterial]:
    """Yield decrypted text for the shortest practical local scope.

    The mutable source buffer is overwritten on exit as best-effort cleanup.
    Hashing and UTF-8 decoding can create immutable ``bytes`` and ``str``
    copies that Python cannot reliably zeroize, so this is not a guarantee
    that every process-memory copy is erased.
    """

    metadata = application_narrative_metadata(
        database,
        onboarding,
        application_id,
        expected_content_hash=expected_content_hash,
    )
    if metadata is None:
        raise JobOpsError(
            "APPLICATION_NARRATIVE_MISSING",
            "No generated narrative is available for this application.",
        )
    raw = bytearray(onboarding.read_bytes(metadata["secure_ref"]))
    try:
        if sha256_bytes(bytes(raw)) != metadata["content_hash"]:
            raise JobOpsError(
                "APPLICATION_NARRATIVE_HASH_MISMATCH",
                "The generated narrative failed its application binding check.",
            )
        try:
            text = bytes(raw).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise JobOpsError(
                "APPLICATION_NARRATIVE_INVALID",
                "The generated narrative is not valid UTF-8 text.",
            ) from exc
        yield ApplicationNarrativeMaterial(
            secure_ref=metadata["secure_ref"],
            content_hash=metadata["content_hash"],
            text=validate_application_narrative_text(text),
        )
    finally:
        # Best effort for the mutable source buffer only; immutable Python
        # copies created above cannot be reliably overwritten in place.
        raw[:] = b"\0" * len(raw)
