from __future__ import annotations

import json
import socket
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from _support import PROJECT, project_temp
from jobops.authorized_discovery import (
    CONTROL_METADATA_KEY,
    AuthorizedDiscoveryControl,
    authorized_discovery_source_from_url,
    normalize_authorized_discovery_config,
    validate_authorized_discovery_config,
)
from jobops.authorized_discovery_runner import (
    _resolve_public_endpoints,
    fetch_authorized_source,
    run_authorized_discovery,
)
from jobops.db import JobOpsDB
from jobops.errors import JobOpsError
from jobops.private_onboarding import PrivateOnboarding


START = datetime(2026, 8, 19, 12, 0, tzinfo=timezone.utc)


class MemoryStore:
    def __init__(self, root):
        self.private_root = root
        self.values = {}
        self.counter = 0

    def put_bytes(self, value, *, reference=None):
        self.counter += 1
        ref = reference or f"secure-ref:{self.counter:032x}"
        self.values[ref] = bytes(value)
        from jobops.util import sha256_bytes
        return {"secure_ref": ref, "ciphertext_sha256": sha256_bytes(b"cipher-" + value)}

    def get_bytes(self, reference):
        return self.values[reference]

    def ciphertext_sha256(self, reference):
        from jobops.util import sha256_bytes
        return sha256_bytes(b"cipher-" + self.values[reference])

    def delete(self, reference):
        self.values.pop(reference, None)

    def test(self, reference):
        return reference in self.values


def sample_config():
    return {
        "sources": [
            {
                "provider": "company",
                "company_domain": "example.com",
                "official_entry_url": "https://careers.example.com/jobs",
                "feed_url": "https://careers.example.com/jobs",
            },
            {
                "provider": "greenhouse",
                "company_domain": "example.org",
                "official_entry_url": "https://www.example.org/careers",
                "feed_url": "https://boards-api.greenhouse.io/v1/boards/example/jobs?content=true",
            },
        ],
        "include_terms": ["credit risk", "risk analyst"],
        "exclude_terms": ["director"],
        "location_terms": ["New York", "Remote"],
    }


