# sirius 阶段二：本机状态与下一次验证

更新：2026-10-30。

## 当前结论

**尚不能宣称这台设备已成功启动到可用 iOS 界面。** 本机已通过 OS、SystemOS、可写 Data 挂载和 launchd early boot，SpringBoard/backboardd 等进程已执行，但 256 MiB Data 卷随后出现 ENOSPC。USB 后来静默，不能通过日志确认当前屏幕内容。

完整证据、时间口径和镜像哈希见 [本机启动核验](evidence/sirius-20261030-local-boot.md)。[HANDOFF-AGENT.md](HANDOFF-AGENT.md) 保留主机工具链恢复信息，但它把扩容 seed 的 checkpoint 索引 `0/0` 判断为损坏的结论已被后续挂载成功的事实推翻。

## 已在本机观察到的阶段

本机刷入 kernel `de820c21…` / payload `cd7e050f…`，Data 容器为 **256 MiB**，位于绝对 LBA `35627008`：

- `OwnerData` 挂载为 `/dev/disk3s1`。
- launchd 打印 `Early boot complete`，采集相对时间为 132.2 秒。
- SpringBoard、backboardd、iconservicesagent、PosterBoard 有 exec 记录；这不等于这些进程完成初始化或界面可用。
- GOP display 注册，最多确认六次 framebuffer copy。
- FTS521 初始化、HID 驱动发布和 TOUCH down/move/up 坐标被记录；没有本机可见 UI 响应证据。
- Adreno 616 GMU phase 2 打印 HFI v1 回答；尚无 Metal 设备或完整 GPU 图形渲染驱动。
- 从采集时间 185.8 秒起，containermanagerd 报 ENOSPC。`ephemeral-storage=1` 仍不足以让 256 MiB Data 支撑启动负载。

`docs/evidence/` 中的 2026-10-28/29 HuajiFruit 日志来自参考仓库，不应作为这台设备的新测试结果。`porting.toml` 保留本仓库历史并增加本机结果，没有替换成参考仓库的完成状态。

## 已修正、已构建、尚未上机的候选

- 原始 kernel SHA-256：`b94137f50c1558336e7df296ffd1157a1d4b0a850b8b9458b5b52d223c1ad971`
- 完整 vendor 镜像 SHA-256：`a5620f295a09ca8ca6d179d12669777cf2d8356b42307505a7c4bd39a61709bb`
- 镜像路径：`../UniXNU/out/sirius/ipados-17.7.11-21H461/sirius/payload/sirius-payload-usb-control.img`
- 镜像内 trust cache：`7416ca4f8377146626bb284e2133c76bee6cb4e3ee0af5d648b186235a7323a7`
- 仍带 256 MiB RAM seed 兜底；新候选本身**不解决 Data 容量不足**。

修正内容：

- USB RX 长度从完成 TRB 的 residual 字节计算，检查 HWO/error，而非取 endpoint event 高位。
- FASTBOOT/REBOOT 只识别精确的换行命令，支持跨 USB 包分片，不匹配任意字符串中的子串。
- 两个 USB poll 入口互斥，保护共享 event cursor、TRB 和 TX 状态。
- SPMI 单字节命令的 byte count 字段为 0（长度减一）；拒绝在错误版本、owner、subtype 上写 PMIC。
- 复位前必须读、写、读回成功；失败时不继续 PSCI fastboot 复位。
- 已从默认 boot args 移除 `sirius-panic-autoreset=15`：panic buffer 可能保存 recovered assertion，不能仅凭它非空就复位。

构建、prelink、payload sidecar/boot args 校验和 USB helper 回归通过。未提交、未推送、未刷此候选。

## 自动调试脚本的实际状态

`agent-flash.sh` 的上一轮 boot/vendor/Data 刷写和一次 U-Boot fastboot bounce 已成功执行。**自动从 XNU 返回 ABL fastboot 未验证成功**：当前旧内核收到主机提交的九字节 `FASTBOOT\n` 后没有枚举 fastboot，后续 12 秒控制台读取为零字节。

SPMI 日志证明了 PM660 PON 的读取（v5、channel 7、owner 0、subtype 04、spare 00），不证明写/readback/PSCI 路线成功。软件定时线程也不能保证从整机硬锁死恢复。没有独立硬件 watchdog backstop。

脚本行为已收紧：

- 默认仅刷 vendor；未知 product 不执行命令。
- `--userdata-window` 必须同时显式给 `--allow-userdata`，只允许在首轮初始化 Data；普通重刷不要重复使用它。
- boot/Data 前置刷写失败后停止，不继续刷 vendor 或重启。
- 串口命令只发送给 SIRIUS01，使用有界非阻塞写。
- 控制台摘要应分别记录启动阶段、存储错误、panic、进程、帧复制，不能从进程 exec 推断可见 UI；旧的 `ios-ui-up` 判定已被撤回。

目前未留任何自动刷机守护进程。当前 USB 静默的旧内核不能远程安装上述修复，需要人工返回一次 ABL fastboot。

## Data 容量修复的外部依赖

主机为 Arch Linux，无 `hdiutil/diskutil`，也没有 GitHub CLI；当前公开 release 只有 32 和 256 MiB seed。**不能用 truncate 或手工修改 NXSB/spaceman 元数据替代 APFS 扩容。**

已提供 [grow-data-container-macos.sh](../scripts/grow-data-container-macos.sh)，在 macOS 上对现有 seed 的副本扩容，保留输入并拒绝覆盖输出：

```bash
bash scripts/grow-data-container-macos.sh existing-owner-data.cdr owner-data-8g.cdr
```

脚本语法和 Linux 拒绝执行检查已通过；macOS 实际扩容尚未运行。拿到 8 GiB 容器后，在本机用 `make-data-window-image.py` 生成 sparse 窗口镜像并校验几何、哈希、分区边界。初始化会替换现有 Data，刷写前必须有用户对该替换的明确授权。

## 下一次测试的完成标准

1. 先仅刷新的 vendor 候选，验证 USB 流和 FASTBOOT readback/复位是否恢复；保留现有 Data。
2. 准备并核验足够大的 APFS 容器，获准后一次性替换 Data 窗口。
3. 持续抓取 boot 日志；要求 early boot 通过且无 ENOSPC/致命 panic，同时观察到持续帧呈现。
4. 最终通过真机画面与实际交互确认 Hello/Setup 或 SpringBoard 可见且响应触摸；不能以进程启动摘要代替这项验证。
