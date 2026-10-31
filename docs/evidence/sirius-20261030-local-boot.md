# Local boot verification — 2026-10-30

This record concerns the Arch Linux host and the connected sirius unit. Logs copied from HuajiFruit are reference observations on another setup, not proof of this unit's behavior.

## Deployed images

- Kernel: `de820c21b7af8a9430bcb19938242dd0297ba232e689f9a31c698443d98ba920`.
- Vendor payload: `cd7e050f355ed6474b170019966ed7c608b99216e1f9e170bd73ff8b862b32f7`.
- Data: 256 MiB APFS container at absolute LBA 35627008, not an 8 GiB container.
- Capture: `out/boot-de820c21b7af-20261030-174714.timeline`, 365.3 seconds from capture start. Arrival timestamps are not kernel uptime or exact times from reboot; the capture started after the flashing script's post-reboot wait.

Selected first-arrival lines:

```text
32.0 SIRIUS RESET: spmi ver=0x50000000 ch=7 owner=0 subtype=0x04(rc=0) spare=0x00(rc=0)
32.0 NEMMC data window lba 35627008
32.0 NEMMC data window blocks 524288
32.2 NEMMC data volume (eMMC) /dev/disk3s1
32.2 apfs_log_op_with_proc:3091: disk3s1 mount-complete volume OwnerData, requested by: init (pid 1); parent: kernel_task (pid 0)
32.2 SIRIUS DISPLAY: GOP display registered (1080x2240 at 0x9c000000, rowBytes 4320)
32.2 GPU phase 2: GMU up, HFI v1 answers (phase=10)
33.1 TOUCH hid event driver IOHIDEventDriver published as the built-in touch screen
132.2 [System Event] [2693715266] [INFO] [Subsystem: launchd] [Event: boot] Early boot complete. Continuing system boot.
132.7 SPAWN 122s exec 27 SpringBoard
162.2 SPAWN 151s exec 55 backboardd
185.8 fs_tx_enter_internal:27549: disk3s1 containermanagerd: ENOSPC: ttype DELETE num_blocks 6010 free_blocks 5938 blocks_in_txn 22 num_records 2000 num_data_blocks 0 num_active_txns 1 (reserved ranges total blocks: 3490) current xid: 78 threshold_blocks 0
289.3 fs_map_file_offset_ext:21029: disk3s1 Cannot start transaction with XID 0: No space left on device(28)
296.2 NEMMC fb copies 6
```

Conclusion: root, SystemOS and writable Data mount; early boot finishes; UI processes execute; a few frames are copied and touch coordinates are received. This does not establish a visible, usable SpringBoard. The original `ios-ui-up` verdict was a false success: it only checked process launches and ignored ENOSPC. `ephemeral-storage=1` does not make a 256 MiB Data volume sufficient.

## Follow-up on the running kernel

A 12-second console capture returned zero bytes. A separate nonblocking write submitted nine bytes (`FASTBOOT\n`); no bytes were received in the observation window and the device remained USB `1209:0001`, with no fastboot device. Thus only SPMI reads are device-verified; the write/readback/reset route and unattended return remain unverified. A silent CDC device does not prove the entire kernel stopped.

## Corrected candidate (built, not flashed)

- Kernel: `b94137f50c1558336e7df296ffd1157a1d4b0a850b8b9458b5b52d223c1ad971`.
- Vendor payload: `a5620f295a09ca8ca6d179d12669777cf2d8356b42307505a7c4bd39a61709bb`.
- Image: `../UniXNU/out/sirius/ipados-17.7.11-21H461/sirius/payload/sirius-payload-usb-control.img`.
- USB receives the actual completed TRB's residual count, rejects hardware-owned/error TRBs, and parses exact newline-terminated commands across packets. The monitor and USB worker serialize access to the shared event cursor and TRBs.
- SPMI byte count is length minus one (zero for a single byte). Bootloader writes are restricted to the expected v5 PM660 PON channel/owner/subtype; a failed read or write/readback verification prevents fastboot reset.
- Default panic autoreset removed: the panic text buffer can contain a recovered assertion while the kernel is still running.
- Flashing refuses unknown products and stops after failed prerequisite writes. Reinitializing a Data window requires explicit `--allow-userdata`.

