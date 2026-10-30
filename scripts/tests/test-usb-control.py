#!/usr/bin/env python3
"""Exercise the actual host-control C helpers without XNU or USB hardware."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


class UsbControlTests(unittest.TestCase):
    def test_completed_trb_and_command_stream(self):
        source = r'''
#include <assert.h>
#include "SiriusUsbControl.h"
int main(void) {
    assert(sirius_usb_received_bytes(503, 0, 0, 512) == 9);
    assert(sirius_usb_received_bytes(512, 0, 0, 512) == 0);
    assert(sirius_usb_received_bytes(0, 0, 0, 512) == 512);
    assert(sirius_usb_received_bytes(513, 0, 0, 512) == 0);
    assert(sirius_usb_received_bytes(503, 1, 0, 512) == 0);
    assert(sirius_usb_received_bytes(503, 0, 1, 512) == 0);
    assert(sirius_usb_received_bytes(0x100001f7, 0, 0, 512) == 0);
    struct sirius_usb_control parser = {0};
    const char *part = "FAST";
    for (unsigned i = 0; part[i]; ++i)
        assert(sirius_usb_control_feed(&parser, part[i]) == SIRIUS_USB_NO_COMMAND);
    part = "BOOT\r\n";
    for (unsigned i = 0; part[i]; ++i)
        assert(sirius_usb_control_feed(&parser, part[i]) ==
            (part[i] == '\n' ? SIRIUS_USB_FASTBOOT : SIRIUS_USB_NO_COMMAND));
    part = "prefixFASTBOOT\nFASTBOOT-extra\n\nREBOOT\n";
    unsigned reboots = 0;
    for (unsigned i = 0; part[i]; ++i) {
        enum sirius_usb_command cmd = sirius_usb_control_feed(&parser, part[i]);
        assert(cmd != SIRIUS_USB_FASTBOOT);
        reboots += cmd == SIRIUS_USB_REBOOT;
    }
    assert(reboots == 1);
    for (unsigned i = 0; i < 1000; ++i)
        assert(sirius_usb_control_feed(&parser, 'x') == SIRIUS_USB_NO_COMMAND);
    assert(sirius_usb_control_feed(&parser, '\n') == SIRIUS_USB_NO_COMMAND);
    part = "FASTBOOT\n";
    for (unsigned i = 0; part[i]; ++i)
        assert(sirius_usb_control_feed(&parser, part[i]) ==
            (part[i] == '\n' ? SIRIUS_USB_FASTBOOT : SIRIUS_USB_NO_COMMAND));
    return 0;
}
'''
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)
            (path / 'test.c').write_text(source)
            subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror',
                            '-I', str(ROOT / 'package'), str(path / 'test.c'),
                            '-o', str(path / 'test')], check=True)
            subprocess.run([str(path / 'test')], check=True)


if __name__ == '__main__':
    unittest.main()
