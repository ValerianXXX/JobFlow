"""Replay a hash-pinned synthetic live observation through current preview code.

No provider or credential access. The original failed observation is immutable.
This isolates validator changes; it is not a second real model sample.
"""
from __future__ import annotations
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('complete_claim_replay_inputs', ROOT / 'scripts/evaluate-complete-claims.py')
pilot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pilot)
OBSERVATION = ROOT / 'evaluations/2026-10-29/complete-claims-pilot-before.json'
OBSERVATION_LF_SHA256 = '04e1d542e1a7e8239f69c68c532eb0a1791df684fab98eecd2b973dc6e6eb046'
VISUAL_OBSERVATION = ROOT / 'evaluations/2026-10-29/complete-claims-visual-followup.json'
VISUAL_OBSERVATION_LF_SHA256 = 'c29b260a5cfb8ead88de20dffbea291cf85fa769bb7d5c0508c819ca3e15f551'


def load_observation(kind='initial'):
    if kind not in {'initial', 'visual-followup'}:
        raise ValueError('Only pinned public synthetic observations are accepted')
    path, expected_hash, count = (VISUAL_OBSERVATION, VISUAL_OBSERVATION_LF_SHA256, 3) if kind == 'visual-followup' else (OBSERVATION, OBSERVATION_LF_SHA256, 6)
    raw = path.read_bytes().replace(b'\r\n', b'\n')
    if hashlib.sha256(raw).hexdigest() != expected_hash:
        raise ValueError('Frozen synthetic observation changed')
    value = json.loads(raw)
    if value.get('synthetic_only') is not True or len(value['arms']) != count:
        raise ValueError('Unexpected observation population')
    return value


class ReplayEngine(pilot.layout.ObservedHermes):
    def __init__(self, responses):
        super().__init__({'model': 'recorded-synthetic-response', 'provider': 'replay'})
        self.responses = list(responses)
        self.cursor = 0

    def _invoke(self, request):
        if self.cursor >= len(self.responses):
            raise pilot.layout.JobOpsError('EVALUATION_REPLAY_EXHAUSTED', 'No matching recorded response remains; no provider fallback is available.')
        row = self.responses[self.cursor]
        if row['task'] != request.get('task') or row.get('tool_calls') != 0:
            raise pilot.layout.JobOpsError('EVALUATION_REPLAY_MISMATCH', 'Recorded task or tool audit differs.')
        numbered = request.get('line_numbered_document')
        if numbered and ([line.split('\t', 1)[1] for line in numbered] != row.get('source_lines')
                         or int(numbered[0].split('\t', 1)[0]) != row.get('line_number_start')):
            raise pilot.layout.JobOpsError('EVALUATION_REPLAY_MISMATCH', 'The current parser text differs from the frozen request; no output was substituted.')
        self.cursor += 1
        self.calls.append({'task': row['task'], 'status': 'RECORDED_REPLAY', 'live_provider_request': False})
        return {'ok': True, 'status': 'ok', 'toolSummary': {'calls': 0, 'tools': []},
                'result': {'content': row['result_content']}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--observation', choices=('initial', 'visual-followup'), default='initial')
    args = parser.parse_args()
    checkpoint = args.output.with_suffix('.jsonl')
    if args.output.exists() or checkpoint.exists():
        parser.error('Use a fresh output; original observations must not be overwritten')
    before = load_observation(args.observation)
    cases = {c['id']: c for c in pilot.complete.load_manifest()['cases']}
    files = ['src/jobops/ai_runtime.py', 'src/jobops/_pdf_region_worker.py', 'src/jobops/onboarding_center.py',
             'src/jobops/multimodal.py', 'scripts/replay-complete-claims.py']
    report = {'observed_at': pilot.iso_utc(), 'synthetic_only': True, 'mode': 'SAME_OUTPUT_LOCAL_REPLAY',
              'observation_lf_sha256': VISUAL_OBSERVATION_LF_SHA256 if args.observation == 'visual-followup' else OBSERVATION_LF_SHA256,
              'base_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
              'source_lf_sha256': {f: hashlib.sha256((ROOT / f).read_bytes().replace(b'\r\n', b'\n')).hexdigest() for f in files},
              'live_provider_calls': 0, 'current_stage_model_cost_usd': 0, 'original_live_cost_usd': None,
              'visual_replacement_disabled': True,
              'visual_note': 'Replays validate frozen responses only. They do not measure a new prompt or provider run; replacement is disabled.',
              'independent_human_acceptance': False, 'whole_material_acceptance': False, 'arms': []}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with checkpoint.open('x', encoding='utf-8') as stream:
        for old in before['arms']:
            engine = ReplayEngine(old['responses'])
            with patch.dict('os.environ', {'JOBFLOW_VISUAL_MAX_REPAIR': '0'}):
                row = pilot.layout.run_arm(cases[old['case']], old['mode'], engine, corpus_dir=pilot.complete.CORPUS, assessor=pilot.assess)
            row['before_status'] = old['status']
            row['before_code'] = old.get('code')
            row['recorded_responses_consumed'] = row.pop('adapter_requests')
            row['unused_original_responses'] = len(engine.responses) - engine.cursor
            report['arms'].append(row)
            stream.write(json.dumps(row, ensure_ascii=False) + '\n')
            stream.flush()
            print(json.dumps({'case': row['case'], 'status': row['status'], 'candidates': len(row.get('candidates', [])), 'code': row.get('code')}), flush=True)
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write('\n')


if __name__ == '__main__':
    main()
