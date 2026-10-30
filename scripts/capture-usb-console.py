#!/usr/bin/env python3
"""Capture the sirius XNU USB console (CDC-ACM over DWC3).

The kernel streams its console mirror ring on a bulk IN endpoint, so the host sees the boot log
from the very first line once the gadget enumerates. The port is a plain CDC-ACM tty, so no USB
library is needed: termios raw mode and a read loop are enough.

    python3 scripts/capture-usb-console.py --follow --wait 120 --duration 280 \\
        --output out/boot.log --timeline out/boot.timeline --summary out/boot.summary.json
"""
import argparse
import glob
import json
import os
import re
import sys
import time

try:
    import select
    import termios
    import tty
except ImportError:  # Windows: the port is a COM port, read through kernel32
    import ctypes
    import winreg
    from ctypes import wintypes
    termios = None


def find_port():
    if termios is None:
        key = r'SYSTEM\CurrentControlSet\Enum\USB\VID_1209&PID_0001'
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key) as device:
                for index in range(winreg.QueryInfoKey(device)[0]):
                    instance = winreg.EnumKey(device, index)
                    try:
                        with winreg.OpenKey(device, instance + r'\Device Parameters') as params:
                            name = winreg.QueryValueEx(params, 'PortName')[0]
                    except OSError:
                        continue
                    if 'SIRIUS' in instance.upper() and name in Console.present_ports():
                        return name
        except OSError:
            pass
        return None
    # Prefer /dev/ttyACM* directly (or the realpath of /dev/serial/by-id/*) and wait until udev
    # has applied 0666 permissions (99-sirius-console.rules) so os.open never hits EACCES.
    candidates = []
    for candidate in sorted(glob.glob('/dev/serial/by-id/*')):
        if 'SIRIUS' in candidate.upper() or 'sirius' in candidate:
            candidates.append(os.path.realpath(candidate))
    for candidate in sorted(glob.glob('/dev/ttyACM*')):
        real = os.path.realpath(candidate)
        if real not in candidates:
            candidates.append(real)
    for path in candidates:
        if os.path.exists(path) and os.access(path, os.R_OK | os.W_OK):
            return path
    return candidates[0] if candidates else None


def wait_for_port(timeout):
    deadline = time.time() + timeout
    while time.time() < deadline:
        port = find_port()
        if port is not None and (termios is None or os.access(port, os.R_OK | os.W_OK)):
            return port
        time.sleep(0.2)
    return find_port()


class Console:
    """The open console port: a raw termios tty on Linux, an unconfigured COM handle on Windows."""

    @staticmethod
    def present_ports():
        names = set()
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r'HARDWARE\DEVICEMAP\SERIALCOMM') as comm:
                for index in range(winreg.QueryInfoKey(comm)[1]):
                    names.add(winreg.EnumValue(comm, index)[1])
        except OSError:
            pass
        return names

    def __init__(self, port):
        if termios is None:
            self.kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
            self.kernel32.CreateFileW.restype = wintypes.HANDLE
            handle = self.kernel32.CreateFileW('\\\\.\\' + port, 0xC0000000, 0, None, 3, 0, None)
            if handle == wintypes.HANDLE(-1).value:
                raise ctypes.WinError(ctypes.get_last_error())
            self.handle = handle
            timeouts = (wintypes.DWORD * 5)(0xFFFFFFFF, 0xFFFFFFFF, 500, 0, 1000)
            self.kernel32.SetCommTimeouts(self.handle, ctypes.byref(timeouts))
            self.buffer = ctypes.create_string_buffer(4096)
            return
        real = os.path.realpath(port)
        self.fd = os.open(real, os.O_RDWR | os.O_NOCTTY)
        tty.setraw(self.fd)
        attrs = termios.tcgetattr(self.fd)
        attrs[2] &= ~(termios.PARENB | termios.CSTOPB | termios.CSIZE)
        attrs[2] |= termios.CS8
        attrs[2] &= ~termios.CRTSCTS
        attrs[4] = attrs[5] = termios.B115200
        termios.tcsetattr(self.fd, termios.TCSANOW, attrs)

    def write(self, data):
        if termios is None:
            done = wintypes.DWORD()
            if not self.kernel32.WriteFile(self.handle, data, len(data), ctypes.byref(done), None):
                raise ctypes.WinError(ctypes.get_last_error())
        else:
            os.write(self.fd, data)

    def read(self):
        """Up to 4096 bytes; b'' after half a second of silence, None at end-of-file."""
        if termios is None:
            done = wintypes.DWORD()
            if not self.kernel32.ReadFile(self.handle, self.buffer, 4096, ctypes.byref(done), None):
                raise ctypes.WinError(ctypes.get_last_error())
            return self.buffer.raw[:done.value]
        ready, _, _ = select.select([self.fd], [], [], 0.5)
        if not ready:
            return b''
        return os.read(self.fd, 4096) or None

    def close(self):
        if termios is None:
            self.kernel32.CloseHandle(self.handle)
        else:
            os.close(self.fd)


