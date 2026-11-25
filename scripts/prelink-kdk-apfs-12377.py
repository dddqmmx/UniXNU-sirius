#!/usr/bin/env python3
"""Audit and prelink KDK 25F84 APFS, AMFI, and Zlib into the sirius ordinary-ARM64 xnu-12377 kernel.

Runs the four fail-closed gates from UniXNU-zeta against the built sirius kernel:
 1. audit-zeta-apfs-closure.py       (6 KDK executables: APFS, AMFI, Zlib, AppleImage4, CoreTrust, LWL)
 2. audit-zeta-apfs-vtables.py       (5 primary OSObject/IOService/IOStorage/IOMedia/IOBlockStorageDevice tables)
 3. audit-zeta-apfs-extra-vtables.py (23 paired C++ vtables + instance-size checks)
 4. emit-zeta-apfs-prelink.py        (PAC-to-ordinary instruction lowering, split-segment relocation, XML metadata)
 5. test_zeta_prelink_emission_pointers.py (independent pointer & instruction verification of emitted bytes)
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def ensure_kdk_extracted(tree: Path) -> Path:
    extracted = tree / '.cache/kdk-25F84-extracted'
    stamp = extracted / '.extracted'
    if stamp.is_file():
        return extracted / 'System/Library'
    dmg = tree / '.cache/kdk/Kernel_Debug_Kit_26.5.2_build_25F84.dmg'
    if not dmg.is_file():
        raise SystemExit(f'Missing KDK DMG at {dmg}; build the kernel first')
    spec = importlib.util.spec_from_file_location('kh', tree / 'scripts/kdk-headers.py')
    kh = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(kh)
    if extracted.exists():
        shutil.rmtree(extracted)
    extracted.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR')) as tmp:
        pkg = kh.copy_pkg(dmg, Path(tmp))
        entries = kh.xar_entries(pkg)
        offset, length, _ = entries['KDK.pkg/Payload']
        prefixes = (
            'System/Library/Kernels/kernel.development.vmapple',
            'System/Library/Extensions/',
        )
        with open(pkg, 'rb') as handle:
            handle.seek(offset)
            count = kh.extract_cpio(kh.Stream(kh.pbzx_chunks(handle, length)), prefixes, extracted)
            print(f'prelink-kdk-apfs-12377: extracted {count} KDK files into {extracted}', flush=True)
    stamp.write_text('ok\n')
    return extracted / 'System/Library'


def run_step(label: str, cmd: list[str], env: dict[str, str] | None = None, allow_codes: tuple[int, ...] = (0,)) -> None:
    print(f'== {label}', flush=True)
    result = subprocess.run(cmd, env=env, text=True, capture_output=True)
    if result.stdout:
        print(result.stdout.rstrip(), flush=True)
    if result.returncode not in allow_codes:
        if result.stderr:
            print(result.stderr.rstrip(), file=sys.stderr, flush=True)
        raise SystemExit(f'{label} failed with exit code {result.returncode}')


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--work', type=Path,
                        default=Path('/home/dddqmmx/UniXNU/UniXNU-ios26/out/sirius/ios-26.5.2-23F84-arm64/sirius_ios26'))
    parser.add_argument('--zeta-scripts', type=Path,
                        default=Path('/home/dddqmmx/UniXNU/UniXNU-zeta/scripts'))
    parser.add_argument('--roots', nargs='+',
                        default=[
                            'com.apple.filesystems.apfs',
                            'com.apple.driver.AppleMobileFileIntegrity',
                            'com.apple.AppleFSCompression.AppleFSCompressionTypeZlib',
                        ])
    args = parser.parse_args()

    work = args.work.resolve()
    tree = work / 'tree'
    zeta = args.zeta_scripts.resolve()
    native = work / 'kernel/mach.development.bcm2837.unstripped.noctf'
    if not native.is_file():
        raise SystemExit(f'Missing native kernel: {native}')

    kdk_syslib = ensure_kdk_extracted(tree)
    kdk_ext = kdk_syslib / 'Extensions'
    kdk_kernel = kdk_syslib / 'Kernels/kernel.development.vmapple'
    xnu_source = tree / '.cache/xnu-work'
    storage_source = tree / 'refer/IOStorageFamily'
    crypto_source = tree / 'refer/corecrypto-2023'

    out = work / 'apfs-prelink'
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    closure_json = out / 'closure.json'
    vtables_json = out / 'vtables.json'
    extra_json = out / 'extra-vtables.json'
    prelink_dir = out / 'prelink'

    base_env = dict(os.environ)
    try:
        import capstone  # noqa: F401
    except ModuleNotFoundError:
        extra_sp = '/home/dddqmmx/project/yakuza-ge/venv/lib/python3.14/site-packages'
        if Path(extra_sp).is_dir():
            base_env['PYTHONPATH'] = extra_sp + (':' + base_env['PYTHONPATH'] if base_env.get('PYTHONPATH') else '')
    root_flags = [item for r in args.roots for item in ('--root', r)]
    run_step('1/5 closure audit', [
        sys.executable, str(zeta / 'audit-zeta-apfs-closure.py'),
        '--native', str(native),
        '--kdk-extensions', str(kdk_ext),
        *root_flags,
        '--output', str(closure_json),
    ])

    run_step('2/5 primary vtables audit', [
        sys.executable, str(zeta / 'audit-zeta-apfs-vtables.py'),
        '--native', str(native),
        '--kdk-kernel', str(kdk_kernel),
        '--kdk-storage', str(kdk_ext / 'IOStorageFamily.kext/Contents/MacOS/IOStorageFamily'),
        '--apfs', str(kdk_ext / 'apfs.kext/Contents/MacOS/apfs'),
        '--output', str(vtables_json),
    ])

    importer_paths = {
        'com.apple.filesystems.apfs': kdk_ext / 'apfs.kext/Contents/MacOS/apfs',
        'com.apple.driver.AppleMobileFileIntegrity': kdk_ext / 'AppleMobileFileIntegrity.kext/Contents/MacOS/AppleMobileFileIntegrity',
        'com.apple.AppleFSCompression.AppleFSCompressionTypeZlib': kdk_ext / 'AppleFSCompressionTypeZlib.kext/Contents/MacOS/AppleFSCompressionTypeZlib',
    }
    apfs_flags = [item for r in args.roots for item in ('--apfs', str(importer_paths[r]))]
    # Exit code 3 is expected when AppleEffaceableStorage and CoreAnalyticsHub are external missing providers;
    # emit-zeta-apfs-prelink.py's gate() explicitly verifies paired_tables == 23 and missing == {AppleEffaceableStorage, CoreAnalyticsHub}.
    run_step('3/5 extended vtables audit', [
        sys.executable, str(zeta / 'audit-zeta-apfs-extra-vtables.py'),
        '--native', str(native),
        '--kdk-provider', str(kdk_kernel),
        '--kdk-provider', str(kdk_ext / 'IOStorageFamily.kext/Contents/MacOS/IOStorageFamily'),
        '--kdk-provider', str(kdk_ext / 'AppleEffaceableStorage.kext/Contents/MacOS/AppleEffaceableStorage'),
        '--kdk-provider', str(kdk_ext / 'CoreAnalyticsFamily.kext/Contents/MacOS/CoreAnalyticsFamily'),
        *apfs_flags,
        '--xnu-source', str(xnu_source),
        '--storage-source', str(storage_source),
        '--record-instance-sizes',
        '--output', str(extra_json),
    ], env=base_env, allow_codes=(0, 3))

    run_step('4/5 emit ordinary-ARM64 prelinked kernel', [
        sys.executable, str(zeta / 'emit-zeta-apfs-prelink.py'),
        '--native', str(native),
        '--closure', str(closure_json),
        '--vtables', str(vtables_json),
        '--extra-vtables', str(extra_json),
        '--kdk-extensions', str(kdk_ext),
        '--xnu-source', str(xnu_source),
        '--storage-source', str(storage_source),
        '--crypto-source', str(crypto_source),
        '--output', str(prelink_dir),
    ], env=base_env)

    env = dict(base_env, ZETA_PRELINK_EMISSION_DIR=str(prelink_dir))
    run_step('5/5 independent emitted pointer & PAC verification', [
        sys.executable,
        str(zeta / 'tests/test_zeta_prelink_emission_pointers.py'),
    ], env=env)

    target = work / 'kernel/kernel.macho.apfs-prelinked'
    shutil.copy2(prelink_dir / 'kernel.macho', target)
    report = json.loads((prelink_dir / 'report.json').read_text())
    print(f'PASS: prelinked kernel ready at {target} ({report["output_bytes"]} bytes, sha256 {report["output_sha256"]})')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
