# sirius → iOS 26.5.2 交接文档

写于 2026-11-05。目标：把 sirius（小米 8 SE，SDM710）从 iPadOS 17.7.11 迁到 iOS 26.5.2
（23F84，xnu-12377），参考实现是 UniXNU-zeta（Surface Duo 2，同样是**无 PAC** 的 SoC）。

## 1. 当前状态

内核已经能在本机（Linux）完整构建并刷进设备；设备上目前没有任何输出；已经建好「内核自复位探针」
的自动二分循环，但**设备此刻是暗的**（屏幕上停在彩条 + panic 文字，USB 完全无枚举），需要人工
按电源键约 15 秒唤醒回 fastboot。

| 阶段 | 状态 | 证据 / 位置 |
| --- | --- | --- |
| P0 内核构建能力 | 完成（Linux 上验证） | UniXNU 分支 `ios26` 提交 `cb60245` + APFS/CSR/KDK ABI 开关同步 |
| P1 OS profile / arch 层 | 已切换为 iPhone `ios-26.5.2-23F84-arm64`（`iPhone18,2` / `v54ap`） | `profiles/arch/arm64-ios26`、`profiles/os/ios-26.5.2-23F84-arm64` |
| P2 board 与 CI | 已切换为 `ios-26.5.2-23F84-arm64`，`porting.toml` 14 项检查通过 | UniXNU-sirius 分支 `ios26-26.5.2` |
| 内核产物 | 22 MB arm64 Mach-O（含 APFS SHA3/digest-pair KPI 与自检），静态 if 完整性已封 | `out/sirius/ios-26.5.2-23F84-arm64/sirius_ios26/kernel/` |
| 设备上启动 | 待用新 `ios-26.5.2-23F84-arm64` 镜像二分验证（手机已在 `fastboot` 在线） | 见 §11、§12 |
| P3 板级驱动（控制台/存储/显示） | DWC3 控制台已带 SDM710 QUSB2/GCC/SMMU 与 PMIC 0x88f 复位标记；存储/显示未迁 | 见 §8、§11 |
| P4 userland 降级管线 | 已接入 `scripts/prepare-ios26-userland.py` 预检与包装，待解密容器与 `python-capstone` | 见 §8 |

## 2. 决定路线的三个结论

1. **SDM710 没有 PAC，upstream 的 arm64e 路线用不了。** upstream `docs/porting/arm64e.md` 明写
   arm64e 需要 CPU 原生实现指针认证（受支持设备只有 SM8550 的 sheng），而 upstream 的 arm64 目标
   只到 iOS 16 / iPadOS 17。upstream main 确实已经带 `profiles/os/ipados-26.5.2-23F84` 和
   `patches/xnu-12377`，但那是 arm64e 的。⇒ 只能走 zeta 的「arm64e userland 离线降级成 arm64」
   加 arm64 内核这条路。
2. **xnu-12377 的 `/chosen/random-seed` 必须 256 字节。** 本机这棵树原本给 64 字节，xnu-12377 的
   `arm_vm_init` 会显式 panic（zeta 的 README 记录了同一件事）。upstream 已修，本分支已移植。
   这条发生在控制台之前，症状正好是「全无输出」。
3. **sirius 的 DWC3 需要板级准备。** `package/sirius_usb.c` 的注释写明：没有 QUSB2 PHY 初始化，
   DWC3 就没有 PIPE 时钟、端点命令永不完成（观察到核心停在 DEVCTRLHLT）。17 端口的内核自己做了
   GCC 时钟、QUSB2 PHY、UTMI 时钟选择与 SMMU 绑定；fork 的 DWC3 控制台（zeta 的 SM8350 路线）
   **明确假设固件已经准备好控制器**。所以即使内核跑得很好，这个控制台也很可能永远不枚举。
   另外 `CNTP_CTL_EL0` 的写入会 trap（zeta 在 SM8350 上踩过），已用
   `CONFIG_XNU_VIRTUAL_TIMER_ONLY=y` 规避。

## 3. 资产与位置

- UniXNU：worktree `~/UniXNU/UniXNU-ios26`，分支 `ios26`，提交 **cb60245**
  （基于本机 main `066c8d8`，它落后自己的 upstream main 约 376 个提交）。
- sirius：worktree `~/UniXNU/UniXNU-sirius-ios26`，分支 `ios26-26.5.2`，提交 **1a984ae**。
  主检出 `~/UniXNU/UniXNU-sirius`（iPadOS 17 路径）未被修改。
