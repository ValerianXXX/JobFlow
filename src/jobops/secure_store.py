from __future__ import annotations

import base64
import json
import os
import subprocess
import uuid
from pathlib import Path

from .errors import JobOpsError
from .security import validate_secure_reference
from .util import has_reparse_component, sha256_file


class WindowsDPAPIStore:
    def __init__(self, script_path: Path, *, local_app_data: Path | None = None) -> None:
        self.script_path = script_path.resolve(strict=True)
        self.local_app_data = local_app_data

    @property
    def private_root(self) -> Path:
        base = self.local_app_data or Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return base / "JobOps" / "private"

    def cipher_path(self, reference: str) -> Path:
        validate_secure_reference(reference)
        return self.private_root / (reference.removeprefix("secure-ref:") + ".dpapi")

    def _run(self, operation: str, reference: str | None = None, payload: str | None = None, *, input_encoding: str = "Utf8", output_encoding: str = "Utf8") -> subprocess.CompletedProcess[str]:
        command = [
            "powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive",
            "-ExecutionPolicy", "Bypass", "-File", str(self.script_path),
            "-Operation", operation, "-InputEncoding", input_encoding, "-OutputEncoding", output_encoding,
        ]
        if reference:
            validate_secure_reference(reference)
            command += ["-Reference", reference]
        environment = os.environ.copy()
        if self.local_app_data:
            environment["LOCALAPPDATA"] = str(self.local_app_data)
        completed = subprocess.run(
            command,
            input=payload,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
            env=environment,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if completed.returncode != 0:
            raise JobOpsError("SECURE_STORE_FAILED", "Windows DPAPI operation failed without exposing private content.", operation=operation, returncode=completed.returncode)
        return completed

    def put_bytes(self, value: bytes, *, reference: str | None = None) -> dict[str, str | bool]:
        if not value:
            raise JobOpsError("SECURE_VALUE_EMPTY", "Cannot store an empty application-private value.")
        generated = reference is None
        target_reference = reference or ("secure-ref:" + uuid.uuid4().hex)
        try:
            result = json.loads(self._run("Put", reference=target_reference, payload=base64.b64encode(value).decode("ascii"), input_encoding="Base64").stdout)
        except Exception:
            # The caller-selected reference lets us remove a new ciphertext even if the
            # helper was interrupted after its atomic file replacement but before reply.
            if generated and self.cipher_path(target_reference).is_file():
                try:
                    self._run("Delete", reference=target_reference)
                except Exception:
                    pass
            raise
        validate_secure_reference(result["secure_ref"])
        if result["secure_ref"] != target_reference:
            if generated and self.cipher_path(target_reference).is_file():
                try:
                    self._run("Delete", reference=target_reference)
                except Exception:
                    pass
            raise JobOpsError("SECURE_STORE_REFERENCE_MISMATCH", "DPAPI returned an unexpected secure reference.")
        return result

    def get_bytes(self, reference: str) -> bytes:
        encoded = self._run("Get", reference=reference, output_encoding="Base64").stdout
        try:
            return base64.b64decode(encoded, validate=True)
        except Exception as exc:
            raise JobOpsError("SECURE_STORE_FAILED", "DPAPI output could not be decoded.", operation="Get") from exc

    def ciphertext_sha256(self, reference: str) -> str:
        path = self.cipher_path(reference)
        if has_reparse_component(path, self.private_root) or not path.is_file():
            raise JobOpsError(
                "SECURE_CIPHERTEXT_UNAVAILABLE",
                "The registered private ciphertext is missing or no longer a regular local file.",
            )
        try:
            return sha256_file(path)
        except OSError as exc:
            raise JobOpsError(
                "SECURE_CIPHERTEXT_UNAVAILABLE",
                "The registered private ciphertext could not be verified before decryption.",
            ) from exc

    def put(self, secret: str) -> dict[str, str | bool]:
        return self.put_bytes(secret.encode("utf-8"))

    def get_for_internal_use(self, reference: str) -> str:
        return self.get_bytes(reference).decode("utf-8")

    def test(self, reference: str) -> bool:
        result = json.loads(self._run("Test", reference=reference).stdout)
        return result["status"] == "PRESENT"

    def delete(self, reference: str) -> None:
        self._run("Delete", reference=reference)

    def validate_roundtrip(self, synthetic_secret: str) -> dict[str, object]:
        stored = self.put(synthetic_secret)
        reference = str(stored["secure_ref"])
        try:
            recovered = self.get_for_internal_use(reference)
            return {"status": "PASS" if recovered == synthetic_secret else "FAIL", "secure_ref_created": True, "ciphertext_sha256": stored["ciphertext_sha256"], "plaintext_logged": False}
        finally:
            self.delete(reference)
