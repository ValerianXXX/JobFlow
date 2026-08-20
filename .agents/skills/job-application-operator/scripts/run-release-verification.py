#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path


def root() -> Path:
    for candidate in Path(__file__).resolve().parents:
        if (candidate / ".jobops-root").is_file():
            return candidate
    raise SystemExit("JOBOPS_PROJECT_ROOT_NOT_FOUND")


PROJECT = root()
sys.path.insert(0, str(PROJECT / "src"))
from jobops.db import JobOpsDB  # noqa: E402
from jobops.release import verify_release, write_release_reports  # noqa: E402
from jobops.util import write_json  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--require-independent", action="store_true")
    args = parser.parse_args()
    test = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"], cwd=PROJECT, capture_output=True, text=True, timeout=1800, check=False)
    output = test.stdout + "\n" + test.stderr
    match = re.search(r"Ran\s+(\d+)\s+tests?", output)
    passed = int(match.group(1)) if match and test.returncode == 0 else 0
    categories: dict[str, int] = {}
    for module in re.findall(r"\((test_[A-Za-z0-9_]+)\.[^)]+\)\s+\.\.\.\s+ok", output):
        categories[module] = categories.get(module, 0) + 1
    test_report = {
        "status": "PASS" if test.returncode == 0 and match else "FAIL", "passed": passed,
        "failed": 0 if test.returncode == 0 else 1, "categories": categories,
        "schema_count": len(list((PROJECT / "schemas").glob("*.schema.json"))),
        "command": "python -m unittest discover -s tests -v", "output_sha256": __import__("hashlib").sha256(output.encode()).hexdigest(),
    }
    write_json(PROJECT / "reports" / "release-test-results.json", test_report)
    skill_script = Path.home() / ".codex" / "skills" / ".system" / "skill-creator" / "scripts" / "quick_validate.py"
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(PROJECT / "tests" / "skill_validation_shim")
    skill = subprocess.run([sys.executable, str(skill_script), str(PROJECT / ".agents" / "skills" / "job-application-operator")], cwd=PROJECT, capture_output=True, text=True, timeout=60, check=False, env=environment)
    write_json(PROJECT / "reports" / "skill-validation.json", {
        "status": "PASS" if skill.returncode == 0 else "FAIL", "returncode": skill.returncode,
        "validator": "$CODEX_HOME/skills/.system/skill-creator/scripts/quick_validate.py",
        "output": (skill.stdout + skill.stderr).strip()[:2000],
    })
    if test_report["status"] != "PASS" or skill.returncode != 0:
        print(json.dumps({"status": "FAIL", "tests": test_report, "skill_returncode": skill.returncode}, ensure_ascii=False, indent=2))
        return 2
    database = JobOpsDB(PROJECT / "state" / "jobops.db")
    database.initialize()
    result = verify_release(PROJECT, database, require_independent=args.require_independent)
    git = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=PROJECT, capture_output=True, text=True,
        timeout=30, check=False,
    )
    source_commit = git.stdout.strip()
    if git.returncode != 0 or re.fullmatch(r"[0-9a-f]{40}", source_commit) is None:
        print(json.dumps({"status": "FAIL", "code": "RELEASE_SOURCE_COMMIT_UNAVAILABLE"}, ensure_ascii=False, indent=2))
        return 2
    result["source_commit"] = source_commit
    write_release_reports(PROJECT, result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