- 参考副本（不属于任何仓库，交接时可丢）：
  - `~/.unixnu/ref/unixnu-ios26`：fork `none24smu/UniXNU`；`origin/ios26` = 68c5134、
    `origin/fix/cache-batch-kernel` = 35c384e（44 个补丁的来源）、`up/main` = e67a79e（已 fetch 的 upstream）。
  - `~/.unixnu/ref/xnu-12377`：apple-oss xnu，`ac9718fb1af618d5ce8678d0dc6e8a58f252216f`。
  - `~/.unixnu/ref/refer/*`：libplatform-375.120.2、libdispatch-1542.100.32、AvailabilityVersions-157.2、
    IOStorageFamily-337.100.1、libpthread-539.100.4、corecrypto-2023 `11fba65`。
  - `~/UniXNU/UniXNU-zeta`（eb48928）：方法学参考。
- 构建产物：`~/UniXNU/UniXNU-ios26/out/sirius/ipados-26.5.2-23F84-arm64/sirius_ios26/`
  （`kernel/`、`payload/`、`logs/kernel.log`、`logs/payload-patch.log`）。
- 可刷镜像（512 MiB，刷 vendor）：
  - `~/UniXNU/UniXNU-sirius-ios26/out/ios26-payload-v2.img`：带 256 字节种子 +
    VIRTUAL_TIMER_ONLY + DWC3 控制台（未验证能否枚举）。
  - `.../out/ios26-probe.img`：上一版 + ADT 触发的控制台复位探针（8 秒）。内核由 payload 步骤
    自动打上入口色带（蓝/品红）+ panic 屏幕补丁。
  - `.../out/blobs/ios26-entry.img`：**入口即复位**版（把入口补丁的阶段 1 桩末尾改写为「等 8 秒 →
    PSCI 复位」）。入口阶段已确认会执行（屏幕出现过彩条），所以这个镜像**一定**会自己复位，
    用来验证复位循环与计时基线。

## 4. 构建（必须在 Linux 上，照做）

工具链已装在 `~/.unixnu/root/opt/unixnu-linux`（`llvm/bin/clang`、`CommandLineTools/usr/bin/ld`、
`xnu-host-cc`、`libexec/migcom`、`XcodeDefault.xctoolchain/usr/bin/iig`、`bin/lzfse` 六项齐全；
`unifdef`、`mtools`、`tcsh` 在 `~/.unixnu/toolchain/bin`）。**必须**在 `~/.unixnu/run.sh` 的
user-namespace + overlay 里跑，它把 `/opt` 指向工具链并把 shim 挂到 `/usr/bin`：

```sh
cd ~/UniXNU/UniXNU-ios26
~/.unixnu/run.sh bash -c 'UNIXNU_STEPS=kernel,payload python3 /tmp/build_ios26_kernel.py'
```

`/tmp/build_ios26_kernel.py` 的副本已归档为 `~/UniXNU/UniXNU-sirius-ios26/scripts/build-ios26-dev.py`
（交接后改用它）。它做的事：用解析好的 board 构造 Context，`feeds.resolve` + `feeds.attach_cached`
（复用与 17 构建共享的 `out/feeds` 缓存；该目录是指向 `~/UniXNU/UniXNU/out/feeds` 的软链），
`tree.assemble`，然后 `kernel.build` 与 `payload.build`。命令行上带着
`--local-module refer:<name>=~/.unixnu/ref/refer/<name>`、`--local-drivers/package=~/UniXNU/UniXNU-sirius/...`
（device 侧已填充的 worktree）、`--local-patches/scripts=<ios26 worktree>`。

三个容易踩的点：

- 改完 `configs/*_defconfig` 必须重新跑
  `./unixnu defconfig sirius_ios26_defconfig -D ../UniXNU-sirius-ios26`，否则 `.config` 不变。
- 打包镜像（`scripts/make-uboot-payload.py`）需要 `mcopy`：把 `~/.unixnu/toolchain/bin` 加进 PATH。
- 没有 `[kernel.prelink]` 的板子，payload 默认取 `[kernel] outputs` 的产物；这是本分支在
  `scripts/unixnu/payload.py` 里的改动。

## 5. 刷写与观察

