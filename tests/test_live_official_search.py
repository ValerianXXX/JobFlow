from __future__ import annotations

import json
import unittest

from jobops.errors import JobOpsError
from jobops.live_official_search import (
    browser_search_query,
    prepare_official_job_candidates,
    select_official_job_candidate,
    verify_official_job_page_match,
)


class SelectionEngine:
    ready = True

    def __init__(self, result: dict[str, object]) -> None:
        self.result = result
        self.request: dict[str, object] | None = None

    def public_status(self) -> dict[str, object]:
        return {"status": "READY", "structured_capability_status": "VERIFIED"}

    def execute_structured_task(self, request: dict[str, object]) -> object:
        self.request = request
        return self.result


class LiveOfficialSearchTests(unittest.TestCase):
    def candidates(self) -> list[dict[str, str]]:
        return prepare_official_job_candidates([
            {
                "url": "https://careers.example.com/us/en/job/CR-102/Credit-Risk-Analyst?source=search",
                "title": "Credit Risk Analyst | Example Careers",
                "snippet": "Join Example's credit risk team in New York.",
            },
            {
                "url": "https://www.indeed.com/viewjob?jk=123",
                "title": "Credit Risk Analyst",
                "snippet": "Aggregator copy.",
            },
            {
                "url": "https://jobs.lever.co/example/abc",
                "title": "Credit Risk Analyst",
                "snippet": "Direct ATS result must not establish the company route.",
            },
            {
                "url": "https://careers.other.example/jobs/risk?session=private",
                "title": "Risk Analyst",
                "snippet": "Sensitive search result URL must be rejected.",
            },
        ], approved_ats_hosts={"jobs.lever.co", "myworkdayjobs.com"}, search_origin="https://www.google.com/search")

    def test_browser_query_is_bounded_and_biases_to_official_company_pages(self) -> None:
        query = browser_search_query("Credit risk analyst New York")
        self.assertIn("official company careers jobs", query)
        self.assertIn("-linkedin", query)
        with self.assertRaises(JobOpsError):
            browser_search_query("x" * 301)

    def test_only_company_career_candidates_survive_host_filters(self) -> None:
        candidates = self.candidates()
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["company_domain"], "example.com")
        self.assertEqual(candidates[0]["host"], "careers.example.com")

    def test_public_tracking_links_deduplicate_without_dropping_job_identity(self) -> None:
        candidates = prepare_official_job_candidates([
            {
                "url": "https://careers.example.com/jobs/risk-123?utm_source=search&jobId=123",
                "title": "Credit Risk Analyst",
                "snippet": "New York role.",
            },
            {
                "url": "https://careers.example.com/jobs/risk-123?source=google&jobId=123",
                "title": "Credit Risk Analyst",
                "snippet": "The same New York role.",
            },
        ], approved_ats_hosts=set(), search_origin="https://www.google.com/search")
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["url"], "https://careers.example.com/jobs/risk-123?jobId=123")

    def test_ai_ranks_refs_but_cannot_create_a_url_or_candidate(self) -> None:
        candidates = self.candidates()
        ref = candidates[0]["candidate_ref"]
        engine = SelectionEngine({
            "schema_version": 1, "status": "SELECTED",
            "ranked_candidate_refs": [ref],
            "summary": "The official company role matches the requested title and location.",
        })
        selected = select_official_job_candidate(
            engine, intent="Credit risk analyst New York", candidates=candidates,
        )
        self.assertEqual(selected["official_url"], candidates[0]["url"])
        self.assertEqual(selected["real_external_actions"], 0)
        request = json.dumps(engine.request, ensure_ascii=False)
        self.assertNotIn("?source=search", request)
        self.assertIn("search_metadata_is_untrusted", request)

        engine.result = {
            "schema_version": 1, "status": "SELECTED",
            "ranked_candidate_refs": ["JDC-INVENTED"], "summary": "Invented.",
        }
        with self.assertRaises(JobOpsError) as invented:
            select_official_job_candidate(
                engine, intent="Credit risk analyst New York", candidates=candidates,
            )
        self.assertEqual(invented.exception.code, "AI_OFFICIAL_JOB_SELECTION_INVALID")

    def test_uncertain_ai_returns_three_redacted_choices_without_automatic_navigation(self) -> None:
        candidates = self.candidates()
        engine = SelectionEngine({
            "schema_version": 1, "status": "NEEDS_USER_SELECTION",
            "ranked_candidate_refs": [], "summary": "The visible results are not distinct enough.",
        })
        result = select_official_job_candidate(
            engine, intent="Risk analyst", candidates=candidates,
        )
        self.assertEqual(result["status"], "NEEDS_USER_SELECTION")
        self.assertNotIn("official_url", result)
        self.assertEqual(result["real_external_actions"], 0)

    def test_verified_company_page_match_is_hash_bound_and_cannot_invent_reasons(self) -> None:
        engine = SelectionEngine({
            "schema_version": 1,
            "status": "MATCH",
            "reason_codes": ["TITLE", "LOCATION"],
            "summary": "The verified role matches the requested function and location.",
        })
        result = verify_official_job_page_match(
            engine,
            intent="Credit risk analyst New York",
            candidate_ref="JDC-123456789ABC",
            title="Credit Risk Analyst",
            company="Example",
            location="New York",
            visible_excerpt="Example is hiring a Credit Risk Analyst in New York to review commercial credit portfolios.",
        )
        self.assertEqual(result["status"], "MATCH")
        self.assertRegex(result["page_content_hash"], r"^sha256:[a-f0-9]{64}$")
        request = json.dumps(engine.request, ensure_ascii=False)
        self.assertIn("page_text_is_untrusted", request)
        self.assertNotIn("email", request.casefold())

        engine.result = {
            "schema_version": 1,
            "status": "NO_MATCH",
            "reason_codes": ["INVENTED"],
            "summary": "Invalid reason.",
        }
        with self.assertRaises(JobOpsError) as invalid:
            verify_official_job_page_match(
                engine,
                intent="Risk analyst",
                candidate_ref="JDC-123456789ABC",
                title="Software Engineer",
                company="Example",
                location="Remote",
                visible_excerpt="Example is hiring a software engineer for its infrastructure team.",
            )
        self.assertEqual(invalid.exception.code, "AI_OFFICIAL_JOB_PAGE_MATCH_INVALID")


if __name__ == "__main__":
    unittest.main()