class AuthorizedDiscoveryTests(unittest.TestCase):
    def control(self, temp):
        database = JobOpsDB(temp / "jobops.db")
        database.initialize()
        store = MemoryStore(temp / "private")
        onboarding = PrivateOnboarding(database, store)
        return database, store, AuthorizedDiscoveryControl(database, onboarding, PROJECT / "schemas")

    def test_exact_sources_and_private_filters_are_normalized(self) -> None:
        value = normalize_authorized_discovery_config(sample_config(), now=START)
        self.assertEqual(len(value["sources"]), 2)
        self.assertEqual(value["sources"][0]["source_id"][:4], "ADS-")
        self.assertEqual(value["safety"]["final_submit"], "USER_ONLY")
        self.assertEqual(validate_authorized_discovery_config(value), value)

        bad = sample_config()
        bad["sources"][1]["feed_url"] = "https://evil.example/jobs"
        with self.assertRaises(JobOpsError) as rejected:
            normalize_authorized_discovery_config(bad, now=START)
        self.assertEqual(rejected.exception.code, "DISCOVERY_SOURCE_FEED_HOST_INVALID")

        sensitive = sample_config()
        sensitive["sources"][0]["feed_url"] += "?token=secret"
        sensitive["sources"][0]["official_entry_url"] = sensitive["sources"][0]["feed_url"]
        with self.assertRaises(JobOpsError) as rejected:
            normalize_authorized_discovery_config(sensitive, now=START)
        self.assertEqual(rejected.exception.code, "DISCOVERY_SOURCE_SENSITIVE_URL")

    def test_public_board_roots_map_to_exact_read_only_provider_feeds(self) -> None:
        cases = (
            (
                "https://job-boards.greenhouse.io/example_board",
                "greenhouse",
                "https://boards-api.greenhouse.io/v1/boards/example_board/jobs",
            ),
            (
                "https://jobs.lever.co/example-site",
                "lever",
                "https://api.lever.co/v0/postings/example-site?mode=json",
            ),
            (
                "https://jobs.ashbyhq.com/example_board",
                "ashby",
                "https://api.ashbyhq.com/posting-api/job-board/example_board",
            ),
            (
                "https://jobs.smartrecruiters.com/example-company",
                "smartrecruiters",
                "https://api.smartrecruiters.com/v1/companies/example-company/postings",
            ),
        )
        for public_url, provider, feed_url in cases:
            with self.subTest(provider=provider):
                source = authorized_discovery_source_from_url(public_url)
                self.assertEqual(source["provider"], provider)
                self.assertEqual(source["feed_url"], feed_url)

        exact_feed = authorized_discovery_source_from_url(
            "https://api.smartrecruiters.com/v1/companies/example-company/postings?limit=100&offset=0"
        )
        self.assertEqual(exact_feed["provider"], "smartrecruiters")
        self.assertEqual(
            exact_feed["feed_url"],
            "https://api.smartrecruiters.com/v1/companies/example-company/postings?limit=100&offset=0",
        )

    def test_company_subdomain_root_is_valid_but_individual_ats_job_is_not_widened(self) -> None:
        company = authorized_discovery_source_from_url("https://careers.example.com/")
        self.assertEqual(company["provider"], "company")
        self.assertEqual(company["feed_url"], "https://careers.example.com/")

        individual = authorized_discovery_source_from_url("https://jobs.lever.co/example-site/posting-123")
        self.assertEqual(individual["provider"], "company")
        self.assertEqual(individual["feed_url"], "https://jobs.lever.co/example-site/posting-123")

        with self.assertRaises(JobOpsError) as generic_home:
            authorized_discovery_source_from_url("https://example.com/")
        self.assertEqual(generic_home.exception.code, "OFFICIAL_CAREERS_PATH_NOT_PROVEN")
        with self.assertRaises(JobOpsError) as bad_mode:
            authorized_discovery_source_from_url("https://api.lever.co/v0/postings/example-site?mode=iframe")
        self.assertEqual(bad_mode.exception.code, "DISCOVERY_SOURCE_QUERY_INVALID")

    def test_network_fetch_is_dns_pinned_and_rejects_encoded_or_mixed_private_responses(self) -> None:
        public_endpoint = (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, ("93.184.216.34", 443))

        class Headers:
            def __init__(self, values):
                self.values = values

            def get(self, name, default=None):
                return self.values.get(name, default)

            def get_content_type(self):
                return self.values.get("Content-Type", "application/octet-stream").split(";", 1)[0]

        class Response:
            status = 200

            def __init__(self, *, encoding="identity"):
                self.headers = Headers({
                    "Content-Length": "15",
                    "Content-Type": "text/html; charset=utf-8",
                    "Content-Encoding": encoding,
                })

            def read(self, _limit):
                return b"<html>ok</html>"

        connections = []

        class Connection:
            def __init__(self, host, endpoint, *, timeout):
                self.host = host
                self.endpoint = endpoint
                self.timeout = timeout
                self.request_args = None
                connections.append(self)

            def request(self, method, path, *, headers):
                self.request_args = (method, path, headers)

            def getresponse(self):
                return Response()

            def close(self):
                return None

        source = {"feed_url": "https://careers.example.com/jobs?team=risk", "source_format": "html"}
        with (
            mock.patch(
                "jobops.authorized_discovery_runner._resolve_public_endpoints",
                return_value=(public_endpoint,),
            ),
            mock.patch("jobops.authorized_discovery_runner._PinnedHTTPSConnection", Connection),
        ):
            self.assertEqual(fetch_authorized_source(source), b"<html>ok</html>")
        self.assertEqual(connections[0].endpoint, public_endpoint)
        self.assertEqual(connections[0].request_args[0:2], ("GET", "/jobs?team=risk"))
        self.assertEqual(connections[0].request_args[2]["Accept-Encoding"], "identity")

        with mock.patch(
            "jobops.authorized_discovery_runner.socket.getaddrinfo",
            return_value=[
                (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("93.184.216.34", 443)),
                (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("127.0.0.1", 443)),
            ],
        ):
            with self.assertRaises(JobOpsError) as rebinding:
                _resolve_public_endpoints("careers.example.com")
        self.assertEqual(rebinding.exception.code, "DISCOVERY_NETWORK_HOST_BLOCKED")

        class EncodedConnection(Connection):
            def getresponse(self):
                return Response(encoding="gzip")

        with (
            mock.patch(
                "jobops.authorized_discovery_runner._resolve_public_endpoints",
                return_value=(public_endpoint,),
            ),
            mock.patch("jobops.authorized_discovery_runner._PinnedHTTPSConnection", EncodedConnection),
        ):
            with self.assertRaises(JobOpsError) as encoded:
                fetch_authorized_source(source)
        self.assertEqual(encoded.exception.code, "DISCOVERY_NETWORK_CONTENT_ENCODING_INVALID")

        with self.assertRaises(JobOpsError) as port:
            fetch_authorized_source({"feed_url": "https://careers.example.com:8443/jobs", "source_format": "html"})
        self.assertEqual(port.exception.code, "DISCOVERY_NETWORK_URL_INVALID")

    def test_configuration_persists_only_an_opaque_reference_and_expires(self) -> None:
        with project_temp() as temp:
            database, store, control = self.control(temp)
            state = control.configure(
                sample_config(), interval_minutes=60, authorization_hours=24,
                max_new_per_run=10, user_confirmed=True, now=START,
            )
            self.assertEqual(state["status"], "READY")
            self.assertEqual(state["source_count"], 2)
            self.assertEqual(state["inbox_limit"], 250)
            self.assertEqual(state["task_registration_state"], "REGISTRATION_REQUIRED")
            public_text = json.dumps(state)
            self.assertNotIn("credit risk", public_text)
            self.assertNotIn("example.com", public_text)

            with database.connect() as connection:
                raw_text = str(connection.execute(
                    "SELECT value FROM metadata WHERE key=?", (CONTROL_METADATA_KEY,),
                ).fetchone()["value"])
            self.assertNotIn("credit risk", raw_text)
            self.assertNotIn("example.com", raw_text)
            self.assertEqual(len(store.values), 1)
            self.assertEqual(control.state(now=START + timedelta(hours=24))["status"], "AUTHORIZATION_EXPIRED")

    def test_reconfiguration_atomically_switches_immutable_references_and_cleans_failures(self) -> None:
        with project_temp() as temp:
            database, store, control = self.control(temp)
            first = control.configure(
                sample_config(), interval_minutes=60, authorization_hours=24,
                max_new_per_run=10, user_confirmed=True, now=START,
            )
            with database.connect() as connection:
                first_raw = json.loads(str(connection.execute(
                    "SELECT value FROM metadata WHERE key=?", (CONTROL_METADATA_KEY,),
                ).fetchone()["value"]))
            first_reference = first_raw["config_ref"]
            original = bytes(store.values[first_reference])

            changed_config = sample_config()
            changed_config["include_terms"] = ["credit analyst"]
            second = control.configure(
                changed_config, interval_minutes=120, authorization_hours=48,
                max_new_per_run=15, user_confirmed=True, now=START + timedelta(minutes=1),
            )
            with database.connect() as connection:
                second_raw = json.loads(str(connection.execute(
                    "SELECT value FROM metadata WHERE key=?", (CONTROL_METADATA_KEY,),
                ).fetchone()["value"]))
                active_refs = connection.execute(
                    "SELECT COUNT(*) FROM private_refs WHERE kind=? AND status='ACTIVE'", ("authorized_discovery_config",),
                ).fetchone()[0]
            self.assertEqual(second["generation"], first["generation"] + 1)
            second_reference = second_raw["config_ref"]
            self.assertNotEqual(second_reference, first_reference)
            self.assertEqual(active_refs, 1)
            self.assertEqual(len(store.values), 1)
            self.assertNotIn(first_reference, store.values)
            rotated = bytes(store.values[second_reference])
            self.assertNotEqual(rotated, original)

            failing_config = sample_config()
            failing_config["include_terms"] = ["market risk"]
            with mock.patch.object(control, "_save", side_effect=RuntimeError("synthetic metadata failure")):
                with self.assertRaises(RuntimeError):
                    control.configure(
                        failing_config, interval_minutes=180, authorization_hours=72,
                        max_new_per_run=20, user_confirmed=True, now=START + timedelta(minutes=2),
                    )
            self.assertEqual(store.values[second_reference], rotated)
            self.assertEqual(control.state(now=START + timedelta(minutes=2))["generation"], second["generation"])
            self.assertEqual(len(store.values), 1)

    def test_concurrent_configuration_change_cannot_overwrite_the_current_ciphertext(self) -> None:
        with project_temp() as temp:
            _, store, control = self.control(temp)
            first = control.configure(
                sample_config(), interval_minutes=60, authorization_hours=24,
                max_new_per_run=10, user_confirmed=True, now=START,
            )
            original_reference = next(iter(store.values))
            original_value = bytes(store.values[original_reference])
            actual_import = control.onboarding.import_bytes

            def import_then_pause(*args, **kwargs):
                stored = actual_import(*args, **kwargs)
                control.pause(user_confirmed=True, now=START + timedelta(minutes=1))
                return stored

            changed = sample_config()
            changed["include_terms"] = ["market risk"]
            with mock.patch.object(control.onboarding, "import_bytes", side_effect=import_then_pause):
                with self.assertRaises(JobOpsError) as rejected:
                    control.configure(
                        changed, interval_minutes=120, authorization_hours=24,
                        max_new_per_run=10, user_confirmed=True,
                        now=START + timedelta(minutes=1),
                    )
            self.assertEqual(rejected.exception.code, "DISCOVERY_CONTROL_CONCURRENT_CHANGE")
            state = control.state(now=START + timedelta(minutes=1))
            self.assertEqual(state["generation"], first["generation"] + 1)
            self.assertEqual(state["status"], "PAUSED")
            self.assertEqual(store.values, {original_reference: original_value})

    def test_generation_lease_and_kill_switch_block_stale_commit(self) -> None:
        with project_temp() as temp:
            _, _, control = self.control(temp)
            configured = control.configure(
                sample_config(), interval_minutes=60, authorization_hours=24,
                max_new_per_run=10, user_confirmed=True, now=START,
            )
            control.mark_task_registration(registered=True, generation=configured["generation"], now=START)
            lease = control.claim_due_run(now=START + timedelta(minutes=60))
            private = control.read_private_config(
                run_id=lease["run_id"], generation=lease["generation"],
                now=START + timedelta(minutes=60),
            )
            self.assertEqual(len(private["sources"]), 2)

            killed = control.kill_switch(user_confirmed=True, now=START + timedelta(minutes=61))
            self.assertEqual(killed["status"], "PAUSED")
            self.assertEqual(killed["pause_reason"], "USER_KILL_SWITCH")
            self.assertEqual(killed["task_registration_state"], "REMOVAL_REQUIRED")
            with self.assertRaises(JobOpsError) as stale:
                control.record_run(
                    run_id=lease["run_id"], generation=lease["generation"],
                    result={"source_count": 2, "network_requests": 2, "candidate_count": 4, "new_candidate_count": 3, "error_count": 0},
                    now=START + timedelta(minutes=62),
                )
            self.assertEqual(stale.exception.code, "DISCOVERY_RUN_STALE")

    def test_run_cannot_commit_after_the_authorization_window_expires(self) -> None:
        with project_temp() as temp:
            _, _, control = self.control(temp)
            configured = control.configure(
                sample_config(), interval_minutes=60, authorization_hours=2,
                max_new_per_run=10, user_confirmed=True, now=START,
            )
            control.mark_task_registration(registered=True, generation=configured["generation"], now=START)
            lease = control.claim_due_run(now=START + timedelta(minutes=119))
            with self.assertRaises(JobOpsError) as expired:
                control.record_run(
                    run_id=lease["run_id"], generation=lease["generation"],
                    result={
                        "source_count": 2, "network_requests": 2,
                        "candidate_count": 0, "new_candidate_count": 0, "error_count": 0,
                    },
                    now=START + timedelta(minutes=121),
                )
            self.assertEqual(expired.exception.code, "DISCOVERY_RUN_STALE")

    def test_result_counts_are_bounded_and_bound_to_the_authorized_sources(self) -> None:
        with project_temp() as temp:
            _, _, control = self.control(temp)
            configured = control.configure(
                sample_config(), interval_minutes=60, authorization_hours=4,
                max_new_per_run=10, user_confirmed=True, now=START,
            )
            control.mark_task_registration(registered=True, generation=configured["generation"], now=START)
            lease = control.claim_due_run(now=START + timedelta(minutes=60))
            with self.assertRaises(JobOpsError) as malformed:
                control.record_run(
                    run_id=lease["run_id"], generation=lease["generation"],
                    result={
                        "source_count": 2, "network_requests": 2,
                        "candidate_count": 1, "new_candidate_count": 2, "error_count": 0,
                    }, now=START + timedelta(minutes=60),
                )
            self.assertEqual(malformed.exception.code, "DISCOVERY_RUN_RESULT_INVALID")
            with self.assertRaises(JobOpsError) as wrong_source_count:
                control.record_run(
                    run_id=lease["run_id"], generation=lease["generation"],
                    result={
                        "source_count": 1, "network_requests": 1,
                        "candidate_count": 0, "new_candidate_count": 0, "error_count": 0,
                    }, now=START + timedelta(minutes=60),
                )
            self.assertEqual(wrong_source_count.exception.code, "DISCOVERY_RUN_RESULT_INVALID")

    def test_candidate_aggregate_is_recomputed_from_validated_records(self) -> None:
        with project_temp() as temp:
            _, _, control = self.control(temp)
            config = sample_config()
            config["sources"] = [config["sources"][0]]
            configured = control.configure(
                config, interval_minutes=60, authorization_hours=4,
                max_new_per_run=10, user_confirmed=True, now=START,
            )
            control.mark_task_registration(
                registered=True, generation=configured["generation"], now=START,
            )
            lease = control.claim_due_run(now=START + timedelta(minutes=60))
            private = control.read_private_config(
                run_id=lease["run_id"], generation=lease["generation"],
                now=START + timedelta(minutes=60),
            )
            source_id = private["sources"][0]["source_id"]
            result = control.commit_candidates(
                run_id=lease["run_id"], generation=lease["generation"],
                candidates=[{
                    "source_id": source_id,
                    "provider": "company",
                    "company_domain": "example.com",
                    "official_url": "https://careers.example.com/jobs/risk-analyst",
                    "title": "Risk Analyst",
                    "location": "Remote",
                }],
                # Caller aggregates are untrusted. Candidate totals must be
                # derived again inside the write transaction.
                result={
                    "source_count": 1, "network_requests": 1,
                    "candidate_count": 4999, "new_candidate_count": 4999,
                    "error_count": 0,
                },
                now=START + timedelta(minutes=60),
            )
            self.assertEqual(result["run_result"]["candidate_count"], 1)
            self.assertEqual(result["run_result"]["new_candidate_count"], 1)

    def test_task_registration_requires_a_live_authorization(self) -> None:
        with project_temp() as temp:
            _, _, control = self.control(temp)
            configured = control.configure(
                sample_config(), interval_minutes=60, authorization_hours=1,
                max_new_per_run=10, user_confirmed=True, now=START,
            )
            with self.assertRaises(JobOpsError) as expired:
                control.mark_task_registration(
                    registered=True, generation=configured["generation"],
                    now=START + timedelta(hours=1),
                )
            self.assertEqual(expired.exception.code, "DISCOVERY_TASK_REGISTRATION_NOT_ALLOWED")

            paused = control.pause(user_confirmed=True, now=START + timedelta(minutes=1))
            with self.assertRaises(JobOpsError) as rejected:
                control.mark_task_registration(
                    registered=True, generation=paused["generation"],
                    now=START + timedelta(minutes=2),
                )
            self.assertEqual(rejected.exception.code, "DISCOVERY_TASK_REGISTRATION_NOT_ALLOWED")
            unregistered = control.mark_task_registration(
                registered=False, generation=paused["generation"],
                now=START + timedelta(minutes=2),
            )
            self.assertEqual(unregistered["task_registration_state"], "NOT_REGISTERED")

    def test_completed_run_is_aggregate_only_and_three_partial_failures_pause(self) -> None:
        with project_temp() as temp:
            database, _, control = self.control(temp)
            state = control.configure(
                sample_config(), interval_minutes=60, authorization_hours=12,
                max_new_per_run=10, user_confirmed=True, now=START,
            )
            control.mark_task_registration(registered=True, generation=state["generation"], now=START)
            for index in range(3):
                when = START + timedelta(minutes=61 * (index + 1))
                lease = control.claim_due_run(now=when)
                result = control.record_run(
                    run_id=lease["run_id"], generation=lease["generation"],
                    result={"source_count": 2, "network_requests": 2, "candidate_count": 0, "new_candidate_count": 0, "error_count": 1},
                    now=when,
                )
            self.assertEqual(result["status"], "PAUSED")
            self.assertEqual(result["pause_reason"], "REPEATED_FAILURES")
            self.assertFalse(result["read_only_network_authorized"])
            with database.connect() as connection:
                payloads = [str(row[0]) for row in connection.execute(
                    "SELECT payload_json FROM events WHERE event_type LIKE 'AUTHORIZED_DISCOVERY_%'"
                ).fetchall()]
            self.assertTrue(payloads)
            self.assertNotIn("credit risk", "".join(payloads))
            self.assertNotIn("example.com", "".join(payloads))
            self.assertNotIn("secure-ref:", "".join(payloads))

    def test_invalid_confirmation_bounds_and_state_tampering_fail_closed(self) -> None:
        with project_temp() as temp:
            database, _, control = self.control(temp)
            cases = [
                ({"interval_minutes": 59, "authorization_hours": 1, "max_new_per_run": 1, "user_confirmed": True}, "DISCOVERY_INTERVAL_INVALID"),
                ({"interval_minutes": 60, "authorization_hours": 169, "max_new_per_run": 1, "user_confirmed": True}, "DISCOVERY_AUTHORIZATION_WINDOW_INVALID"),
                ({"interval_minutes": 60, "authorization_hours": 1, "max_new_per_run": 101, "user_confirmed": True}, "DISCOVERY_RUN_LIMIT_INVALID"),
                ({"interval_minutes": 60, "authorization_hours": 1, "max_new_per_run": 1, "inbox_limit": 9, "user_confirmed": True}, "DISCOVERY_INBOX_LIMIT_INVALID"),
                ({"interval_minutes": 60, "authorization_hours": 1, "max_new_per_run": 1, "user_confirmed": False}, "EXPLICIT_CONFIRMATION_REQUIRED"),
            ]
            for kwargs, code in cases:
                with self.subTest(code=code), self.assertRaises(JobOpsError) as failure:
                    control.configure(sample_config(), now=START, **kwargs)
                self.assertEqual(failure.exception.code, code)

            control.configure(
                sample_config(), interval_minutes=60, authorization_hours=2,
                max_new_per_run=5, user_confirmed=True, now=START,
            )
            with database.connect() as connection:
                raw = json.loads(str(connection.execute(
                    "SELECT value FROM metadata WHERE key=?", (CONTROL_METADATA_KEY,),
                ).fetchone()["value"]))
                raw["source_count"] = 3
                connection.execute(
                    "UPDATE metadata SET value=? WHERE key=?", (json.dumps(raw), CONTROL_METADATA_KEY),
                )
            with self.assertRaises(JobOpsError) as changed:
                control.state(now=START)
            self.assertEqual(changed.exception.code, "DISCOVERY_CONTROL_STATE_CHANGED")

    def test_runner_reads_exact_sources_and_populates_only_the_candidate_inbox(self) -> None:
        with project_temp() as temp:
            database, _, control = self.control(temp)
            state = control.configure(
                sample_config(), interval_minutes=60, authorization_hours=12,
                max_new_per_run=10, user_confirmed=True, now=START,
            )
            control.mark_task_registration(registered=True, generation=state["generation"], now=START)
            payloads = {
                "company": b"""<!doctype html><a data-location='New York' href='/jobs/credit-risk-analyst'>Credit Risk Analyst</a>""",
                "greenhouse": json.dumps({
                    "jobs": [{
                        "absolute_url": "https://boards.greenhouse.io/example/jobs/101",
                        "title": "Senior Risk Analyst",
                        "location": {"name": "Remote"},
                    }]
                }).encode("utf-8"),
            }
            calls = []

            def fetch(source):
                calls.append(source["source_id"])
                return payloads[source["provider"]]

            first = run_authorized_discovery(
                control, approved_ats_hosts=[], fetcher=fetch,
                now=START + timedelta(minutes=60),
            )
            self.assertEqual(len(calls), 2)
            self.assertEqual(first["control"]["run_result"]["new_candidate_count"], 2)
            self.assertEqual(first["inbox"]["counts"]["NEW"], 2)
            self.assertEqual(len(first["inbox"]["candidates"]), 2)
            self.assertTrue(all(item["official_url"].startswith("https://") for item in first["inbox"]["candidates"]))
            with database.connect() as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM applications").fetchone()[0], 0)
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0], 0)

            second = run_authorized_discovery(
                control, approved_ats_hosts=[], fetcher=fetch,
                now=START + timedelta(minutes=120),
            )
            self.assertEqual(second["control"]["run_result"]["new_candidate_count"], 0)
            self.assertEqual(second["inbox"]["counts"]["NEW"], 2)

    def test_failed_fetch_is_counted_as_a_real_network_attempt(self) -> None:
        with project_temp() as temp:
            _, _, control = self.control(temp)
            config = sample_config()
            config["sources"] = [config["sources"][0]]
            state = control.configure(
                config, interval_minutes=60, authorization_hours=12,
                max_new_per_run=10, user_confirmed=True, now=START,
            )
            control.mark_task_registration(registered=True, generation=state["generation"], now=START)

            def fail_after_request(_source):
                raise OSError("synthetic TLS failure")

            result = run_authorized_discovery(
                control, approved_ats_hosts=[], fetcher=fail_after_request,
                now=START + timedelta(minutes=60),
            )
            self.assertEqual(result["network_requests"], 1)
            self.assertEqual(result["control"]["run_result"]["network_requests"], 1)
            self.assertEqual(result["control"]["run_result"]["error_count"], 1)

    def test_direct_public_ats_source_is_tenant_bound(self) -> None:
        with project_temp() as temp:
            _, _, control = self.control(temp)
            config = sample_config()
            config["sources"] = [{
                "provider": "company",
                "company_domain": "greenhouse.io",
                "official_entry_url": "https://boards.greenhouse.io/example/jobs",
                "feed_url": "https://boards.greenhouse.io/example/jobs",
            }]
            state = control.configure(
                config, interval_minutes=60, authorization_hours=12,
                max_new_per_run=10, user_confirmed=True, now=START,
            )
            control.mark_task_registration(registered=True, generation=state["generation"], now=START)
            payload = b"""<!doctype html>
            <a data-location='Remote' href='/example/jobs/101'>Credit Risk Analyst</a>
            <a data-location='Remote' href='/other-company/jobs/202'>Credit Risk Analyst</a>"""
            result = run_authorized_discovery(
                control, approved_ats_hosts=[], fetcher=lambda _source: payload,
                now=START + timedelta(minutes=60),
            )
            self.assertEqual(result["inbox"]["counts"]["NEW"], 1)
            candidate = result["inbox"]["candidates"][0]
            self.assertEqual(candidate["provider"], "greenhouse")
            self.assertIn("/example/jobs/101", candidate["official_url"])

    def test_resume_rejects_a_corrupted_encrypted_configuration(self) -> None:
        with project_temp() as temp:
            _, store, control = self.control(temp)
            configured = control.configure(
                sample_config(), interval_minutes=60, authorization_hours=12,
                max_new_per_run=10, user_confirmed=True, now=START,
            )
            paused = control.pause(user_confirmed=True, now=START + timedelta(minutes=1))
            reference = next(iter(store.values))
            store.values[reference] = b"tampered"
            with self.assertRaises(JobOpsError):
                control.resume(
                    authorization_hours=12, user_confirmed=True,
                    now=START + timedelta(minutes=2),
                )
            current = control.state(now=START + timedelta(minutes=2))
            self.assertEqual(current["generation"], paused["generation"])
            self.assertEqual(current["status"], "PAUSED")

    def test_candidate_commit_revalidates_encrypted_source_and_provider_bindings(self) -> None:
        with project_temp() as temp:
            database, _, control = self.control(temp)
            config = sample_config()
            config["sources"] = [config["sources"][0]]
            configured = control.configure(
                config, interval_minutes=60, authorization_hours=12,
                max_new_per_run=10, user_confirmed=True, now=START,
            )
            control.mark_task_registration(registered=True, generation=configured["generation"], now=START)
            lease = control.claim_due_run(now=START + timedelta(minutes=60))
            private = control.read_private_config(
                run_id=lease["run_id"], generation=lease["generation"],
                now=START + timedelta(minutes=60),
            )
            source_id = private["sources"][0]["source_id"]
            with self.assertRaises(JobOpsError) as rejected:
                control.commit_candidates(
                    run_id=lease["run_id"], generation=lease["generation"],
                    candidates=[{
                        "source_id": source_id,
                        "provider": "company",
                        "company_domain": "example.com",
                        "official_url": "https://unrelated.invalid/jobs/credit-risk",
                        "title": "Credit Risk Analyst",
                        "location": "Remote",
                    }],
                    result={
                        "source_count": 1, "network_requests": 1,
                        "candidate_count": 1, "new_candidate_count": 0,
                        "error_count": 0,
                    },
                    now=START + timedelta(minutes=60),
                )
            self.assertEqual(rejected.exception.code, "DISCOVERY_CANDIDATE_SOURCE_MISMATCH")
            with database.connect() as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM discovery_candidates").fetchone()[0], 0)

    def test_runner_enforces_inbox_cap_and_candidate_changes_require_confirmation(self) -> None:
        with project_temp() as temp:
            _, _, control = self.control(temp)
            config = sample_config()
            config["sources"] = [config["sources"][0]]
            state = control.configure(
                config, interval_minutes=60, authorization_hours=12,
                max_new_per_run=1, inbox_limit=10, user_confirmed=True, now=START,
            )
            control.mark_task_registration(registered=True, generation=state["generation"], now=START)
            payload = b"""<!doctype html>
            <a data-location='New York' href='/jobs/credit-risk-one'>Credit Risk Analyst</a>
            <a data-location='Remote' href='/jobs/risk-analyst-two'>Risk Analyst</a>"""
            result = run_authorized_discovery(
                control, approved_ats_hosts=[], fetcher=lambda source: payload,
                now=START + timedelta(minutes=60),
            )
            self.assertEqual(result["control"]["run_result"]["new_candidate_count"], 1)
            candidate_id = result["inbox"]["candidates"][0]["candidate_id"]
            with self.assertRaises(JobOpsError) as rejected:
                control.set_candidate_status(candidate_id=candidate_id, status="QUEUED", user_confirmed=False)
            self.assertEqual(rejected.exception.code, "EXPLICIT_CONFIRMATION_REQUIRED")
            changed = control.set_candidate_status(
                candidate_id=candidate_id, status="QUEUED", user_confirmed=True,
                now=START + timedelta(minutes=61),
            )
            self.assertEqual(changed["status"], "QUEUED")
            self.assertFalse(changed["application_created"])
            with self.assertRaises(JobOpsError) as immutable:
                control.set_candidate_status(
                    candidate_id=candidate_id, status="IGNORED", user_confirmed=True,
                    now=START + timedelta(minutes=62),
                )
            self.assertEqual(immutable.exception.code, "DISCOVERY_CANDIDATE_TRANSITION_INVALID")

    def test_kill_switch_during_fetch_blocks_every_candidate_write(self) -> None:
        with project_temp() as temp:
            database, _, control = self.control(temp)
            config = sample_config()
            config["sources"] = [config["sources"][0]]
            state = control.configure(
                config, interval_minutes=60, authorization_hours=12,
                max_new_per_run=10, user_confirmed=True, now=START,
            )
            control.mark_task_registration(registered=True, generation=state["generation"], now=START)

            def fetch(_source):
                control.kill_switch(user_confirmed=True, now=START + timedelta(minutes=60, seconds=1))
                return b"<a data-location='Remote' href='/jobs/risk-analyst'>Risk Analyst</a>"

            with self.assertRaises(JobOpsError) as stopped:
                run_authorized_discovery(
                    control, approved_ats_hosts=[], fetcher=fetch,
                    now=START + timedelta(minutes=60),
                )
            self.assertEqual(stopped.exception.code, "DISCOVERY_RUN_STALE")
            with database.connect() as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM discovery_candidates").fetchone()[0], 0)

    def test_private_config_failure_releases_the_run_without_waiting_for_lease_expiry(self) -> None:
        with project_temp() as temp:
            _, store, control = self.control(temp)
            state = control.configure(
                sample_config(), interval_minutes=60, authorization_hours=12,
                max_new_per_run=10, user_confirmed=True, now=START,
            )
            control.mark_task_registration(registered=True, generation=state["generation"], now=START)
            reference = next(iter(store.values))
            store.values[reference] = b"tampered"
            with self.assertRaises(JobOpsError) as rejected:
                run_authorized_discovery(
                    control, approved_ats_hosts=[], fetcher=lambda _source: b"unused",
                    now=START + timedelta(minutes=60),
                )
            self.assertEqual(rejected.exception.code, "SECURE_CIPHERTEXT_HASH_MISMATCH")
            released = control.state(now=START + timedelta(minutes=60))
            self.assertEqual(released["last_run_status"], "COMPLETED_WITH_ERRORS")
            self.assertEqual(released["status"], "READY")
            self.assertEqual(released["consecutive_failures"], 1)


if __name__ == "__main__":
    unittest.main()
