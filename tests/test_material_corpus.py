from __future__ import annotations
from copy import deepcopy
import importlib.util
import unittest
import _support
from _support import PROJECT

spec = importlib.util.spec_from_file_location("material_corpus", PROJECT / "scripts/evaluate-material-corpus.py")
corpus = importlib.util.module_from_spec(spec)
spec.loader.exec_module(corpus)


class MaterialCorpusTests(unittest.TestCase):
    def test_frozen_population_and_split_do_not_depend_on_observed_results(self):
        cases = corpus.load_cases()
        self.assertEqual(len(cases), 36)
        self.assertEqual(len({c["id"] for c in cases}), 36)
        for language in ("en", "zh", "mixed"):
            rows = [c for c in cases if c["language"] == language]
            self.assertEqual(len(rows), 12)
            self.assertEqual(sum(c["split"] == "held_out" for c in rows), 4)
        self.assertEqual(len(corpus.load_cases("development")), 24)

    def test_preserved_numbers_alone_do_not_pass_fact_or_entity_context(self):
        case = corpus.load_cases("development")[0]
        numbers = " ".join(f["number"] for e in case["entities"] for f in e["facts"])
        measured = corpus.measure(case, numbers, {})
        self.assertEqual(measured["verbatim_facts_retained"], 0)
        self.assertEqual(measured["entity_contexts_retained"], 0)

    def test_interleaved_entities_fail_despite_preserved_whole_facts(self):
        case = corpus.load_cases("development")[0]
        interleaved = "\n".join(line for pair in zip(*(e["lines"] for e in case["entities"])) for line in pair)
        measured = corpus.measure(case, interleaved, {})
        self.assertEqual(measured["verbatim_facts_retained"], 4)
        self.assertEqual(measured["entity_contexts_retained"], 0)

    def test_changed_ownership_or_numbers_remain_missing_not_repaired(self):
        case = next(c for c in corpus.load_cases("development") if c["id"] == "en-single")
        text = "\n".join(line for e in case["entities"] for line in e["lines"])
        self.assertEqual(corpus.measure(case, text, {})["verbatim_facts_retained"], 4)
        changed = text.replace("Team reviewed", "Applicant reviewed").replace("prepared 7 reports", "prepared 9 reports")
        self.assertEqual(corpus.measure(case, changed, {})["verbatim_facts_retained"], 2)
        self.assertEqual(corpus.measure(case, "", {})["entity_contexts_retained"], 0)


if __name__ == "__main__":
    unittest.main()