```sh
# 刷 vendor（白名单：vendor；boot/userdata 需要显式允许）
~/UniXNU/UniXNU-sirius/scripts/agent-flash.sh --partition vendor <img> --timeout 240
# 抓 USB 控制台（当前 12377 控制台还不会枚举，所以抓不到内容）
python3 ~/UniXNU/UniXNU-sirius/scripts/capture-usb-console.py --follow --wait 600 \
  --duration 1500 --output out/boot.log --timeline out/boot.timeline --summary out/boot.json
# 打包镜像
PATH=~/.unixnu/toolchain/bin:$PATH python3 scripts/make-uboot-payload.py \
  --payload <payload.img> --unixnu-efi <UniXNU.efi> --darwinboot-efi <DarwinBoot.efi> \
  --marker --no-prelinked-kexts --output out/<name>.img
```

`UniXNU.efi` 与 `DarwinBoot.efi` 复用 iPadOS 17 构建的：
`~/UniXNU/UniXNU/out/sirius/ipados-17.7.11-21H461/sirius/payload/{UniXNU.efi,DarwinBoot.efi}`
（它们是装载器，与内核版本无关）。`--no-prelinked-kexts` 因为 26 内核没有预链接的 Apple kexts，
没有 AppleARMCPU personality 需要中和。

注意 `agent-flash.sh` 在刷完的 25 秒观察窗口里会误报一次 `XNU CDC-ACM console is up`——那是它
`ls /dev/ttyACM*` 的假阳性，不要据此判断。

## 6. 探针与无输出二分

内核镜像（payload 步骤自动）已经带：入口阶段 1（屏幕顶部 1/16 **蓝色**带，写 `0xFF0000FF`）、
阶段 2（第 2 条 1/16 **品红**带，写 `0xFFFF00FF`，紧跟 `msr SCTLR_EL1, x0` 即开 MMU 前）、
以及 panic 屏幕（`_panic` 首字已改成跳转）。设备上出现过彩条 ⇒ 内核确实被启动了。

无输出条件下判断「走到哪」的办法是**复位探针**：内核等 N 秒后走 PSCI `SYSTEM_RESET`，手机自己
回到 fastboot，用时间判断。board.toml 里现成的开关是：

```toml
entry_probe_reset_delay = 8
entry_probe_reset_after = "_PE_init_platform"   # _arm_init 里第 N 次调用该符号（默认 occurrence 1）
# 另有 entry_probe_reset_in / _occurrence / _reset_before
```

一条已知限制：`patch-xnu-entry-probe.py` 的代码洞在同时装 panic 屏幕 + 复位探针时会不够
（`logs/payload-patch.log` 出现 `the padding ends at 0xfffffff00751dff0` 即为此）。要么先去掉
`panic_screen`，要么把探针放进别的空闲零区（`0xfffffff00751c254`、`0xfffffff00751e784` 都是空闲的）。

手工的「入口即复位」版本（`out/blobs/ios26-entry.img`）是我用 `scripts/dev/ios26-entry-probe.s`
汇编出的位置无关桩：把入口补丁阶段 1 桩的最后一条 `b` 改指到该桩，桩里 `cntfrq/cntvct` 等 8 秒后
`smc`（PSCI `0x84000009`）。它验证「复位 → 自动回 fastboot → 计时」这条循环。

## 7. 本分支改了什么（简表）

UniXNU（`cb60245`）：`patches/xnu-12377/`（44 个补丁 + 来源说明）、`scripts/build-xnu-12377.sh`
（格式 2 + Linux 可用）、`scripts/{build-platform,build-pthread,build-storage}-12377.py`、
`kdk-headers.py`、`corecrypto-12377-kpi.h`、`source-pthread-12377.c`、`native-dwc3-console.c`
（含 ADT 触发的复位探针）、`analyze-arm64e-kernelcache.py`、`profiles/arch/arm64-ios26`、
`profiles/os/ipados-26.5.2-23F84-arm64`；驱动侧：`CONFIG_XNU_BUILD_SCRIPT`、`[kernel] outputs`、
无 prelink 时不预链接、`[os] arch` 决定 arch 层、Linux 部署目标跟随 `[os] ios_version`、
payload 取无 prelink 产物的默认值；`build-corecrypto-14.py --xnu-12377`、
`seal-source-crypto-kernel.py --static-if-defaults`、`prepare-storage-headers.py` 接受 337.100.1、
`build-firehose.sh`/`linux/prepare-xnu.py` 接受 fakeroot 与工作树覆盖、`xnu_symbols.kernel_names`
接受 object_directory、`devicetree.py` 的 256 字节种子。

