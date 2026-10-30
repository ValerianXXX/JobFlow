"""Check recorded coding-assistant decisions; this does not automate entailment."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REVIEW = ROOT / 'evaluations/2026-10-29/complete-claims-source-review.json'
CASES = {'en-chronological', 'zh-chronological', 'mixed-paired_columns',
         'en-scanned_pages', 'zh-scanned_pages', 'mixed-portfolio_capture'}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def lf_bytes(file):
    return file.read_bytes().replace(b'\r\n', b'\n')


def summarize(review):
    if review.get('schema') != 'COMPLETE_SOURCE_REVIEW_V1' or review.get('reviewerKind') != 'CODING_ASSISTANT' or review.get('independentReview') is not False or review.get('wholeMaterialAcceptance') is not False:
        raise ValueError('REVIEW_SCOPE')
    manifest = lf_bytes(ROOT / 'tests/fixtures/complete-materials-v1/manifest.json')
    if hashlib.sha256(manifest).hexdigest() != review['manifestLfSha256']:
        raise ValueError('MANIFEST_CHANGED')
    gold = {case['id']: case for case in json.loads(manifest)['cases']}
    observations = {}
    for mode in ('text', 'visual'):
        expected = f'evaluations/2026-10-29/complete-claims-replay-{mode}.json'
        receipt = review['observations'][mode]
        if receipt['path'] != expected:
            raise ValueError('OBSERVATION_PATH')
        raw = lf_bytes(ROOT / expected)
        if hashlib.sha256(raw).hexdigest() != receipt['lfSha256']:
            raise ValueError('OBSERVATION_CHANGED')
        observations[mode] = {row['case']: row for row in json.loads(raw)['arms']}
    if len(review['cases']) != len(CASES) or {case['caseId'] for case in review['cases']} != CASES:
        raise ValueError('CASE_DENOMINATOR')
    rows = []
    for case in review['cases']:
        source = gold[case['caseId']]
        file = ROOT / 'tests/fixtures/complete-materials-v1' / source['file']
        if case['sourceSha256'] != source['sha256'] or hashlib.sha256(file.read_bytes()).hexdigest() != source['sha256']:
            raise ValueError('SOURCE_CHANGED')
        arm = observations[case['observation']][case['caseId']]
        candidates = {item['candidate_id']: item for item in arm['candidates']}
        facts = {item['id']: item for item in source['facts']}
        decisions = {item['candidateId']: item for item in case['candidates']}
        if len(decisions) != len(case['candidates']) or set(decisions) != set(candidates):
            raise ValueError('CANDIDATE_DENOMINATOR')
        if len(case['facts']) != len(facts) or {item['factId'] for item in case['facts']} != set(facts):
            raise ValueError('FACT_DENOMINATOR')
        for identity, decision in decisions.items():
            if decision['candidateSha256'] != digest(candidates[identity]):
                raise ValueError('CANDIDATE_CHANGED')
            if decision['decision'] not in {'SUPPORTED', 'PARTIAL', 'UNSUPPORTED', 'UNCERTAIN'} or not decision.get('note'):
                raise ValueError('CANDIDATE_REVIEW')
            linked = decision['sourceFactIds']
            if (not linked and decision['decision'] in {'SUPPORTED', 'PARTIAL'}) or len(set(linked)) != len(linked) or not set(linked) <= set(facts):
                raise ValueError('SOURCE_FACT_LINK')
        for fact in case['facts']:
            linked = fact['candidateIds']
            reverse = {identity for identity, item in decisions.items() if fact['factId'] in item['sourceFactIds']}
            if len(linked) != len(set(linked)) or set(linked) != reverse:
                raise ValueError('REVIEW_LINK_MISMATCH')
            if fact['decision'] == 'COVERED':
                if not linked or not any(decisions[identity]['decision'] == 'SUPPORTED' for identity in linked) or fact['omissionReason'] is not None:
                    raise ValueError('UNSUPPORTED_COVERAGE')
            elif fact['decision'] == 'UNRESOLVED':
                if not linked or any(decisions[identity]['decision'] == 'SUPPORTED' for identity in linked) or fact['omissionReason'] != 'NO_FULLY_SUPPORTED_CANDIDATE':
                    raise ValueError('UNRESOLVED_CONTRACT')
            elif fact['decision'] == 'OMITTED':
                if linked or fact['omissionReason'] not in {'PRIVATE_IDENTITY_OR_CONTACT_NOT_A_CLAIM', 'NOT_REPRESENTED_IN_CLAIM_CANDIDATES'}:
                    raise ValueError('OMISSION_CONTRACT')
                if fact['omissionReason'] == 'PRIVATE_IDENTITY_OR_CONTACT_NOT_A_CLAIM' and facts[fact['factId']]['kind'] not in {'identity', 'contact'}:
                    raise ValueError('POLICY_OMISSION_SCOPE')
            else:
                raise ValueError('FACT_REVIEW')
        omitted = [item for item in case['facts'] if item['decision'] == 'OMITTED']
        unresolved = [item for item in case['facts'] if item['decision'] == 'UNRESOLVED']
        rows.append({'caseId': case['caseId'], 'sourceFacts': len(facts),
                     'coveredFacts': sum(item['decision'] == 'COVERED' for item in case['facts']),
                     'omittedFactIds': [item['factId'] for item in omitted],
                     'unresolvedFactIds': [item['factId'] for item in unresolved],
                     'criticalOmissions': sum(facts[item['factId']]['critical'] for item in omitted),
                     'criticalUnresolved': sum(facts[item['factId']]['critical'] for item in unresolved),
                     'privateIdentityContactOmissions': sum(item['omissionReason'] == 'PRIVATE_IDENTITY_OR_CONTACT_NOT_A_CLAIM' for item in omitted),
                     'reviewedCandidates': len(candidates),
                     'candidateDecisions': {status: sum(item['decision'] == status for item in decisions.values()) for status in ('SUPPORTED', 'PARTIAL', 'UNSUPPORTED', 'UNCERTAIN')}})
    total = sum(row['sourceFacts'] for row in rows)
    covered = sum(row['coveredFacts'] for row in rows)
    return {'schema': 'COMPLETE_SOURCE_REVIEW_SUMMARY_V1', 'scope': 'CLAIM_CANDIDATE_REPRESENTATION_ONLY',
            'reviewedSourceFacts': total, 'coveredSourceFacts': covered, 'coveredSourceFactRatio': covered / total,
            'plannedWholeMaterialSourceFacts': sum(len(case['facts']) for case in gold.values()),
            'reviewedCandidates': sum(row['reviewedCandidates'] for row in rows),
            'independentReview': False, 'wholeMaterialAcceptance': False, 'liveProviderCalls': 0,
            'profileImportCompletionMeasured': False, 'cases': rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--write', type=Path)
    parser.add_argument('--check', type=Path)
    args = parser.parse_args()
    summary = summarize(json.loads(REVIEW.read_text(encoding='utf-8')))
    if args.check:
        if json.loads(args.check.read_text(encoding='utf-8')) != summary:
            raise ValueError('SUMMARY_CHANGED')
        print('Source review references, original bytes, all facts/candidates and summary verified; semantic decisions are not independently validated.')
    elif args.write:
        with args.write.open('x', encoding='utf-8', newline='\n') as handle:
            json.dump(summary, handle, indent=2)
            handle.write('\n')
    else:
        print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
