#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Unattended debug loop for Xiaomi Mi 8 SE (sirius):
#   flash -> reboot -> capture CDC-ACM console -> structured summary -> verdict ->
#   append evidence to docs/evidence/agent-loop.md -> return to ABL fastboot (when multi-round).
set -euo pipefail

HERE=$(cd -- "$(dirname -- "$0")" && pwd)
REPO=$(cd -- "$HERE/.." && pwd)
UNIXNU_DIR="${UNIXNU_DIR:-$REPO/../UniXNU}"
OUT_DIR="$UNIXNU_DIR/out/sirius/ipados-17.7.11-21H461/sirius"

IMG="$OUT_DIR/payload/sirius-payload-data.img"
IMAGE_SET=0
DURATION="${DURATION:-280}"
WAIT_PORT="${WAIT_PORT:-120}"
IDLE_STOP="${IDLE_STOP:-30}"
ROUNDS="${ROUNDS:-1}"
# Console process evidence does not establish a visible iOS UI.
GOAL="${GOAL:-ui-processes-started}"
RESET_AFTER="${RESET_AFTER:-0}"
BOOT_IMG="${BOOT_IMG:-}"
USERDATA_WIN="${USERDATA_WIN:-}"
ALLOW_USERDATA=0
RECORD_MD="$REPO/docs/evidence/agent-loop.md"

usage_error() {
    printf 'agent-loop: %s\n' "$1" >&2
    exit 2
}

while [ $# -gt 0 ]; do
    case "$1" in
        --image|--boot-image|--userdata-window|--duration|--wait|--idle-stop|--rounds|--goal)
            if [ $# -lt 2 ] || [ -z "$2" ] || [[ "$2" == --* ]]; then
                usage_error "missing value for $1"
            fi ;;
    esac
    case "$1" in
        --image)            [ "$IMAGE_SET" = 0 ] || usage_error 'image specified more than once'
                            IMG="$2"; IMAGE_SET=1; shift 2 ;;
        --boot-image)       BOOT_IMG="$2"; shift 2 ;;
        --userdata-window)  USERDATA_WIN="$2"; shift 2 ;;
        --duration)         DURATION="$2"; shift 2 ;;
        --wait)             WAIT_PORT="$2"; shift 2 ;;
        --idle-stop)        IDLE_STOP="$2"; shift 2 ;;
        --rounds)           ROUNDS="$2"; shift 2 ;;
        --goal)             GOAL="$2"; shift 2 ;;
        --reset-after)      RESET_AFTER=1; shift ;;
        --allow-userdata)   ALLOW_USERDATA=1; shift ;;
        -*)                 usage_error "unknown option: $1" ;;
        *)                  [ "$IMAGE_SET" = 0 ] || usage_error "unexpected argument: $1"
                            IMG="$1"; IMAGE_SET=1; shift ;;
    esac
done

case "$GOAL" in
    kernel-up|root-mounted|launchd-started|data-mounted|early-boot-complete|ui-processes-started) ;;
    *) usage_error "unsupported goal: $GOAL (visible UI cannot be established by this console)" ;;
esac
[[ "$ROUNDS" =~ ^[1-9][0-9]*$ ]] || usage_error 'rounds must be a positive integer'
if [ -n "$USERDATA_WIN" ] && [ "$ALLOW_USERDATA" != 1 ]; then
    usage_error '--userdata-window requires explicit --allow-userdata (overwrites Data)'
fi


mkdir -p "$(dirname "$RECORD_MD")" "$REPO/out"
if [ ! -f "$RECORD_MD" ]; then
    cat > "$RECORD_MD" <<'EOF'
# Unattended Agent Debug Loop Record (`sirius`)

| Time (Local) | Round | Kernel SHA-256 | Payload SHA-256 | Verdict | Early Boot | Key Spawns / Panic | Duration |
| --- | --- | --- | --- | --- | --- | --- | --- |
EOF
fi

KERNEL_FILE="$OUT_DIR/kernel/kernel.macho"
KERNEL_SHA="unknown"
if [ -f "$KERNEL_FILE" ]; then
    KERNEL_SHA=$(sha256sum "$KERNEL_FILE" | awk '{print substr($1,1,12)}')
