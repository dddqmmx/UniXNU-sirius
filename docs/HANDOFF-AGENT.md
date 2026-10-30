# sirius 交接：现状、下一步、以及「全自动调试环路」的设计任务

写给接手的 agent。目标不变：**让这台小米 8 SE (sirius, SDM710) 用 UniXNU 的内核把
iPadOS 17.7.11 (21H461) 用户态跑起来**。下面的每一条都来自 2026-10-30 的实际硬件观测，
不是推测；没上机验证的会写明。

---

## 0. 一台机器上的环境（必须先恢复）

主机：Arch Linux，**没有免密 sudo**，没有 podman/docker。仓库外的东西都在 `~/.unixnu/`：

| 路径 | 作用 |
| --- | --- |
| `~/.unixnu/run.sh CMD...` | **所有内核构建都走它**。`unshare -rm` + 每次新建的 overlay，把 `/opt` 绑到 `~/.unixnu/root/opt`（里面有 `unixnu-linux` 工具链），把带 Darwin shim（`arch csh xcrun plutil codesign`）的 overlay 绑到 `/usr/bin`。没有它，`setup-toolchain.py` 装不进去，XNU 的 makefile 也找不到 `/usr/bin/xcrun`。 |
| `~/.unixnu/toolchain/bin` | 自编的 `unifdef tcsh mtools lzfse`（`run.sh` 会把它放进 PATH）。 |
| `~/.unixnu/apfsprogs/usr/bin` | 解包出来的 `apfs-label`、`fsck.apfs`（只读检查 APFS 镜像；`fsck.apfs` 几乎不输出，别指望它诊断）。 |
| `~/.unixnu/get-seed.sh 32` | 从 GitHub Release 下 Data seed 并解密到 `/tmp/seed-32/`。 |
| `~/.unixnu/flash-when-fastboot.sh IMAGE` | 轮询 `fastboot devices`，**只在 `product: sirius`（ABL）时**刷 vendor，然后 `fastboot reboot`；遇到 U-Boot 自己的 fastboot（`product: mtp`）会先 reboot 它。日志在 `~/.unixnu/flash-when-fastboot.log`。 |
| `/etc/udev/rules.d/99-sirius-console.rules` | XNU 的 USB 控制台（`1209:0001`）权限 0666。**已经装好**；重装机器时需要重装。 |

构建命令（都在 `/home/dddqmmx/UniXNU/UniXNU`，设备仓在 `../UniXNU-sirius`）：

```sh
./unixnu defconfig sirius_defconfig -D ../UniXNU-sirius   # 改 defconfig / board.toml 后
./unixnu tree                                            # 设备仓的脚本/package 同步进树
~/.unixnu/run.sh bash -c './unixnu build kernel'         # XNU + 归档 + prelink（15-40 分钟）
~/.unixnu/run.sh bash -c './unixnu build payload \
    --trust-cache ~/UniXNU/UniXNU-sirius/.cache/firmware/target-volumes/original-static-system.bundle \
    --data-ramdisk /tmp/seed-32/owner-data.cdr'
# 加上 UniXNU.efi + DarwinBoot.efi + BOOTUNIXNU 标记，得到可刷的 FAT 卷：
cd out/sirius/ipados-17.7.11-21H461/sirius/tree
python3 -I ~/UniXNU/UniXNU-sirius/scripts/make-uboot-payload.py \
  --payload ../payload/payload.img \
  --unixnu-efi ../package/Build/Out/AARCH64-DEBUG/UniXNU.efi \
  --darwinboot-efi build/DarwinBoot/DEBUG_CLANGPDB/AARCH64/DarwinBoot.efi \
  --marker --output ../payload/sirius-payload-data.img
```

其它必须知道的构建事实：

- **U-Boot 只能用 GCC 交叉工具链编**。clang + 手搓的 `aarch64-linux-gnu-*` shim 能编出镜像，
  但 ABL 会拒绝加载（`Failed to load/authenticate boot image: Load Error`）。装
  `aarch64-linux-gnu-gcc`，然后 `cd ~/UniXNU/UniXNU-sirius && ./uboot/build.sh
  CROSS_COMPILE=aarch64-linux-gnu- JOBS=16`。
