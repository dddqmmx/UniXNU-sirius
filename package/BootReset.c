// SPDX-License-Identifier: MIT
// SDM710 (sirius) SPMI PON_SOFT_RB_SPARE + PSCI SYSTEM_RESET glue for unattended fastboot return.
//
// On SDM710 with PM660 (USID 0), the PON peripheral is at PID 0x08 (pon@800 in pm660.dtsi,
// compatible "qcom,pm8998-pon", Gen2 PON). Its software reboot-reason register is
// PON_SOFT_RB_SPARE at offset 0x8f (register 0x88f in U-Boot's PID:offset encoding).
// Gen2 PON encodes the reboot mode in bits [7:1] (reason_shift = 1), and pm660.dtsi defines
// mode-bootloader = <0x2>, so writing (spare & 0x01) | (0x02 << 1) = 0x04 into 0x88f before
// a PSCI SYSTEM_RESET makes ABL stop in fastboot (product: sirius) without holding Volume-Down.
//
// Mapped once during normal boot (boot_reset_init) so that only the two 4 KiB pages for the
// PON channel's observer and read-write windows are taken from io_submap (which is 8 MiB total),
// and subsequent reads/writes in any context (heartbeat, USB RX command, panic autoreset) are
// lock-free MMIO accesses.
#include <kern/thread.h>
#include <kern/clock.h>
#include <machine/machine_routines.h>
#include <string.h>
#include <pexpert/pexpert.h>

extern void sirius_usb_log_char(char c);
extern char boot_progress_panic_text[2048];

extern volatile uint32_t armcpu_wdog[6], armcpu_imem[4];
extern volatile int64_t armcpu_scm[8];
extern volatile uint64_t armcpu_wdog_pets, armcpu_deadline_start, armcpu_deadline_ticks, armcpu_deadline_seen;
extern volatile uint64_t armcpu_wdog_live[14] __attribute__((weak_import));
extern void armcpu_wdog_sample(void) __attribute__((weak_import));
extern volatile uint32_t native_data_persist_state __attribute__((weak_import));
extern volatile uint32_t native_data_persist_header __attribute__((weak_import));
extern volatile uint64_t native_data_persist_present __attribute__((weak_import));
extern volatile uint64_t native_data_persist_writes __attribute__((weak_import));
extern volatile uint64_t native_data_persist_errors __attribute__((weak_import));
extern volatile uint64_t native_data_writeback_flushes __attribute__((weak_import));
extern volatile uint64_t native_legacy_copy_n __attribute__((weak_import));
extern volatile uint64_t native_legacy_copy_last __attribute__((weak_import));
extern volatile uint64_t native_legacy_copy_max __attribute__((weak_import));
extern volatile uint64_t native_legacy_copy_sum __attribute__((weak_import));

enum {
  PsciVersion = 0x84000000u,
  PsciFeatures = 0x8400000au,
  PsciSystemReset = 0x84000009u,

  SpmiCorePa = 0x0c440000u,
  SpmiCnfgPa = 0x0c40a000u,
  SpmiChnlsPa = 0x0c600000u,
  SpmiObsPa = 0x0e600000u,

  PmicArbVersion = 0x0000u,
  PmicArbFeatures = 0x0004u,
  ApidMapOffsetV5 = 0x0900u,
  ApidMapOffsetV3 = 0x0800u,

  SpmiRegCmd0 = 0x00u,
  SpmiRegConfig = 0x04u,
  SpmiRegStatus = 0x08u,
  SpmiRegWdata = 0x10u,
  SpmiRegRdata = 0x18u,

  SpmiCmdWriteLong = 0x00u,
  SpmiCmdReadLong = 0x01u,

  PonUsid = 0x00u,
  PonPid = 0x08u,
  PonSubtypeOff = 0x05u,
  PonSoftRbSpareOff = 0x8fu,
  PonBootloaderVal = 0x04u,   // mode-bootloader (2) << reason_shift (1)
};

