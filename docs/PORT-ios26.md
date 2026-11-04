# sirius 适配 iOS 26.5.2 (23F84 / xnu-12377) 的计划

本文记录把当前 iPadOS 17.7.11 (21H461) 的 sirius 端口迁到 iOS 26.5.2 的可行性结论、
已经过验证的参考资产和分阶段动作。参考实现是 UniXNU-zeta（Surface Duo 2，SM8350），
它在无 PAC 的 SoC 上已经跑起 iOS 26.5.2。

## 结论：为什么不能走 upstream 的 iOS 26 路线

| 事实 | 证据 |
| --- | --- |
| 本机 UniXNU 检出落后它自己的 upstream main 376 个提交 | `git merge-base --is-ancestor 066c8d8 up/main` 为真；`rev-list --count 066c8d8..up/main` = 376 |
| upstream main 已经带 iOS 26.5.2 与 iOS 27 的 OS profile 和 xnu-12377 补丁 | `profiles/os/ipados-26.5.2-23F84`、`profiles/os/ipados-27.0.1-24A446`、`patches/xnu-12377/`（60 个补丁） |
| 但 upstream 的 arm64e 路线要求 CPU 原生实现 PAC | `docs/porting/arm64e.md`：arm64e 需要 FEAT_PAuth 与五个 key 寄存器写入不 trap；受支持设备只有 Sheng（SM8550） |
| upstream 的 arm64 目标只到 iOS 16 / iPadOS 17 | `docs/guide/requirements.md`：arm64 目标为 iOS 16 与 iPadOS 17，iOS/iPadOS 18 之后只支持 arm64e |
| sirius 的 Kryo 360 是 ARMv8.2，无 PAC | 本仓库 README 的硬件表；zeta 的内核探针也报 `ID_AA64ISAR1_EL1` 的 APA/API/GPA/GPI 全零 |

所以 SDM710 无法使用 upstream 的 arm64e 体系，而 iOS 26 的 userland 只有 arm64e。
唯一已被证明可行的办法是 zeta 的做法：把 arm64e 的 userland（launchd、dyld、dyld shared
cache、kext 对应的用户态依赖）离线降级成普通 arm64 指令，按 4 KiB 页重签名，再用静态
trust cache 放行；内核侧额外需要「4 KiB 的 shared region slide」与「无 PAC 时把认证
rebase 写回普通指针」两个补丁。

## 已验证的参考资产

| 资产 | 位置 | 状态 |
| --- | --- | --- |
| fork 的 xnu-12377 补丁序列 | `none24smu/UniXNU` 分支 `fix/cache-batch-kernel`（44 个补丁）与 `ios26`（21 个基线补丁） | 已验证：按文件名顺序用 `patch -p1` 打到 apple-oss `xnu-12377.121.6` 上，44/44 与 21/21 全部干净应用 |
| Apple 内核源码 | `xnu-12377.121.6` = commit `ac9718fb1af618d5ce8678d0dc6e8a58f252216f` | 已验证与 zeta `feeds.toml` 的 pin 一致 |
| 内核构建脚本 | `scripts/build-xnu-12377.sh` 及 `build-{platform,pthread,storage}-12377.py`、`kdk-headers.py`、`source-pthread-12377.c`、`corecrypto-12377-kpi.h`、`native-dwc3-console.c`、`analyze-arm64e-kernelcache.py` | 已导入本机 UniXNU 的 `ios26` worktree，语法与依赖检查通过 |
| 用户态降级管线 | `UniXNU-zeta` @ `eb48928`：`rewrite-zeta-userland.py`、`zeta_userland_{code,pauth}.py`、`zeta_dyld_cache.py`、`prove-zeta-dyld-cache.py`、`rewrite-zeta-dyld-cache.py`、`make-zeta-trust-bundle.py`、`grow-zeta-apfs-container.py`、`write-zeta-apfs-files.py`、`replace-zeta-root-files.py`、`make-zeta-root-delta.py` | 未移植，只作参考 |
| 两个内核补丁（4 KiB + 无 PAC） | `patches/xnu-12377/native-shared-region-small-pages.patch`、`native-shared-region-plain-auth.patch` | 已随序列导入 |

