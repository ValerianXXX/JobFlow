"""Wording origins and review decisions must describe the current user content."""
from copy import deepcopy
import unittest

from _support import project_temp
import test_onboarding_center as fixtures
from jobops.onboarding_center import OnboardingCenterService


class ClaimWordingReviewTests(unittest.TestCase):
    def service(self, root):
        return fixtures.OnboardingCenterTests().make_service(root)

    def confirm(self, service):
        claims = service.bootstrap()['claims']
        service.save_review({'profile_review': 'CONFIRMED',
            'claim_decisions': {item['claim_id']: 'CONFIRMED' for item in claims if not item['deleted']}})
        return claims

    def edit(self, item, statement=None, deleted=False):
        return {'statement': statement or item['statement'], 'category': item['category'], 'deleted': deleted}

    def test_edit_without_new_confirmation_resets_only_affected_review(self):
        with project_temp() as root:
            service, onboarding, _, _, _ = self.service(root)
            claims = self.confirm(service)
            original = onboarding.read_bytes(service._latest_ref('claim_candidates'))
            service.save_review({'claim_edits': {claims[0]['claim_id']: self.edit(claims[0],
                'The applicant revised the synthetic project description for another review.')}})
            after = service.bootstrap()
            self.assertEqual(after['claims'][0]['decision'], 'PENDING')
            self.assertEqual(after['claims'][1]['decision'], 'CONFIRMED')
            self.assertEqual(after['profile_review'], 'PENDING')
            self.assertEqual(onboarding.read_bytes(service._latest_ref('claim_candidates')), original)
            self.assertEqual(after['claims'][0]['wording_provenance']['origin'], 'USER_EDITED')
            self.assertFalse(after['claims'][0]['wording_provenance']['matches_ai_candidate'])

    def test_explicit_confirmation_of_direct_edit_is_bound_to_that_wording(self):
        with project_temp() as root:
            service, _, _, _, _ = self.service(root)
            item = self.confirm(service)[0]
            text = 'The applicant explicitly confirms this revised synthetic project description.'
            service.save_review({'claim_edits': {item['claim_id']: self.edit(item, text)},
                'claim_decisions': {item['claim_id']: 'CONFIRMED'}, 'profile_review': 'CONFIRMED'})
            current = service.bootstrap()['claims'][0]
            self.assertEqual(current['statement'], text)
            self.assertEqual(current['decision'], 'CONFIRMED')
            self.assertEqual(current['wording_provenance']['origin'], 'USER_EDITED')
            self.assertFalse(current['wording_provenance']['matches_ai_candidate'])
            self.assertFalse(service.bootstrap()['external_claim_approval']['current'])

    def test_noop_save_keeps_confirmed_wording_and_profile(self):
        with project_temp() as root:
            service, _, _, _, _ = self.service(root)
            item = self.confirm(service)[0]
            before = deepcopy(service.bootstrap()['claims'][0])
            service.save_review({'claim_edits': {item['claim_id']: self.edit(item)}})
            after = service.bootstrap()
            self.assertEqual(after['claims'][0], before)
            self.assertEqual(after['profile_review'], 'CONFIRMED')

    def test_restore_without_new_confirmation_does_not_keep_rejection_or_old_approval(self):
        with project_temp() as root:
            service, _, _, _, _ = self.service(root)
            item = self.confirm(service)[0]
            service.save_review({'claim_edits': {item['claim_id']: self.edit(item, deleted=True)}})
            self.assertEqual(service.bootstrap()['claims'][0]['decision'], 'REJECTED')
            service.save_review({'claim_edits': {item['claim_id']: self.edit(item)}})
            after = service.bootstrap()
            self.assertEqual(after['claims'][0]['decision'], 'PENDING')
            self.assertFalse(after['claims'][0]['deleted'])
            self.assertEqual(after['profile_review'], 'PENDING')

    def test_import_choice_records_original_candidate_separately_from_user_edit(self):
        for edited in [False, True]:
            with self.subTest(edited=edited), project_temp() as root:
                service, _, _, _, _ = self.service(root)
                preview = service.preview_source('project_case', '.txt',
                    b'I designed a synthetic workflow that connected research, approvals,\n'
                    b'implementation, and a 4,000-record evaluation set to improve review accuracy by 20%.\n')
                candidate = preview['candidates'][0]
                statement = candidate['statement'] + (' The applicant added a synthetic clarification.' if edited else '')
                service.commit_source(preview['source_id'], [{'candidate_id': candidate['candidate_id'],
                    'selected': True, 'statement': statement, 'category': candidate['category']}])
                claim = next(item for item in service.bootstrap()['claims'] if item.get('source_id') == preview['source_id'])
                origin = claim['wording_provenance']
                self.assertEqual(origin['origin'], 'USER_EDITED' if edited else 'AI_CANDIDATE')
                self.assertEqual(origin['matches_ai_candidate'], not edited)
                self.assertTrue(origin['candidate_sha256'].startswith('sha256:'))
                self.assertEqual(origin['candidate_sha256'] == origin['wording_sha256'], not edited)
                self.assertEqual(claim['decision'], 'PENDING')

    def test_merge_and_split_preserve_parent_snapshots_without_claiming_new_model_work(self):
        with project_temp() as root:
            service, onboarding, _, _, _ = self.service(root)
            claims = service.bootstrap()['claims']
            original = onboarding.read_bytes(service._latest_ref('claim_candidates'))
            merged = service.transform_claims({'action': 'MERGE', 'claim_ids': [c['claim_id'] for c in claims],
                'category': 'project', 'statement': 'The applicant combined two synthetic statements for explicit review.'})
            item = next(c for c in service.bootstrap()['claims'] if c['claim_id'] == merged['created_claim_ids'][0])
            origin = item['wording_provenance']
            self.assertEqual(origin['origin'], 'USER_MERGED')
            self.assertFalse(origin['matches_ai_candidate'])
            self.assertEqual([p['claim_id'] for p in origin['parents']], [c['claim_id'] for c in claims])
            self.assertTrue(all(p['wording_sha256'].startswith('sha256:') for p in origin['parents']))
            result = service.transform_claims({'action': 'SPLIT', 'claim_ids': merged['created_claim_ids'],
                'category': 'project', 'statements': ['The applicant split the first synthetic statement for review.',
                    'The applicant split the second synthetic statement for review.']})
            children = [c for c in service.bootstrap()['claims'] if c['claim_id'] in result['created_claim_ids']]
            self.assertEqual(len(children), 2)
            for child in children:
                self.assertEqual(child['wording_provenance']['origin'], 'USER_SPLIT')
                self.assertEqual(child['wording_provenance']['parents'][0]['wording_sha256'], origin['wording_sha256'])
                self.assertFalse(child['wording_provenance']['matches_ai_candidate'])
                self.assertEqual(child['decision'], 'PENDING')
            self.assertEqual(onboarding.read_bytes(service._latest_ref('claim_candidates')), original)

    def test_changed_ancestor_resets_descendants_even_if_stale_confirmations_are_submitted(self):
        with project_temp() as root:
            service, _, _, _, _ = self.service(root)
            claims = service.bootstrap()['claims']
            merged = service.transform_claims({'action': 'MERGE', 'claim_ids': [c['claim_id'] for c in claims],
                'category': 'project', 'statement': 'The applicant combined the original synthetic statements for review.'})
            split = service.transform_claims({'action': 'SPLIT', 'claim_ids': merged['created_claim_ids'],
                'category': 'project', 'statements': ['The first synthetic statement depends on the original project facts.',
                    'The second synthetic statement depends on the original project facts.']})
            self.confirm(service)
            children = set(split['created_claim_ids'])
            before = {c['claim_id']: c['statement'] for c in service.bootstrap()['claims'] if c['claim_id'] in children}
            service.save_review({'claim_edits': {claims[0]['claim_id']: self.edit(claims[0],
                'The applicant corrected an original synthetic project fact during source review.', deleted=True)},
                'claim_decisions': {identifier: 'CONFIRMED' for identifier in children}, 'profile_review': 'CONFIRMED'})
            after = [c for c in service.bootstrap()['claims'] if c['claim_id'] in children]
            self.assertEqual({c['claim_id']: c['statement'] for c in after}, before)
            self.assertTrue(all(c['decision'] == 'PENDING' and c['source_review_required'] for c in after))
            self.assertEqual(service.bootstrap()['profile_review'], 'PENDING')
            # An explicit subsequent review may confirm one child; it cannot confirm its sibling.
            identifier = split['created_claim_ids'][0]
            service.save_review({'claim_decisions': {identifier: 'CONFIRMED'}})
            after = {c['claim_id']: c for c in service.bootstrap()['claims'] if c['claim_id'] in children}
            self.assertEqual(after[identifier]['decision'], 'CONFIRMED')
            self.assertFalse(after[identifier]['source_review_required'])
            self.assertEqual(after[split['created_claim_ids'][1]]['decision'], 'PENDING')

    def test_legacy_derived_wording_does_not_become_an_ai_validated_candidate(self):
        with project_temp() as root:
            service, _, _, _, _ = self.service(root)
            claims = service.bootstrap()['claims']
            created = service.transform_claims({'action': 'MERGE', 'claim_ids': [c['claim_id'] for c in claims],
                'category': 'project', 'statement': 'A legacy synthetic merge remains a human wording decision.'})
            reference, state = service.ensure_state()
            state['material_claims'][0].pop('wording_provenance', None)
            service._save_state(reference, state)
            item = next(c for c in service.bootstrap()['claims'] if c['claim_id'] == created['created_claim_ids'][0])
            self.assertEqual(item['wording_provenance']['origin'], 'USER_MERGED')
            self.assertFalse(item['wording_provenance']['matches_ai_candidate'])
            self.assertEqual(item['wording_provenance']['parents'], [])
            self.assertNotEqual(item['lifecycle_status'], 'AI_FILTERED_REQUIRES_CONFIRMATION')

    def test_hash_mismatched_origin_is_visible_as_unknown_after_reload(self):
        with project_temp() as root:
            service, onboarding, _, _, _ = self.service(root)
            claims = service.bootstrap()['claims']
            service.transform_claims({'action': 'MERGE', 'claim_ids': [c['claim_id'] for c in claims],
                'category': 'project', 'statement': 'The original synthetic merge requires a fresh review.'})
            reference, state = service.ensure_state()
            state['material_claims'][0]['statement'] = 'A changed synthetic merge cannot inherit the old wording receipt.'
            service._save_state(reference, state)
            restored = OnboardingCenterService(service.project, service.database, onboarding, ai_engine=service.ai_engine)
            item = next(c for c in restored.bootstrap()['claims'] if not c['deleted'])
            self.assertEqual(item['wording_provenance']['origin'], 'UNVERIFIED')
            self.assertFalse(item['wording_provenance']['matches_ai_candidate'])


if __name__ == '__main__':
    unittest.main()
