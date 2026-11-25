#!/usr/bin/env python3
"""Compile sirius's SDCC1 eMMC storage for the xnu-12377 (iOS 26) kernel and archive it.

UniXNU's build-xnu-12377.sh runs this when CONFIG_XNU_BOARD_STORAGE=y and force-loads the archive
it writes, build/board-storage-12377/libBoardStorage.a. The archive holds
 - the public IOStorageFamily (UniXNU scripts/build-storage-12377.py --family-only),
 - kits/sirius/sirius_emmc.c, the SDCC1 transport proved on the iPadOS 17 kernel,
 - kits/sirius/NativeEmmc.cpp built with SIRIUS_XNU_12377=1, the IOBlockStorageDevice on it,
 - kits/sirius/SiriusBoardStorage.cpp, the BoardStorage class native-board-storage.patch matches.

The 10063 recipes (build-sirius-emmc.py, build-native-emmc.py) bound NativeEmmc to the original
kernelcache's IOStorageFamily by address; the 12377 kernel prelinks no kext, so the family is compiled
from source and linked into the kernel next to the driver.

Every object must be ordinary arm64 for iOS 26, define nothing the kernel already defines, and import
only what the kernel or the archive itself defines.
"""
import json
from pathlib import Path
import struct
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from xnu_symbols import defined_names, kernel_names

OBJ = ROOT / 'build/xnu-12377.121.6/ios-obj/DEVELOPMENT_ARM64_BCM2837'
OUT = ROOT / 'build/board-storage-12377'
KIT = ROOT / 'kits/sirius'
OUT.mkdir(parents=True, exist_ok=True)

subprocess.run([sys.executable, str(ROOT / 'scripts/build-storage-12377.py'), '--family-only'], cwd=ROOT,
               check=True)
family_dir = ROOT / 'build/storage-12377/family'
family = [family_dir / name for name in json.loads((family_dir / 'report.json').read_text())['objects_built']]


def record(relative):
    return json.loads((OBJ / relative).read_text().strip().rstrip(','))


# IOKit C++ as the kernel compiles IOPlatformExpert.cpp; C as it compiles the platform expert's serial
# layer, which the native DWC3 console is also built from.
CXX = record('iokit/DEVELOPMENT/IOPlatformExpert.cpo.json')
C = record('pexpert/DEVELOPMENT/pe_serial.o.json')
EXTRA = ['-DSIRIUS_XNU_12377=1', '-I', str(ROOT / 'build/storage-abi/include'), '-fno-stack-check',
         '-Wall', '-Wno-unused-parameter', '-Wno-unused-function', '-Wno-unused-variable',
         '-Wno-unused-but-set-variable', '-Wno-c99-designator', '-Wno-reorder-init-list',
         '-Wno-deprecated-declarations', '-Wno-unnecessary-virtual-specifier']


def compile_one(source, recipe):
    destination = OUT / (source.stem + '.o')
    command, arguments, index = [], recipe['arguments'], 0
    while index < len(arguments):
        value = arguments[index]
        if value == recipe['file']:
            command.append(str(source))
        elif value == '-o':
            command += ['-o', str(destination)]
            index += 1
        elif value in ('-MF', '-MJ', '-MT', '-MQ'):
            index += 1
        elif value == '-Xclang' and arguments[index + 1].startswith('-W'):
            index += 1
        elif value not in ('-MD', '-MMD', '-MP') and not value.startswith('-W'):
            command.append(value)
        index += 1
    command += EXTRA
    destination.with_suffix('.command.json').write_text(json.dumps(command, indent=2) + '\n')
    result = subprocess.run(command, cwd=recipe['directory'], capture_output=True, text=True)
    destination.with_suffix('.log').write_text(result.stdout + result.stderr)
    if result.returncode:
        raise SystemExit(f'{source.name}: compilation failed\n{result.stdout}{result.stderr}')
    warnings = (result.stdout + result.stderr).count('warning:')
    data = destination.read_bytes()
    if struct.unpack_from('<4I', data) != (0xfeedfacf, 0x100000c, 0, 1):
        raise SystemExit(f'{source.name}: expected an ordinary ARM64 object')
    at, deployment = 32, None
    for _ in range(struct.unpack_from('<I', data, 16)[0]):
        kind, size = struct.unpack_from('<II', data, at)
        if kind == 0x32:
            deployment = struct.unpack_from('<II', data, at + 8)
        at += size
    if deployment != (2, 26 << 16):
        raise SystemExit(f'{source.name}: unexpected deployment {deployment}')
    print(f'BoardStorage 12377: compiled {source.name} ({warnings} warnings, see {destination.with_suffix(".log").name})',
          flush=True)
    return destination


board = [compile_one(KIT / 'sirius_emmc.c', C),
         compile_one(KIT / 'NativeEmmc.cpp', CXX),
         compile_one(KIT / 'SiriusBoardStorage.cpp', CXX)]


def undefined(paths):
    names = set()
    for path in paths:
        names |= {line.strip() for line in subprocess.check_output(
            ['xcrun', 'nm', '-g', '-u', '-j', str(path)], text=True).splitlines() if line.startswith('_')}
    return names


kernel = kernel_names(ROOT, object_directory=OBJ)
board_defined = defined_names(board, True)
archive_defined = board_defined | defined_names(family, True)
collisions = sorted(board_defined & kernel)
missing = sorted(undefined(board) - archive_defined - kernel - {'___dso_handle'})
report = {'objects': [path.name for path in family + board], 'kernel_collisions': collisions,
          'missing_imports': missing, 'family_objects': len(family), 'read_only': True,
          'hardware_qualified': False}
(OUT / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
if collisions or missing:
    raise SystemExit(f'BoardStorage symbol audit: collisions={collisions}; missing={missing}')

archive = OUT / 'libBoardStorage.a'
archive.unlink(missing_ok=True)
subprocess.run(['xcrun', 'ar', 'rcs', str(archive), *map(str, family + board)], check=True)
print(f'BoardStorage 12377: {len(family)} IOStorageFamily + {len(board)} sirius eMMC objects; '
      'no kernel collisions, no unavailable imports')
