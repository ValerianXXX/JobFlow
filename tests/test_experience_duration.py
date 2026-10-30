"""Frozen user-visible duration and eligibility regressions; synthetic facts only."""
from datetime import datetime, timezone
import unittest
from unittest.mock import patch

from _support import PROJECT
from jobops.eligibility import _evaluate_requirement
from jobops.jd_analyzer import Requirement
from jobops.onboarding_center import FIELD_BY_ID, OnboardingCenterService


class FixedClock(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 10, 29, 12, tzinfo=timezone.utc)


def claim(key, start, end, *, category="work", decision="CONFIRMED", deleted=False):
    return {"claim_id": "CLM-" + key, "category": category, "decision": decision,
            "deleted": deleted, "statement": "The applicant performed synthetic documented work.",
            "entity": {"entity_id": "ENT-" + key, "entity_type": category,
                       "organization": "Synthetic Organization", "role": "Analyst",
                       "start_date": start, "end_date": end}}


def build_profile(claims):
    service = object.__new__(OnboardingCenterService)
    service.schemas = PROJECT / "schemas"
    service._base_profile = lambda: {}
    service._claims_for_ui = lambda state: claims
    state = {"updated_at": "2026-10-29T12:00:00Z", "sources": [],
             "answers": {field: {"status": "UNKNOWN", "value": None} for field in FIELD_BY_ID}}
    with patch("jobops.onboarding_center.datetime", FixedClock):
        return service._build_profile(state, "secure-ref:SYNTHETIC_DURATION_001")


def gate(profile, text):
    return _evaluate_requirement(Requirement("REQ-SYNTHETIC", "years", text, "SINGLE", (text,), None), profile)