- 设备仓 `uboot/src` 的 gitlink 指着一个**上游不存在的提交**（`667807d…`）。已用
  `git config submodule.uboot/src.update none` 绕过；实际 checkout 在 `a2a847b1a84`（分支
  sirius/v2.0，`porting.toml` 记录的就是它）。`unixnu fetch` 因此不碰它。
- **`dtbo` 分区必须擦掉**（`fastboot erase dtbo`），否则 U-Boot 起不来。已经擦过。
- Data seed **只能由 macOS 的 hdiutil/diskutil 生成**。设备仓
  `.github/workflows/data-seed.yml` 在 GitHub macOS runner 上生成，推它到 `main` 就会跑，
  并把加密产物发到 Release `data-seed-<size>mib`（解密口令 `unixnu-sirius-bringup`）。
  **`size_mib=32` 是可直接用的那份**；**`256` 那份被 `diskutil apfs resizeContainer` 撑大后
  APFS 认不出卷（checkpoint desc/data 都是 0），不要用**。想不用 GitHub：任何一台 Mac 上跑
  UniXNU 的 `scripts/prepare-owner-data-fixture.py` 即可（32 MiB 就是它的原始产物）。

---

## 1. 已经完成并上机验证的部分

提交在 `57UU/UniXNU-sirius`（fork 是 `dddqmmx/UniXNU-sirius`，`main` 与本地一致，
最新 `ec21d02`）。逐条都对应一次真机启动的串口日志（`/dev/ttyACM0`）：

1. **构建链**：Linux 上的 XNU 内核（PAN 版）、UniXNU.efi、DarwinBoot.efi、payload FAT 卷、
   U-Boot boot 镜像都能从仓库复现；U-Boot 用 GCC 编的那份被 ABL 接受。
2. **PAN 修复（关键）**：`CONFIG_XNU_NATIVE_PAN=y`。SDM710 的 `SCTLR_EL1.SPAN` 为 0，EL0
   进异常时硬件自动置 `PSTATE.PAN`；`PAN=n` 的 copyio 没有清 PAN 的代码，于是 pid 1 在
   `load_init_program_at_path` 的 `copyout()` 里对 `0x1000` 反复取故障（数千万次）。
   打开后：8 个核全过 copyout，`execve activate 0`，launchd 的 syscall 74/372/294/20/427/381/5/…
   全部返回。**驱动不再自己清 PAN**（参考实现也不清）。
3. **共享缓存**：launchd 的 `shared_region_check_np`（294）原本 ENOMEM → 打开
   `CONFIG_XNU_OWNER_CACHE_MOUNT/STARTUP/UNION_SYSTEMOS` + `XNU_BRINGUP_OWNER_CACHE`，
   SystemOS cryptex 以 union 挂在 `/` 上，`CONFIG_XNU_NATIVE_CS_HASH_CACHE=y`，ADT 带
   `/chosen/boot-manifest-hash`。现在 294 返回 0，`fcntl(92)`/`mmap(197)`/`munmap(73)` 也返回 0。
4. **eMMC 提速**：驱动改用参考实现的实现（`package/NativeEmmc.cpp` 现在就是参考版 +
   少量桩），多块 CMD18/Auto-CMD12、256 块一段；时钟阶梯 `sirius_emmc_hs=3` 已上机到
   **50 MHz**（`EMMC fast rate=50000 kHz multi=on run=256`，`ext_csd rev=8 width=2 timing=1`）。
5. **payload/窗口**：`[payload] size_mib = 512`（内核+seed 放得下），
   `[payload.adt] owned_memory_size = "0xaa000000"`（2.66 GiB，与参考实机一致）。
   32 MiB seed 落在 `0xa3be1000`（`NEMMC data seed at 00000000a3be1000`）。
6. **SystemOS 授权**：`sirius-allow-unverified=1` 让板级钩子
   `native_verified_block_root_authorized` / `native_verified_block_volume_device`
   授权 eMMC 窗口，`NEMMC owner volume /dev/disk6s1` → SystemCryptex 挂载成功。
7. **看门狗**：`sheng-disable-watchdogd=1 sheng-lite=1`。

