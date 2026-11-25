#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Wait for sirius to come back to ABL fastboot on its own, and report how long it took.
#
# The kernel resets through PSCI after it has written the PM660 bootloader reason, so the ABL
# stops in fastboot instead of booting the system again. This waits for `fastboot devices` to see
# the board again and prints the elapsed time: the time answers "how far did the kernel get"
# (a band marker or a console capture answers it more precisely when the transport works).
#
#   scripts/ios26-await-return.sh [--timeout SECONDS] [--serial SN]
set -euo pipefail

TIMEOUT=300
SERIAL="${SIRIUS_SERIAL:-21e05050}"
while [ $# -gt 0 ]; do
    case "$1" in
        --timeout) TIMEOUT="${2:?}"; shift 2 ;;
        --serial)  SERIAL="${2:?}"; shift 2 ;;
        -h|--help) sed -n '2,9p' "$0"; exit 0 ;;
        *) echo "ios26-await-return: unknown option $1" >&2; exit 2 ;;
    esac
done
[[ "$TIMEOUT" =~ ^[1-9][0-9]*$ ]] || { echo "ios26-await-return: timeout must be positive" >&2; exit 2; }

START=$(date +%s)
while :; do
    if timeout 6 fastboot devices 2>/dev/null | grep -q "^$SERIAL"; then
        ELAPSED=$(( $(date +%s) - START ))
        echo "fastboot back after ${ELAPSED}s (serial $SERIAL)"
        exit 0
    fi
    if [ $(( $(date +%s) - START )) -ge "$TIMEOUT" ]; then
        echo "no fastboot return within ${TIMEOUT}s; the phone needs a manual power cycle" >&2
        exit 1
    fi
    sleep 2
done
