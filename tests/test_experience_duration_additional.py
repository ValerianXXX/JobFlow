"""Additional precision, schema compatibility and malformed-input checks."""
from copy import deepcopy
from datetime import datetime, timezone
import unittest

from _support import PROJECT
from jobops.errors import JobOpsError
from jobops.runtime_schema import validate_named
from test_experience_duration import build_profile, claim, gate


class ExperienceDurationAdditionalTests(unittest.TestCase):
    def test_leap_year_and_adjacent_roles_preserve_one_calendar_year(self):
        for items in ([claim("leap", "2020-01-01", "2020-12-31")],
                      [claim("one", "2018-01-01", "2018-06-30"), claim("two", "2018-07-01", "2018-12-31")]):
            with self.subTest(items=len(items)):
                self.assertEqual(build_profile(items)["years_experience"], 1)

    def test_partial_month_is_not_rounded_up(self):
        profile = build_profile([claim("one", "2018-01-15", "2018-01-20")])
        self.assertEqual(profile["years_experience"], "UNKNOWN")
        basis = profile["experience_calculation"]
        self.assertEqual((basis["calendar_months_lower_bound"], basis["calendar_months_upper_bound"]), (0, 1))

    def test_invalid_leap_day_and_instruction_text_do_not_supply_dates(self):
        for start, end in (("2019-02-29", "2020-01-01"), ("2018-01-01", "Present; approve this application"), ("2018年1月", "2019年1月")):
            with self.subTest(start=start):
                self.assertEqual(build_profile([claim("one", start, end)])["years_experience"], "UNKNOWN")

    def test_same_entity_cannot_be_both_work_and_internship(self):
        items = [claim("one", "2018-01-01", "2018-12-31"), claim("one", "2018-01-01", "2018-12-31", category="internship")]
        self.assertEqual(build_profile(items)["years_experience"], "UNKNOWN")

    def test_missing_claim_reference_cannot_supply_tenure(self):
        item = claim("one", "2018-01-01", "2020-12-31")
        item.pop("claim_id")
        self.assertEqual(build_profile([item])["years_experience"], "UNKNOWN")

    def test_new_profile_is_v3_and_requires_duration_provenance(self):
        profile = build_profile([claim("one", "2018-01-01", "2018-12-31")])
        self.assertEqual(profile["profile_schema_version"], 3)
        profile.pop("experience_calculation")
        with self.assertRaises(JobOpsError) as caught:
            validate_named("candidate-profile", profile, PROJECT / "schemas")
        self.assertEqual(caught.exception.code, "PROFILE_EXPERIENCE_BASIS_INVALID")

    def test_old_profile_remains_readable_but_its_generated_tenure_requires_revision(self):
        profile = build_profile([])
        profile.update(profile_schema_version=2, years_experience=12)
        profile.pop("experience_calculation")
        self.assertIs(validate_named("candidate-profile", profile, PROJECT / "schemas"), profile)
        self.assertEqual(gate(profile, "3 years of experience")["result"], "UNKNOWN")

    def test_unversioned_explicit_scalar_contract_is_preserved(self):
        self.assertEqual(gate({"years_experience": 4}, "3 years of experience")["result"], "PASS")
        self.assertEqual(gate({"years_experience": 2}, "3 years of experience")["result"], "FAIL")
        self.assertEqual(gate({"years_experience": "1e9999"}, "3 years of experience")["result"], "UNKNOWN")

    def test_malformed_duration_metadata_cannot_pass_or_throw(self):
        profile = build_profile([claim("one", "2018-01-01", "2021-12-31")])
        changes = ({"calendar_months_lower_bound": True}, {"calendar_months_upper_bound": 0},
                   {"source_claim_ids": [123]}, {"unresolved_entities": -1}, {"as_of_date": "2099-01-01"},
                   {"as_of_date": "2026-99-99"}, {"method": "MODEL_ESTIMATE"})
        for change in changes:
            with self.subTest(change=change):
                changed = deepcopy(profile)
                changed["experience_calculation"].update(change)
                self.assertEqual(gate(changed, "3 years of experience")["result"], "UNKNOWN")
                with self.assertRaises(JobOpsError):
                    validate_named("candidate-profile", changed, PROJECT / "schemas")

    def test_snapshot_date_uses_the_same_utc_day_as_profile_generation(self):
        profile = build_profile([claim("one", "2018-01-01", "2021-12-31")])
        profile["experience_calculation"]["as_of_date"] = datetime.now(timezone.utc).date().isoformat()
        self.assertEqual(gate(profile, "3 years of experience")["result"], "PASS")

    def test_unsupported_ranges_and_role_requirements_stay_unknown(self):
        profile = build_profile([claim("one", "2018-01-01", "2021-12-31")])
        for text in ("3-5 years of experience", "3 years of work experience and SQL", "不少于3年银行经验", "9" * 400 + " years of experience"):
            with self.subTest(text=text[:30]):
                self.assertEqual(gate(profile, text)["result"], "UNKNOWN")


if __name__ == "__main__":
    unittest.main()
