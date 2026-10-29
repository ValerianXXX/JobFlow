from copy import deepcopy
import importlib.util
import unittest
from unittest.mock import patch

from _support import PROJECT

spec = importlib.util.spec_from_file_location('complete_claim_replay', PROJECT / 'scripts/replay-complete-claims.py')
replay = importlib.util.module_from_spec(spec)
spec.loader.exec_module(replay)


class CompleteClaimReplayTests(unittest.TestCase):
    def test_visual_observation_is_fixed_and_arbitrary_paths_are_rejected(self):
        value = replay.load_observation('visual-followup')
        self.assertEqual(len(value['arms']), 3)
        self.assertEqual(len(value['adapter_requests']), 6)
        self.assertTrue(all(a['mode'] == 'visual' for a in value['arms']))
        with self.assertRaises(ValueError):
            replay.load_observation('../private')

    def test_frozen_observation_and_no_network_fallback(self):
        observed = replay.load_observation()
        self.assertEqual(len(observed['arms']), 6)
        self.assertEqual(len(observed['adapter_requests']), 9)
        row = observed['arms'][0]['responses'][0]
        request = {'task': row['task'], 'line_numbered_document': [f"{i}\t{text}" for i, text in enumerate(row['source_lines'], row['line_number_start'])]}
        engine = replay.ReplayEngine([row])
        with patch.object(replay.pilot.layout.WSLHermesCLIEngine, '_invoke') as provider:
            result = engine._invoke(request)
            self.assertEqual(result['result']['content'], row['result_content'])
            with self.assertRaises(replay.pilot.layout.JobOpsError) as error:
                engine._invoke(request)
            self.assertEqual(error.exception.code, 'EVALUATION_REPLAY_EXHAUSTED')
            provider.assert_not_called()

    def test_changed_source_lines_offsets_tasks_and_tools_are_not_replayed(self):
        row = {'task': 'SYNTHETIC', 'source_lines': ['Source'], 'line_number_start': 2, 'tool_calls': 0, 'result_content': '{}'}
        for request in ({'task': 'OTHER', 'line_numbered_document': ['2\tSource']},
                        {'task': 'SYNTHETIC', 'line_numbered_document': ['2\tChanged']},
                        {'task': 'SYNTHETIC', 'line_numbered_document': ['1\tSource']}):
            with self.subTest(request=request), self.assertRaises(replay.pilot.layout.JobOpsError) as error:
                replay.ReplayEngine([row])._invoke(request)
            self.assertEqual(error.exception.code, 'EVALUATION_REPLAY_MISMATCH')
        unsafe = {**deepcopy(row), 'tool_calls': 1}
        with self.assertRaises(replay.pilot.layout.JobOpsError):
            replay.ReplayEngine([unsafe])._invoke({'task': 'SYNTHETIC'})


if __name__ == '__main__':
    unittest.main()
