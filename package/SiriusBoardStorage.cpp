/*
 * BoardStorage of sirius for the iOS 26 (xnu-12377) kernel.
 *
 * UniXNU's native-board-storage.patch matches the IOService class BoardStorage on the platform
 * expert device, and the public IOStorageFamily classes on what it publishes. Here that class runs
 * the two calls the 10063 kernel made from bsd_init (patches/xnu/sirius-usb-console.patch): the SDCC1
 * transport probe of sirius_emmc.c, then NativeEmmc, which publishes the eMMC as an
 * IOBlockStorageDevice. scripts/build-board-storage-12377.py builds the three files together.
 *
 * Both report through sirius_usb_log_char(), which on the 10063 kernel stored characters in the
 * sirius_usb.c ring. The 12377 console (UniXNU scripts/native-dwc3-console.c) already carries
 * kprintf to the host, so the characters are collected into lines here and each line is one kprintf.
 */
#include <IOKit/IOService.h>
#include <IOKit/IOLib.h>
#include <machine/machine_routines.h>
#include <pexpert/pexpert.h>
#include <libkern/libkern.h>

extern "C" void sirius_emmc_probe(void);
void sirius_native_emmc_start(IOService *parent);

class BoardStorage : public IOService
{
	OSDeclareDefaultStructors(BoardStorage);

public:
	bool start(IOService *provider) override;
};

OSDefineMetaClassAndStructors(BoardStorage, IOService);

bool
BoardStorage::start(IOService *provider)
{
	if (!IOService::start(provider)) {
		return false;
	}
	unsigned enabled = 1;
	if (PE_parse_boot_argn("sirius_emmc_drv", &enabled, sizeof(enabled)) && enabled == 0) {
		kprintf("BoardStorage: sirius eMMC disabled by sirius_emmc_drv=0\n");
		return true;
	}
	kprintf("BoardStorage: sirius SDCC1 eMMC probe\n");
	sirius_emmc_probe();
	sirius_native_emmc_start(this);
	kprintf("BoardStorage: sirius eMMC start returned\n");
	return true;
}

/* One line at a time: the transport and the driver write a line in several calls (a label, then the
 * digits), from more than one thread. The line buffer is taken with interrupts off so its holder
 * cannot be preempted on this core while another caller spins for it; kprintf runs outside. */
static char sirius_log_line[256];
static unsigned sirius_log_used;
static volatile uint32_t sirius_log_busy;

extern "C" void
sirius_usb_log_char(char c)
{
	char out[sizeof(sirius_log_line)];
	bool flush = false;
	boolean_t interrupts = ml_set_interrupts_enabled(FALSE);
	while (__atomic_exchange_n(&sirius_log_busy, 1, __ATOMIC_ACQUIRE) != 0) {
	}
	if (c != '\r') {
		sirius_log_line[sirius_log_used++] = c;
	}
	if (c == '\n' || sirius_log_used == sizeof(sirius_log_line) - 1) {
		memcpy(out, sirius_log_line, sirius_log_used);
		out[sirius_log_used] = '\0';
		sirius_log_used = 0;
		flush = true;
	}
	__atomic_store_n(&sirius_log_busy, 0, __ATOMIC_RELEASE);
	ml_set_interrupts_enabled(interrupts);
	if (flush) {
		kprintf("%s", out);
	}
}
