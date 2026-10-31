# sirius 阶段二：本机状态与验证结论

更新：2026-10-31。

## 当前结论

**这台设备已成功挂载并在线扩容 8 GiB 持久化 APFS Data 容器（0 ENOSPC），通过 launchd early boot，启动 `SpringBoard`、`backboardd` 与 `Setup.app`（iOS 欢迎界面），并以约 19 fps（平均每帧 CPU 拷贝 ~10 ms）持续呈现非黑 UI 帧（已累计确认 5514 帧提交与接受、0 拒绝，且内置触摸屏 HID 驱动已绑定并投递触摸报告）。**

完整证据、时间线和镜像哈希见 [本机启动核验](evidence/sirius-20261030-local-boot.md)。

## 已上机验证的最新镜像（2026-10-31 23:08）

- 原始 kernel SHA-256：`ff57d694b4ba38fadd74f396e51e49669f941f180fb0e87e776850c770b96dc5`
- 完整 vendor 镜像 SHA-256：`fad069ea153835c7244de7ea8dc782f34fec7a8dc0f76b393b439173346e0825`
- 镜像路径：`../UniXNU/out/sirius/ipados-17.7.11-21H461/sirius/payload/sirius-payload-swapfix.img`
- 镜像内 trust cache：`7416ca4f8377146626bb284e2133c76bee6cb4e3ee0af5d648b186235a7323a7`
- 镜像内 32 MiB 原始干净 Data seed：`cd3bbd46d39bd105254043a3a39966a83d20ff162d167a2e2fbbf02c431abd35`（卷 UUID `7F00D031-C00B-4368-A3FF-D4788A1323D6`）

## 关键突破与真机观测结果

1. **全自动无按键返回 ABL fastboot**：
   - 修复 DWC3 RX TRB residual 长度计算、跨包精确行解析（`SiriusUsbControl.h`）及 SPMI PM660 PON `0x88f = 0x04` 单字节命令编码（`bc = len - 1 = 0`）与回读校验后，`scripts/agent-flash.sh` 向 `/dev/ttyACM0` 发送 `FASTBOOT\n`，设备在 4.2–7.0 秒内自动进入 ABL fastboot（`product: sirius`）并完成刷写与重启。
2. **无需外部 macOS 的内核态 8 GiB APFS Data 容器在线扩容**：
   - `NativeEmmc::prepareDataWindow()` 检测到 eMMC LBA `35627008` 上的 APFS 容器小于 `2097152` 个 4 KiB 块（8 GiB）时，自动将 payload `/chosen/memory-map/RAMDisk` 中的 32 MiB 干净原始 fixture 写入 eMMC 窗口起始位置，并将 `NativeEmmcBlock` 物理存储发布为 8 GiB（`16777216` 个 512B 扇区）。
   - `kern_exec.c` 将 `/dev/disk3s1`（`OwnerData`）挂载到 `/private/var` 后，`BootWatchdogPolicy.c` 立即调用 `sirius_apfs_try_resize_data("post-mount")`，直接调用预链接 `com.apple.filesystems.apfs` 中的原版 `AppleAPFSUserClient::containerResize`（`0xfffffff005673e7c`，先校验指令序言）并将目标大小设为 `0`（自动扩满 8 GiB 物理存储）。
   - 真机日志确认：`containerResize:4906: disk3 Resize: growing [8192,0] -> [2097152,0]`，`nx_resize:6889: disk3 growing non-fusion 8192+0 -> 2097152+0`，返回状态 `0`，重读 LBA `35627008` 处 `NXSB` 块数为 `2097152`（8 GiB）。
   - 整个 600 秒运行周期内 **`enospc_count: 0`、`no_space_left_count: 0`、`storage_error_count: 0`**。
3. **移除 `sirius-skip-setup=1`，恢复 CPU 渲染的 iOS `Setup.app` 欢迎界面**：
   - 在尚无 Adreno 616 Metal 渲染驱动的阶段，`sirius-skip-setup=1` 会跳过 `Setup.app` 并使 `SpringBoard` 卡在依赖 Metal 的 `PosterBoard` 锁屏壁纸崩溃循环（每 5 秒 `SIGABRT`），导致帧拷贝停留在 4–6 帧。
   - 移除 `sirius-skip-setup=1` 后，真机在 134.8s 完成 early boot，135.2s 启动 `SpringBoard`，165.7s 启动 `backboardd`，171.9s 启动 `iconservicesagent`（CoreUI fixup 生效不崩溃），294.6s 启动 `Setup`（PID 219）及 `WidgetRenderer_Default`、`splashboardd`、`AccessibilityUIServer`。
   - 画面呈现从 5 帧（Apple logo，`nonblack 41011`）进入 `Setup` 持续刷新（`nonblack 28199`），累计确认 **5514 次帧拷贝（5514 次 swap 接受、0 拒绝，平均拷贝耗时 ~10.3 ms，稳定 ~19 fps）**，CPU 负载 `user 11% / sys 12% / idle 75%`。
4. **触摸屏与 KeyStore 状态服务**：
   - `AppleKeyStoreState`（`offline-key-events=1 sheng-aks=1`）累計处理 100 次打开、1430 次状态查询（`NEMMC aks opens/calls 0000006400000596`）。
   - FTS521 触摸控制器与 `ShengHidInput` 组合设备（`dk=0 sheng-touch-hid=1`）由内核态 `IOHIDEventDriver` 绑定为内置触摸屏（`hid_stage = 4`），已验证触摸坐标读取与 HID 报告投递。

## 后续可选方向

1. **Adreno 616 GPU Phase 3 与 Metal 驱动**：在已跑通的 GMU Phase 2（HFI v1 应答、GX 供电）基础上，继续实现 HFI 性能/带宽表、A615 寄存器初始化、ZAP shader、SQE 固件与 CP 环形缓冲区，移植 `Adreno6xx` / `AdrenoMetal`，消除壁纸扩展（`KaleidoscopePoster` 等）对 Metal 的缺失崩溃，届时即可重新启用 `sirius-skip-setup=1` 进入完整 SpringBoard 主屏幕。
2. **eMMC DDR52 / HS200 提速**：当前 eMMC 运行在 50 MHz 8-bit HS SDR（约 30 MiB/s），开启 DDR52 可将前两分钟的 shared cache 哈希校验时间再缩短一半。
