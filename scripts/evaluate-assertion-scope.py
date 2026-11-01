"""Evaluate frozen synthetic scope cases locally; never invoke a model."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from jobops.ai_runtime import _statement_grounding_report


def digest(path):
    return hashlib.sha256(path.read_bytes().replace(b'\r\n', b'\n')).hexdigest()


def counts(rows):
    return {'unsafe': sum(not r['expected_valid'] for r in rows),
        'unsafe_accepted': sum(not r['expected_valid'] and r['actual_valid'] for r in rows),
        'supported': sum(r['expected_valid'] for r in rows),
        'supported_filtered': sum(r['expected_valid'] and not r['actual_valid'] for r in rows)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Use a new observation path; preserve earlier evidence')
    fixture_path = ROOT / 'tests/fixtures/assertion_scope/cases.json'
    assert digest(fixture_path) == '52cb85ff82c1350e60a4946e688127eb4a3a6174fc7453321c60bf784295fa6c'
    fixture = json.loads(fixture_path.read_text(encoding='utf-8'))
    assert fixture['synthetic_only'] is True and len(fixture['cases']) == 84
    started = time.perf_counter()
    rows = []
    for case in fixture['cases']:
        report = _statement_grounding_report(case['statement'], case['source'])
        rows.append({**{k: case[k] for k in ['id', 'group', 'language', 'expected_valid']},
            'actual_valid': report['valid'], 'diagnostics': report})
    result = {'observed_at_utc': datetime.now(timezone.utc).isoformat(),
        'protocol': fixture['protocol'], 'synthetic_only': True,
        'measurement': 'FROZEN_DEVELOPMENT_COUNTEREXAMPLES_NOT_BLINDED_ACCURACY',
        'source_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
        'fixture_lf_sha256': digest(fixture_path),
        'source_lf_sha256': {p: digest(ROOT / p) for p in
            ['src/jobops/ai_runtime.py', 'src/jobops/claim_assertion_status.py', 'scripts/evaluate-assertion-scope.py']},
        'elapsed_ms': round(1000 * (time.perf_counter() - started), 3),
        'summary': counts(rows),
        'by_family': {g: counts([r for r in rows if r['group'] == g]) for g in sorted({r['group'] for r in rows})},
        'by_language': {g: counts([r for r in rows if r['language'] == g]) for g in sorted({r['language'] for r in rows})},
        'new_provider_calls': 0, 'fresh_model_comparison': False, 'historical_model_cost': 'UNKNOWN',
        'full_material_acceptance': False, 'independent_human_acceptance': False,
        'cases': rows}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf-8', newline='\n') as target:
        json.dump(result, target, ensure_ascii=False, indent=2)
        target.write('\n')
    print(json.dumps({'summary': result['summary'], 'by_family': result['by_family']}))


if __name__ == '__main__':
    main()
