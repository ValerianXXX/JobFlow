"""Six frozen complete synthetic materials through the actual Hermes preview.

At most 18 adapter requests, including failed attempts and repairs. Exact full
statements are a conservative diagnostic; unmatched paraphrases, missing scope
and extra Claims require explicit source-based adjudication, never an LLM score.
This runner accepts no applicant path and creates no approved Claim/application.
"""
from __future__ import annotations

import argparse
from difflib import SequenceMatcher
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import sys
import unicodedata

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from jobops.util import iso_utc


def script(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


complete = script('complete_parser_inputs', 'evaluate-complete-materials.py')
layout = script('complete_preview_harness', 'evaluate-layout-claims.py')
# Freeze before observing any complete-material model output. These are six
# development files, not a new holdout or a complete 36-material acceptance run.
PILOT = (
    ('en-chronological', 'existing_text'),
    ('zh-chronological', 'existing_text'),
    ('mixed-paired_columns', 'pdf_regions'),
    ('en-scanned_pages', 'visual'),
    ('zh-scanned_pages', 'visual'),
    ('mixed-portfolio_capture', 'visual'),
)
CALL_LIMIT = 18


def normalized(text):
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', str(text))).casefold()


def assess(case, candidates, text):
    lines = text.splitlines()
    rows = []
    for fact in case['facts']:
        matches, suggestions = [], []
        for candidate in candidates:
            statement = str(candidate.get('statement', ''))
            provenance, entity = candidate.get('provenance') or {}, candidate.get('entity') or {}
            start, end = provenance.get('line_start'), provenance.get('line_end')
            excerpt = '\n'.join(lines[start - 1:end]) if type(start) is int and type(end) is int and 1 <= start <= end <= len(lines) else ''
            full_wording = normalized(fact['text']) in normalized(statement)
            source_wording = normalized(fact['text']) in normalized(excerpt)
            entity_matches = not fact.get('organization') or (
                normalized(entity.get('organization', '')) == normalized(fact['organization'])
                and normalized(entity.get('role', '')) == normalized(fact['role'])
                and [normalized(entity.get(k, '')) for k in ('start_date', 'end_date')] == [normalized(d) for d in fact['dates']]
            )
            if full_wording and source_wording and entity_matches and candidate.get('selected') is False:
                matches.append(candidate['candidate_id'])
            similarity = SequenceMatcher(None, normalized(fact['text']), normalized(statement), autojunk=False).ratio()
            if similarity >= .3 or full_wording or source_wording:
                suggestions.append({'candidate_id': candidate['candidate_id'], 'text_similarity': round(similarity, 3),
                                    'full_wording': full_wording, 'source_wording': source_wording,
                                    'expected_entity_fields_match': entity_matches})
        suggestions.sort(key=lambda row: (row['full_wording'], row['source_wording'], row['text_similarity']), reverse=True)
        rows.append({'fact_id': fact['id'], 'kind': fact['kind'], 'critical': fact['critical'],
                     'exact_grounded_candidate_ids': matches, 'adjudication_suggestions': suggestions[:3],
                     'semantic_status': 'REQUIRES_SOURCE_ADJUDICATION'})
    # All facts stay in the ledger, including contact/identity, missing data and
    # limitations. Existing product policy excludes contacts from Claim output;
    # do not silently remove them to inflate a Claim-recall denominator.
    return {'source_facts': len(rows), 'exact_grounded_wording_matches': sum(bool(r['exact_grounded_candidate_ids']) for r in rows),
            'facts': rows, 'semantic_recall': None, 'unsupported_claims': None,
            'unflagged_critical_errors': None, 'independent_adjudication': False,
            'scope': 'All source facts retained; similarity only suggests review candidates and is never a correctness score.'}


class ObservedHermes(layout.ObservedHermes):
    def __init__(self, route, *, request_limit=CALL_LIMIT):
        super().__init__(route, request_limit=request_limit)
        self.responses = []

    def _invoke(self, request):
        result = super()._invoke(request)
        numbered = request.get('line_numbered_document')
        row = {'task': request.get('task'), 'adapter_request_index': len(self.calls),
               'result_content': result['result']['content'], 'tool_calls': result['toolSummary']['calls']}
        if numbered:
            row.update(line_number_start=int(numbered[0].split('\t', 1)[0]),
                       source_lines=[line.split('\t', 1)[1] for line in numbered])
        if request.get('physical_source_pages'):
            row.update(physical_source_pages=request['physical_source_pages'],
                       source_sha256=request['source_sha256'],
                       replacement_requested=bool(request.get('replacement_request')))
        self.responses.append(row)
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live-hermes', action='store_true', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--visual-followup', action='store_true',
                        help='Only the three originally failed visual arms; hard cap nine additional adapter requests')
    args = parser.parse_args()
    checkpoint = args.output.with_suffix('.jsonl')
    if args.output.exists() or checkpoint.exists():
        parser.error('Use a fresh output; existing observations are immutable')
    cases = {c['id']: c for c in complete.load_manifest()['cases']}
    route = layout.previous.public_hermes_route()
    selected = tuple(row for row in PILOT if row[1] == 'visual') if args.visual_followup else PILOT
    request_limit = 9 if args.visual_followup else CALL_LIMIT
    engine = ObservedHermes(route, request_limit=request_limit)
    files = ['src/jobops/ai_runtime.py', 'src/jobops/ai_connections.py', 'src/jobops/onboarding_center.py',
             'src/jobops/_pdf_region_worker.py', 'src/jobops/document_coverage.py', 'src/jobops/multimodal.py',
             'scripts/evaluate-layout-claims.py', 'scripts/evaluate-complete-claims.py']
    report = {'observed_at': iso_utc(), 'synthetic_only': True, 'pilot': list(selected),
              'observation_kind': 'FRESH_VISUAL_FOLLOWUP' if args.visual_followup else 'INITIAL_PILOT',
              'base_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
              'working_tree_observation': True, 'manifest_lf_sha256': complete.MANIFEST_LF_SHA256,
              'source_lf_sha256': {f: hashlib.sha256((ROOT / f).read_bytes().replace(b'\r\n', b'\n')).hexdigest() for f in files},
              'adapter_request_limit': request_limit, 'model_route_before': route,
              'cost_usd': None, 'usage_tokens': None, 'provider_api_calls': None,
              'billing_note': 'Provider-internal retries and billed usage unavailable; unknown is not zero.',
              'arms': [], 'business_completion': 'NOT_ATTEMPTED', 'independent_human_acceptance': False}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with checkpoint.open('x', encoding='utf-8') as stream:
        for case_id, mode in selected:
            first = len(engine.responses)
            row = layout.run_arm(cases[case_id], mode, engine, corpus_dir=complete.CORPUS, assessor=assess)
            row['responses'] = engine.responses[first:]
            # Even a failed source can have a valid transcription; retain it
            # separately from the source's production success/failure status.
            row.setdefault('analyzed_text', engine.analyzed_text)
            report['arms'].append(row)
            stream.write(json.dumps(row, ensure_ascii=False) + '\n')
            stream.flush()
            print(json.dumps({'case': case_id, 'status': row['status'], 'requests': row['adapter_requests'],
                              'candidates': len(row.get('candidates', []))}), flush=True)
    try:
        report['model_route_after'] = layout.previous.public_hermes_route()
    except Exception:
        report['model_route_after'] = {'status': 'UNAVAILABLE'}
    report['model_route_unchanged'] = report['model_route_after'] == route
    report['adapter_requests'] = engine.calls
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(report, stream, indent=2, ensure_ascii=False)
        stream.write('\n')
    print(json.dumps({'adapter_requests': len(engine.calls), 'cost_usd': None, 'whole_material_acceptance': False}))


if __name__ == '__main__':
    main()