Host checks: USB C helper regression and four mock fastboot refusal/failure tests pass. Full kernel build and prelink pass. No new image was flashed, no Data volume was replaced, and no commit or push was made in this follow-up.

## Remaining dependencies

The reproducible preparation script is `scripts/grow-data-container-macos.sh EXISTING.cdr NEW-8G.cdr` (run with Bash on macOS; shell syntax and Linux refusal checked, macOS execution pending). It preserves its input, grows a copy and verifies the resulting volume. A larger APFS Data container must be generated/expanded using macOS `hdiutil` and `diskutil`, then checked before deployment. The Linux host has neither, GitHub CLI is absent, and the public releases currently contain only 32 and 256 MiB seeds. A raw truncate alone does not expand APFS. Do not patch spaceman or NXSB fields by hand as a substitute.

Deployment also requires the currently unresponsive unit to return to ABL fastboot manually once. Preserve the existing Data container or get explicit authorization before replacing it with a freshly generated seed. No unattended flash daemon has been left running.

## Device result of the corrected candidate (flashed 19:24, vendor only)

- Flashed vendor payload `a5620f295a09ca8ca6d179d12669777cf2d8356b42307505a7c4bd39a61709bb` over the existing 256 MiB Data window (kernel `b94137f5...`). Data was not replaced.
- Capture `out/usb-control-boot.timeline`, 330 s: SPMI read `ch=7 owner=0 subtype=0x04 spare=0x00`; `OwnerData` mounted at `/dev/disk3s1` (11.0 s); `Early boot complete` at 111.7 s; SpringBoard, backboardd, iconservicesagent, PosterBoard executed; no fatal panic.
- Storage still fails: 932 ENOSPC and 130 "No space left" lines. The first ENOSPC is at 11.0 s with `free_blocks 774`, and the xid is 228289 where the first run was in the low thousands. **The existing Data window is not clean: the previous boot's writes persisted and left almost no free space.** A fresh container is required, not just a bigger one.
- **Unattended return to ABL fastboot verified**: writing `FASTBOOT\n` to `/dev/ttyACM0` produced `fastboot devices` output after 4.2 s with no button press. This validates the TRB-residual length fix, the exact-line parser and the PM660 write/readback path.
- `agent-flash.sh` had a defect found on the way: with no SIRIUS01 tty present, the unmatched glob made `readlink` fail under `set -e`, so it exited before flashing (log showed only the start line). Fixed with `nullglob` and a regression test; the full suite (25 tests) passes.

## In-kernel 8 GiB APFS Data container resize and Setup UI boot (2026-10-31 19:19)

