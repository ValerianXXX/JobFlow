import unittest

from jobops.candidate_coverage import source_reference_coverage


class CandidateCoverageTests(unittest.TestCase):
    def candidate(self, start, end, entity=None):
        value = {"provenance": {"line_start": start, "line_end": end}}
        if entity is not None:
            value["entity"] = entity
        return value

    def test_uncited_constraint_remains_visible(self):
        result = source_reference_coverage("Remote only.\nPrepared two reports.\n", [self.candidate(2, 2)])
        self.assertEqual(result["cited_source_lines"], 1)
        self.assertEqual(result["uncited_line_ranges"], [{"line_start": 1, "line_end": 1}])
        self.assertIsNone(result["semantic_recall"])
        self.assertFalse(result["fact_completeness_verified"])

    def test_whole_document_citation_cannot_prove_fact_completeness(self):
        result = source_reference_coverage("Team outcome.\nApplicant contribution.\n", [self.candidate(1, 2)])
        self.assertEqual(result["uncited_source_lines"], 0)
        self.assertFalse(result["fact_completeness_verified"])

    def test_empty_candidates_keep_all_content_lines(self):
        result = source_reference_coverage("Header\n\nRemote only.\nUnknown authorization.\n", [])
        self.assertEqual(result["nonempty_source_lines"], 3)
        self.assertEqual(result["uncited_source_lines"], 3)
        self.assertEqual(result["uncited_line_ranges"], [{"line_start": 1, "line_end": 1}, {"line_start": 3, "line_end": 4}])

    def test_union_counts_duplicates_and_entity_context_once(self):
        result = source_reference_coverage("Employer\nRole\nOutcome\n", [
            self.candidate(2, 3, {"line_start": 1, "line_end": 2}), self.candidate(2, 2)])
        self.assertEqual(result["cited_source_lines"], 3)
        self.assertEqual(result["invalid_reference_count"], 0)

    def test_malformed_and_outside_references_do_not_cover_lines(self):
        for candidate in (self.candidate(True, 1), self.candidate(0, 1), self.candidate(1, 3),
                          self.candidate(2, 1), self.candidate("1", "2"), {}, None):
            with self.subTest(candidate=candidate):
                result = source_reference_coverage("First\nSecond\n", [candidate])
                self.assertEqual(result["cited_source_lines"], 0)
                self.assertEqual(result["invalid_reference_count"], 1)

    def test_segmented_input_withholds_coordinate_claim(self):
        result = source_reference_coverage("A long line", [self.candidate(1, 1)], segmented_lines=1)
        self.assertEqual(result["status"], "COORDINATES_UNAVAILABLE")
        self.assertNotIn("cited_source_lines", result)
        self.assertFalse(result["fact_completeness_verified"])

    def test_visible_ranges_are_bounded_without_losing_total(self):
        text = "\n".join("Line" for _ in range(100))
        result = source_reference_coverage(text, [self.candidate(i, i) for i in range(2, 101, 2)])
        self.assertEqual(result["uncited_source_lines"], 50)
        self.assertEqual(len(result["uncited_line_ranges"]), 40)
        self.assertTrue(result["range_list_truncated"])

    def test_result_does_not_copy_source_or_candidate_content(self):
        marker = "SYNTHETIC-PRIVATE-CONTENT"
        candidate = {**self.candidate(1, 1), "statement": marker, "source_path": marker}
        self.assertNotIn(marker, str(source_reference_coverage(marker, [candidate])))


if __name__ == "__main__":
    unittest.main()
