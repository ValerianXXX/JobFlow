"""Isolated synthetic HTTP fixture for browser acceptance. No real AI or mailbox."""

from __future__ import annotations
import io
import argparse
import json
import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from PIL import Image, ImageDraw
from jobops.demo import create_demo_service
from jobops.onboarding_center import OnboardingCenterService
from jobops.onboarding_server import create_server
from jobops.util import sha256_bytes
from test_workbench import eml


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--stdio-control", action="store_true")
    parser.add_argument("--mail-review-fixture", action="store_true")
    args = parser.parse_args()
    project = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="jobflow-workbench-ui-") as temporary:
        service = create_demo_service(project, Path(temporary))
        if args.mail_review_fixture:
            for body, html in [
                ("This is not an interview invitation.", False),
                ("邀请您参加面试。\n> 本次申请未通过。", False),
                ("<p>We are not moving forward.</p><blockquote>We invite you to an interview.</blockquote>", True),
            ]:
                raw = eml(body, html=html)
                assert OnboardingCenterService.import_status_eml(service, raw)["added"] == 1
                assert OnboardingCenterService.import_status_eml(service, raw)["added"] == 0
        else:
            OnboardingCenterService.import_status_eml(service, eml())
        # Add a real, hash-bound synthetic snapshot alongside the demo's
        # deliberately unavailable legacy snapshot. Neither is a live JD.
        snapshot = (
            b"Synthetic role: inspect fictional workflow evidence. No live employer."
        )
        snapshot_path = service.project / "workspace" / "jobs" / "ui-synthetic.txt"
        snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        snapshot_path.write_bytes(snapshot)
        with service.database.connect() as connection:
            connection.execute(
                "INSERT INTO jd_snapshots VALUES(?,?,?,?,?)",
                (
                    "SNP-UI-SYNTHETIC",
                    "JOB-DEFACED00001",
                    sha256_bytes(snapshot),
                    "workspace/jobs/ui-synthetic.txt",
                    "2026-10-28T14:00:00Z",
                ),
            )
        image = Image.new("RGB", (700, 400), "white")
        draw = ImageDraw.Draw(image)
        draw.text((30, 60), "SYNTHETIC SOURCE", fill="black", font_size=26)
        draw.text(
            (30, 130), "Team served 12 stores in 2025.", fill="black", font_size=24
        )
        out = io.BytesIO()
        image.save(out, format="PNG")
        raw = out.getvalue()
        stored = service.onboarding.import_bytes(
            "onboarding_source_document", raw, synthetic=True
        )
        reference, state = service.ensure_state()
        summary = {
            "analysis_mode": "SYNTHETIC_UI_FIXTURE",
            "visual_verification_required": True,
            "raw_lines": 1,
            "visual_regions": [
                {
                    "page": 1,
                    "bbox": [0.04, 0.3, 0.8, 0.45],
                    "line_start": 1,
                    "line_end": 1,
                }
            ],
        }
        metadata = {
            "source_id": "SRC-UI-SYNTHETIC",
            "secure_ref": stored["secure_ref"],
            "sha256": sha256_bytes(raw),
            "raw_retained": True,
            "extension": ".png",
            "category": "project_case",
            "safe_display_name": "synthetic-source.png",
            "extraction_summary": summary,
        }
        candidate = {
            "candidate_id": "SYNTHETIC-UI-CANDIDATE",
            "statement": "Team served 12 stores in 2025.",
            "category": "project",
            "selected": False,
            "selection_reason": "SYNTHETIC_UI_FIXTURE",
            "provenance": {"line_start": 1, "line_end": 1},
        }
        state["pending_sources"] = [
            {
                "source_id": metadata["source_id"],
                "metadata": metadata,
                "extraction_summary": summary,
                "candidates": [candidate],
                "suggestions": [],
            }
        ]
        service._save_state(reference, state)
        server = create_server(service, token="synthetic-workbench-ui")

        def stop_on_input():
            sys.stdin.readline()
            server.shutdown()

        if args.stdio_control:
            threading.Thread(target=stop_on_input, daemon=True).start()
        print(
            json.dumps(
                {
                    "url": f"http://127.0.0.1:{server.server_port}/session/synthetic-workbench-ui/"
                }
            ),
            flush=True,
        )
        try:
            server.serve_forever()
        finally:
            server.server_close()
            service.close()


if __name__ == "__main__":
    main()
