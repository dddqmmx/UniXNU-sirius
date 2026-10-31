# Unattended Agent Debug Loop Record (`sirius`)

| Time (Local) | Round | Kernel SHA-256 | Payload SHA-256 | Verdict | Early Boot | Key Spawns / Panic | Duration |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-10-30 17:56:40 | 1/1 | `de820c21b7af` | `cd7e050f355e` | `storage-full` (corrected) | 132.2 | UI processes executed; ENOSPC from 185.8s; only six framebuffer copies confirmed | 365.3s |
| 2026-10-30 19:24:57 | 2/2 | `b94137f50c15` | `a5620f295a09` | `storage-failure` | 111.7 | Unattended `FASTBOOT\n` -> ABL fastboot verified in 4.2s; existing 256 MiB Data full | 330.0s |
| 2026-10-31 19:19:04 | 3/3 | `ad6a97095a20` | `afa7def2f867` | `ui-processes-started` | 134.8 | In-kernel APFS `containerResize` `8192 -> 2097152` blocks (8 GiB); 0 ENOSPC; `Setup` launched at 294.6s; 5514 frames copied (~19 fps, nonblack verified) | 380.4s (+25s live) |
| 2026-10-31 23:08:50 | 4/4 | `ff57d694b4ba` | `fad069ea1538` | `ui-processes-started` | 129.9 | Fixed `verify_swap failed` freeze on `1536x2048` canvas via full-screen scaling blit (`1536x2048 -> 1080x2240`); 1332/1332 swaps accepted (0 rejected), `Setup` at 194.6s, fast-tap flush and SpringBoard no-dim prefs active | 280.0s |

The original automatic verdict was `ios-ui-up`, inferred solely from process launches. It was incorrect. See [local boot verification](sirius-20261030-local-boot.md). Times are measured from capture start, not from reboot.
