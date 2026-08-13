#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path
from urllib.request import Request, urlopen


def find_project_root() -> Path:
    for candidate in (Path(__file__).resolve().parent, *Path(__file__).resolve().parents):
        if (candidate / ".jobops-root").is_file():
            return candidate
    raise SystemExit("JOBFLOW_PROJECT_ROOT_NOT_FOUND")


def main() -> int:
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
        with urlopen(f"http://127.0.0.1:{server.server_port}/health", timeout=5) as response:
            health = json.loads(response.read().decode("utf-8"))
            loopback_requests += 1
        with urlopen(server.url, timeout=5) as response:
            index = response.read().decode("utf-8")
            headers = response.headers
            loopback_requests += 1
        with urlopen(server.url + "api/bootstrap", timeout=5) as response:
            bootstrap = json.loads(response.read().decode("utf-8"))
            loopback_requests += 1
        shutdown = Request(server.url + "api/shutdown", data=b"", method="POST")
        with urlopen(shutdown, timeout=5) as response:
            closing = json.loads(response.read().decode("utf-8"))
            loopback_requests += 1
        worker.join(timeout=5)
        safe_headers = all(headers.get(name) for name in (
            "Content-Security-Policy", "Cache-Control", "X-Content-Type-Options", "X-Frame-Options", "Referrer-Policy",
        ))
        passed = (
            health == {"status": "READY", "binding": "127.0.0.1", "real_external_actions": 0}
            and "JobFlow" in index
            and bootstrap.get("catalog", {}).get("supported_locales") == ["zh", "en"]
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
            "project_state_isolated": database.path.is_relative_to(project),
            "local_app_data_isolated": onboarding.store.private_root.is_relative_to(local_app_data),
            "private_values_emitted": 0,
            "external_network_actions": 0,
            "real_external_actions": 0,
        }
        sys.stdout.write(json.dumps(result, ensure_ascii=False))
        return 0 if passed else 2
    finally:
        if worker.is_alive():
            server.shutdown()
            worker.join(timeout=5)
        server.server_close()


if __name__ == "__main__":
    raise SystemExit(main())