static volatile int spmi_initialized;
static uint32_t spmi_hw_ver;
static int spmi_pon_channel = -1;
static uint32_t spmi_pon_owner = 0xff;
static volatile uint32_t *spmi_obs_base;
static volatile uint32_t *spmi_rw_base;
static uint8_t spmi_pon_subtype;
static uint8_t spmi_pon_initial_spare;

static uint32_t sirius_autoreset_s;
static uint32_t sirius_panic_autoreset_s;
static unsigned sirius_panic_seen_at;
static volatile int sirius_reset_triggered;

static void reset_usb_puts(const char *s)
{
  while (*s) {
    sirius_usb_log_char(*s++);
  }
}

static uint64_t psci(uint64_t fid, uint64_t a1)
{
  register uint64_t x0 __asm__("x0") = fid, x1 __asm__("x1") = a1, x2 __asm__("x2") = 0, x3 __asm__("x3") = 0;
  __asm__ volatile("smc #0" : "+r"(x0), "+r"(x1), "+r"(x2), "+r"(x3) :: "memory", "x4", "x5", "x6", "x7", "x8", "x9", "x10", "x11", "x12",
                   "x13", "x14", "x15", "x16", "x17");
  return x0;
}

static int spmi_pon_read(uint8_t off, uint8_t *val)
{
  if (!spmi_obs_base) {
    return -1;
  }
  spmi_obs_base[SpmiRegConfig / 4] = 0;
  __asm__ volatile("dsb sy" ::: "memory");
  uint32_t cmd = ((uint32_t)SpmiCmdReadLong << 27) | ((uint32_t)off << 4);
  spmi_obs_base[SpmiRegCmd0 / 4] = cmd;
  __asm__ volatile("dsb sy" ::: "memory");
  uint32_t status = 0;
  for (unsigned tries = 0; tries < 200000; ++tries) {
    status = spmi_obs_base[SpmiRegStatus / 4];
    if (status != 0) {
      break;
    }
  }
  if (status != 1u) {
    return -2;
  }
  *val = (uint8_t)(spmi_obs_base[SpmiRegRdata / 4] & 0xffu);
  return 0;
}

static int spmi_pon_write(uint8_t off, uint8_t val)
{
  if (!spmi_rw_base || spmi_hw_ver < 0x50000000u || spmi_hw_ver >= 0x70000000u ||
      spmi_pon_owner != 0 || spmi_pon_subtype != 0x04) {
    return -1;
  }
  spmi_rw_base[SpmiRegConfig / 4] = 0;
  spmi_rw_base[SpmiRegWdata / 4] = (uint32_t)val;
  __asm__ volatile("dsb sy" ::: "memory");
  /* The byte-count field is length minus one: zero for this one-byte write. */
  uint32_t cmd = ((uint32_t)SpmiCmdWriteLong << 27) | ((uint32_t)off << 4);
  spmi_rw_base[SpmiRegCmd0 / 4] = cmd;
  __asm__ volatile("dsb sy" ::: "memory");
  uint32_t status = 0;
  for (unsigned tries = 0; tries < 200000; ++tries) {
    status = spmi_rw_base[SpmiRegStatus / 4];
    if (status != 0) {
      break;
    }
  }
  if (status != 1u) {
    return -2;
  }
  return 0;
}

