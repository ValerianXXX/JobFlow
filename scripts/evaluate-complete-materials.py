"""Frozen complete synthetic corpus, real local parsers, no model requests.

Raw verbatim retention is a parser diagnostic, not approved-Claim accuracy.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from contextlib import contextmanager
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from jobops.errors import JobOpsError
from jobops.onboarding_center import OnboardingCenterService
from jobops.source_quality import document_text_preflight
from jobops.util import iso_utc

spec = importlib.util.spec_from_file_location('short_probe_helpers', ROOT / 'scripts/evaluate-material-corpus.py')
helpers = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helpers)
CORPUS = ROOT / 'tests/fixtures/complete-materials-v1'
MANIFEST_LF_SHA256 = '11d22cb621ed917f1f014804bde9ff911d39816cd5087e54fe19f1bb2bb170ce'


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def load_manifest():
    raw = (CORPUS / 'manifest.json').read_bytes().replace(b'\r\n', b'\n')
    if digest(raw) != MANIFEST_LF_SHA256:
        raise ValueError('Frozen complete-material manifest changed')
    value = json.loads(raw)
    if value.get('synthetic_only') is not True or len(value['cases']) != 36:
        raise ValueError('Unexpected complete-material population')
    for case in value['cases'] + value['challenges']:
        name = case['file']
        if Path(name).name != name or Path(name).suffix not in {'.pdf', '.docx', '.png'}:
            raise ValueError('Fixture path outside frozen directory')
        if digest((CORPUS / name).read_bytes()) != case['sha256']:
            raise ValueError('Frozen complete material changed')
    return value


def normalized(text):
    return re.sub(r'\s+', '', text).casefold()


def measure(case, text):
    stream = normalized(text)
    consumed = defaultdict(int)
    missing = set(case.get('unavailable_fact_ids', []))
    records, occurrences = [], defaultdict(list)
    for fact in case['facts']:
        needle = normalized(fact['text'])
        if needle not in occurrences:
            occurrences[needle] = [match.start() for match in re.finditer(re.escape(needle), stream)]
        ordinal = consumed[needle]
        consumed[needle] += 1
        locations = occurrences[needle]
        found = ordinal < len(locations)
        records.append({'fact_id': fact['id'], 'critical': fact['critical'],
                        'available_in_input': fact['id'] not in missing,
                        'verbatim_retained': found,
                        'normalized_offset': locations[ordinal] if found else None})
    # Duplicate identical statements need distinct physical occurrences. They
    # still do not establish semantic association with the right employment.
    available = [row for row in records if row['available_in_input']]
    return {'expected_available_facts': len(available),
            'verbatim_retained': sum(row['verbatim_retained'] for row in available),
            'expected_critical_facts': sum(row['critical'] for row in available),
            'verbatim_critical_retained': sum(row['critical'] and row['verbatim_retained'] for row in available),
            'absent_source_facts_reappeared': [r['fact_id'] for r in records if not r['available_in_input'] and r['verbatim_retained']],
            'facts': records,
            'semantic_claim_recall': None, 'misattributions': None, 'unflagged_critical_claim_errors': None}


def run_case(service, case, mode, transcript_dir):
    started = time.monotonic()
    data = (CORPUS / case['file']).read_bytes()
    text, meta = '', {}
    result = {'case': case['id'], 'mode': mode, 'status': 'NOT_RUN'}
    with helpers.extraction_mode(mode):
        try:
            text, _, meta = service._extract_text(data, Path(case['file']).suffix, 'resume')
            quality = document_text_preflight(text, extension=Path(case['file']).suffix, page_count=meta.get('document_page_count'))
            result.update(status='EXTRACTED' if quality['status'] != 'FAIL' else 'PREFLIGHT_BLOCKED', quality=quality)
        except JobOpsError as error:
            result.update(status='BLOCKED', code=error.code)
        except Exception as error:
            result.update(status='ERROR', error_type=type(error).__name__)
    transcript = transcript_dir / (case['id'] + '--' + mode + '.txt')
    transcript.write_text(text, encoding='utf-8', newline='\n')
    result.update(elapsed_ms=round((time.monotonic()-started)*1000),
                  transcript_sha256=digest(text.encode()),
                  source_review_required=meta.get('visual_verification_required'),
                  document_page_count=meta.get('document_page_count'),
                  parser_regions=len(meta.get('visual_regions') or []),
                  assessment=measure(case, text))
    if case.get('kind'):
        result['challenge'] = {'kind': case['kind'], 'required_behavior': case['required_behavior'],
                               'model_or_approval_behavior': 'NOT_EXERCISED_BY_PARSER_COMPARISON',
                               'incomplete_source_accepted_by_text_stage': bool(case['kind'] in {'blank_middle', 'missing_middle'} and result['status'] == 'EXTRACTED')}
    return result


def evaluate(split, include_challenges, output):
    manifest = load_manifest()
    cases = [c for c in manifest['cases'] if c['split'] == split]
    if include_challenges:
        if split != 'development':
            raise ValueError('Challenges derive from development materials only')
        cases += manifest['challenges']
    if output.exists() or output.with_suffix('.jsonl').exists():
        raise ValueError('Refusing to overwrite observations')
    output.parent.mkdir(parents=True, exist_ok=True)
    transcripts = output.parent / (output.stem + '-transcripts')
    transcripts.mkdir(exist_ok=False)
    service = object.__new__(OnboardingCenterService)
    service.onboarding = helpers.PathStaging()
    rows = []
    with output.with_suffix('.jsonl').open('x', encoding='utf-8', newline='\n') as checkpoint:
        for case in cases:
            row = {key: case[key] for key in ('id', 'language', 'family', 'split', 'sha256')}
            for mode in ('existing_text', 'pdf_regions'):
                row[mode] = run_case(service, case, mode, transcripts)
                checkpoint.write(json.dumps(row[mode], ensure_ascii=False) + '\n')
                checkpoint.flush()
            rows.append(row)
    summary = {}
    for mode in ('existing_text', 'pdf_regions'):
        for population in ('complete', 'challenge'):
            selected = [row[mode] for row in rows if ('challenge' in row[mode]) == (population == 'challenge')]
            summary[mode + '/' + population] = {
                'cases': len(selected), 'extracted': sum(r['status'] == 'EXTRACTED' for r in selected),
                'expected_available_facts': sum(r['assessment']['expected_available_facts'] for r in selected),
                'verbatim_retained': sum(r['assessment']['verbatim_retained'] for r in selected),
                'critical_retained': sum(r['assessment']['verbatim_critical_retained'] for r in selected),
                'expected_critical_facts': sum(r['assessment']['expected_critical_facts'] for r in selected),
                'elapsed_ms': sum(r['elapsed_ms'] for r in selected)}
    receipt = {'schema_version': 1, 'observed_at_utc': iso_utc(), 'synthetic_only': True,
               'base_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
               'working_tree_observation': True, 'manifest_lf_sha256': MANIFEST_LF_SHA256,
               'source_lf_sha256': {name: digest((ROOT / name).read_bytes().replace(b'\r\n', b'\n')) for name in ('scripts/evaluate-complete-materials.py', 'src/jobops/onboarding_center.py', 'src/jobops/_pdf_region_worker.py', 'src/jobops/local_document_parser.py', 'src/jobops/document_qa.py', 'src/jobops/document_coverage.py', 'src/jobops/source_quality.py', '.agents/skills/job-application-operator/scripts/extract-pdf-text.py')},
               'split': split, 'model_calls': 0, 'paid_model_cost_usd': 0,
               'human_labor_cost': None, 'whole_product_acceptance': False,
               'limitations': ['Verbatim parser retention is not semantic Claim correctness.', 'Occurrence allocation does not establish correct organization, date or ownership attribution.', 'All facts are synthetic and coding-assistant authored; no independent realism validation.', 'No model, source approval, Claim approval, output document or external business workflow is exercised.', 'Image-only inputs intentionally need separately consented OCR/vision.'],
               'summary': summary, 'cases': rows}
    with output.open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(receipt, stream, indent=2, ensure_ascii=False)
        stream.write('\n')
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--split', choices=('development', 'held_out'), default='development')
    parser.add_argument('--include-challenges', action='store_true')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    receipt = evaluate(args.split, args.include_challenges, args.output)
    print(json.dumps(receipt['summary'], indent=2))


if __name__ == '__main__':
    main()