本机已拉好的参考副本（都在 `~/.unixnu/ref/`，不属于任何仓库）：

```
unixnu-ios26      fork none24smu/UniXNU；origin/ios26=68c5134，origin/fix/cache-batch-kernel=35c384e，up/main=e67a79e
xnu-12377         apple-oss-distributions/xnu @ ac9718fb1af618d5ce8678d0dc6e8a58f252216f
```

## 分阶段动作

P0 内核构建能力（本机 UniXNU）：

1. `scripts/unixnu/kernel.py` 现在写死 `scripts/build-xnu-4k-baseline.sh`，需要支持
   `CONFIG_XNU_BUILD_SCRIPT` 选择 `build-xnu-12377.sh`，并支持 `[kernel] outputs`
   与「无 `[kernel.prelink]` 时不预链接」。
2. `scripts/build-corecrypto-14.py` 与 `scripts/seal-source-crypto-kernel.py` 需要并入
   fork 的 `--xnu-12377` / `--static-if-defaults` 分支，不要整文件替换，否则会破坏
   iPadOS 17 的 Source crypto 路径。
3. 补丁序列已预置在 `patches/xnu-12377/`；`patches/drivers/ArmCpu-*.patch` 是 ArmCpu
   在 12377 上的改动，`NativeUfs-sm8350.patch` 只服务 zeta，sirius 不需要。

P1 OS profile：新增一个 arm64 的 iOS 26.5.2 profile（不能改 upstream 那个 arm64e 的
`ipados-26.5.2-23F84`）。要点：`[os] arch = "arm64"`、`xnu_version = "12377.121.6"`、
`[kernel]` 不写 `prelink` 而写 `outputs`、`[ipsw]` 用 23F84 的还原镜像、`[adt]` 表、
`text_fixups` 与 trust cache 摘要。

P2 board 侧：`configs/sirius_ios26_defconfig`（照 zeta 的开关：`CONFIG_XNU_BUILD_SCRIPT`、
`CONFIG_XNU_NATIVE_ARCH_TIMER`、`CONFIG_XNU_VIRTUAL_TIMER_ONLY`、`CONFIG_XNU_SOURCE_CORECRYPTO14`、
`CONFIG_XNU_SOURCE_PTHREAD`、`CONFIG_XNU_ARM_DT_PLATFORM`、`CONFIG_XNU_ARMCPU`、
`CONFIG_XNU_NATIVE_PAN`、`CONFIG_XNU_DT_PANIC_REGION`），`board.toml` 切 `os_profile`、
补 `patch_series` 与 `[kernel] archives`。

P3 board package 移植：`package/` 里的内核侧服务（NativeEmmc、BootProgress、SiriusUsb、
SiriusPmgr、ShengHidInput 的接入、panic screen、各种 probe）目前都按 xnu-10063 的 API 写，
需要逐个对 12377 编译；upstream main 里 sheng 的 26/27 版本可以直接对照。

P4 用户态降级管线：把 zeta 的脚本泛化后移植（去掉 zeta/SM8350 特有的部分），产出
降级后的 launchd、dyld、dyld shared cache（约 2960 万个 PAC word）、合并静态 trust cache
和重签名的 root 镜像。

P5 硬件验证：需要设备，按 porting 检查表逐步记录。

## 需要决定的事

1. 目标身份用 iPadOS 26.5.2（`iPad17,3`，与 upstream 的 23F84 profile 一致）还是
   zeta 的 iOS 26.5.2（`iPhone18,2`）。建议 iPadOS：sirius 现有整套用户态栈都是 iPadOS，
   iPad 的 DeviceTree 依赖也更少。
2. UniXNU 基线：继续在本地 `066c8d8` 上做 arm64 的 zeta 路线，还是先升到 upstream main。
   建议前者（SDM710 无 PAC，upstream 的 arm64e 体系用不上），但升级能拿到更多通用工具。
