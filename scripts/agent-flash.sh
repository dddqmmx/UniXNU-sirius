#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Wait for (or trigger) the Xiaomi Mi 8 SE (sirius) ABL fastboot (product: sirius),
# flash a whitelisted partition with retry, reboot into XNU, and auto-clear any
# intermediate U-Boot fastboot (product: mtp) bounce.
#
# Safety whitelist:
#   - "vendor" is always allowed.
#   - "boot" is only allowed with --allow-boot (or --boot-image).
#   - "userdata" is only allowed with --allow-userdata.
#   - All other partitions are unconditionally refused.
set -euo pipefail

PART="vendor"
IMG=""
BOOT_IMG=""
USERDATA_WIN=""
ALLOW_BOOT=0
ALLOW_USERDATA=0
WAIT_TIMEOUT=1800
LOG_FILE="${AGENT_FLASH_LOG:-$HOME/.unixnu/flash-when-fastboot.log}"

usage() {
    cat <<'EOF' >&2
Usage: agent-flash.sh [options] <IMAGE>
  --partition <name>        Target partition (default: vendor; whitelist: vendor, boot, userdata)
  --boot-image <path>       Also flash boot with this U-Boot image before flashing vendor
  --userdata-window <path>  Also flash userdata with this sparse Data-window image (data-window.simg)
  --allow-boot              Allow flashing the boot partition
  --allow-userdata          Allow flashing the userdata partition (sparse image)
  --timeout <seconds>       Max seconds to wait for ABL fastboot (default: 1800)
  --log <path>              Log file path (default: ~/.unixnu/flash-when-fastboot.log)
EOF
    exit 2
}

while [ $# -gt 0 ]; do
    case "$1" in
        --partition)        PART="${2:?}"; shift 2 ;;
        --boot-image)       BOOT_IMG="${2:?}"; ALLOW_BOOT=1; shift 2 ;;
        --userdata-window)  USERDATA_WIN="${2:?}"; shift 2 ;;
        --allow-boot)       ALLOW_BOOT=1; shift ;;
        --allow-userdata)   ALLOW_USERDATA=1; shift ;;
        --timeout)          WAIT_TIMEOUT="${2:?}"; shift 2 ;;
        --log)              LOG_FILE="${2:?}"; shift 2 ;;
        -h|--help)          usage ;;
        -*)                 echo "Unknown option: $1" >&2; usage ;;
        *)                  IMG="$1"; shift ;;
    esac
done

[ -n "$IMG" ] || usage
[ -f "$IMG" ] || { echo "agent-flash: image not found: $IMG" >&2; exit 1; }
if [ -n "$USERDATA_WIN" ]; then
    [ "$ALLOW_USERDATA" = 1 ] || { echo "agent-flash: --userdata-window requires --allow-userdata" >&2; exit 1; }
    [ -f "$USERDATA_WIN" ] || { echo "agent-flash: Data image not found: $USERDATA_WIN" >&2; exit 1; }
fi
[[ "$WAIT_TIMEOUT" =~ ^[1-9][0-9]*$ ]] || { echo "agent-flash: timeout must be a positive integer" >&2; exit 1; }

case "$PART" in
    vendor) ;;
    boot)
        [ "$ALLOW_BOOT" = 1 ] || { echo "agent-flash: refusing to flash 'boot' without --allow-boot" >&2; exit 1; }
        ;;
    userdata)
        [ "$ALLOW_USERDATA" = 1 ] || { echo "agent-flash: refusing to flash 'userdata' without --allow-userdata" >&2; exit 1; }
        ;;
    *)
        echo "agent-flash: SAFETY REFUSAL: partition '$PART' is not in whitelist (vendor, boot, userdata)" >&2
        exit 1
        ;;
esac

mkdir -p "$(dirname "$LOG_FILE")"
note() {
    local line="[$(date '+%Y-%m-%d %H:%M:%S')] $*"
    echo "$line" | tee -a "$LOG_FILE"
}

IMG_SHA=$(sha256sum "$IMG" | awk '{print $1}')
note "=== agent-flash start: part=$PART img=$IMG sha256=$IMG_SHA ==="
if [ -n "$BOOT_IMG" ]; then
    [ -f "$BOOT_IMG" ] || { echo "agent-flash: boot image not found: $BOOT_IMG" >&2; exit 1; }
    BOOT_SHA=$(sha256sum "$BOOT_IMG" | awk '{print $1}')
    note "also armed to flash boot=$BOOT_IMG sha256=$BOOT_SHA"
fi

send_fastboot() {
    timeout 3 python3 -I - "$1" <<'PY'
import os
import sys
fd = os.open(sys.argv[1], os.O_WRONLY | os.O_NOCTTY | os.O_NONBLOCK)
try:
    os.write(fd, b'FASTBOOT\n')
finally:
    os.close(fd)
PY
}

