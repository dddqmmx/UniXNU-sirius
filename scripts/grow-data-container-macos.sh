#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
set -euo pipefail

if [ "$#" -ne 2 ]; then
    printf 'Usage: %s EXISTING-OWNER-DATA.cdr NEW-8G-DATA.cdr\n' "$0" >&2
    exit 2
fi
[ "$(uname -s)" = Darwin ] || { printf 'Requires macOS hdiutil and diskutil\n' >&2; exit 1; }
[ -f "$1" ] || { printf 'Missing source container\n' >&2; exit 1; }
[ ! -e "$2" ] || { printf 'Refusing to overwrite destination\n' >&2; exit 1; }

python3 -I - "$1" "$2" <<'PY'
from pathlib import Path
import shutil
import struct
import sys
source, target = map(Path, sys.argv[1:])
with source.open('rb') as stream:
    head = stream.read(4096)
if len(head) != 4096 or head[32:36] != b'NXSB':
    raise SystemExit('Source does not start with an APFS container superblock')
block_size, blocks = struct.unpack_from('<IQ', head, 36)
if block_size != 4096 or blocks * block_size != source.stat().st_size:
    raise SystemExit('Source APFS geometry differs from file size')
if source.stat().st_size >= 8 * 1024**3:
    raise SystemExit('Source is already at least 8 GiB')
with source.open('rb') as src, target.open('xb') as dst:
    shutil.copyfileobj(src, dst)
    dst.truncate(8 * 1024**3)
PY

store=''
cleanup() {
    if [ -n "$store" ]; then
        hdiutil detach "/dev/$store" -force >/dev/null || true
    fi
}
trap cleanup EXIT
attached=$(hdiutil attach -imagekey diskimage-class=CRawDiskImage -nomount -nobrowse "$2")
store=$(printf '%s\n' "$attached" | python3 -I -c 'import re,sys; text=sys.stdin.read(); match=re.search(r"/dev/(disk[0-9]+)\b",text); print(match[1] if match else "")')
[ -n "$store" ] || { printf 'Could not identify attached physical store\n' >&2; exit 1; }
container=$(diskutil apfs list -plist | python3 -I -c '
import plistlib,sys
listing=plistlib.load(sys.stdin.buffer)
found=[c["ContainerReference"] for c in listing.get("Containers",[]) if any(s["DeviceIdentifier"]==sys.argv[1] for s in c.get("PhysicalStores",[]))]
if len(found)!=1:
 raise SystemExit("Expected exactly one APFS container on the attached store")
print(found[0])' "$store")
diskutil apfs resizeContainer "$container" 0
diskutil verifyVolume "/dev/${container}s1"
hdiutil detach "/dev/$store"
store=''
python3 -I - "$2" <<'PY'
from pathlib import Path
import hashlib
import struct
import sys
p = Path(sys.argv[1])
with p.open('rb') as stream:
    head = stream.read(4096)
block_size, blocks = struct.unpack_from('<IQ', head, 36)
if head[32:36] != b'NXSB' or block_size != 4096 or blocks * block_size != 8 * 1024**3:
    raise SystemExit('Expanded APFS geometry did not reach 8 GiB')
with p.open('rb') as stream:
    digest = hashlib.file_digest(stream, 'sha256').hexdigest()
print(f'Validated container: {p}\nBytes: {blocks * block_size}\nSHA-256: {digest}')
PY
