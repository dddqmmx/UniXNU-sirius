import sys, pathlib, os
L = pathlib.Path('/home/dddqmmx/UniXNU/UniXNU-ios26')
S17 = pathlib.Path('/home/dddqmmx/UniXNU/UniXNU-sirius')
SW = pathlib.Path('/home/dddqmmx/UniXNU/UniXNU-sirius-ios26')
R = pathlib.Path('/home/dddqmmx/.unixnu/ref/refer')
sys.path.insert(0, str(L / 'scripts'))
from unixnu import cli, feeds, tree as tree_mod, kernel as kernel_mod, payload as payload_mod
from unixnu.config import LOCAL_KINDS

print('LOCAL_KINDS:', LOCAL_KINDS, flush=True)
argv = ['build', 'kernel', '-D', str(SW), '-j', '10']
# Every source the build needs, from this machine: no submodule fetch, no clone.
# The branch worktree holds the ios26 patch/script/config versions, but its submodule
# directories are empty: the drivers and package come from the populated 17 worktree.
for kind in LOCAL_KINDS:
    base = SW if kind in ('patches', 'scripts') else S17
    if (base / kind).is_dir() and any((base / kind).iterdir()):
        argv.append(f'--local-{kind}={base / kind}')
    elif (S17 / kind).is_dir():
        argv.append(f'--local-{kind}={S17 / kind}')
# Components that live in the UniXNU repository come from the populated main checkout; the
# device's own forks (ArmCpu, DarwinBoot) stay from --local-drivers above.
L = pathlib.Path('/home/dddqmmx/UniXNU/UniXNU')
for child in sorted((L / 'drivers').iterdir()):
    if child.is_dir() and child.name != 'ArmCpu':
        argv.append(f'--local-module=driver:{child.name}={child}')
for child in sorted((L / 'kits').iterdir()):
    if child.is_dir():
        argv.append(f'--local-module=kit:{child.name}={child}')
for module in ('xnu', 'libplatform', 'libdispatch', 'AvailabilityVersions',
               'IOStorageFamily', 'libpthread', 'corecrypto-2023'):
    path = R / module if (R / module).exists() else R / ('libpthread-539' if module == 'libpthread' else module)
    argv.append(f'--local-module=refer:{module}={path}')
print('argv:', ' '.join(argv), flush=True)
ctx = cli.Context(cli.parser().parse_args(argv))
modules = feeds.resolve(ctx)
# Use the feed trees already cached in out/feeds (shared with the iPadOS 17 build) instead of
# fetching them again; the revisions match, so attach_cached picks them up.
feeds.attach_cached(ctx, modules)
print('resolved; tree =', ctx.work / 'tree', flush=True)
steps = set(os.environ.get('UNIXNU_STEPS', 'kernel,payload').split(','))
tree_mod.assemble(ctx, modules)
print('tree assembled', flush=True)
if 'kernel' in steps:
    kernel_mod.build(ctx, modules)
    print('KERNEL BUILD FINISHED', flush=True)
if 'payload' in steps:
    payload_mod.build(ctx)
    print('PAYLOAD BUILD FINISHED', flush=True)

