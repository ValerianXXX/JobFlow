"""Private human context-review records, never answers or external approval."""
from __future__ import annotations

from collections import Counter

from .errors import JobOpsError
from .source_context import CONTEXT_FIELDS
from .util import canonical_json, iso_utc, sha256_bytes, stable_id

MAX_REVIEW_EVENTS = 4096
REASONS = {
    'PROFILE_REVIEWED': {''}, 'REOPEN': {''},
    'FOLLOW_UP': {'NEEDS_EVIDENCE', 'NEEDS_CLARIFICATION'},
    'DISMISSED': {'NOT_APPLICABLE', 'OUTDATED', 'DUPLICATE'},
}


def _answer_content(answer):
    return {key: answer.get(key) for key in ('value', 'status', 'source', 'use_policy')}


def track_answer_versions(state, previous):
    """A changed-then-restored value cannot revive an earlier review.

    Called at the encrypted-state write boundary, covering every answer writer.
    Harmless timestamp refreshes and unrelated fields do not invalidate records.
    """
    versions = previous.get('context_answer_versions', {})
    result = {}
    for field in CONTEXT_FIELDS:
        version = versions.get(field, 0)
        if type(version) is not int or not 0 <= version <= 1_000_000:
            raise JobOpsError('SOURCE_CONTEXT_HISTORY_INVALID', 'The private review version needs recovery.')
        changed = _answer_content(state.get('answers', {}).get(field, {})) != _answer_content(previous.get('answers', {}).get(field, {}))
        result[field] = version + int(changed)
    state['context_answer_versions'] = result


def _history(state):
    history = state.get('source_context_reviews', [])
    if not isinstance(history, list) or len(history) > MAX_REVIEW_EVENTS:
        raise JobOpsError('SOURCE_CONTEXT_HISTORY_INVALID', 'The private review history needs recovery.')
    previous = {}
    keys = {'review_id', 'note_id', 'source_id', 'binding_sha256', 'answer_sha256',
            'previous_review_id', 'decision', 'reason', 'recorded_at', 'approved_for_external'}
    for event in history:
        if (not isinstance(event, dict) or set(event) != keys
                or not isinstance(event.get('note_id'), str)
                or not all(isinstance(event.get(key), str) for key in
                           ('review_id', 'source_id', 'binding_sha256', 'decision', 'reason', 'recorded_at'))
                or not (event.get('answer_sha256') is None or isinstance(event['answer_sha256'], str))
                or not (event.get('previous_review_id') is None or isinstance(event['previous_review_id'], str))
                or event.get('approved_for_external') is not False
                or event.get('decision') not in REASONS
                or event.get('reason') not in REASONS[event['decision']]
                or event.get('previous_review_id') != previous.get(event['note_id'])
                or event.get('review_id') != _event_id({k: v for k, v in event.items() if k != 'review_id'})):
            raise JobOpsError('SOURCE_CONTEXT_HISTORY_INVALID', 'The private review history failed its integrity check.')
        previous[event['note_id']] = event['review_id']
    return history


def _event_id(event):
    return stable_id('CTXREV', canonical_json(event).hex())


