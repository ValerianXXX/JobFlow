"""Seeded synthetic PDF profile; real loopback API, DPAPI and optional Word.

This tests the post-confirmation resume flow, not AI extraction or onboarding.
It never reads a user profile, calls a model, or reaches an employer.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import sys
import tempfile
import threading
from unittest.mock import patch

import _support
from _support import PROJECT
import test_onboarding_center as fixtures
from test_standard_resume import approved_pdf_service
from jobops.onboarding_server import create_server
from jobops.secure_store import WindowsDPAPIStore


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stdio-control", action="store_true")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="jobflow-standard-resume-") as temporary:
        root = Path(temporary)
        script_root = PROJECT / ".agents/skills/job-application-operator/scripts"
        store = WindowsDPAPIStore(script_root / "secure-store.ps1", local_app_data=root / "local")
        with patch.object(fixtures, "MemorySecureStore", return_value=store):
            service, _, _, _ = approved_pdf_service(root)
        convert = service.standard_resume_pdf
        def conversion_diagnostic(payload):
            try:
                return convert(payload)
            except Exception as exc:
                cause = exc.__cause__
                print(json.dumps({"operation": "SYNTHETIC_PDF_CONVERSION", "code": getattr(exc, "code", None),
                                  "stage": getattr(exc, "details", {}).get("failure_stage"),
                                  "cause_type": type(cause).__name__, "cause_code": getattr(cause, "code", None),
                                  "cause_stage": getattr(cause, "details", {}).get("failure_stage"),
                                  "returncode": getattr(cause, "details", {}).get("returncode")}), file=sys.stderr, flush=True)
                raise
        service.standard_resume_pdf = conversion_diagnostic
        export_script = service.project / ".agents/skills/job-application-operator/scripts/export-docx-pdf.ps1"
        export_script.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(script_root / "export-docx-pdf.ps1", export_script)
        server = create_server(service, token="synthetic-standard-resume")

        def stop_on_input():
            sys.stdin.readline()
            server.shutdown()

        if args.stdio_control:
            threading.Thread(target=stop_on_input, daemon=True).start()
        print(json.dumps({"url": f"http://127.0.0.1:{server.server_port}/session/synthetic-standard-resume/", "synthetic_only": True}), flush=True)
        try:
            server.serve_forever()
        finally:
            server.server_close()
            service.close()
    print(json.dumps({"temporary_state_removed": not root.exists()}), flush=True)


if __name__ == "__main__":
    main()