class ExperienceDurationTests(unittest.TestCase):
    def test_closed_job_does_not_accumulate_time_after_departure(self):
        profile = build_profile([claim("one", "2018-01-01", "2018-12-31")])
        self.assertEqual(profile["years_experience"], 1)

    def test_gaps_are_not_working_time(self):
        profile = build_profile([claim("one", "2018-01-01", "2018-12-31"), claim("two", "2022-01-01", "2022-12-31")])
        self.assertEqual(profile["years_experience"], 2)

    def test_overlapping_jobs_count_calendar_time_once(self):
        profile = build_profile([claim("one", "2018-01-01", "2019-12-31"), claim("two", "2019-01-01", "2020-12-31")])
        self.assertEqual(profile["years_experience"], 3)

    def test_many_claims_for_one_job_do_not_add_years(self):
        item = claim("one", "2018-01-01", "2018-12-31")
        profile = build_profile([dict(item, claim_id=f"CLM-SYNTHETIC-{index}") for index in range(20)])
        self.assertEqual(profile["years_experience"], 1)
        self.assertEqual(profile["experience_calculation"]["work_entities"], 1)

    def test_month_precision_is_a_range_not_an_exact_year(self):
        profile = build_profile([claim("one", "2018-01", "2018-12")])
        self.assertEqual(profile["years_experience"], "UNKNOWN")
        basis = profile["experience_calculation"]
        self.assertEqual((basis["calendar_months_lower_bound"], basis["calendar_months_upper_bound"]), (10, 12))

    def test_year_precision_is_not_a_guessed_duration(self):
        profile = build_profile([claim("one", "2018", "2019")])
        self.assertEqual(profile["years_experience"], "UNKNOWN")
        basis = profile["experience_calculation"]
        self.assertEqual((basis["calendar_months_lower_bound"], basis["calendar_months_upper_bound"]), (0, 24))

    def test_ongoing_role_is_bounded_by_the_profile_snapshot_date(self):
        profile = build_profile([claim("one", "2025-01-01", "Present")])
        self.assertEqual(profile["years_experience"], "UNKNOWN")
        basis = profile["experience_calculation"]
        self.assertEqual(basis["as_of_date"], "2026-10-29")
        self.assertEqual((basis["calendar_months_lower_bound"], basis["calendar_months_upper_bound"]), (21, 22))

    def test_invalid_missing_reversed_and_future_dates_remain_unknown(self):
        for start, end in (("", "2024"), ("2018-99", "2020-01"), ("2025-01", "2024-01"), ("2027-01", "2028-01"), ("2018-01", "2027-01"), ("2018-01", "unknown")):
            with self.subTest(start=start, end=end):
                profile = build_profile([claim("one", start, end)])
                self.assertEqual(profile["years_experience"], "UNKNOWN")
                self.assertEqual(profile["experience_calculation"]["unresolved_entities"], 1)

    def test_internship_is_not_silently_counted_as_generic_work_tenure(self):
        profile = build_profile([claim("one", "2018-01-01", "2018-12-31", category="internship")])
        self.assertEqual(profile["years_experience"], "UNKNOWN")
        self.assertEqual(profile["experience_calculation"]["internship_entities_excluded"], 1)

    def test_unconfirmed_deleted_and_nonwork_claims_are_excluded(self):
        profile = build_profile([claim("one", "2018-01-01", "2025-12-31", decision="PENDING"),
                                 claim("two", "2018-01-01", "2025-12-31", deleted=True),
                                 claim("three", "2018-01-01", "2025-12-31", category="project")])
        self.assertEqual(profile["years_experience"], "UNKNOWN")
        self.assertEqual(profile["experience_calculation"]["work_entities"], 0)

    def test_conflicting_dates_for_one_entity_are_not_added_as_two_jobs(self):
        one = claim("one", "2018-01-01", "2018-12-31")
        two = claim("two", "2021-01-01", "2021-12-31")
        two["entity"]["entity_id"] = one["entity"]["entity_id"]
        profile = build_profile([one, two])
        self.assertEqual(profile["years_experience"], "UNKNOWN")
        self.assertEqual(profile["experience_calculation"]["unresolved_entities"], 1)

    def test_valid_dated_work_is_preserved_when_another_role_needs_confirmation(self):
        profile = build_profile([claim("one", "2018-01-01", "2019-12-31"), claim("two", "2021", "UNKNOWN")])
        self.assertEqual(profile["years_experience"], "UNKNOWN")
        basis = profile["experience_calculation"]
        self.assertEqual(basis["calendar_months_lower_bound"], 24)
        self.assertIsNone(basis["calendar_months_upper_bound"])
        self.assertEqual(basis["coverage"], "DOCUMENTED_CLAIMS_ONLY")
        self.assertEqual(set(basis["source_claim_ids"]), {"CLM-one", "CLM-two"})

    def test_dated_lower_bound_can_satisfy_only_a_generic_years_requirement(self):
        profile = build_profile([claim("one", "2018-01", "2021-12")])
        self.assertEqual(gate(profile, "At least 3 years of work experience")["result"], "PASS")
        self.assertEqual(gate(profile, "至少3年工作经验")["result"], "PASS")
        for text in ("3 years of Python", "3 years of full-time experience", "3 years of management experience", "3年银行工作经验"):
            with self.subTest(text=text):
                self.assertEqual(gate(profile, text)["result"], "UNKNOWN")

    def test_insufficient_documented_history_does_not_assert_complete_career_failure(self):
        profile = build_profile([claim("one", "2018-01-01", "2018-12-31")])
        self.assertEqual(gate(profile, "At least 3 years of work experience")["result"], "UNKNOWN")

    def test_partial_date_boundary_does_not_pass_from_the_upper_bound(self):
        profile = build_profile([claim("one", "2018-01", "2020-12")])
        self.assertEqual(gate(profile, "3 years of experience")["result"], "UNKNOWN")

    def test_legacy_generated_profile_without_duration_provenance_requires_review(self):
        self.assertEqual(gate({"profile_schema_version": 2, "years_experience": 12}, "3 years of experience")["result"], "UNKNOWN")

    def test_invalid_legacy_numeric_values_are_unknown_instead_of_crashing_or_passing(self):
        for value in (True, float("inf"), float("nan"), -1, "several", {}, []):
            with self.subTest(value=value):
                self.assertEqual(gate({"years_experience": value}, "3 years of experience")["result"], "UNKNOWN")

    def test_fractional_generic_requirement_does_not_read_only_the_final_digit(self):
        profile = build_profile([claim("one", "2018-01-01", "2021-12-31")])
        result = gate(profile, "3.5 years of work experience")
        self.assertEqual(result["result"], "PASS")
        self.assertEqual(result["components"]["required"], 3.5)


if __name__ == "__main__":
    unittest.main()
