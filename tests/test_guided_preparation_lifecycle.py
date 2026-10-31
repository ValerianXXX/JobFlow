"""Synthetic preparation ownership contracts, frozen before the lifecycle repair.

The real service, locks and SQLite schema are used. Application preparation is a
controlled in-process operation; no model, employer, mailbox or approval is used.
"""
from __future__ import annotations

from contextlib import ExitStack
import http.client
import json
import threading
import time
import unittest
from unittest import mock

from _support import project_temp
import test_onboarding_center as fixtures
from jobops.browser_assist import COMPANION_EXTENSION_ORIGIN
from jobops.errors import JobOpsError
from jobops.onboarding_server import create_server


class GuidedPreparationLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = self.stack.enter_context(project_temp())
        self.service = fixtures.OnboardingCenterTests().make_service(self.root, with_ai=False)[0]
        self.token = 'a' * 54
        self.service._guided_intakes[self.token] = {
            'token': self.token, 'intake_id': 'GIN-123456789ABC', 'paired': True,
            'status': 'AWAITING_APPLICATION_FORM_CAPTURE',
            'started_epoch': time.time(), 'expires_epoch': time.time() + 600,
            'expires_at': '2099-01-01T00:00:00Z',
        }
        self.payload = {'url': 'https://example.invalid/apply',
                        'sanitized_html': '<form><input name="synthetic"></form>', 'blocker_signals': []}
        self.entered, self.release, self.finished = threading.Event(), threading.Event(), threading.Event()
        self.workers, self.received, self.closers, self.close_errors = [], [], [], []
        self.resource_closes = []
        self.stack.enter_context(mock.patch.object(self.service.browser_assist, 'close',
            side_effect=lambda: self.resource_closes.append('browser')))
        self.stack.enter_context(mock.patch.object(self.service.ai_connections, 'close',
            side_effect=lambda: self.resource_closes.append('ai')))
        self.stack.enter_context(mock.patch.object(self.service, 'capture_guided_application_form',
                                                  side_effect=self.operation))
        self.addCleanup(self.drain)

    def operation(self, token, payload, **kwargs):
        # Match the real capture method's service lock. Closing must not hold it
        # while it waits for an admitted operation to finish.
        with self.service._lock:
            self.workers.append(threading.current_thread())
            self.received.append((token, payload, kwargs))
            self.entered.set()
            if not self.release.wait(4):
                raise AssertionError('Synthetic operation was not released')
            (self.root / 'last-preparation-write.txt').write_text('synthetic result', encoding='utf-8')
            self.finished.set()
            return {'status': 'REVIEW_PACKET_READY', 'application_id': 'APP-123456789ABC',
                    'automatic_retry': False, 'real_external_actions': 0}

    def start(self, payload=None):
        return self.service.start_guided_application_form_preparation(
            self.token, self.payload if payload is None else payload, extension_origin=COMPANION_EXTENSION_ORIGIN)

    def status(self):
        return self.service.guided_application_form_preparation_status(
            self.token, extension_origin=COMPANION_EXTENSION_ORIGIN)

    def completed(self):
        self.release.set()
        self.assertTrue(self.finished.wait(2))
        for worker in self.workers:
            worker.join(2)
            self.assertFalse(worker.is_alive())
        return self.status()

    def closer(self, callback=None):
        closed = threading.Event()
        def close():
            try:
                (callback or self.service.close)()
            except BaseException as error:
                self.close_errors.append(error)
            finally:
                closed.set()
        thread = threading.Thread(target=close, daemon=True)
        self.closers.append(thread)
        thread.start()
        return closed

    def drain(self):
        self.release.set()
        for worker in self.workers:
            worker.join(5)
            self.assertFalse(worker.is_alive(), 'Owned preparation outlived its fixture')
        for closer in self.closers:
            closer.join(5)
            self.assertFalse(closer.is_alive(), 'Owned close outlived its fixture')
        self.service.close()

    def test_close_waits_for_the_admitted_write_before_closing_dependencies(self):
        self.start(); self.assertTrue(self.entered.wait(2))
        closed = self.closer()
        self.assertFalse(closed.wait(0.15), 'Close returned before the admitted write')
        self.assertEqual(self.resource_closes, [])
        self.release.set(); self.assertTrue(closed.wait(2))
        self.assertEqual(self.close_errors, [])
        self.assertTrue(self.finished.is_set())
        self.assertEqual(self.resource_closes, ['browser', 'ai'])
        self.assertEqual((self.root / 'last-preparation-write.txt').read_text(), 'synthetic result')
        self.assertFalse(self.workers[0].is_alive())

    def test_closed_service_rejects_new_preparation_without_starting_a_worker(self):
        self.service.close()
        with self.assertRaises(JobOpsError) as error:
            self.start()
        self.assertEqual(error.exception.code, 'GUIDED_PREPARATION_CLOSING')
        self.assertEqual(self.received, [])

    def test_closing_service_rejects_admission_while_an_owned_worker_is_draining(self):
        self.start(); self.assertTrue(self.entered.wait(2))
        joining = threading.Event()
        worker = self.workers[0]; original_join = worker.join
        def observed_join(*args, **kwargs):
            joining.set(); return original_join(*args, **kwargs)
        with mock.patch.object(worker, 'join', side_effect=observed_join):
            closed = self.closer()
            self.assertTrue(joining.wait(0.5), 'Close did not drain its preparation worker')
            with self.assertRaises(JobOpsError) as error:
                self.start()
            self.assertEqual(error.exception.code, 'GUIDED_PREPARATION_CLOSING')
            self.release.set(); self.assertTrue(closed.wait(2))
        self.assertEqual(self.close_errors, [])
        self.assertEqual(len(self.received), 1)

    def test_start_failure_leaves_no_permanently_pending_job_and_allows_explicit_retry(self):
        with mock.patch.object(threading.Thread, 'start', side_effect=RuntimeError('synthetic private detail')):
            with self.assertRaises(JobOpsError) as error:
                self.start()
        self.assertEqual(error.exception.code, 'GUIDED_PREPARATION_START_FAILED')
        self.assertNotIn('synthetic private detail', json.dumps(error.exception.as_dict()))
        with self.assertRaises(JobOpsError) as missing:
            self.status()
        self.assertEqual(missing.exception.code, 'GUIDED_INTAKE_PREPARATION_NOT_FOUND')
        self.start(); self.assertTrue(self.entered.wait(2))
        self.assertEqual(self.completed()['status'], 'REVIEW_PACKET_READY')
        self.assertEqual(len(self.received), 1)

    def test_identical_retry_is_idempotent_but_a_changed_pending_form_is_rejected(self):
        self.start(); self.assertTrue(self.entered.wait(2))
        self.assertEqual(self.start(dict(self.payload))['status'], 'PREPARING_APPLICATION')
        with self.assertRaises(JobOpsError) as error:
            self.start({**self.payload, 'url': 'https://example.invalid/other'})
        self.assertEqual(error.exception.code, 'GUIDED_PREPARATION_INPUT_CHANGED')
        self.assertEqual(len(self.received), 1)

    def test_completed_packet_cannot_be_returned_for_a_changed_form(self):
        self.start(); self.assertTrue(self.entered.wait(2))
        result = self.completed()
        result['application_id'] = 'mutated view'
        self.assertEqual(self.start()['application_id'], 'APP-123456789ABC')
        with self.assertRaises(JobOpsError) as error:
            self.start({**self.payload, 'sanitized_html': '<form><input name="different"></form>'})
        self.assertEqual(error.exception.code, 'GUIDED_PREPARATION_INPUT_CHANGED')
        self.assertEqual(len(self.received), 1)

    def test_input_snapshot_is_independent_and_polling_preserves_external_action_boundary(self):
        self.start(); self.assertTrue(self.entered.wait(2))
        self.payload['url'] = 'https://example.invalid/changed'
        self.payload['blocker_signals'].append('LOGIN')
        result = self.completed()
        self.assertEqual(self.received[0][1]['url'], 'https://example.invalid/apply')
        self.assertEqual(self.received[0][1]['blocker_signals'], [])
        self.assertEqual(result['real_external_actions'], 0)
        self.assertFalse(result['automatic_retry'])

    def test_repeated_close_runs_dependency_cleanup_once(self):
        self.service.close(); self.service.close()
        self.assertEqual(self.resource_closes, ['browser', 'ai'])

    def test_preparation_cannot_join_itself_by_closing_the_service(self):
        def self_close(*args, **kwargs):
            self.workers.append(threading.current_thread()); self.entered.set()
            try:
                self.service.close()
                return {'status': 'REVIEW_PACKET_READY'}
            finally:
                self.finished.set()
        with mock.patch.object(self.service, 'capture_guided_application_form', side_effect=self_close):
            self.start(); self.assertTrue(self.entered.wait(2))
            result = self.completed()
        self.assertEqual(result['status'], 'FORM_CAPTURE_FAILED')
        self.assertEqual(result['code'], 'GUIDED_PREPARATION_CLOSE_FROM_WORKER')
        self.assertEqual(self.resource_closes, [])

    def test_a_post_start_exception_retains_ownership_and_prevents_duplicate_preparation(self):
        actual_start = threading.Thread.start
        def start_then_fail(worker):
            actual_start(worker)
            raise RuntimeError('synthetic post-start detail')
        with mock.patch.object(threading.Thread, 'start', start_then_fail):
            with self.assertRaises(JobOpsError) as error:
                self.start()
        self.assertEqual(error.exception.code, 'GUIDED_PREPARATION_START_UNCONFIRMED')
        self.assertTrue(self.entered.wait(2))
        self.assertEqual(self.start()['status'], 'PREPARING_APPLICATION')
        closed = self.closer()
        self.assertFalse(closed.wait(0.15))
        self.release.set(); self.assertTrue(closed.wait(2))
        self.assertEqual(self.close_errors, []); self.assertEqual(len(self.received), 1)

    def test_a_concurrent_replay_observes_completion_after_its_initial_cache_miss(self):
        missed, proceed = threading.Event(), threading.Event()
        self.addCleanup(proceed.set)
        results, errors = [], []
        cached = self.service._guided_preparations.cached
        def delayed_cache(*args):
            result = cached(*args)
            if threading.current_thread().name == 'synthetic-delayed-replay' and not missed.is_set():
                self.assertIsNone(result)
                missed.set()
                self.assertTrue(proceed.wait(3))
            return result
        def request():
            try:
                results.append(self.start())
            except BaseException as exc:
                errors.append(exc)
        with mock.patch.object(self.service._guided_preparations, 'cached', side_effect=delayed_cache):
            caller = threading.Thread(target=request, name='synthetic-delayed-replay', daemon=True)
            self.closers.append(caller); caller.start()
            self.assertTrue(missed.wait(1))
            self.start(); self.assertTrue(self.entered.wait(2)); self.completed()
            # The real preparation updates the intake lease before returning.
            self.service._guided_intakes[self.token]['status'] = 'REVIEW_PACKET_READY'
            proceed.set(); caller.join(2)
        self.assertFalse(caller.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(results[0]['status'], 'REVIEW_PACKET_READY')
        self.assertEqual(len(self.received), 1)

    def test_discarding_progress_does_not_release_a_live_worker(self):
        self.start(); self.assertTrue(self.entered.wait(2))
        self.service._guided_preparations.discard(self.token)
        with self.assertRaises(JobOpsError) as missing:
            self.status()
        self.assertEqual(missing.exception.code, 'GUIDED_INTAKE_PREPARATION_NOT_FOUND')
        closed = self.closer()
        self.assertFalse(closed.wait(0.15))
        self.release.set(); self.assertTrue(closed.wait(2))
        self.assertEqual(self.close_errors, [])
        self.assertTrue(self.finished.is_set())
        with self.assertRaises(JobOpsError):
            self.status()  # A late result must not resurrect discarded progress.

    def test_expired_cached_result_cannot_bypass_the_intake_deadline(self):
        self.start(); self.assertTrue(self.entered.wait(2)); self.completed()
        future = time.time() + 601
        with mock.patch('jobops.guided_preparation.time.time', return_value=future):
            for request in (self.start, self.status):
                with self.assertRaises(JobOpsError) as error:
                    request()
                self.assertEqual(error.exception.code, 'GUIDED_INTAKE_EXPIRED')
        self.assertEqual(len(self.received), 1)

    def test_cleanup_failure_remains_visible_after_draining_and_is_not_retried(self):
        self.start(); self.assertTrue(self.entered.wait(2))
        failure = RuntimeError('synthetic dependency cleanup error')
        def fail_close():
            self.resource_closes.append('browser')
            raise failure
        with mock.patch.object(self.service.browser_assist, 'close', side_effect=fail_close):
            closed = self.closer()
            self.assertFalse(closed.wait(0.15))
            self.release.set(); self.assertTrue(closed.wait(2))
            self.assertEqual(self.close_errors, [failure])
            with self.assertRaises(RuntimeError) as repeated:
                self.service.close()
            self.assertIs(repeated.exception, failure)
        self.assertEqual(self.resource_closes, ['browser', 'ai'])
        # The fixture expects closure to succeed. This test already asserted the
        # persistent failure twice; do not ask its cleanup to hide that failure.
        self.stack.enter_context(mock.patch.object(self.service, 'close', return_value=None))

    def test_concurrent_closers_share_the_same_completion_barrier(self):
        self.start(); self.assertTrue(self.entered.wait(2))
        first, second = self.closer(), self.closer()
        self.assertFalse(first.wait(0.15)); self.assertFalse(second.is_set())
        self.release.set()
        self.assertTrue(first.wait(2)); self.assertTrue(second.wait(2))
        self.assertEqual(self.close_errors, [])
        self.assertEqual(self.resource_closes, ['browser', 'ai'])

    def test_close_between_lease_validation_and_admission_does_not_launch_work(self):
        validated, proceed = threading.Event(), threading.Event()
        self.addCleanup(proceed.set)
        admit = self.service._guided_preparations.start
        errors = []
        def delayed_admission(*args, **kwargs):
            validated.set(); self.assertTrue(proceed.wait(3))
            return admit(*args, **kwargs)
        def request():
            try:
                self.start()
            except JobOpsError as error:
                errors.append(error.code)
        with mock.patch.object(self.service._guided_preparations, 'start', side_effect=delayed_admission):
            caller = threading.Thread(target=request, daemon=True)
            self.closers.append(caller); caller.start()
            self.assertTrue(validated.wait(1)); self.service.close()
            proceed.set(); caller.join(2)
        self.assertFalse(caller.is_alive())
        self.assertEqual(errors, ['GUIDED_PREPARATION_CLOSING'])
        self.assertEqual(self.received, [])

    def test_worker_self_close_does_not_deadlock_an_already_waiting_external_closer(self):
        joining = threading.Event()
        def self_close(*args, **kwargs):
            self.workers.append(threading.current_thread()); self.entered.set()
            self.assertTrue(self.release.wait(3))
            self.service.close()
        with mock.patch.object(self.service, 'capture_guided_application_form', side_effect=self_close):
            self.start(); self.assertTrue(self.entered.wait(2))
            worker = self.workers[0]; original_join = worker.join
            def observed_join(*args, **kwargs):
                joining.set(); return original_join(*args, **kwargs)
            with mock.patch.object(worker, 'join', side_effect=observed_join):
                closed = self.closer(); self.assertTrue(joining.wait(1))
                self.release.set(); self.assertTrue(closed.wait(2))
        self.assertEqual(self.close_errors, [])
        self.assertEqual(self.status()['code'], 'GUIDED_PREPARATION_CLOSE_FROM_WORKER')
        self.assertEqual(self.resource_closes, ['browser', 'ai'])

    def test_http_server_close_waits_for_its_accepted_background_preparation(self):
        server = create_server(self.service, token='synthetic-preparation-session')
        loop = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.01}, daemon=True)
        loop.start()
        client = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=3)
        try:
            body = json.dumps(self.payload).encode()
            client.request('POST', f'/intake/{self.token}/capture-form', body=body, headers={
                'Origin': COMPANION_EXTENSION_ORIGIN, 'Content-Type': 'application/json', 'Content-Length': str(len(body))})
            response = client.getresponse()
            self.assertEqual(response.status, 200)
            self.assertEqual(json.loads(response.read())['status'], 'PREPARING_APPLICATION')
            self.assertTrue(self.entered.wait(2))
            server.shutdown()
            closed = self.closer(server.server_close)
            self.assertFalse(closed.wait(0.15), 'HTTP server closed while local preparation still writes')
            self.assertEqual(self.resource_closes, [])
            self.release.set(); self.assertTrue(closed.wait(2))
            self.assertEqual(self.close_errors, [])
            self.assertTrue(self.finished.is_set())
        finally:
            self.release.set(); client.close(); server.shutdown()
            for closer in self.closers: closer.join(5)
            server.server_close(); loop.join(3)
            self.assertFalse(loop.is_alive())


if __name__ == '__main__':
    unittest.main()