void boot_reset_init(void)
{
  if (__atomic_exchange_n(&spmi_initialized, 1, __ATOMIC_ACQ_REL) != 0) {
    return;
  }
  volatile uint32_t *core = (volatile uint32_t *)ml_io_map(SpmiCorePa, 0x2000);
  volatile uint32_t *cnfg = (volatile uint32_t *)ml_io_map(SpmiCnfgPa, 0x2000);
  if (!core || !cnfg) {
    reset_usb_puts("SIRIUS RESET: spmi core/cnfg map failed\n");
    return;
  }
  spmi_hw_ver = core[PmicArbVersion / 4];
  uint32_t max_ch = core[PmicArbFeatures / 4] & 0x7ffu;
  if (max_ch == 0 || max_ch > 512) {
    max_ch = 512;
  }
  uint32_t map_off = (spmi_hw_ver >= 0x50000000u) ? ApidMapOffsetV5 : ApidMapOffsetV3;
  int found_ch = -1;
  uint32_t found_owner = 0xff;
  for (uint32_t i = 0; i < max_ch; ++i) {
    uint32_t periph = core[(map_off + 4u * i) / 4u];
    uint32_t usid = (periph >> 16) & 0xfu;
    uint32_t pid = (periph >> 8) & 0xffu;
    if (usid == PonUsid && pid == PonPid) {
      uint32_t owner = cnfg[i] & 0x7u;
      if (found_ch < 0 || owner == 0) {
        found_ch = (int)i;
        found_owner = owner;
        if (owner == 0) {
          break;
        }
      }
    }
  }
  spmi_pon_channel = found_ch;
  spmi_pon_owner = found_owner;
  if (found_ch < 0) {
    reset_usb_puts("SIRIUS RESET: pon pid 0x08 not found in SPMI APID map\n");
    return;
  }
  uint64_t obs_pa = (uint64_t)SpmiObsPa + (uint64_t)found_ch * 0x80ULL;
  vm_offset_t obs_page = ml_io_map((vm_offset_t)(obs_pa & ~0xfffULL), 0x2000);
  if (obs_page) {
    spmi_obs_base = (volatile uint32_t *)(obs_page + (obs_pa & 0xfffULL));
  }
  uint64_t rw_pa = (uint64_t)SpmiChnlsPa + (uint64_t)found_ch * 0x10000ULL;
  vm_offset_t rw_page = ml_io_map((vm_offset_t)rw_pa, 0x2000);
  if (rw_page) {
    spmi_rw_base = (volatile uint32_t *)rw_page;
  }
  uint8_t sub = 0xff, spare = 0xff;
  int r1 = spmi_pon_read(PonSubtypeOff, &sub);
  int r2 = spmi_pon_read(PonSoftRbSpareOff, &spare);
  spmi_pon_subtype = sub;
  spmi_pon_initial_spare = spare;

  char msg[160];
  snprintf(msg, sizeof(msg),
           "SIRIUS RESET: spmi ver=0x%08x ch=%d owner=%u subtype=0x%02x(rc=%d) spare=0x%02x(rc=%d)\n",
           spmi_hw_ver, spmi_pon_channel, spmi_pon_owner, sub, r1, spare, r2);
  reset_usb_puts(msg);

  uint32_t auto_s = 0, panic_s = 0;
  if (!PE_parse_boot_argn("sheng-autoreset", &auto_s, sizeof(auto_s))) {
    PE_parse_boot_argn("sirius-autoreset", &auto_s, sizeof(auto_s));
  }
  PE_parse_boot_argn("sirius-panic-autoreset", &panic_s, sizeof(panic_s));
  sirius_autoreset_s = auto_s;
  sirius_panic_autoreset_s = panic_s;
  if (sirius_autoreset_s > 0) {
    char mark_msg[96];
    extern int boot_reset_mark_bootloader(char *, unsigned);
    boot_reset_mark_bootloader(mark_msg, sizeof(mark_msg));
  }
}

int boot_reset_probe(char *text, unsigned cap)
{
  boot_reset_init();
  const uint64_t version = psci(PsciVersion, 0);
  const int64_t reset = (int64_t)psci(PsciFeatures, PsciSystemReset);
  uint8_t spare = 0xff;
  int rc = spmi_pon_read(PonSoftRbSpareOff, &spare);
  snprintf(text, cap,
           "psci v%llx reset=%lld | spmi v%x ch=%d own=%u sub=%02x spare=%02x(rc=%d) auto=%us",
           (unsigned long long)version, (long long)reset, spmi_hw_ver, spmi_pon_channel,
           spmi_pon_owner, spmi_pon_subtype, spare, rc, sirius_autoreset_s);
  return (version >= 0x2 && spmi_pon_channel >= 0 && rc == 0) ? 0 : 1;
}