**最后一次真机停在**（内核 `70b55019…` 之前的 `2baf271`，卷 `8db16ee3…`）：
`offline scratch Data mount failed: 2 @kern_exec.c:8230` → panic → 面板红色 panic 屏。
即 **Data 卷挂载失败（ENOENT）**。同一启动里已经看到：
`disk1 device_handle block size 4096 block count 8192`、`checkpoint search: largest xid 4 …
desc 6 data 10` —— 说明 32 MiB seed 的**容器**被 APFS 认出来了，但 `disk1s1` 没出现
（参考实机日志里是 `disk3s1 OwnerData mount-complete`）。

### 最新一次改动（已提交 `ec21d02`，**尚未上机**）

把 `package/NativeEmmc.cpp` 整个换成参考实现的驱动（`HuajiFruit/UniXNU-sirius`），它自己
把 RAM seed 发布成读写块设备（`prepareDataSeed()` / `ramReadWrite()`），并用
`native_ram_block_volume_seed()` 回答 owner Data 挂载。唯一新增：为它通过 `weak_import`
读取的**只用于日志**的量（`sirius_cpufreq_*`、`sirius_fb_*`）补了定义，否则静态链接不过。
`kern_exec.c` 里也给 `native_ram_block_gpu_addon` 补了「没有」的空实现（在驱动里）。

对应内核 `d2dd4dfe…`、卷 `121f277a…`，内容与 sha256 见 §5。

---

## 2. 下一步该做什么（按优先级）

1. **先刷最新镜像看 Data 卷**（卷已在磁盘上，`~/.unixnu/flash-when-fastboot.sh
   .../payload/sirius-payload-data.img`；手机进 ABL fastboot 即自动刷 + 重启；刷完 U-Boot
   fastboot 会再弹出来一次，`fastboot -s 21e05050 reboot` 即可进 XNU）。
   要看的行：`NEMMC data volume /dev/diskN s1`、`OwnerData … mount-complete`、
   `load_init_program: attempting to load /sbin/launchd`、launchd 的 syscall 序列。
   参考实机的完整对照日志：
   `https://raw.githubusercontent.com/HuajiFruit/UniXNU-sirius/e02fa174…/docs/evidence/sirius-usb-console-20261028-data.log`。
2. 若 Data 卷仍不出现：`native_ram_block_volume_seed()` 用「provider 链上有 RAM 窗口」来找
   卷；打开 `NEMMC media table after publish` 一行（参考驱动自带）看 APFS 究竟发布了哪些
   IOMedia。注意参考实机 `[payload.adt] owned_memory_size = 0xaa000000` 且 seed 在
   `0xa3cc1000`，与我们现在的位置几乎一致，所以窗口本身不是原因。
3. Data 卷成功后，launchd 的第一批 boot task 会跑起来（参考实机进程 `mount -P 1` 曾以 66
   退出，需要 `/chosen/boot-manifest-hash` —— 我们已经加了；之后还有 `mount-phase-2`、
   `Early boot complete`）。参考实现后续的坑（Data 卷 ENOSPC、watchdogd、`sirius-skip-setup`）
   见 `docs/devices/sirius-huajifruit.md` 与 `uniXNU` 仓 `docs/.vitepress/device-records/`。
4. 显示/输入/GPU 都是 `planned`：面板是 U-Boot 的 framebuffer 文本，不是 Darwin 显示驱动。
   参考实机已经在做 GopFramebuffer / 触摸，可作为后续路线。

---

## 3. 设计任务：让 agent 全自动调试（重点）

现状：每一次迭代都要**人**把手机弄进 ABL fastboot（音量下 + 电源），agent 才能刷；
刷完还得**人**确认。这是整条环路上唯一必须人工的地方。请设计并实现一条
**agent 可以无人值守跑无数轮**的环路，包含下面四块，并在
`porting.toml` 的 `diagnostics` 区记录设计与证据：

### 3.1 结论 → fastboot，不需要按键

已有一半的零件，逐个验证并接线：

- **U-Boot 侧**：`unixnu-sirius.env` 里已有 `check_bcb=bcb load 0 misc; if bcb test command ~
  uboot-fastboot; then bcb clear command; bcb store; run fastboot; …`，
  `bootmenu_3=Enable fastboot mode=run fastboot`。`CONFIG_FASTBOOT_OEM_RUN=y`、`CONFIG_DM_PMIC=y`、
  `CONFIG_PMIC_QCOM=y` 都已打开（`~/.unixnu/../UniXNU-sirius/uboot/build/.config`），只差
  `CONFIG_CMD_PMIC`（`cmd/pmic.c`，`depends on DM_PMIC`，为它打开后 `pmic list/dev/read/write`
  就能用）。
