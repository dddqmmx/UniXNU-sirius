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
