#!/usr/bin/env python3
"""Check refusal and prerequisite-failure paths without accessing hardware."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'agent-flash.sh'


class FlashTests(unittest.TestCase):
    def run_flash(self, options, fail_partition=None):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image = root / 'payload.img'
            image.write_bytes(b'test')
            calls = root / 'calls'
            mock = root / 'fastboot'
            mock.write_text('''#!/usr/bin/env python3
import os,sys
args=sys.argv[1:]
with open(os.environ['FLASH_TEST_CALLS'],'a') as f:
 f.write(' '.join(args)+'\\n')
if args==['devices']:
 print('testserial\\tfastboot')
elif 'getvar' in args:
 print('product: sirius',file=sys.stderr)
elif 'flash' in args and args[args.index('flash')+1]==os.environ.get('FLASH_TEST_FAIL'):
 sys.exit(1)
''')
            mock.chmod(0o755)
            sleep = root / 'sleep'
            sleep.write_text('#!/bin/sh\nexit 0\n')
            sleep.chmod(0o755)
            readlink = root / 'readlink'
            readlink.write_text('#!/bin/sh\nprintf "/dev/null\\n"\n')
            readlink.chmod(0o755)
            env = dict(os.environ, PATH=str(root) + ':' + os.environ['PATH'],
                       FLASH_TEST_CALLS=str(calls), FLASH_TEST_FAIL=fail_partition or '',
                       AGENT_FLASH_LOG=str(root / 'flash.log'))
            args = [option.replace('{image}', str(image)) for option in options]
            result = subprocess.run(['bash', str(SCRIPT), *args, str(image)], env=env,
                                    capture_output=True, text=True, timeout=15)
            return result, calls.read_text() if calls.exists() else ''

    def test_flashes_vendor_when_no_xnu_console_is_attached(self):
        result, calls = self.run_flash(['--timeout', '5'])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('flash vendor ', calls)
        self.assertIn('reboot', calls)

    def test_unknown_partition_refused_before_usb_access(self):
        result, calls = self.run_flash(['--partition', 'system'])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('SAFETY REFUSAL', result.stderr)
        self.assertEqual(calls, '')

    def test_userdata_window_needs_explicit_permission(self):
        result, calls = self.run_flash(['--userdata-window', '{image}'])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('requires --allow-userdata', result.stderr)
        self.assertEqual(calls, '')

    def test_failed_boot_prevents_vendor_flash_and_reboot(self):
        result, calls = self.run_flash(['--boot-image', '{image}', '--timeout', '2'], 'boot')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(calls.count('flash boot '), 3)
        self.assertNotIn('flash vendor', calls)
        self.assertNotIn('reboot', calls)

    def test_failed_data_prevents_vendor_flash_and_reboot(self):
        result, calls = self.run_flash(['--userdata-window', '{image}', '--allow-userdata',
                                        '--timeout', '2'], 'userdata')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(calls.count('flash userdata '), 3)
        self.assertNotIn('flash vendor', calls)
        self.assertNotIn('reboot', calls)


if __name__ == '__main__':
    unittest.main()