- **进 ABL fastboot 的正路**：把 `PON_SOFT_RB_SPARE`（PM660 SPMI USID PID 8，offset 0x8f，
  U-Boot `pmic` 命令里写作寄存器 `0x88f`）写成 `0x04`（= `mode-bootloader`），然后 PSCI
  `SYSTEM_RESET`。这是 Linux `reboot bootloader` 做的事，ABL 认它。设计里必须给出：
  用 `pmic list` / `pmic dev` 找出 PM660 的 U-Boot 设备名（`pmic_qcom` 读 DTS `reg` 取
  USID），先 `pmic read 0x88f` 证明可读、再 read-back 验证写入，最后才 reset。
  **不要**刷写失败就盲写 PMIC：先在 U-Boot 命令行里验证（串口/U-Boot 自己的 fastboot
  `oem run` 都能试，`CONFIG_FASTBOOT_OEM_RUN` 已开）。
- **内核侧（不需要 U-Boot 也成立的路）**：`package/BootLog.c` 已实现
  `sheng-autoreset=<秒>`：到点写 reset cell 并 `boot_reset_bootloader()`（PSCI
  `SYSTEM_RESET`），`package/BootReset.c` 在 sirius 上是 fajita 的占位实现
  （`boot_reset_mark_bootloader()` 返回「fajita 不需要 cell」——**对 sirius 是错的**，
  sirius 的 cell 是 PM660 `0x88f`，需要从内核里通过 SPMI 写，或至少把
  `0x04` 通过 U-Boot 侧写）。同时 `package/BootLog.c` 的落盘走的是 `native_ufs_log_*`，
  sirius 没有 UFS，这条线程实际不跑（`boot_log_tick` 是 weak，`BootProgress.c` 只在
  `boot_log_tick` 存在时调用）。**设计里要明确**：sirius 需要自己的「到点就 reset」实现，
  最省事的形态是内核里一个线程：读 `sheng-autoreset=<秒>`，到点 PSCI reset（先把复位
  原因写进 SPMI spare，使这次 reset 落在 ABL fastboot）。现成的 SPMI 读写参考在
  Android/Linux 的 `spmi-msm` 驱动与 U-Boot `drivers/spmi/spmi-msm.c`。
- 另外 `scripts/patch-xnu-entry-probe.py` 支持 `--reset-delay N` / `--reset-after SYMBOL
  --in FUNCTION --occurrence N` 做「到点复位」，但它只做 PSCI reset，不写 BCB/PMIC，落在
  ABL 的**普通启动**而不是 fastboot；可以作为「挂了也能重来」的兜底，不能单独完成闭环。

### 3.2 自动刷写与重启

- `~/.unixnu/flash-when-fastboot.sh` 已经是「等到 ABL fastboot → 刷 vendor → reboot」。
  需要补：**刷完回到 XNU 也自动**（U-Boot fastboot 弹出时 `fastboot reboot`）；
  刷写失败（`Status read failed`）重试而不是退出；把每轮镜像的 sha256 与时间写进日志。
- 建议把它移进仓库（`scripts/agent-flash.sh`），因为它是调试环路的一部分，不能只活在
  `~/.unixnu/`。

### 3.3 串口即日志

- XNU 的 CDC-ACM 控制台（`1209:0001`，`/dev/ttyACM0`，udev 已给 0666）。**读法很关键**：
  内核只在主机 attach 时把环形缓冲交给主机一次；`cat` 抓到的第一段就是本次启动的完整尾部。
  现有的 `scripts/capture-usb-console.py --follow` 支持断线重连，但**实测它的 by-id 路径
  在权限修复前后都会报 EACCES**，直接 `cat /dev/ttyACM0` 最可靠。
- 环形缓冲会**滚掉早期行**（例如 `NEMMC pan`/`EMMC fast` 在几秒后就没了），所以设计里
  要么在 attach 之前就 `cat`（刷完 reboot 后立刻起一个 `cat` 到文件），要么让内核在
  panic/关键 checkpoints 重新打印。
