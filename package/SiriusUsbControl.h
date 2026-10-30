// SPDX-License-Identifier: MIT
#pragma once
#include <stdint.h>
#include <string.h>

static inline uint32_t
sirius_usb_received_bytes(uint32_t size, uint32_t control, uint32_t event_status,
    uint32_t capacity)
{
    uint32_t remaining = size & 0x00ffffffu;
    /* DWC3 writes residual bytes into the TRB, not the completion event. */
    if ((control & 1u) || (event_status & 1u) || (size >> 28) || remaining > capacity) {
        return 0;
    }
    return capacity - remaining;
}

enum sirius_usb_command {
    SIRIUS_USB_NO_COMMAND,
    SIRIUS_USB_FASTBOOT,
    SIRIUS_USB_REBOOT,
};

struct sirius_usb_control {
    char line[9];
    unsigned used;
    unsigned overflow;
};

static inline enum sirius_usb_command
sirius_usb_control_feed(struct sirius_usb_control *parser, char value)
{
    if (value == '\r') {
        return SIRIUS_USB_NO_COMMAND;
    }
    if (value != '\n') {
        if (parser->used < sizeof(parser->line) - 1) {
            parser->line[parser->used++] = value;
        } else {
            parser->overflow = 1;
        }
        return SIRIUS_USB_NO_COMMAND;
    }
    enum sirius_usb_command command = SIRIUS_USB_NO_COMMAND;
    parser->line[parser->used] = '\0';
    if (!parser->overflow) {
        if (!strcmp(parser->line, "FASTBOOT")) {
            command = SIRIUS_USB_FASTBOOT;
        } else if (!strcmp(parser->line, "REBOOT")) {
            command = SIRIUS_USB_REBOOT;
        }
    }
    parser->used = 0;
    parser->overflow = 0;
    return command;
}
