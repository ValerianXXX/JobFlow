from __future__ import annotations

import base64
import hashlib
import hmac
import json
import unittest
from types import SimpleNamespace

from _support import project_temp
from jobops.companion_binding import (
    BINDING_ALGORITHM,
    BINDING_SCHEMA_VERSION,
    canonical_pair_message,
    sign_pair_response,
    validate_pair_request,
)
from jobops.errors import JobOpsError
from jobops.onboarding_server import OnboardingRequestHandler


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


class CompanionBindingTests(unittest.TestCase):
    def _install(self, local_app_data, *, installation_id: str, secret: bytes) -> dict[str, object]:
        root = local_app_data / "JobOps"
        root.mkdir(parents=True)
        (root / "browser-companion-binding.json").write_text(
            json.dumps(
                {
                    "schema_version": BINDING_SCHEMA_VERSION,
                    "installation_id": installation_id,
                    "secret_b64url": _b64url(secret),
                },
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )
        return {
            "schema_version": BINDING_SCHEMA_VERSION,
            "algorithm": BINDING_ALGORITHM,
            "installation_id": installation_id,
            "challenge": _b64url(b"c" * 32),
        }

    def test_pair_response_is_installation_bound_and_secret_never_crosses_protocol(self) -> None:
        with project_temp() as root:
            local_app_data = root / "isolated-local-app-data"
            installation_id = "a" * 32
            secret = b"s" * 32
            request = self._install(local_app_data, installation_id=installation_id, secret=secret)
            response = {
                "status": "BROWSER_COMPANION_PAIRED",
                "mode": "APPLICATION_ASSIST",
                "assist_id": "BAS-SYNTHETIC-HMAC",
                "application_id": "APP-SYNTHETIC-HMAC",
                "allowed_page_origin": "https://apply.example.test",
                "provider": "workday",
                "route_kind": "ATS_REDIRECT",
                "current_step": 1,
                "max_steps": 20,
                "expires_at": "2099-01-01T00:00:00Z",
            }
            proof = sign_pair_response(
                protocol_version=2,
                extension_version="0.6.1",
                base_url="http://127.0.0.1:43123",
                assist_path="/assist/" + "x" * 54,
                binding_request=request,
                response=response,
                local_app_data=local_app_data,
            )
            message = canonical_pair_message(
                protocol_version=2,
                extension_version="0.6.1",
                base_url="http://127.0.0.1:43123",
                assist_path="/assist/" + "x" * 54,
                installation_id=installation_id,
                challenge=str(request["challenge"]),
                response=response,
            )
            expected = _b64url(hmac.new(secret, message, hashlib.sha256).digest())
            self.assertEqual(proof["proof"], expected)
            self.assertEqual(proof["challenge"], request["challenge"])
            self.assertNotIn("secret", json.dumps(proof).casefold())
            self.assertNotIn(_b64url(secret), json.dumps(proof))
            tampered = dict(response, provider="company")
            tampered_message = canonical_pair_message(
                protocol_version=2,
                extension_version="0.6.1",
                base_url="http://127.0.0.1:43123",
                assist_path="/assist/" + "x" * 54,
                installation_id=installation_id,
                challenge=str(request["challenge"]),
                response=tampered,
            )
            self.assertNotEqual(
                _b64url(hmac.new(secret, tampered_message, hashlib.sha256).digest()),
                proof["proof"],
            )

    def test_fake_loopback_cannot_answer_for_another_installation_or_challenge(self) -> None:
        with project_temp() as root:
            local_app_data = root / "isolated-local-app-data"
            request = self._install(local_app_data, installation_id="b" * 32, secret=b"k" * 32)
            wrong_installation = dict(request, installation_id="c" * 32)
            with self.assertRaises(JobOpsError) as mismatch:
                validate_pair_request(wrong_installation, local_app_data=local_app_data)
            self.assertEqual(mismatch.exception.code, "BROWSER_COMPANION_BINDING_MISMATCH")
            malformed_challenge = dict(request, challenge=_b64url(b"too-short"))
            with self.assertRaises(JobOpsError) as malformed:
                validate_pair_request(malformed_challenge, local_app_data=local_app_data)
            self.assertEqual(malformed.exception.code, "BROWSER_COMPANION_BINDING_REQUEST_INVALID")

    def test_explicit_local_app_data_boundary_is_not_replaced_by_process_environment(self) -> None:
        with project_temp() as root:
            isolated = root / "isolated"
            wrong = root / "wrong"
            request = self._install(isolated, installation_id="d" * 32, secret=b"i" * 32)
            self._install(wrong, installation_id="e" * 32, secret=b"w" * 32)
            self.assertEqual(
                validate_pair_request(request, local_app_data=isolated),
                ("d" * 32, request["challenge"]),
            )
            with self.assertRaises(JobOpsError) as mismatch:
                validate_pair_request(request, local_app_data=wrong)
            self.assertEqual(mismatch.exception.code, "BROWSER_COMPANION_BINDING_MISMATCH")

    def test_http_pair_hook_uses_the_current_onboarding_store_boundary(self) -> None:
        with project_temp() as root:
            local_app_data = root / "isolated-local-app-data"
            request = self._install(local_app_data, installation_id="f" * 32, secret=b"h" * 32)
            payload = {
                "protocol_version": 2,
                "extension_version": "0.6.1",
                "companion_binding": request,
            }
            handler = object.__new__(OnboardingRequestHandler)
            handler.server = SimpleNamespace(
                server_port=43123,
                service=SimpleNamespace(
                    onboarding=SimpleNamespace(
                        store=SimpleNamespace(
                            local_app_data=local_app_data,
                            private_root=local_app_data / "JobOps" / "private",
                        )
                    )
                ),
            )
            handler.headers = {"Host": "127.0.0.1:43123"}
            handler._optional_json_body = lambda: payload
            self.assertEqual(handler._companion_pair_body(), payload)
            result = {
                "status": "GUIDED_INTAKE_PAIRED",
                "mode": "JOB_CAPTURE",
                "intake_id": "GIN-SYNTHETIC-HMAC",
                "capture_status": "AWAITING_JOB_PAGE_CAPTURE",
                "allowed_company_domain": "example.test",
                "expires_at": "2099-01-01T00:00:00Z",
            }
            signed = handler._sign_companion_pair(
                assist_path="/intake/" + "x" * 54,
                pair_request=payload,
                result=result,
            )
            self.assertEqual(signed["status"], result["status"])
            self.assertRegex(signed["companion_binding"]["proof"], r"^[A-Za-z0-9_-]{43}$")


if __name__ == "__main__":
    unittest.main()