def review_state(state):
    history = _history(state)
    latest = {event['note_id']: event for event in history}
    history_counts = Counter(event['note_id'] for event in history)
    sources = {source['source_id']: source for source in state.get('sources', [])}
    pending = {source['source_id'] for source in state.get('pending_sources', [])}
    valid_transcripts = {}
    for source_id, source in sources.items():
        transcript = source.get('_analysis_transcript') or {}
        valid_transcripts[source_id] = (transcript.get('schema_version') == 1
            and isinstance(transcript.get('text'), str)
            and sha256_bytes(transcript['text'].encode()) == transcript.get('text_sha256'))
    notes = {}
    for note in state.get('source_context_notes', []):
        source = sources.get(note.get('source_id'), {})
        transcript = source.get('_analysis_transcript') or {}
        field = note.get('field_id')
        valid = (source.get('raw_retained') is True and source.get('sha256') == note.get('source_sha256')
                 and note.get('status') == 'UNCONFIRMED_SOURCE_CONTEXT' and note.get('approved_for_external') is False
                 and (note.get('kind') == 'profile_constraint' and field in CONTEXT_FIELDS
                      or note.get('kind') == 'evidence_gap' and field == '')
                 and isinstance(note.get('quote'), str)
                 and sha256_bytes(note['quote'].encode()) == note.get('quote_sha256')
                 and valid_transcripts.get(note.get('source_id'), False))
        binding = sha256_bytes(canonical_json({
            'source': {key: source.get(key) for key in ('source_id', 'sha256')},
            'text_sha256': transcript.get('text_sha256'),
            'note': {key: note.get(key) for key in ('note_id', 'kind', 'field_id', 'line_start', 'line_end', 'quote_sha256')},
            'generation': state.get('context_source_generations', {}).get(note.get('source_id'), 0),
            'revision': state.get('revision_number', 1),
        }))
        answer = state.get('answers', {}).get(field, {})
        answer_hash = sha256_bytes(canonical_json([_answer_content(answer),
            state.get('context_answer_versions', {}).get(field, 0)])) if field else None
        event = latest.get(note['note_id'])
        status = 'PENDING'
        if event:
            if event['binding_sha256'] != binding:
                status = 'SOURCE_CHANGED'
            elif event['decision'] == 'PROFILE_REVIEWED':
                status = ('ANSWER_CHANGED' if event['answer_sha256'] != answer_hash else
                          'PROFILE_UNKNOWN_REVIEWED' if answer.get('status') == 'UNKNOWN' else 'PROFILE_REVIEWED')
            else:
                status = 'PENDING' if event['decision'] == 'REOPEN' else event['decision']
        if not valid:
            status = 'SOURCE_UNAVAILABLE'
        notes[note['note_id']] = {
            'binding_sha256': binding, 'answer_sha256': answer_hash,
            'review_id': event['review_id'] if event else None,
            'status': status, 'reason': event['reason'] if event else '',
            'available': bool(valid and note['source_id'] not in pending and state.get('status') != 'ONBOARDING_COMPLETE'),
            'replacement_pending': note['source_id'] in pending,
            'history_count': history_counts[note['note_id']],
            'approved_for_external': False,
        }
    return {'version': 1, 'notes': notes, 'event_count': len(history), 'automatic_answer_update': False,
            'fact_completeness_verified': False, 'approved_for_external': False}


def prepare_review(state, payload):
    keys = {'note_id', 'expected_binding_sha256', 'expected_review_id', 'expected_answer_sha256',
            'decision', 'reason', 'user_confirmed'}
    if (not isinstance(payload, dict) or set(payload) != keys or payload.get('user_confirmed') is not True
            or not isinstance(payload.get('note_id'), str) or not isinstance(payload.get('decision'), str)
            or payload['decision'] not in REASONS or not isinstance(payload.get('reason'), str)
            or payload['reason'] not in REASONS[payload['decision']]):
        raise JobOpsError('SOURCE_CONTEXT_REVIEW_INVALID', 'Choose a review decision and explicitly confirm it.')
    view = review_state(state)['notes'].get(payload['note_id'])
    note = next((note for note in state.get('source_context_notes', []) if note['note_id'] == payload['note_id']), None)
    if not view or not view['available']:
        raise JobOpsError('SOURCE_CONTEXT_REVIEW_UNAVAILABLE', 'Confirm or discard pending analysis, then refresh this source review.')
    if (view['binding_sha256'] != payload['expected_binding_sha256']
            or view['answer_sha256'] != payload['expected_answer_sha256']):
        raise JobOpsError('SOURCE_CONTEXT_REVIEW_STALE', 'The source or saved answer changed. Refresh and review the current version.')
    if payload['decision'] == 'PROFILE_REVIEWED' and note['kind'] != 'profile_constraint':
        raise JobOpsError('SOURCE_CONTEXT_REVIEW_INVALID', 'An evidence gap cannot approve or review a Profile answer.')
    history = _history(state)
    previous = next((event for event in reversed(history) if event['note_id'] == note['note_id']), None)
    answer_hash = view['answer_sha256'] if payload['decision'] == 'PROFILE_REVIEWED' else None
    if view['review_id'] != payload['expected_review_id']:
        if (previous and previous['binding_sha256'] == view['binding_sha256']
                and previous['previous_review_id'] == payload['expected_review_id']
                and previous['answer_sha256'] == answer_hash
                and (previous['decision'], previous['reason']) == (payload['decision'], payload['reason'])):
            return note, previous, False
        raise JobOpsError('SOURCE_CONTEXT_REVIEW_STALE', 'Another review was saved. Refresh before changing its decision.')
    if len(history) >= MAX_REVIEW_EVENTS:
        raise JobOpsError('SOURCE_CONTEXT_HISTORY_LIMIT', 'The private review history reached its bounded capacity. Preserve it before continuing.')
    event = {'note_id': note['note_id'], 'source_id': note['source_id'], 'binding_sha256': view['binding_sha256'],
             'answer_sha256': answer_hash, 'previous_review_id': view['review_id'], 'decision': payload['decision'],
             'reason': payload['reason'], 'recorded_at': iso_utc(), 'approved_for_external': False}
    event['review_id'] = _event_id(event)
    return note, event, True
