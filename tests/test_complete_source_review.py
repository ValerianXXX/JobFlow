from copy import deepcopy
import importlib.util
import json
import unittest

from _support import PROJECT

spec = importlib.util.spec_from_file_location('complete_source_review', PROJECT / 'scripts/check-complete-source-review.py')
review = importlib.util.module_from_spec(spec)
spec.loader.exec_module(review)


class CompleteSourceReviewTests(unittest.TestCase):
    def setUp(self):
        self.value = json.loads(review.REVIEW.read_text(encoding='utf-8'))

    def test_all_observed_candidates_and_source_facts_remain_in_scope(self):
        result = review.summarize(self.value)
        self.assertEqual(result['reviewedSourceFacts'], 282)
        self.assertEqual(result['reviewedCandidates'], 197)
        self.assertEqual(result['plannedWholeMaterialSourceFacts'], 1716)
        self.assertFalse(result['wholeMaterialAcceptance'])
        self.assertFalse(result['independentReview'])

    def test_dropping_a_missing_fact_or_candidate_cannot_inflate_recall(self):
        for key in ('facts', 'candidates'):
            changed = deepcopy(self.value)
            changed['cases'][0][key].pop()
            with self.assertRaises(ValueError):
                review.summarize(changed)

    def test_changed_candidate_and_observation_cannot_reuse_review(self):
        for mutate in (
            lambda value: value['cases'][0]['candidates'][0].update(candidateSha256='0' * 64),
            lambda value: value['observations']['text'].update(path='../private'),
            lambda value: value['observations']['text'].update(lfSha256='0' * 64)):
            changed = deepcopy(self.value)
            mutate(changed)
            with self.assertRaises(ValueError):
                review.summarize(changed)

    def test_constraints_cannot_be_hidden_as_contact_policy(self):
        self.value['cases'][0]['facts'][2]['omissionReason'] = 'PRIVATE_IDENTITY_OR_CONTACT_NOT_A_CLAIM'
        with self.assertRaisesRegex(ValueError, 'POLICY_OMISSION_SCOPE'):
            review.summarize(self.value)

    def test_linked_facts_do_not_bypass_candidate_or_reviewer_status(self):
        self.value['cases'][0]['candidates'][0]['decision'] = 'UNCERTAIN'
        with self.assertRaisesRegex(ValueError, 'UNSUPPORTED_COVERAGE'):
            review.summarize(self.value)
        self.value['independentReview'] = True
        with self.assertRaisesRegex(ValueError, 'REVIEW_SCOPE'):
            review.summarize(self.value)

    def test_partial_or_uncertain_facts_remain_in_denominator_without_credit(self):
        case = self.value['cases'][0]
        decision = case['candidates'][0]
        decision['decision'] = 'PARTIAL'
        linked = set(decision['sourceFactIds'])
        for fact in case['facts']:
            if fact['factId'] in linked:
                fact.update(decision='UNRESOLVED', omissionReason='NO_FULLY_SUPPORTED_CANDIDATE')
        result = review.summarize(self.value)
        self.assertEqual(result['reviewedSourceFacts'], 282)
        self.assertEqual(result['coveredSourceFacts'], 241 - len(linked))
        self.assertEqual(set(result['cases'][0]['unresolvedFactIds']), linked)

    def test_unsupported_extra_assertions_can_be_recorded_without_fabricating_evidence(self):
        case = self.value['cases'][0]
        decision = case['candidates'][0]
        linked = set(decision['sourceFactIds'])
        decision.update(decision='UNSUPPORTED', sourceFactIds=[])
        for fact in case['facts']:
            if fact['factId'] in linked:
                fact.update(decision='OMITTED', candidateIds=[], omissionReason='NOT_REPRESENTED_IN_CLAIM_CANDIDATES')
        result = review.summarize(self.value)
        self.assertEqual(result['reviewedCandidates'], 197)
        self.assertEqual(result['coveredSourceFacts'], 241 - len(linked))
        self.assertEqual(result['cases'][0]['candidateDecisions']['UNSUPPORTED'], 1)


if __name__ == '__main__':
    unittest.main()