int boot_reset_mark_bootloader(char *text, unsigned cap)
{
  boot_reset_init();
  uint8_t before = 0xff, after = 0xff;
  int r_before = spmi_pon_read(PonSoftRbSpareOff, &before);
  if (r_before != 0) {
    snprintf(text, cap, "pon 0x88f read failed (%d); refusing write", r_before);
    return -1;
  }
  uint8_t want = (uint8_t)((before & 0x01u) | PonBootloaderVal);
  int w_rc = spmi_pon_write(PonSoftRbSpareOff, want);
  int r_after = spmi_pon_read(PonSoftRbSpareOff, &after);
  int ok = (w_rc == 0 && r_after == 0 && (after & 0xfeu) == PonBootloaderVal);
  snprintf(text, cap, "pon 0x88f: %02x -> %02x (want %02x) w=%d r=%d %s",
           before, after, want, w_rc, r_after, ok ? "ok" : "FAIL");
  char line[128];
  snprintf(line, sizeof(line), "SIRIUS RESET: %s\n", text);
  reset_usb_puts(line);
  return ok ? 0 : -1;
}

int boot_reset_watchdog(uint32_t seconds, char *text, unsigned cap)
{
  snprintf(text, cap, "sirius: no independent hardware watchdog (%u s requested)", seconds);
  return 1;
}

int boot_reset_bootloader(char *text, unsigned cap)
{
  char mark[80];
  int mrc = boot_reset_mark_bootloader(mark, sizeof(mark));
  snprintf(text, cap, "reset: PSCI SYSTEM_RESET (%s)", mark);
  if (mrc != 0) {
    reset_usb_puts("SIRIUS RESET: refusing reset: bootloader reason not verified\n");
    return mrc;
  }
  reset_usb_puts("SIRIUS RESET: calling PSCI SYSTEM_RESET -> ABL fastboot\n");
  __asm__ volatile("dsb sy\n\tisb" ::: "memory");
  psci(PsciSystemReset, 0);
  return 99;
}

void sirius_reset_to_fastboot(const char *reason)
{
  if (__atomic_exchange_n(&sirius_reset_triggered, 1, __ATOMIC_ACQ_REL) != 0) {
    return;
  }
  char line[128];
  snprintf(line, sizeof(line), "SIRIUS RESET: triggered (%s)\n", reason ? reason : "requested");
  reset_usb_puts(line);
  char result[120];
  int result_code = boot_reset_bootloader(result, sizeof(result));
  if (result_code != 0) {
    __atomic_store_n(&sirius_reset_triggered, 0, __ATOMIC_RELEASE);
  }
}

void sirius_reset_normal(const char *reason)
{
  if (__atomic_exchange_n(&sirius_reset_triggered, 1, __ATOMIC_ACQ_REL) != 0) {
    return;
  }
  boot_reset_init();
  uint8_t before = 0;
  if (spmi_pon_read(PonSoftRbSpareOff, &before) == 0) {
    spmi_pon_write(PonSoftRbSpareOff, before & 0x01u);
  }
  char line[128];
  snprintf(line, sizeof(line), "SIRIUS RESET: normal reboot (%s)\n", reason ? reason : "requested");
  reset_usb_puts(line);
  __asm__ volatile("dsb sy\n\tisb" ::: "memory");
  psci(PsciSystemReset, 0);
}

void boot_log_tick(unsigned now)
{
  if (!spmi_initialized) {
    boot_reset_init();
  }
  if (sirius_autoreset_s > 0 && now >= sirius_autoreset_s) {
    sirius_reset_to_fastboot("sheng-autoreset deadline");
  }
  if (boot_progress_panic_text[0] != 0) {
    if (sirius_panic_seen_at == 0) {
      sirius_panic_seen_at = now ? now : 1;
    }
    uint32_t delay = sirius_panic_autoreset_s ? sirius_panic_autoreset_s : (sirius_autoreset_s ? 15u : 0u);
    if (delay > 0 && now >= sirius_panic_seen_at + delay) {
      sirius_reset_to_fastboot("panic-autoreset");
    }
  }
}