- Kernel: `ad6a97095a206e35eb250701c808638d62b0bd85f6abb7da4a1c29e7092a81a8`.
- Vendor payload (`sirius-payload-8g-setup.img`): `afa7def2f86774f356c0f5997e4802cb37a6606c2b4782bb8de7b13a6d77519f` (carrying the 32 MiB clean APFS Data fixture `cd3bbd46d39bd105254043a3a39966a83d20ff162d167a2e2fbbf02c431abd35`, volume UUID `7F00D031-C00B-4368-A3FF-D4788A1323D6`).
- Deployment: `scripts/agent-flash.sh` sent `FASTBOOT\n` over `/dev/ttyACM0`, transitioned the running XNU kernel into ABL fastboot (`product: sirius`) in 7 s without button presses, flashed `vendor` only, and rebooted into XNU.
- Root causes addressed:
  1. **8 GiB persistent APFS Data container without external macOS tooling**: When `NativeEmmc::prepareDataWindow()` observes an eMMC Data container smaller than `2097152` 4 KiB blocks (8 GiB) at LBA `35627008`, it writes the clean 32 MiB seed from `/chosen/memory-map/RAMDisk` into the start of the 8 GiB eMMC write window (`16777216` 512-byte sectors) and publishes the 8 GiB `NativeEmmcBlock` physical store. Immediately after `kern_exec.c` mounts `/dev/disk3s1` (`OwnerData`), `BootWatchdogPolicy.c` invokes `sirius_apfs_try_resize_data("post-mount")`, which calls the prelinked Apple `com.apple.filesystems.apfs` method `AppleAPFSUserClient::containerResize` (`0xfffffff005673e7c`, prologue-verified) on the mounted `AppleAPFSContainer` with `new_size = 0` (grow to fill physical store).
  2. **Setup UI vs `sirius-skip-setup=1` without Metal**: With no Adreno 616 Metal driver yet (only GMU phase 2 is active), `sirius-skip-setup=1` bypassed `Setup.app` and forced `SpringBoard` into the lock-screen wallpaper path (`PosterBoard`), which crash-looped with `SIGABRT` every 5 s and left display presentation stuck at 4–6 frames. Removing `sirius-skip-setup=1` allows `SpringBoard` to launch CPU-rendered `Setup.app`.
- Selected lines from `out/boot-8g-setup2.timeline` and `out/boot-8g-setup2-live.timeline`:

```text
33.0 NEMMC data window: writing clean seed sectors 65536
33.0 NEMMC data window: clean seed written, blocks 8192
33.0 NEMMC data window: armed to grow APFS container to blocks 2097152
33.0 NEMMC data window lba 35627008
33.0 NEMMC data window blocks 16777216
33.1 NEMMC data volume (eMMC) /dev/disk3s1
33.1 apfs_log_op_with_proc:3091: disk3s1 mount-complete volume OwnerData, requested by: init (pid 1); parent: kernel_task (pid 0)
33.1 NEMMC resize: calling containerResize at post-mount
33.1 containerResize:4837: Called on Container
33.1 containerResize:4906: disk3 Resize: growing [8192,0] -> [2097152,0]
33.1 nx_resize:6889: disk3 growing non-fusion 8192+0 -> 2097152+0
33.1 nx_resize:6891: disk3 8192 -> 2097152, total required metadata 6439 blocks
33.1 nx_resize_alloc_metadata_ranges:6637: disk3 find_new_metadata(): [6160]@365, [195]@6525, [68]@6720, [16]@6788
33.1 NEMMC resize: rc/status 0000000000000000
33.1 NEMMC resize: nxsb blocks after 2097152
40.9 NEMMC touch events/contacts/hid/reports 0002000004000000
134.8 [System Event] [2582260902] [INFO] [Subsystem: launchd] [Event: boot] Early boot complete. Continuing system boot.
135.2 SPAWN 122s exec 27 SpringBoard
165.7 SPAWN 152s exec 55 backboardd
171.9 SPAWN 158s exec 102 iconservicesagent
209.0 NEMMC fb copies 5
209.0 NEMMC fb frame nonblack 41011
294.6 SPAWN 281s exec 219 Setup
297.2 SPAWN 283s exec 223 WidgetRenderer_Default
297.9 SPAWN 284s exec 224 splashboardd
300.2 SPAWN 287s exec 226 AccessibilityUIServer
313.3 NEMMC fb copies 146
313.3 NEMMC fb frame nonblack 28199
377.1 NEMMC fb copies 1327
377.1 NEMMC fb copy mean us 10326
377.1 NEMMC fb swaps calls/acc/rej 0005300053000000
```

- Follow-up capture (`out/boot-8g-setup2-live.timeline`, beat `0x119` = 281 beats / ~560 s uptime):
  - `NEMMC fb copies 5514`, `NEMMC fb swaps calls/acc/rej 00158a0158a00000` (5514 swaps submitted, 5514 accepted, 0 rejected; sustained ~19 fps at ~10 ms mean CPU copy time).
  - `NEMMC load user % 11`, `sys % 12`, `idle % 75`.
  - `enospc_count: 0`, `no_space_left_count: 0`, `storage_error_count: 0`, `fatal_panics: []`.

