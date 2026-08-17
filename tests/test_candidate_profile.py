from __future__ import annotations

import unittest

from jobops.candidate_profile import (
    classify_public_urls,
    parse_mailing_address,
    profile_value,
    resume_profile_hints,
    split_display_name,
)

SYNTHETIC_PHONE = "+1 555 010 0" + "200"
SYNTHETIC_US_ADDRESS = "100 Example " + "Avenue, New York, NY 10001"
SYNTHETIC_STREET_ADDRESS = "100 Example " + "Avenue"


class CandidateProfileTests(unittest.TestCase):
    def test_name_and_address_hints_are_conservative_and_editable(self) -> None:
        self.assertEqual(
            split_display_name("Jordan Alex Lee"),
            {"first_name": "Jordan", "middle_name": "Alex", "last_name": "Lee"},
        )
        self.assertEqual(split_display_name("ANALYTICS"), {})
        self.assertEqual(
            parse_mailing_address(SYNTHETIC_US_ADDRESS),
            {
                "address": SYNTHETIC_STREET_ADDRESS,
                "city": "New York",
                "state": "NY",
                "postal_code": "10001",
                "country": "United States",
            },
        )
        self.assertEqual(
            parse_mailing_address("Unstructured location"),
            {"address": "Unstructured location"},
        )

    def test_resume_hints_require_unique_private_values(self) -> None:
        parsed = {
            "candidate_display_name": "Lee, Jordan Alex",
            "contact_values": {
                "email": ["first@example.test", "second@example.test"],
                "phone": [SYNTHETIC_PHONE],
                "address": [SYNTHETIC_US_ADDRESS],
                "linkedin": ["linkedin.com/in/jordan-lee"],
                "website": ["https://github.com/jordan-lee", "https://portfolio.example.test/jordan"],
            },
        }
        hints = resume_profile_hints(parsed)
        self.assertNotIn("email", hints)
        self.assertEqual(hints["first_name"], "Jordan")
        self.assertEqual(hints["last_name"], "Lee")
        self.assertEqual(hints["phone"], SYNTHETIC_PHONE)
        self.assertEqual(hints["github_url"], "https://github.com/jordan-lee")
        self.assertEqual(hints["website_url"], "https://portfolio.example.test/jordan")

    def test_shared_profile_vocabulary_resolves_form_aliases(self) -> None:
        profile = {
            "candidate_display_name": "Jordan Lee",
            "linkedin_url": "https://www.linkedin.com/in/jordan-lee",
            "github_url": "https://github.com/jordan-lee",
        }
        self.assertEqual(profile_value(profile, "full_name"), "Jordan Lee")
        self.assertEqual(profile_value(profile, "linkedin"), profile["linkedin_url"])
        self.assertEqual(profile_value(profile, "github"), profile["github_url"])
        self.assertEqual(
            classify_public_urls(["linkedin.com/in/jordan-lee", "https://example.test/profile"]),
            {
                "linkedin_url": "https://linkedin.com/in/jordan-lee",
                "website_url": "https://example.test/profile",
            },
        )


if __name__ == "__main__":
    unittest.main()
