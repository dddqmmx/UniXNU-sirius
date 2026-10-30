#!/usr/bin/env python3
"""Exercise the real shell loop in a temporary checkout with no hardware tools."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


REPO = Path(__file__).resolve().parents[2]


class AgentLoopTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        scripts = self.repo / 'scripts'
        scripts.mkdir()
        shutil.copyfile(REPO / 'scripts/agent-loop.sh', scripts / 'agent-loop.sh')
        # These replace every hardware entry point, including on the unfixed loop.
        (scripts / 'agent-flash.sh').write_text('''printf 'flash stub\\n' >> "$CALLS"
printf '%s\\n' "$@" >> "$CALLS"
''')
        (scripts / 'capture-usb-console.py').write_text('''import json, os, pathlib, sys
with open(os.environ['CALLS'], 'a') as calls:
    calls.write('capture stub\\n')
if os.environ.get('WRITE_SUMMARY', '1') == '1':
    path = pathlib.Path(sys.argv[sys.argv.index('--summary') + 1])
    path.write_text(os.environ['SUMMARY'])
sys.exit(int(os.environ.get('CAPTURE_STATUS', '0')))
''')
        self.calls = self.repo / 'calls'
        self.image = self.repo / "payload's image.img"
        self.image.write_bytes(b'test payload')
        self.env = os.environ.copy()
        for key in ('GOAL', 'ROUNDS', 'RESET_AFTER', 'BOOT_IMG', 'USERDATA_WIN'):
            self.env.pop(key, None)
        self.env.update(UNIXNU_DIR=str(self.repo / 'build'), CALLS=str(self.calls))

    def run_loop(self, verdict='ui-processes-started', args=(), **env):
        self.env['SUMMARY'] = json.dumps({'verdict': verdict, 'duration_s': 10,
                                          'key_spawns': ['SpringBoard@1.0s']})
        self.env.update(env)
        return subprocess.run(['bash', str(self.repo / 'scripts/agent-loop.sh'),
                               '--image', str(self.image), *args], env=self.env,
                              text=True, capture_output=True, timeout=10)

    def test_default_goal_is_process_stage(self):
        result = self.run_loop()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("goal 'ui-processes-started' reached", result.stdout)
        self.assertNotIn('ios-ui-up', result.stdout)

    def test_unmet_goal_returns_nonzero_after_all_rounds(self):
        result = self.run_loop('early-boot-complete', args=('--rounds', '2'))
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.calls.read_text().count('capture stub'), 2)
        self.assertIn('not reached', result.stderr)

    def test_failures_and_warning_do_not_meet_default_goal(self):
        for verdict in ('panic', 'storage-failure', 'panic-warning', 'no-output'):
            with self.subTest(verdict=verdict):
                result = self.run_loop(verdict)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn('reached!', result.stdout)

    def test_failure_detail_is_not_hidden_by_spawns(self):
        self.env['SUMMARY'] = ''
        # Override the value set by run_loop through its explicit environment argument.
        result = self.run_loop('storage-failure', SUMMARY=json.dumps({
            'verdict': 'storage-failure', 'duration_s': 10,
            'key_spawns': ['SpringBoard@1.0s'],
            'storage_errors': [{'time_s': 2.0, 'text': 'disk3s1: ENOSPC'}]}))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('ENOSPC', result.stdout)

    def test_missing_invalid_summary_and_capture_failure_do_not_succeed(self):
        for env in ({'WRITE_SUMMARY': '0'}, {'SUMMARY': '{broken'}, {'CAPTURE_STATUS': '1'}):
            with self.subTest(env=env):
                self.assertNotEqual(self.run_loop(**env).returncode, 0)

    def test_explicit_goal_and_environment_goal(self):
        result = self.run_loop('early-boot-complete', args=('--goal', 'early-boot-complete'))
        self.assertEqual(result.returncode, 0, result.stderr)
        result = self.run_loop('root-mounted', GOAL='root-mounted')
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_unknown_option_is_rejected_before_side_effects(self):
        result = self.run_loop(args=('--duraton', '1'))
        self.assertEqual(result.returncode, 2)
        self.assertIn('unknown option', result.stderr)
        self.assertFalse(self.calls.exists())
        self.assertFalse((self.repo / 'docs').exists())

    def test_invalid_goals_and_rounds_are_rejected_before_side_effects(self):
        for args in (('--goal', 'ios-ui-up'), ('--goal', 'panic'), ('--goal', 'typo'),
                     ('--rounds', '0'), ('--rounds', '-1'), ('--rounds', 'nope')):
            with self.subTest(args=args):
                self.assertEqual(self.run_loop(args=args).returncode, 2)
                self.assertFalse(self.calls.exists())

    def test_missing_option_value_and_extra_positional_are_rejected(self):
        for args in (('--goal',), ('--image',), ('extra.img',), ('--goal', '--reset-after')):
            with self.subTest(args=args):
                self.assertEqual(self.run_loop(args=args).returncode, 2)
                self.assertFalse(self.calls.exists())

    def test_userdata_requires_explicit_authorization_before_side_effects(self):
        for args, env in [(('--userdata-window', 'data.img'), {}),
                          ((), {'USERDATA_WIN': 'data.img', 'ALLOW_USERDATA': '1'})]:
            with self.subTest(args=args, env=env):
                result = self.run_loop(args=args, **env)
                self.assertEqual(result.returncode, 2)
                self.assertIn('--allow-userdata', result.stderr)
                self.assertFalse(self.calls.exists())

    def test_userdata_authorization_is_passed_only_for_explicit_first_round(self):
        result = self.run_loop('early-boot-complete', args=(
            '--userdata-window', 'data.img', '--allow-userdata', '--rounds', '2'))
        self.assertEqual(result.returncode, 1)
        calls = self.calls.read_text()
        self.assertEqual(calls.count('--userdata-window'), 1)
        self.assertEqual(calls.count('--allow-userdata'), 1)
        self.assertEqual(calls.count('flash stub'), 2)

    def test_normal_retry_never_flashes_userdata(self):
        self.run_loop('storage-failure', args=('--rounds', '2'))
        calls = self.calls.read_text()
        self.assertNotIn('--userdata-window', calls)
        self.assertNotIn('--allow-userdata', calls)

    def test_positional_image_still_works(self):
        self.env['SUMMARY'] = json.dumps({'verdict': 'ui-processes-started'})
        result = subprocess.run(['bash', str(self.repo / 'scripts/agent-loop.sh'), str(self.image)],
                                env=self.env, text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