def build_summary(ordered_lines, total_bytes, duration_s):
    """Summarize observed evidence, not UI visibility or continued system health.

    Event counts describe distinct captured lines (the console ring is deduplicated).
    Frame copies are cumulative driver counters, not proof of a visible iOS frame.
    """
    summary = {
        'total_bytes': total_bytes,
        'distinct_lines': len(ordered_lines),
        'duration_s': round(duration_s, 1),
        'spmi_reset': None,
        'emmc_fast_khz': None,
        'root_mounted': False,
        'systemos_mounted': False,
        'data_volume': None,
        'launchd_started': False,
        'early_boot_complete_s': None,
        'display_registered': None,
        'touch_status': None,
        'gpu_status': None,
        'cpufreq_votes_mhz': {},
        'key_spawns': [],
        'highest_beat': -1,
        'panic_text': None,
        'panic_warnings': [],
        'fatal_panics': [],
        'enospc_count': 0,
        'no_space_left_count': 0,
        'storage_error_count': 0,
        'storage_errors': [],
        'frame_copy_count': None,
        'frame_copy_samples': [],
        'process_launches': [],
        'process_exits': [],
        'process_crashes': [],
        'ui_visible': None,
        'boot_stage': 'no-output' if not ordered_lines else 'kernel-up',
    }
    in_panic = False
    panic_lines = []
    panic_buffers = []
    current_panic = []
    seen_spawns = set()
    for ts, text in ordered_lines:
        if text == 'NEMMC PANIC TEXT BEGIN':
            if current_panic:
                panic_buffers.append(current_panic)
                current_panic = []
            in_panic = True
            continue
        if text == 'NEMMC PANIC TEXT END':
            panic_buffers.append(current_panic)
            current_panic = []
            in_panic = False
            continue
        # not_in_kdp is a debugger-entry warning, even when mirrored through the
        # panic buffer. Detect fatal evidence independently so warnings cannot
        # suppress it in this or a later buffer.
        fatal = re.search(r'panic\(cpu\b|\bpanic:\s|Debugger message:\s*panic\b|'
                          r'\bKernel (?:panic|trap)\b|\b[A-Za-z0-9_]+\[\d+\] exited EL[01]\b',
                          text, re.IGNORECASE)
        if in_panic:
            panic_lines.append(text)
            current_panic.append({'time_s': round(ts, 1), 'text': text})
        elif re.search(r'\bnot_in_kdp\b', text):
            summary['panic_warnings'].append({'time_s': round(ts, 1), 'text': text})
        if fatal:
            summary['fatal_panics'].append({'time_s': round(ts, 1), 'text': text})
        enospc = bool(re.search(r'\bENOSPC\b', text, re.IGNORECASE))
        no_space = 'no space left on device' in text.lower()
        summary['enospc_count'] += int(enospc)
        summary['no_space_left_count'] += int(no_space)
        if enospc or no_space:
            summary['storage_errors'].append({'time_s': round(ts, 1), 'text': text})
        m = re.fullmatch(r'NEMMC fb copies (\d+)', text)
        if m:
            count = int(m.group(1))
            summary['frame_copy_count'] = count
            summary['frame_copy_samples'].append({'time_s': round(ts, 1), 'count': count})
        if text.startswith('SIRIUS RESET:'):
            summary['spmi_reset'] = text
        m = re.search(r'EMMC fast rate=(\d+) kHz', text)
        if m:
            summary['emmc_fast_khz'] = int(m.group(1))
        if 'mount-complete volume DawnUpdate' in text and 'OS,' in text:
            summary['root_mounted'] = True
        if 'mount-complete volume DawnUpdate' in text and 'SystemCryptex' in text:
            summary['systemos_mounted'] = True
        if text.startswith('NEMMC data volume ('):
            summary['data_volume'] = text
        elif 'mount-complete volume OwnerData' in text and not summary['data_volume']:
            summary['data_volume'] = text
        if 'load_init_program: attempting to load /sbin/launchd' in text or text.startswith('sirius: init syscall '):
            summary['launchd_started'] = True
        if 'Early boot complete' in text and summary['early_boot_complete_s'] is None:
            summary['early_boot_complete_s'] = round(ts, 1)
        if 'GOP display registered' in text:
            summary['display_registered'] = text
        if text.startswith('TOUCH '):
            summary['touch_status'] = text
        if text.startswith('GPU '):
            summary['gpu_status'] = text
        m = re.match(r'^SPAWN (\d+)s exec (\d+) (\S+)', text)
        if m:
            proc = m.group(3)
            summary['process_launches'].append({
                'time_s': round(ts, 1), 'boot_s': int(m.group(1)),
                'pid': int(m.group(2)), 'name': proc,
            })
            if proc in ('SpringBoard', 'backboardd', 'Setup', 'PosterBoard', 'iconservicesagent', 'datamigrator') and proc not in seen_spawns:
                seen_spawns.add(proc)
                summary['key_spawns'].append(f'{proc}@{ts:.1f}s')
        m = re.match(r'^SPAWN (\d+)s exit (\d+) (\S+) status=([0-9a-fA-F]+) '
                     r'ns=(\d+) code=([0-9a-fA-F]+)', text)
        if m:
            event = {
                'time_s': round(ts, 1), 'boot_s': int(m.group(1)),
                'pid': int(m.group(2)), 'name': m.group(3),
                'status': int(m.group(4), 16), 'namespace': int(m.group(5)),
                'code': int(m.group(6), 16),
            }
            summary['process_exits'].append(event)
            # status is a hex wait status: a nonzero exit code alone is not a crash.
            signal = event['status'] & 0x7f
            if signal not in (0, 0x7f):
                summary['process_crashes'].append(dict(event, signal=signal))
        m = re.search(r'Process\[(\d+)\] crashed:\s*(\S+)', text)
        if m:
            summary['process_crashes'].append({
                'time_s': round(ts, 1), 'pid': int(m.group(1)),
                'name': m.group(2).rstrip('.'), 'text': text,
            })
        m = re.match(r'^NEMMC alive beats/reads ([0-9a-fA-F]{16})', text)
        if m:
            val = int(m.group(1), 16)
            beat = val >> 32
            if beat > summary['highest_beat']:
                summary['highest_beat'] = beat

    if current_panic:
        panic_buffers.append(current_panic)
    for buffer in panic_buffers:
        # A multiline not_in_kdp diagnostic is a warning, unless independent
        # fatal evidence was recognized above. Other panic-buffer contents are
        # conservatively fatal even if capture ended before the END marker.
        warning = any(re.search(r'\bnot_in_kdp\b', event['text']) for event in buffer)
        target = summary['panic_warnings'] if warning else summary['fatal_panics']
        target.extend(event for event in buffer if event['text'].strip() and event not in target)
    if panic_lines:
        summary['panic_text'] = '\n'.join(panic_lines)
    summary['storage_error_count'] = len(summary['storage_errors'])
    if any(s.startswith(('SpringBoard@', 'Setup@', 'backboardd@')) for s in summary['key_spawns']):
        summary['boot_stage'] = 'ui-processes-started'
    elif summary['early_boot_complete_s'] is not None:
        summary['boot_stage'] = 'early-boot-complete'
    elif summary['data_volume']:
        summary['boot_stage'] = 'data-mounted'
    elif summary['launchd_started']:
        summary['boot_stage'] = 'launchd-started'
    elif summary['root_mounted']:
        summary['boot_stage'] = 'root-mounted'

    if summary['fatal_panics']:
        summary['verdict'] = 'panic'
    elif summary['storage_errors']:
        summary['verdict'] = 'storage-failure'
    elif summary['panic_warnings']:
        summary['verdict'] = 'panic-warning'
    else:
        summary['verdict'] = summary['boot_stage']
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', default=None,
                        help='CDC-ACM device, default: the SIRIUS01 port (first /dev/ttyACM*, or its COM port on Windows)')
    parser.add_argument('--wait', type=float, default=60.0, help='seconds to wait for the port')
    parser.add_argument('--duration', type=float, default=0.0,
                        help='seconds from the start of the script (the wait for the port included), 0 = until Ctrl-C')
    parser.add_argument('--output', default='out/sirius-usb-console.log', help='log file (bytes appended)')
    parser.add_argument('--follow', action='store_true',
                        help='keep waiting for the port after EOF or re-enumeration')
    parser.add_argument('--match', action='append', default=[],
                        help='stop as soon as the captured bytes contain this text (repeatable)')
    parser.add_argument('--match-window', type=float, default=4096.0,
                        help='how many trailing bytes the --match patterns are tested against')
    parser.add_argument('--timeline', default=None,
                        help='also write each distinct line once, prefixed with the seconds since the script '
                             'started at which it first arrived; start the script right after the reboot')
    parser.add_argument('--summary', default=None,
                        help='write a structured JSON summary of the boot stages reached')
    parser.add_argument('--reset-to-fastboot', action='store_true',
                        help='send FASTBOOT over CDC-ACM before exiting so the kernel writes PM660 0x88f=0x04 and resets to ABL fastboot')
    parser.add_argument('--idle-stop', type=float, default=0.0,
                        help='stop once no new line has arrived for this many seconds (0 = never); the eMMC '
                             'heartbeat adds one every two seconds while the kernel runs, so silence means it stopped')
    args = parser.parse_args()
    started = time.time()
    timeline = open(args.timeline, 'a', encoding='utf-8') if args.timeline else None
    seen_lines = set()
    ordered_lines = []
    partial = b''
    last_new_line = None

    port = args.port or wait_for_port(args.wait)
    if port is None:
        print(f'no CDC-ACM port appeared within {args.wait:.0f}s', file=sys.stderr)
        return 1

    os.makedirs(os.path.dirname(args.output) or '.', exist_ok=True)
    total = 0
    end = started + args.duration if args.duration else None
    active_console = None
    with open(args.output, 'ab') as log:
        try:
            while end is None or time.time() < end:
                if port is None:
                    if not args.follow:
                        break
                    port = wait_for_port(args.wait)
                    if port is None:
                        break
                print(f'capturing {port} -> {args.output}', file=sys.stderr)
                try:
                    console = Console(port)
                    active_console = console
                except OSError as error:
                    print(f'{port}: {error}', file=sys.stderr)
                    port = None
                    time.sleep(0.5)
                    continue
                next_poke = 0.0
                tail = b''
                stop_capture = False
                try:
                    while end is None or time.time() < end:
                        if time.time() >= next_poke:
                            try:
                                console.write(b'\n')
                            except OSError:
                                pass
                            next_poke = time.time() + 1.0
                        if (args.idle_stop > 0 and last_new_line is not None and
                                time.time() - last_new_line >= args.idle_stop):
                            print(f'no new line for {args.idle_stop:.0f}s, stopping after {total} bytes',
                                  file=sys.stderr)
                            stop_capture = True
                            break
                        chunk = console.read()
                        if chunk is None:
                            break
                        if not chunk:
                            continue
                        log.write(chunk)
                        log.flush()
                        total += len(chunk)
                        lines = (partial + chunk).split(b'\n')
                        partial = lines.pop()
                        for line in lines:
                            line = line.rstrip(b'\r')
                            if line and line not in seen_lines:
                                seen_lines.add(line)
                                last_new_line = time.time()
                                decoded = line.decode('utf-8', 'replace')
                                ordered_lines.append((last_new_line - started, decoded))
                                if timeline is not None:
                                    timeline.write(f'{last_new_line - started:8.1f} {decoded}\n')
                        if timeline is not None:
                            timeline.flush()
                        sys.stdout.write(chunk.decode('utf-8', 'replace'))
                        sys.stdout.flush()
                        tail = (tail + chunk)[-int(args.match_window):] if args.match else b''
                        if args.match and any(pat.encode() in tail for pat in args.match):
                            print(f'matched, stopping after {total} bytes', file=sys.stderr)
                            stop_capture = True
                            break
                except OSError:
                    pass
                finally:
                    if stop_capture and args.reset_to_fastboot and active_console is not None:
                        try:
                            active_console.write(b'FASTBOOT\n')
                            time.sleep(0.2)
                        except OSError:
                            pass
                    console.close()
                    active_console = None
                if stop_capture:
                    break
                port = None if args.follow else port
                if not args.follow:
                    break
            if args.reset_to_fastboot:
                p = find_port()
                if p:
                    try:
                        c = Console(p)
                        c.write(b'FASTBOOT\n')
                        time.sleep(0.2)
                        c.close()
                    except OSError:
                        pass
        except KeyboardInterrupt:
            pass
    if timeline is not None:
        timeline.close()
    summary = build_summary(ordered_lines, total, time.time() - started)
    if args.summary:
        os.makedirs(os.path.dirname(args.summary) or '.', exist_ok=True)
        with open(args.summary, 'w', encoding='utf-8') as sf:
            json.dump(summary, sf, indent=2)
            sf.write('\n')
    print(f'\ncaptured {total} bytes ({len(ordered_lines)} distinct lines, verdict={summary["verdict"]})',
          file=sys.stderr)
    return 0


if __name__ == '__main__':
    sys.exit(main())