## Fix for post-interaction UI freeze (`verify_swap failed` on 1536x2048 canvas) and fast-tap coalescing (2026-10-31 23:08)

- Diagnosis (`out/hang-check.timeline` and `out/boot-swapfix2.timeline`):
  1. After 163,393 accepted frames (~2.5 hours uptime) and 46 touch contacts on `Setup.app`, `backboardd` switched its render canvas to the iPad7,1 native `1536x2048` (`0x0600x0x0800`) BGRA surface with source rectangle `r0 = (0, 0, 1536, 2048)` and destination rectangle `r1 = (0, 0, 1080, 2240)` (`NEMMC fb nonstrict surf w/h/fmt 0600080042475241`, `r0 0000000006000800`, `r1 00000000043808c0`).
  2. `NativeLegacyDisplay::validateSwap` previously required both `r0` and `r1` to equal `(0, 0, 1080, 2240)` and `surfH >= 2240`, so it rejected every swap from `swap id 163394` onward with `NativeLegacyDisplay: verify_swap failed for swap id: ...` (`reason = 3`), freezing the screen while `RenderServer` retried at 60 Hz.
  3. In `package/sirius_touch.c`, a fast tap whose `EVT_ENTER` (`0x13`) and `EVT_LEAVE` (`0x33`) both arrived within one 16 ms poll batch set and cleared `contact_down` before `publish_contacts()` ran, coalescing the tap away.
  4. When `sirius-skip-setup=1` was removed, `/private/var/mobile/Library/Preferences/com.apple.springboard.plist` (`BKNoWatchdogs`, `FBLaunchWatchdogExceptions`, `SBDisableAutoDim`, `SBDontDimOrLockOnAC`, `SBDisableIdleSleep`) was also skipped.
- Fixes:
  - `drivers/GopFramebuffer/NativeLegacyDisplay.cpp`: accept scaled full-screen `IOSurface` swaps (`r0 = (0, 0, surfW, surfH)` -> `r1 = (0, 0, width, height)`), implement a 64-bit 8-pixel unrolled nearest-neighbor scaling blit (`1536x2048 -> 1080x2240`, matching the `[0..30480]x[0..20320]` normalized touch axes across the full panel), fallback-accept/retire multi-layer or blank swaps so `RenderServer` never stalls, and keep GOP `setPowerState` in state 1.
  - `package/sirius_touch.c`: flush `publish_contacts()` with a 10 ms separation whenever `EVT_ENTER` and `EVT_LEAVE` for the same touch ID occur in the same FIFO drain batch.
  - `package/BootWatchdogPolicy.c`: always write `com.apple.springboard.plist` (`BKNoWatchdogs`, `SBDisableAutoDim`, `SBDontDimOrLockOnAC`, `SBDisableIdleSleep`) even when `Setup.app` is not skipped.
- Hardware verification (`out/boot-scale.timeline`, kernel `ff57d694b4ba38fadd74f396e51e49669f941f180fb0e87e776850c770b96dc5`, vendor payload `fad069ea153835c7244de7ea8dc782f34fec7a8dc0f76b393b439173346e0825`):
  - Persistent 8 GiB eMMC Data volume mounted directly at `/dev/disk3s1` (`16777216` sectors, `enospc_count: 0`).
  - `sirius springboard prefs: /private/var/mobile/Library/Preferences/com.apple.springboard.plist 0`.
  - `Early boot complete` at 129.9 s, `SpringBoard` at 130.4 s, `backboardd` at 135.0 s, `Setup` at 194.6 s (`Setup` started 100 s earlier on the initialized persistent Data volume).
  - `NEMMC fb copies 1331`, `NEMMC fb swaps calls/acc/rej 0005340053400000` (1332 swaps submitted, 1332 accepted, **0 rejected**, **0 `verify_swap failed`**, ~15–25 ms per scaled frame at ~19 fps).