- 设计一个「一行一条事件、可 grep」的采集脚本：设备出现 → 开始抓，N 秒无输出或匹配到
  `NEMMC PANIC TEXT END` → 停；产出 `boot-<kernel-sha>-<time>.log`，另出一份
  **结构化摘要**（已到哪一阶段：`execve activate`? `OwnerData mount-complete`?
  `Early boot complete`? panic 文本?）。

### 3.4 环路本身

一个可重复的脚本（`scripts/agent-loop.sh` 或 `unixnu agent-cycle`）：
`刷写 → reboot → 抓串口 → 摘要 → 判定结果 → 自动回到 fastboot → 下一轮`。
要求：

- 输入：要刷的镜像、每轮超时、最大轮数。
- 结束条件：抓到的日志里出现「本轮目标」或 `panic`；每轮把「内核 sha、payload sha、
  卷 sha、关键行、耗时」追加到 `docs/evidence/agent-loop.md`。
- 失败自愈：手机不在任何 USB 状态 → 尝试 PMIC/BCB 路径；U-Boot fastboot 弹出来 →
  `fastboot reboot`；`fastboot devices` 空 → 等 60 s 再试。
- **安全**：只允许 `flash vendor`（和必要时的 `erase dtbo`、`flash boot`）。脚本里把要碰
  的分区名写成白名单，其它一律拒绝；`userdata` 只在显式参数下才允许。

设计交付物请写进 `docs/HANDOFF-AGENT-2.md`（或合并到 `docs/HANDOFF.zh-CN.md`），
并在 `porting.toml` 的 `diagnostics` 区补 findings/evidence（真机验证过才算）。

---

## 4. 已知的坑（踩过的）

| 现象 | 原因 / 处理 |
| --- | --- |
| `fastboot getvar` 全都不回、`press key to shutdown` | USB **3.0** 口的问题。用 `~/.unixnu/usb2-only.sh off` 把 SuperSpeed 口禁用（写 `/sys/bus/usb/devices/usbN/*/port*/disable`，需要 root），拔插后设备以 480M 枚举即正常。 |
| `Failed to load/authenticate boot image: Load Error` | boot 镜像不是 GCC 编的，或 `dtbo` 没擦。 |
| ABL 刷写 `Status read failed (No such device)` | ABL 的 fastboot 偶发；重刷一次即可（守护脚本要重试）。 |
| U-Boot 自己的 fastboot（`product: mtp`）刷不了 | 未知，参考实现也记录过；不要指望它，只用来 `oem run` / `reboot`。 |
| `unixnu fetch` 报 `not our ref 667807d…` | 设备仓 `uboot/src` gitlink 指向不存在的提交；`git config submodule.uboot/src.update none`。 |
| `unixnu build payload` 报 payload 太大 | `[payload] size_mib`（现在 512）。 |
| DarwinBoot 打 `NO WINDOW` | `owned_memory_size` 找不到一整段连续内存；现在用 2.66 GiB（参考实机同值）。 |
| Data seed 容器在但卷不出现 | 256 MiB（被 resize 过）那份不行，用 32 MiB 原始 fixture。 |
| 串口权限 | `99-sirius-console.rules` 已装；否则 `sudo chmod 666 /dev/ttyACM0`。 |

---

## 5. 当前产物（2026-10-30 16:00）

```
kernel/kernel.macho             sha256 d2dd4dfeda6bac7b4ef9e24c5ee87f0f5c17fea49830bec42c9e6732cafb96c2
payload/sirius-payload-data.img sha256 121f277a1bee008ac4f26bfd5abc434e2ea6b2ce60df8933e32f0658fb722276
                                (512 MiB，含 32 MiB seed，卷 7F00D031-C00B-4368-A3FF-D4788A1323D6)
kernel args: debug=0x14e kt_fixed_zones=120 … sheng-smp=1 cpus=8 armcpu-timer-intid=19
             sheng-pmgr=2 sirius_usb=2 sirius_emmc_hs=3 sirius_emmc_win=1 launchdsuffix=release
             amfi_get_out_of_my_way=1 sirius-allow-unverified=1 sirius-skip-rootauth=1
             sheng-disable-watchdogd=1 sheng-lite=1
```
（完整 kernel args 由 `unixnu build payload` 打印；`kernel.macho` 里已含 panic screen 补丁。）