# Only the SIRIUS01 console is a control target.
for link in /dev/serial/by-id/*SIRIUS01*; do
    tty_dev=$(readlink -f "$link")
    if [ -c "$tty_dev" ] && [ -w "$tty_dev" ]; then
        note "XNU console detected; sending FASTBOOT command"
        send_fastboot "$tty_dev" >> "$LOG_FILE" 2>&1 || true
    fi
done

deadline=$(( $(date +%s) + WAIT_TIMEOUT ))
flashed=0

while [ "$(date +%s)" -lt "$deadline" ]; do
    dev_line=$(timeout 6 fastboot devices 2>/dev/null | tr -d '\r' | head -n 1 || true)
    if [ -z "$dev_line" ]; then
        sleep 3
        continue
    fi

    sn=$(echo "$dev_line" | awk '{print $1}')
    product=$(timeout 10 fastboot -s "$sn" getvar product 2>&1 | tr -d '\r' | awk -F': ' '/^product:/{print $2; exit}' || true)
    note "fastboot device sn=$sn product=${product:-unknown}"

    if [ "$product" != "sirius" ]; then
        if [ "$product" != "mtp" ]; then
            note "unrecognized product; leaving device untouched"
            sleep 3
            continue
        fi
        note "U-Boot fastboot detected; attempting configured ABL transition"
        timeout 8 fastboot -s "$sn" oem "run:run abl_fastboot" >> "$LOG_FILE" 2>&1 || true
        sleep 6
        continue
    fi

    if [ -n "$BOOT_IMG" ]; then
        for attempt in 1 2 3; do
            note "flashing boot with $BOOT_IMG (attempt $attempt/3)"
            if timeout 120 fastboot -s "$sn" flash boot "$BOOT_IMG" >> "$LOG_FILE" 2>&1; then
                note "boot FLASHED OK"
                BOOT_IMG=""
                break
            fi
            sleep 3
        done
        [ -z "$BOOT_IMG" ] || { note "ERROR: boot flash failed; stopping before vendor"; exit 1; }
    fi

    if [ -n "$USERDATA_WIN" ]; then
        for attempt in 1 2 3; do
            note "flashing userdata Data window with $USERDATA_WIN (attempt $attempt/3)"
            if timeout 180 fastboot -s "$sn" -S 512M flash userdata "$USERDATA_WIN" >> "$LOG_FILE" 2>&1; then
                note "userdata Data window FLASHED OK"
                USERDATA_WIN=""
                break
            fi
            sleep 3
        done
        [ -z "$USERDATA_WIN" ] || { note "ERROR: Data flash failed; stopping before vendor"; exit 1; }
    fi

    for attempt in 1 2 3; do
        note "flashing $PART with $IMG (attempt $attempt/3)"
        flash_args=(-s "$sn")
        if [ "$PART" = "userdata" ]; then
            flash_args+=(-S 512M)
        fi
        if timeout 900 fastboot "${flash_args[@]}" flash "$PART" "$IMG" >> "$LOG_FILE" 2>&1; then
            note "FLASHED $PART OK (sha256=$IMG_SHA)"
            flashed=1
            break
        fi
        note "flash attempt $attempt failed; retrying in 4s"
        sleep 4
    done

    [ "$flashed" = 1 ] || { note "ERROR: flash failed after 3 attempts"; exit 1; }

    note "rebooting from ABL fastboot into U-Boot -> XNU"
    timeout 30 fastboot -s "$sn" reboot >> "$LOG_FILE" 2>&1 || true
    break
done

if [ "$flashed" != 1 ]; then
    note "ERROR: timed out after ${WAIT_TIMEOUT}s waiting for ABL fastboot"
    exit 1
fi

# Step 2: Watch for 25s in case a stale BCB 'uboot-fastboot' causes U-Boot fastboot (product: mtp)
# to intercept the first boot; if it appears, reboot it immediately so the phone enters XNU.
bounce_deadline=$(( $(date +%s) + 25 ))
while [ "$(date +%s)" -lt "$bounce_deadline" ]; do
    if ls /dev/ttyACM* >/dev/null 2>&1; then
        note "XNU CDC-ACM console is up; boot into XNU confirmed"
        exit 0
    fi
    dev_line=$(timeout 4 fastboot devices 2>/dev/null | tr -d '\r' | head -n 1 || true)
    if [ -n "$dev_line" ]; then
        sn=$(echo "$dev_line" | awk '{print $1}')
        product=$(timeout 6 fastboot -s "$sn" getvar product 2>&1 | tr -d '\r' | awk -F': ' '/^product:/{print $2; exit}' || true)
        if [ "$product" = "mtp" ]; then
            note "clearing intermediate U-Boot fastboot ($product) -> rebooting into XNU"
            timeout 15 fastboot -s "$sn" reboot >> "$LOG_FILE" 2>&1 || true
            sleep 4
        fi
    fi
    sleep 2
done

note "=== agent-flash finished ==="