sirius（`1a984ae`）：`board.toml`（新 profile、ADT 的 DWC3 窗口与 `unixnu,qcom-usb0`、
复位探针属性、patch_series 只留 arch/os、`archives = []`）、
`configs/sirius_ios26_defconfig`、`patches/drivers/`（ArmCpu 12377 补丁 rebase 到本板 ArmCpu +
assembly 补丁）、`.github/workflows/kernel.yml` 参数化、`docs/PORT-ios26.md`（更早的计划文档）。

## 8. 未完成

- **P3 板级驱动**：DWC3 控制台的 PHY/时钟/SMMU 准备（从 `package/sirius_usb.c` 移植：
  `sirius_usb_phy_power`、UTMI 选择、`sirius_usb_smmu_plan_bind/apply_bind/guard`）；显示；其余
  `package/` 服务。没有控制台就每一步都盲。
- **存储已移植（2026-11-07，真机验证）**：`CONFIG_XNU_BOARD_STORAGE=y` 让 UniXNU 的
  `build-xnu-12377.sh` 运行 `scripts/build-board-storage-12377.py`，把公开 IOStorageFamily、
  `sirius_emmc.c`、`NativeEmmc.cpp`（`SIRIUS_XNU_12377=1`，只读）和 `SiriusBoardStorage.cpp`
  链进内核；UniXNU 的 `native-board-storage.patch` 在平台设备上匹配 `BoardStorage`。手机上
  得到 disk0 与 disk0s1..disk0s81（vendor = disk0s80，userdata = disk0s81），之后停在
  「Failed to mount root device」，这是没有 root 镜像时的预期结果。日志见
  `docs/evidence/ios26-emmc-20261107.log`。
- **KDK 25F84 APFS/AMFI/Zlib 预链接已验证（2026-11-07，真机验证）**：`scripts/prelink-kdk-apfs-12377.py`
  对开启 `CONFIG_XNU_KDK_IOKIT_ABI=y`、`CONFIG_XNU_APFS_CSR=y`、`CONFIG_XNU_APFS_MLDSA=y` 的
  sirius 内核跑完 5 道门禁（8 个 KDK 可执行文件 0 缺失符号、23 张配对 C++ vtable 全部一致、
  产出 28,131,328 字节普通 ARM64 预链接内核并通过独立指针校验）。手机上 `AppleImage4` 7.0.0、
  `AMFI`（识别 `iPhone18,2`）、`apfs` 2811.121.1、`AppleFSCompressionTypeZlib` 全部正常启动；
  开启 `sirius_emmc_win=1` 后，KDK APFS 在 eMMC 的两个只读 `SHNGROOT` 窗口上完成 `nx_mount`
  并发布 `disk3s1` 与 `disk4s1`（共 89 个 `IOMedia`）。日志见
  `docs/evidence/ios26-apfs-prelink-20261107.log`。
- **xnu-12377 OwnerRoot 桥接、KDK TrustCache ABI 修复与 EL0 `launchd` 执行已验证（2026-11-07，真机验证）**：
  1. `package/NativeEmmc.cpp` 在 `SIRIUS_XNU_12377=1` 下实现 `native_ufs_owner_root_authorized`（启用
     `CONFIG_XNU_NATIVE_UFS_OWNER_ROOT=y` 与 `CONFIG_XNU_TRUST_CACHE_RUNTIME=y`），校验只读挂载、
     `dev_t` 匹配及 `NativeEmmc` 只读 `SHNGROOT` 窗口归属。
  2. 修复 `prepare-xnu-source.py` 把 iOS 16 (`xnu-8796.101.5`) 的 `EXTERNAL_HEADERS/TrustCache/Types.h`
     写进 `.cache/xnu-work` 遮蔽 KDK 25F84 头文件的问题（旧头文件缺少 `TrustCache_t::tombstoned` 与
     `kTCQueryTypeMobileAssetCode = 0x03`，导致 `invalid query type: 3`）。
  3. 在手机上对现有只读 `SHNGROOT` `OS` 窗口做端到端探测（镜像 `out/ios26-rootauth3.img` `84b99f7c...`，
     日志 `docs/evidence/ios26-rootauth-launchd-20261107.log`）：`load_static_trust_cache` 成功加载 2 个外部
     trust cache 模块，KDK APFS 将 `disk4s1` 挂载为 `/`，`OwnerRoot` 放行（`apple_autherr=80, owner=1`），
     Zlib 解压 `/sbin/launchd` 与 `/usr/lib/dyld`，AMFI 验签通过，`execve` 返回 0
     （`proc-trace: exec pid 1 ppid 0 ? error 0`），PID 1 在 EL0 用户态跑起 `dyld` 与 `libignition`，随后通过
     USB ` ZETA:REBOOT-BOOTLOADER
` 无按键退回 fastboot。
  4. 下一步只剩离线准备解密并降级后的 iPhone18,2 23F84 `OS`/`SystemOS` APFS 容器与合并静态 trust cache。
