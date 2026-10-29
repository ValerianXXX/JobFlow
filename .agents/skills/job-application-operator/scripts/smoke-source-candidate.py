#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


def find_project_root() -> Path:
    for candidate in (Path(__file__).resolve().parent, *Path(__file__).resolve().parents):
        if (candidate / ".jobops-root").is_file():
            return candidate
    raise SystemExit("JOBFLOW_PROJECT_ROOT_NOT_FOUND")


def _active_bootstrap_components() -> list[str]:
    """Fixed labels only: no stack text, locals, paths or exception content."""
    labels = {
        ("secure_store.py", "_run"): "encrypted_store",
        ("ats_capabilities.py", "_evidence_bundle_hash"): "ats_evidence",
        ("runtime_schema.py", "validate_named"): "schema_validation",
        ("onboarding_center.py", "bootstrap"): "bootstrap",
    }
    active = set()
    for frame in list(sys._current_frames().values())[:64]:
        for _ in range(64):
            if frame is None:
                break
            label = labels.get((Path(frame.f_code.co_filename).name, frame.f_code.co_name))
            if label:
                active.add(label)
            frame = frame.f_back
    return sorted(active)


def _run_smoke(progress: dict[str, object]) -> dict[str, object]:
    project = find_project_root()
    sys.path.insert(0, str(project / "src"))

    from jobops.db import JobOpsDB
    from jobops.onboarding_center import OnboardingCenterService
    from jobops.onboarding_server import create_server
    from jobops.private_onboarding import PrivateOnboarding
    from jobops.secure_store import WindowsDPAPIStore

    local_app_data = Path(os.environ["LOCALAPPDATA"]).resolve()
    database = JobOpsDB(project / "state" / "jobops.db")
    database.initialize()
    store_script = project / ".agents" / "skills" / "job-application-operator" / "scripts" / "secure-store.ps1"
    onboarding = PrivateOnboarding(database, WindowsDPAPIStore(store_script, local_app_data=local_app_data))
    service = OnboardingCenterService(project, database, onboarding)
    server = create_server(service, port=0, token="synthetic-source-smoke")
    worker = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    worker.start()
    loopback_requests = 0
    try:
        progress["stage"] = "health"
        with urlopen(f"http://127.0.0.1:{server.server_port}/health", timeout=5) as response:
            health = json.loads(response.read().decode("utf-8"))
            loopback_requests += 1
        progress["stage"] = "index"
        with urlopen(server.url, timeout=5) as response:
            index = response.read().decode("utf-8")
            headers = response.headers
            loopback_requests += 1
        progress["stage"] = "bootstrap"
        started = time.monotonic()
        try:
            with urlopen(server.url + "api/bootstrap", timeout=5) as response:
                bootstrap = json.loads(response.read().decode("utf-8"))
                loopback_requests += 1
        except Exception:
            progress["active_components"] = _active_bootstrap_components()
            raise
        finally:
            progress["bootstrap_ms"] = round((time.monotonic() - started) * 1000)
        snapshot = (project / "tests" / "fixtures" / "synthetic-official-job-list.html").read_bytes()
        discovery_query = urlencode({
            "official_url": "https://example.com/careers",
            "company_domain": "example.com",
            "source_format": "html",
        })
        discovery_request = Request(
            server.url + "api/discover-official-jobs?" + discovery_query,
            data=snapshot,
            headers={"Content-Type": "application/octet-stream"},
            method="POST",
        )
        progress["stage"] = "discovery"
        with urlopen(discovery_request, timeout=5) as response:
            discovery = json.loads(response.read().decode("utf-8"))
            loopback_requests += 1
        progress["stage"] = "queue"
        with database.connect() as connection:
            discovery_did_not_mutate_queue = (
                connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0
                and connection.execute("SELECT COUNT(*) FROM intake_queue").fetchone()[0] == 0
            )
        progress["stage"] = "shutdown"
        shutdown = Request(server.url + "api/shutdown", data=b"", method="POST")
        with urlopen(shutdown, timeout=5) as response:
            closing = json.loads(response.read().decode("utf-8"))
            loopback_requests += 1
        worker.join(timeout=5)
        progress["stage"] = "assertions"
        safe_headers = all(headers.get(name) for name in (
            "Content-Security-Policy", "Cache-Control", "X-Content-Type-Options", "X-Frame-Options", "Referrer-Policy",
        ))
        passed = (
            health == {"status": "READY", "binding": "127.0.0.1", "real_external_actions": 0}
            and "JobFlow" in index
            and bootstrap.get("catalog", {}).get("supported_locales") == ["zh", "en"]
            and discovery.get("status") == "LOCAL_SNAPSHOT_PARSED"
            and discovery.get("candidate_count") == 2
            and discovery.get("snapshot_persisted") is False
            and discovery.get("candidate_queue_mutations") == 0
            and discovery.get("network_actions") == 0
            and discovery.get("real_external_actions") == 0
            and discovery_did_not_mutate_queue
            and closing.get("status") == "CLOSING"
            and safe_headers
            and not worker.is_alive()
        )
        result = {
            "status": "PASS" if passed else "FAIL",
            "binding": "127.0.0.1",
            "supported_locales": ["zh", "en"],
            "loopback_requests": loopback_requests,
            "security_headers": "PASS" if safe_headers else "FAIL",
            "offline_discovery": "PASS" if discovery_did_not_mutate_queue else "FAIL",
            "offline_candidates": int(discovery.get("candidate_count", 0)),
            "snapshot_persisted": bool(discovery.get("snapshot_persisted", True)),
            "candidate_queue_mutations": int(discovery.get("candidate_queue_mutations", -1)),
            "project_state_isolated": database.path.is_relative_to(project),
            "local_app_data_isolated": onboarding.store.private_root.is_relative_to(local_app_data),
            "private_values_emitted": 0,
            "external_network_actions": 0,
            "real_external_actions": 0,
            "bootstrap_ms": progress["bootstrap_ms"],
        }
        return result
    finally:
        if worker.is_alive():
            server.shutdown()
            worker.join(timeout=5)
        server.server_close()


def main() -> int:
    progress: dict[str, object] = {"stage": "initialization"}
    started = time.monotonic()
    try:
        result = _run_smoke(progress)
    except Exception as error:
        if isinstance(error, TimeoutError):
            failure_kind = "timeout"
        elif isinstance(error, HTTPError):
            failure_kind = "http_error"
        elif isinstance(error, URLError):
            failure_kind = "connection_error"
        else:
            failure_kind = "unexpected_error"
        result = {"status": "FAIL", "stage": progress["stage"], "failure_kind": failure_kind}
        if "bootstrap_ms" in progress:
            result["bootstrap_ms"] = progress["bootstrap_ms"]
        if "active_components" in progress:
            result["active_components"] = progress["active_components"]
    if result["status"] != "PASS":
        result.setdefault("stage", progress["stage"])
        result.setdefault("failure_kind", "boundary_error")
    result["elapsed_ms"] = round((time.monotonic() - started) * 1000)
    sys.stdout.write(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