3. 存储：zeta 的 UFS 补丁与 `native-ufs-owner-root` 对 sirius 不适用，需要把
   `NativeEmmc` 迁到 12377。

## 未提交、未改动的部分

参考资产放在单独的 worktree `/home/dddqmmx/UniXNU/UniXNU-ios26`（分支 `ios26`，从
`066c8d8` 分出，文件已 `git add`，没有提交）。主检出 `../UniXNU` 与 sirius 的
iPadOS 17 路径都未被修改。

## 进度（2026-11-04）

已完成并验证的部分都在 Linux 上做的；只有内核编译必须 macOS + Xcode，交给 GitHub 工作流。

| 阶段 | 状态 | 位置 |
| --- | --- | --- |
| P0 内核构建能力 | 已在 Linux 上落地并验证补丁序列 | UniXNU 分支 `ios26`（worktree `../UniXNU-ios26`） |
| P1 OS profile 与 arch 层 | 已建立，`defconfig`/`status` 已解析通过 | `profiles/os/ipados-26.5.2-23F84-arm64`、`profiles/arch/arm64-ios26` |
| P2 board 与 CI | 已建立 | UniXNU-sirius 分支 `ios26-26.5.2`（worktree `../UniXNU-sirius-ios26`） |
| P3 board package 移植 | 未开始 | 见下 |
| P4 用户态降级管线 | 未开始 | 见下 |

P0 的内容：`patches/xnu-12377/`（44 个补丁，来自 fork 的 `fix/cache-batch-kernel`）、
`scripts/build-xnu-12377.sh`（改成格式 2 + Linux 可用：复用驱动打好的 `.cache/xnu-work`、
用 `build-firehose.sh` 的 Linux 分支、去掉 `ditto`/`xcodebuild`）、以及新增的
`build-{platform,pthread,storage}-12377.py`、`kdk-headers.py`、`corecrypto-12377-kpi.h`、
`source-pthread-12377.c`、`native-dwc3-console.c`、`analyze-arm64e-kernelcache.py`。
驱动改动：`CONFIG_XNU_BUILD_SCRIPT` 选择内核构建脚本、`[kernel] outputs`、无
`[kernel.prelink]` 时跳过预链接、`[os] arch` 决定 arch 层、Linux 环境的
`-miphoneos-version-min` 与 `DARWIN_TRIPLE` 跟随 profile 的 `ios_version`（26.0）。
`build-corecrypto-14.py` 并入 `--xnu-12377`（保留 Linux 的 pbxproj 回退），
`seal-source-crypto-kernel.py` 并入 `--static-if-defaults`，
`prepare-storage-headers.py` 接受 337.100.1 版本，`build-firehose.sh` 接受
`UNIXNU_FAKEROOT`/`UNIXNU_XNU_WORK`/`UNIXNU_FIREHOSE_IOS_MIN`。

Linux 上的验证结果：`./unixnu defconfig sirius_ios26_defconfig` 解析出
`out/sirius/ipados-26.5.2-23F84-arm64/sirius_ios26/.config`；驱动把 44 个补丁按序
`git apply` 到 `xnu-12377.121.6`（`ac9718fb1af6`）上，0 个 reject，随后
`prepare-xnu-source.py` 补回 5 个被省略的公共头。这一步用
`--local-module refer:xnu=...` 指向本机克隆，没有下载内核源码。

还没做的：内核编译本身（本机没有 `/opt/unixnu-linux` 工具链，也没有 podman），它由
`.github/workflows/kernel.yml` 在 `macos-26` + `Xcode_26.6` 上跑，工作流现在接受
`unixnu_repository`（指向带 `ios26` 分支的 fork）、`defconfig`、`os_profile`，并在
无 prelink 的目标上自动跳过 IOStorageFamily 绑定校验。P3 要把 `package/` 的内核侧服务
从 xnu-10063 API 迁到 12377，P4 要移植 zeta 的降级管线（`rewrite-zeta-userland.py`、
`zeta_dyld_cache.py`、trust bundle、root 镜像改写）。

两个 worktree 里的改动都**没有提交**，主检出和 iPadOS 17 路径未被修改。
