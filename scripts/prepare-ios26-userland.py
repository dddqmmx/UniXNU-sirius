#!/usr/bin/env python3
"""Prepare and audit the sirius iOS 26.5.2 (iPhone18,2 / 23F84) offline userland lowering inputs.

This script enforces strict preflight gates around the reference arm64e -> arm64 lowering
scripts in UniXNU-zeta without duplicating their 3,000+ lines of disassembler/PAC-lowering
machinery or importing any SM8350/UFS board logic.

Why strict gates are required:
- prove-zeta-dyld-cache.py returns exit code 0 even when --only produced a partial proof or
  when unproven/unlowerable instructions remain, and rewrite-zeta-dyld-cache.py does not
  reject partial proof summaries by itself.
- Stock iPhone18,2 23F84 OS (094-54256-108.dmg.aea) and SystemOS (094-54345-109.dmg.aea)
  are AEA-encrypted container images; offline lowering requires already-decrypted raw 4 KiB
  APFS containers or extracted Mach-O / dyld_shared_cache trees.

Reported readiness stages are tracked separately so "input verified" is never conflated
with "boot-ready".
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

EXPECTED_ZETA_COMMIT = 'eb489285a1d9153872f00c93ebb519a269b249b5'
EXPECTED_IPSW = {
    'product': 'iPhone18,2',
    'version': '26.5.2',
    'build': '23F84',
    'device_class': 'v54ap',
    'chip_id': 0x8150,
    'board_id': 14,
}
EXPECTED_PINS = {
    'DeviceTree.v54ap.im4p': '9305d468d6a97f6e57842b5866359ad473434b5d507ea3e4b4123f29edda8218',
    '094-54256-108.dmg.aea.trustcache': 'c57bd130523563b2d68737d17cfa55bc36a33c7b69c226c721403ee5b6760676',
    '094-54345-109.dmg.aea.trustcache': '2fe341653972540bd9c9afa3c82c6451774c046f84a991a974f9461d83dd2f90',
}
MINIMAL_ZETA_SCRIPTS = (
    'rewrite-zeta-userland.py',
    'zeta_userland_code.py',
    'zeta_userland_pauth.py',
    'make-zeta-trust-bundle.py',
)
CACHE_ZETA_SCRIPTS = (
    'zeta_dyld_cache.py',
    'prove-zeta-dyld-cache.py',
    'rewrite-zeta-dyld-cache.py',
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        while chunk := handle.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def check_host_dependencies(require_cache: bool = False) -> dict:
    llvm_mc = shutil.which('llvm-mc')
    capstone_ok = False
    numpy_ok = False
    try:
        import capstone  # noqa: F401
        capstone_ok = True
    except ImportError:
        pass
    try:
        import numpy  # noqa: F401
        numpy_ok = True
    except ImportError:
        pass
    missing = []
    if not llvm_mc:
        missing.append('llvm-mc')
    if not capstone_ok:
        missing.append('python-capstone')
    if require_cache and not numpy_ok:
        missing.append('python-numpy')
    return {
        'llvm_mc': llvm_mc,
        'python_capstone': capstone_ok,
        'python_numpy': numpy_ok,
        'missing': missing,
        'ok': not missing,
    }


def check_zeta_reference(zeta_root: Path, require_cache: bool = False) -> dict:
    zeta_root = zeta_root.resolve()
    commit = None
    if (zeta_root / '.git').exists():
        result = subprocess.run(
            ['git', '-C', str(zeta_root), 'rev-parse', 'HEAD'],
            capture_output=True, text=True, check=False,
        )
        if result.returncode == 0:
            commit = result.stdout.strip()
    needed = list(MINIMAL_ZETA_SCRIPTS) + (list(CACHE_ZETA_SCRIPTS) if require_cache else [])
    script_hashes = {}
    missing = []
    for name in needed:
        path = zeta_root / 'scripts' / name
        if path.is_file():
            script_hashes[name] = sha256_file(path)
        else:
            missing.append(name)
    return {
        'path': str(zeta_root),
        'commit': commit,
        'expected_commit': EXPECTED_ZETA_COMMIT,
        'commit_matches': commit == EXPECTED_ZETA_COMMIT,
        'scripts': script_hashes,
        'missing_scripts': missing,
        'ok': commit == EXPECTED_ZETA_COMMIT and not missing,
    }


def check_ipsw_cache(ipsw_cache: Path) -> dict:
    ipsw_cache = ipsw_cache.resolve()
    files = {}
    missing = []
    mismatched = []
    for name, expected_sha in EXPECTED_PINS.items():
        path = ipsw_cache / name
        if not path.is_file():
            missing.append(name)
            continue
        actual_sha = sha256_file(path)
        files[name] = {
            'path': str(path),
            'size': path.stat().st_size,
            'sha256': actual_sha,
            'expected_sha256': expected_sha,
            'matches': actual_sha == expected_sha,
        }
        if actual_sha != expected_sha:
            mismatched.append(name)
    return {
        'cache_dir': str(ipsw_cache),
        'expected_identity': EXPECTED_IPSW,
        'files': files,
        'missing': missing,
        'mismatched': mismatched,
        'ok': not missing and not mismatched,
    }


def audit_cache_proof(proof_dir: Path, cache_path: Path, zeta_root: Path, allowed_unproven: int = 0) -> dict:
    summary_path = proof_dir / 'summary.json'
    if not summary_path.is_file():
        raise ValueError(f'missing proof summary: {summary_path}')
    summary = json.loads(summary_path.read_text())
    sys.path.insert(0, str((zeta_root / 'scripts').resolve()))
    import zeta_dyld_cache as dc  # type: ignore

    cache = dc.Cache(cache_path)
    expected_images = {path for _, path in cache.images}
    expected_islands = sum(
        1 for cache_file in cache.files
        for _, _, _, _, _, flags, _, _ in cache_file.slid if flags & 8
    )
    proven_images = set(summary.get('images', {}).keys())
    proven_islands = len(summary.get('islands', []))
    totals = summary.get('totals', {})
    unlowerable = int(totals.get('unlowerable', 0))
    unproven = int(totals.get('unproven', 0))
    missing_images = sorted(expected_images - proven_images)
    ok = (
        Path(summary.get('cache', '')).name == cache_path.name
        and not missing_images
        and proven_islands == expected_islands
        and unlowerable == 0
        and unproven <= allowed_unproven
    )
    return {
        'proof_summary': str(summary_path),
        'cache': str(cache_path),
        'expected_image_count': len(expected_images),
        'proven_image_count': len(proven_images),
        'missing_images': missing_images[:20],
        'missing_image_count': len(missing_images),
        'expected_island_count': expected_islands,
        'proven_island_count': proven_islands,
        'totals': totals,
        'unlowerable': unlowerable,
        'unproven': unproven,
        'allowed_unproven': allowed_unproven,
        'ok': ok,
    }


def cmd_status(args: argparse.Namespace) -> int:
    host = check_host_dependencies(require_cache=args.require_cache)
    zeta = check_zeta_reference(args.zeta_root, require_cache=args.require_cache)
    ipsw = check_ipsw_cache(args.ipsw_cache)
    stages = {
        '1_input_identity_validated': ipsw['ok'] and zeta['ok'],
        '2_standalone_lowering_verified': False,
        '3_full_cache_proof_and_signatures_verified': False,
        '4_kdk_closure_and_prelink_gates_passed': False,
        '5_root_data_and_payload_assembled': False,
        '6_hardware_checkpoints_observed': False,
    }
    report = {
        'schema': 'sirius-ios26-userland-preflight-v1',
        'board': 'sirius',
        'soc': 'SDM710',
        'os_profile': 'ios-26.5.2-23F84-arm64',
        'boot_ready': False,
        'lowering_host_ready': host['ok'],
        'stages': stages,
        'host_dependencies': host,
        'zeta_reference': zeta,
        'ipsw_components': ipsw,
        'limitations': [
            'Stock iPhone18,2 23F84 OS (094-54256-108.dmg.aea) and SystemOS (094-54345-109.dmg.aea) are AEA-encrypted; decrypted raw 4 KiB APFS containers or extracted binaries are required before lowering.',
            'Standalone/cache lowering converts PAuth instructions and headers to ARM64_ALL; it does not emulate AMX/SME/SVE if userland dispatches them.',
            'sirius uses SDM710 eMMC (SDCC1), not SM8350 UFS; KDK APFS prelink and NativeEmmc 12377 root binding are still required before userland can mount.',
        ],
    }
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))
    ok = stages['1_input_identity_validated'] and (host['ok'] if args.require_host_tools else True)
    return 0 if ok else 1


def cmd_lower_binary(args: argparse.Namespace) -> int:
    host = check_host_dependencies(require_cache=False)
    zeta = check_zeta_reference(args.zeta_root, require_cache=False)
    ipsw = check_ipsw_cache(args.ipsw_cache)
    if not (host['ok'] and zeta['ok'] and ipsw['ok']):
        raise SystemExit('Preflight failed: run "status" and resolve missing host/reference/IPSW inputs first')
    if not args.input.is_file():
        raise SystemExit(f'Input Mach-O not found: {args.input}')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    rewriter = args.zeta_root.resolve() / 'scripts/rewrite-zeta-userland.py'
    trust_os = args.ipsw_cache.resolve() / '094-54256-108.dmg.aea.trustcache'
    trust_sys = args.ipsw_cache.resolve() / '094-54345-109.dmg.aea.trustcache'
    cmd = [
        sys.executable, '-I', str(rewriter),
        '--input', str(args.input.resolve()),
        '--output', str(args.output.resolve()),
        '--apple-trust-cache', str(trust_os),
        '--apple-trust-cache', str(trust_sys),
        '--report', str(args.report.resolve()),
    ]
    subprocess.run(cmd, check=True)
    report = json.loads(args.report.read_text())
    if report.get('unproven_count', 0) != 0 or report.get('unlowerable'):
        raise SystemExit(f'Standalone rewrite left unproven/unlowerable instructions: {report}')
    print(f'PASS: lowered {args.input.name} -> {args.output} (cdhash {report.get("cdhash")})')
    return 0


def cmd_audit_cache_proof(args: argparse.Namespace) -> int:
    zeta = check_zeta_reference(args.zeta_root, require_cache=True)
    if not zeta['ok']:
        raise SystemExit('Zeta cache reference check failed')
    result = audit_cache_proof(args.proof, args.cache, args.zeta_root, args.allowed_unproven)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))
    return 0 if result['ok'] else 1


def build_parser() -> argparse.ArgumentParser:
    here = Path(__file__).resolve().parents[1]
    default_zeta = here.parent / 'UniXNU-zeta'
    default_ipsw_cache = here.parent / 'UniXNU-ios26/out/ipsw/23F84'

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--zeta-root', type=Path, default=default_zeta,
                        help='Pinned UniXNU-zeta checkout used for read-only lowering scripts')
    parser.add_argument('--ipsw-cache', type=Path, default=default_ipsw_cache,
                        help='Directory holding extracted 23F84 iPhone18,2 IM4P/trustcache files')
    sub = parser.add_subparsers(dest='command', required=True)

    status = sub.add_parser('status', help='Validate iPhone 23F84 pins, host tools and pinned zeta lowering scripts')
    status.add_argument('--require-cache', action='store_true', help='Also check NumPy and shared-cache scripts')
    status.add_argument('--require-host-tools', action='store_true', help='Fail if host disassembler dependencies are missing')
    status.add_argument('--output', type=Path, help='Write JSON status report to this path')
    status.set_defaults(func=cmd_status)

    lower = sub.add_parser('lower-binary', help='Lower one extracted arm64e Mach-O (e.g. launchd or dyld) to arm64')
    lower.add_argument('--input', type=Path, required=True, help='Original thin arm64e Mach-O')
    lower.add_argument('--output', type=Path, required=True, help='Output path for lowered arm64 Mach-O')
    lower.add_argument('--report', type=Path, required=True, help='Output JSON report path')
    lower.set_defaults(func=cmd_lower_binary)

    audit = sub.add_parser('audit-cache-proof', help='Enforce complete image/island coverage on a dyld cache proof')
    audit.add_argument('--cache', type=Path, required=True, help='Main dyld_shared_cache_arm64e file')
    audit.add_argument('--proof', type=Path, required=True, help='Work directory produced by prove-zeta-dyld-cache.py')
    audit.add_argument('--allowed-unproven', type=int, default=0,
                       help='Maximum explicitly accounted-for unproven PAC-shaped data words (default: 0)')
    audit.add_argument('--output', type=Path, help='Write JSON audit report to this path')
    audit.set_defaults(func=cmd_audit_cache_proof)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == '__main__':
    sys.exit(main())
