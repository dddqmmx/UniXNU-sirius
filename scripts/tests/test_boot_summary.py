#!/usr/bin/env python3
"""Host-only regressions for evidence-based USB console summaries."""
import importlib.util
from pathlib import Path
import unittest


REPO = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('capture_usb_console', REPO / 'scripts/capture-usb-console.py')
CAPTURE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CAPTURE)


def summarize(*lines):
    return CAPTURE.build_summary([(float(i), text) for i, text in enumerate(lines)], 123, 10)


class BootSummaryTests(unittest.TestCase):
    def test_ui_spawns_only_prove_process_stage(self):
        for proc in ('SpringBoard', 'backboardd', 'Setup'):
            with self.subTest(proc=proc):
                summary = summarize(f'SPAWN 122s exec 27 {proc}')
                self.assertEqual(summary['verdict'], 'ui-processes-started')
                self.assertIsNone(summary['ui_visible'])
                self.assertEqual(summary['process_launches'][0]['name'], proc)

    def test_storage_failure_overrides_spawns_and_early_boot(self):
        for error in ('ENOSPC: ttype DELETE', 'No space left on device(28)'):
            with self.subTest(error=error):
                summary = summarize('Early boot complete', 'SPAWN 122s exec 27 SpringBoard', error)
                self.assertEqual(summary['verdict'], 'storage-failure')
                self.assertEqual(summary['boot_stage'], 'ui-processes-started')
                self.assertEqual(summary['storage_error_count'], 1)
                self.assertEqual(summary['storage_errors'][0]['text'], error)

    def test_panic_overrides_storage_and_process_stage(self):
        for fatal in ('panic(cpu 0 caller 0xffffff): fatal fault', 'Debugger message: panic'):
            with self.subTest(fatal=fatal):
                summary = summarize('SPAWN 122s exec 27 SpringBoard', 'ENOSPC',
                                    'NEMMC PANIC TEXT BEGIN', fatal, 'NEMMC PANIC TEXT END')
                self.assertEqual(summary['verdict'], 'panic')
                self.assertIn(fatal, summary['panic_text'])
                self.assertEqual(summary['storage_error_count'], 1)

    def test_unwrapped_panic_and_truncated_buffer_are_fatal(self):
        for lines in [('panic(cpu 0 caller 0xffffff): fatal fault',),
                      ('NEMMC PANIC TEXT BEGIN', 'watchdogd[127] exited EL0 watchdogd PC=100b3bb0c')]:
            with self.subTest(lines=lines):
                self.assertEqual(summarize(*lines)['verdict'], 'panic')

    def test_warning_buffer_is_not_fatal_or_a_health_claim(self):
        summary = summarize('SPAWN 122s exec 27 SpringBoard', 'NEMMC PANIC TEXT BEGIN',
                            'pmap.c:3514 Assertion failed: not_in_kdp', 'NEMMC PANIC TEXT END')
        self.assertEqual(summary['verdict'], 'panic-warning')
        self.assertEqual(summary['boot_stage'], 'ui-processes-started')
        self.assertEqual(len(summary['panic_warnings']), 1)
        self.assertEqual(summary['fatal_panics'], [])

    def test_multiline_warning_buffer_is_preserved_as_warning(self):
        summary = summarize('NEMMC PANIC TEXT BEGIN', 'debugger entry failed: not_in_kdp',
                            'PC=0xffffff LR=0xffffff', 'NEMMC PANIC TEXT END')
        self.assertEqual(summary['verdict'], 'panic-warning')
        self.assertEqual(len(summary['panic_warnings']), 2)
        self.assertEqual(summary['fatal_panics'], [])

    def test_warning_does_not_hide_fatal_evidence_in_same_or_later_buffer(self):
        for middle in [(), ('NEMMC PANIC TEXT END', 'NEMMC PANIC TEXT BEGIN')]:
            with self.subTest(middle=middle):
                summary = summarize('NEMMC PANIC TEXT BEGIN', 'not_in_kdp: warning', *middle,
                                    'panic(cpu 0 caller 0xffffff): fatal fault', 'NEMMC PANIC TEXT END')
                self.assertEqual(summary['verdict'], 'panic')
        summary = summarize('NEMMC PANIC TEXT BEGIN', 'not_in_kdp: warning',
                            'NEMMC PANIC TEXT END', 'ENOSPC')
        self.assertEqual(summary['verdict'], 'storage-failure')

    def test_frames_launches_exits_and_crashes_are_separate(self):
        summary = summarize('NEMMC fb copies 0', 'SPAWN 122s exec 27 SpringBoard',
                            'NEMMC fb copies 5', 'NEMMC fb copies 6',
                            'SPAWN 123s exit 20 sysstatuscheck status=100 ns=0 code=0',
                            'SPAWN 124s exit 55 backboardd status=6 ns=2 code=6',
                            'SPAWN 125s exit 56 xpcproxy status=0 ns=0 code=0',
                            'Process[148] crashed: SpringBoard. Too many corpses being created.')
        self.assertEqual(summary['frame_copy_count'], 6)
        self.assertEqual([e['count'] for e in summary['frame_copy_samples']], [0, 5, 6])
        self.assertEqual(len(summary['process_launches']), 1)
        self.assertEqual(len(summary['process_exits']), 3)
        self.assertEqual([e['name'] for e in summary['process_crashes']], ['backboardd', 'SpringBoard'])
        self.assertEqual(summary['process_exits'][0]['status'], 0x100)
        self.assertIsNone(summary['ui_visible'])
        self.assertEqual(summary['verdict'], 'ui-processes-started')

    def test_missing_frame_evidence_differs_from_zero(self):
        self.assertIsNone(summarize()['frame_copy_count'])
        self.assertEqual(summarize('NEMMC fb copies 0')['frame_copy_count'], 0)

    def test_existing_stage_evidence_is_preserved(self):
        cases = [([], 'no-output'), (['ordinary kernel log'], 'kernel-up'),
                 (['mount-complete volume DawnUpdate OS, disk2s1'], 'root-mounted'),
                 (['load_init_program: attempting to load /sbin/launchd'], 'launchd-started'),
                 (['mount-complete volume OwnerData disk3s1'], 'data-mounted'),
                 (['Early boot complete'], 'early-boot-complete')]
        for lines, stage in cases:
            with self.subTest(stage=stage):
                self.assertEqual(summarize(*lines)['verdict'], stage)

    def test_recorded_storage_exhaustion_timeline(self):
        path = REPO / 'out/boot-de820c21b7af-20261030-174714.timeline'
        if not path.exists():
            self.skipTest('local recorded timeline is unavailable; minimal regressions still run')
        lines = [(float(ts), text) for line in path.read_text().splitlines()
                 for ts, text in [line.split(maxsplit=1)]]
        summary = CAPTURE.build_summary(lines, path.stat().st_size, lines[-1][0])
        self.assertEqual(summary['verdict'], 'storage-failure')
        self.assertEqual(summary['boot_stage'], 'ui-processes-started')
        self.assertEqual(summary['enospc_count'], 126)
        self.assertEqual(summary['no_space_left_count'], 8)
        self.assertEqual(summary['storage_error_count'], 134)
        self.assertEqual(summary['frame_copy_count'], 6)
        # Three recorded SPAWN lines are interleaved/corrupt; do not invent
        # complete events from those fragments (one launch and two exits).
        self.assertEqual(len(summary['process_launches']), 434)
        self.assertEqual(len(summary['process_exits']), 258)
        self.assertIn('PosterBoard', [e['name'] for e in summary['process_crashes']])
        self.assertIsNone(summary['ui_visible'])


if __name__ == '__main__':
    unittest.main()