- **P4 userland 降级**：zeta 的 `rewrite-zeta-userland.py`、`zeta_dyld_cache.py`、
  `make-zeta-trust-bundle.py`、`grow-zeta-apfs-container.py`、`write-zeta-apfs-files.py`、
  `replace-zeta-root-files.py`、`make-zeta-root-delta.py` 需要泛化移植；需要下载 23F84 的 iPadOS
  26.5.2 还原镜像（约 7 GB），并注意 zeta 记录的 `.dmg.aea` 加密问题。
- UniXNU 本地树落后 upstream main 约 376 个提交（upstream 已有 ios26 profile 与 256 字节种子等
  修复）；是否升级到 upstream main 是一个需要决策的点。
- 当前探针工具在「panic 屏幕 + 复位探针」同时启用时 padding 不够（见 §6）。

## 9. 下一步（按顺序）

1. 人工唤醒手机回 fastboot（当前是暗的，没有软件办法）。
2. 刷 `out/blobs/ios26-entry.img`，看手机是否在约 8-9 秒后自己回到 fastboot ⇒ 验证复位循环与计时。
3. 用 `entry_probe_reset_after` 逐符号二分（先 `_PE_init_platform`；必要时先关 `panic_screen`）。
4. 并行做 DWC3 控制台的 PHY/时钟/SMMU 移植（根本的可见性），之后重启一次内核即可拿到完整日志。
5. 再往上是存储 → APFS/root → launchd → userland（P4）。

## 10. 坑清单

- `pkill -f capture-usb-console` 会把自己的 shell 一起杀掉（命令行里含同样字符串）；用精确 PID。
- `mcopy`/`mformat` 在 `~/.unixnu/toolchain/bin`，不在默认 PATH。
- 改 defconfig 后必须重跑 `unixnu defconfig`。
- payload 步骤会自动给内核打入口探针 + panic 屏幕补丁（`logs/payload-patch.log`），它会吃掉代码洞。
- 探针工具的偏移常量是 4k baseline 专用，但会按符号表自适应；仍会因 padding 不足而失败（§6）。
- `agent-flash.sh` 的 "XNU CDC-ACM console is up" 是假阳性。
- `~/UniXNU/UniXNU-ios26/out/feeds` 是指向主检出 `out/feeds` 的软链（共享 feed 缓存）。
- 本机内核工具链需要 root 才能原生安装；现成方案是 `~/.unixnu/run.sh`（user namespace + overlay）。
- 交接时请把 `/tmp` 里的镜像与脚本复制进仓库 `out/`（已在 `out/blobs/` 放了入口复位镜像）。

## 11. 2026-11-05 后半夜：自恢复循环（重要修正）

上一轮的「复位探针」**从来没能回到 fastboot**：屏幕上看到的是一秒后重启进系统、不断循环。
原因不是探针没跑，而是 sirius 上 **PSCI SYSTEM_RESET 只是普通重启**：ABL 从 PM660 的
`PON_SOFT_RB_SPARE`（SPMI USID 0、PID 0x08、offset 0x8f）取重启原因，原因 0 就是「继续启动」。
必须先把 `(spare & 1) | (2 << 1) = 0x04` 写进去，下一次 ABL 才会停在 fastboot。这是 iPadOS 17
端口 `package/BootReset.c` 早就验证过的序列，26 的探针把它漏掉了。

已改（本分支未提交）：

- `scripts/native-dwc3-console.c`（UniXNU `ios26` worktree）：
  - 新增 PMIC 标记：`unixnu_qcom_mark_bootloader()`（首次调用时映射 SPMI core/cnfg/obs/rw 四个窗口）
    与 `unixnu_qcom_mark_bootloader_mapped()`（不映射，供 panic 上下文用）。守卫与 10063 相同：
    arbiter v5.x、PON owner=AP、subtype 0x04，写入后回读校验。开关是 ADT 的 `unixnu,qcom-reset=1`。
  - `reset_bootloader()`、探针复位、`unixnu_qcom_reset_to_bootloader()` 都先标记再 PSCI。
    `serial_init` 里提前调用一次 `reset_initialize()`，这样 panic 时标记不需要再 `ml_io_map`。
  - **阶段标记**：`paint_stage()` 在屏幕底部画 16×16 的阶段方块（stage N 号位），只映射那一条
    （整屏放不进 `io_submap`）。`/chosen/unixnu,display`（base/width/height/rowbytes）由 board.toml 提供。
    阶段：1 serial_init 进入、2 板级准备开始、3 板级准备完成、4 端点配置前、5 RUN 前、6 已枚举。
    方块比入口彩条可靠：它不依赖入口探针，也不吃 padding。
