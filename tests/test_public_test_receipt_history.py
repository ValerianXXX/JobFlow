"""Two exact published test receipts must not authorize any other log history."""
import os
from pathlib import Path
import shutil
import subprocess
import unittest

from _support import PROJECT, project_temp
from jobops.public_release import validate_public_paths, verify_public_history
from jobops.release_toolchain import sanitized_command_environment


PREFIX = 'evaluations/2026-11-01/assertion-validation/'
NAMES = ['earlier-runtime-terminal-failure.log', 'public-docs-followup.log']


def payload(name):
    canonical = PROJECT / PREFIX / name.replace('.log', '.txt')
    return (canonical if canonical.exists() else PROJECT / PREFIX / name).read_bytes()


class PublishedReceiptHistoryTests(unittest.TestCase):
    def history(self, snapshots):
        git = Path(os.environ.get('JOBFLOW_RELEASE_GIT_PATH') or shutil.which('git')).resolve(strict=True)
        with project_temp() as root:
            env = sanitized_command_environment('git', executable=git, project=root)
            def run(*args):
                subprocess.run([str(git), *args], cwd=root, env=env, check=True, capture_output=True)
            run('init', '-b', 'main')
            run('config', 'user.name', 'Synthetic Maintainer')
            run('config', 'user.email', 'maintainer@users.noreply.github.com')
            run('config', 'core.autocrlf', 'false')
            for index, snapshot in enumerate(snapshots):
                for relative, content in snapshot.items():
                    path = root / relative
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(content)
                    run('add', '-f', '--', relative)
                run('commit', '-m', f'synthetic receipt observation {index}')
            return verify_public_history(root, git_path=git)

    def test_exact_public_receipts_have_reviewable_history_without_relaxing_current_paths(self):
        paths = {PREFIX + name: payload(name) for name in NAMES}
        self.assertEqual(len(validate_public_paths(paths)), 2)
        result = self.history([paths])
        self.assertEqual(result['status'], 'PASS', result['findings'])

    def test_same_public_bytes_at_another_log_path_stay_blocked(self):
        result = self.history([{'public-copy.log': payload(NAMES[0])}])
        self.assertEqual(result['status'], 'FAIL')
        self.assertIn('private_or_generated_file_tracked', {r['kind'] for r in result['findings']})

    def test_a_changed_historical_version_stays_blocked_after_safe_bytes_return(self):
        relative, original = PREFIX + NAMES[0], payload(NAMES[0])
        changed = original + b'\n' + b'sk-' + b'X' * 24
        result = self.history([{relative: original}, {relative: changed}, {relative: original}])
        self.assertEqual(result['status'], 'FAIL')
        self.assertIn('private_or_generated_file_tracked', {r['kind'] for r in result['findings']})