fi
IMG_SHA=$(sha256sum "$IMG" | awk '{print substr($1,1,12)}')

for round in $(seq 1 "$ROUNDS"); do
    ts=$(date '+%Y%m%d-%H%M%S')
    log_path="$REPO/out/boot-${KERNEL_SHA}-${ts}.log"
    tl_path="$REPO/out/boot-${KERNEL_SHA}-${ts}.timeline"
    sum_path="$REPO/out/boot-${KERNEL_SHA}-${ts}.summary.json"

    echo "=== [agent-loop] round $round/$ROUNDS: kernel=$KERNEL_SHA image=$IMG_SHA ==="
    flash_opts=()
    if [ -n "$BOOT_IMG" ] && [ "$round" = 1 ]; then
        flash_opts+=(--boot-image "$BOOT_IMG")
    fi
    if [ -n "$USERDATA_WIN" ] && [ "$round" = 1 ]; then
        flash_opts+=(--userdata-window "$USERDATA_WIN" --allow-userdata)
    fi
    bash "$HERE/agent-flash.sh" "${flash_opts[@]}" "$IMG"

    cap_opts=(
        --follow
        --wait "$WAIT_PORT"
        --duration "$DURATION"
        --idle-stop "$IDLE_STOP"
        --output "$log_path"
        --timeline "$tl_path"
        --summary "$sum_path"
    )
    if [ "$RESET_AFTER" = 1 ] || [ "$round" -lt "$ROUNDS" ]; then
        cap_opts+=(--reset-to-fastboot)
    fi

    capture_status=0
    python3 -I "$HERE/capture-usb-console.py" "${cap_opts[@]}" || capture_status=$?

    verdict="no-summary"
    eb="-"
    detail="-"
    dur="-"
    if [ -f "$sum_path" ]; then
        if fields=$(python3 -I - "$sum_path" <<'PY'
import json
import sys

with open(sys.argv[1], encoding='utf-8') as stream:
    data = json.load(stream)
verdict = data.get('verdict', 'unknown')
if verdict == 'panic':
    evidence = data.get('fatal_panics') or []
elif verdict == 'storage-failure':
    evidence = data.get('storage_errors') or []
elif verdict == 'panic-warning':
    evidence = data.get('panic_warnings') or []
else:
    evidence = []
detail = (evidence[0]['text'] if evidence else
          ', '.join(data.get('key_spawns', [])) or data.get('panic_text') or
          data.get('data_volume') or '-')
# Four single-line fields; pass the path as argv, never as Python source.
for value in (verdict, data.get('early_boot_complete_s'), detail, f"{data.get('duration_s', 0)}s"):
    print(str(value if value is not None else '-').replace('\n', ' ').replace('\r', ' ').replace('|', '/'))
PY
        ); then
            mapfile -t summary_fields <<< "$fields"
            verdict="${summary_fields[0]}"
            eb="${summary_fields[1]}"
            detail="${summary_fields[2]:0:160}"
            dur="${summary_fields[3]}"
        else
            verdict="invalid-summary"
        fi
    fi


    printf '| %s | %d/%d | `%s` | `%s` | `%s` | %s | %s | %s |\n' \
        "$(date '+%Y-%m-%d %H:%M:%S')" "$round" "$ROUNDS" "$KERNEL_SHA" "$IMG_SHA" \
        "$verdict" "$eb" "$detail" "$dur" >> "$RECORD_MD"

    echo "=== [agent-loop] round $round verdict=$verdict early_boot=$eb detail=$detail ==="
    if [ "$capture_status" -eq 0 ] && [ "$verdict" = "$GOAL" ]; then
        echo "=== [agent-loop] goal '$GOAL' reached! ==="
        exit 0
    fi
done

printf "=== [agent-loop] goal '%s' not reached after %s round(s); last verdict=%s capture_status=%s ===\n" \
    "$GOAL" "$ROUNDS" "$verdict" "$capture_status" >&2
exit 1