- 探针桩（`scripts/patch-xnu-entry-probe.py`）：阶段 3/4 与 `--reset-before` 在复位前 `bl mark`
  （直接 `bl`，一个指令，不吃洞里的字面量池）。
- panic 屏幕（`scripts/xnu-panic-screen.c`）：`--panic-reset N` 用上以后，先调用
  `PANIC_RESET_MARK`（= 上面那个 mapped 版本，符号由 patcher 从内核符号表解析）再 PSCI。
- 驱动（UniXNU `scripts/unixnu/payload.py`）：`[payload] panic_reset` 转发成 `--panic-reset`。
- `scripts/ios26-await-return.sh`：等 `fastboot devices` 重新看到 `21e05050`，打印回 fastboot 用了多少秒
  （时间本身就是「走到哪」的证据；`--timeout` 默认 300 秒）。
- board.toml：`panic_reset = 60`（panic 停留 60 秒后自动回 fastboot，够拍照）、
  `[adt.chosen.bytes] unixnu,display`、`[adt.chosen.u32] unixnu,qcom-reset = 1`。

现在一次刷写后的完整行为应当是：开机 → U-Boot 菜单 → 内核 → 屏幕底部出现阶段方块 → 要么 USB 枚举
出控制台，要么 panic 文字停留 60 秒 → 自动回 ABL fastboot（`fastboot devices` 可见 `21e05050`）。
**这条循环一旦成立，后面就能无人值守地二分**，不再需要人工按键。

构建：`~/.unixnu/run.sh bash -c 'cd ~/UniXNU/UniXNU-ios26 && UNIXNU_STEPS=kernel,payload python3
~/UniXNU/UniXNU-sirius-ios26/scripts/build-ios26-dev.py'`（约 7 分钟；先 assemble 再 build）。

坑：`build-platform-12377.py` 会审计编译出的 `NativeDwc3Console.o`：`__text` 里每个
`isb` 后面必须紧跟 `mrs ..., cntpct_el0`，否则报 “Native USB timeout ISB lacks its emitted
CNTPCT_EL0 read”。所以这个文件里不要写裸 `isb`（用 `dsb`）。

## 12. 探针二分：从 stage 3 开始

第一次用 `ios26-stages.img` 刷写（02:43）后手机 300 秒内没有回到 fastboot，USB 也没有枚举。
没有输出时有三种可能，只能靠「回不回来」区分：内核没走到 serial_init、内核死在
`unixnu_qcom_mark_bootloader` 的 `ml_io_map` 上（映射在 MMU 之后、IOKit 之前本来就不一定可用）、
或者标记本身没写进 PMIC。

所以下一次刷写把入口探针的 **stage 3**（MMU 打开后 10 秒）打开：桩先在 `serial_init` 里
`reset_initialize()` 映射好 SPMI，再由 `bl mark` 写 PMIC 0x88f，然后 PSCI 复位。
`board.toml` 里就是 `entry_probe_reset_delay = 10`（没有 `entry_probe_reset_after`，所以是
stage 3 而不是 stage 4）。判读：

- 手机约 10 秒后自己回到 `fastboot devices` → 标记与复位通路成立，死亡点在 serial_init 之后，
  下一步换 stage 4（`_PE_init_platform`）或直接看底部阶段方块/控制台。
- 仍然自己重启进系统 → PMIC 标记这条路在内核态不通（或 SPMI 映射失败），退回 U-Boot 的
  `abl_fastboot`（`pmic write 0x88f 0x04; reset`，已在该板上验证过），改用「内核画屏幕 + 人读」。
- 完全不动 → 内核连 MMU 之后的桩都没执行到，范围收窄到 `_arm_init` 之前。

这一轮之前的一次 `ios26-stages.img`（没有探针）就是上面第一种失败，屏幕上的现象是
「一秒后重启进系统」的循环——这是用户观察到的，也是把 PMIC 标记做进内核的直接原因。
