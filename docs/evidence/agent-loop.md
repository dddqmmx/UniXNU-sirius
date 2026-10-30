# Unattended Agent Debug Loop Record (`sirius`)

| Time (Local) | Round | Kernel SHA-256 | Payload SHA-256 | Verdict | Early Boot | Key Spawns / Panic | Duration |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-10-30 17:56:40 | 1/1 | `de820c21b7af` | `cd7e050f355e` | `storage-full` (corrected) | 132.2 | UI processes executed; ENOSPC from 185.8s; only six framebuffer copies confirmed | 365.3s |

The original automatic verdict was `ios-ui-up`, inferred solely from process launches. It was incorrect. See [local boot verification](sirius-20261030-local-boot.md). Times are measured from capture start, not from reboot.
