// SPDX-License-Identifier: BSD-2-Clause-Patent
// The touch screen for iOS: publishes ShengHidInput's NativeHidInput on IOHIDResource, fed by the FTS521 driver
// (package/sirius_touch.c: native_touch_* and native_touch_set_sink). Adapted from UniXNU-sheng package/HidInputProbe.cpp
// (08cde20): the same class registration and the same properties backboardd needs on the event service, without scripted
// Home presses.
//
// sirius-touch-hid=1 publishes sheng's default combined device (keyboard collection first, then the touch screen; needs
// sheng-touch-hid=1, which NativeHidInput reads): the in-kernel IOHIDEventDriver of this kernelcache has no digitizer
// personality (its rules: game-controller pointers 1/5 and 1/4, Peppy display 0xc/1, buttons 0xb/1, keyboards 1/6 and 1/7, a
// sensor 0xff0c/3), so a touch-only device is left to the DriverKit AppleUserHIDEventService, whose server is never launched
// on this board. The keyboard usage matches "HID Keyboard Driver", which also dispatches the device's digitizer collection.
// sirius-touch-hid=2 keeps the touch-only device (sheng-touch-split).
#include <IOKit/IOService.h>
#include <IOKit/IOLib.h>
#include <libkern/c++/OSMetaClass.h>
#include <libkern/c++/OSSymbol.h>
#include <libkern/c++/OSIterator.h>
#include <libkern/c++/OSKext.h>
#include <pexpert/pexpert.h>
#include <mach/kmod.h>

extern "C" kern_return_t kernel_thread_start(void (*)(void *, wait_result_t), void *, thread_t *);
extern "C" void thread_deallocate(thread_t);
extern kmod_info_t g_kernel_kmod_info;
extern "C" void native_hid_static_init(void) __asm__("__GLOBAL__sub_I_NativeHidInput.cpp");
extern "C" IOService *native_hid_input_allocate(void);
extern "C" IOService *native_hid_touch_allocate(void);
static unsigned hid_mode;
extern "C" {
extern kern_return_t OSKextLoadKextWithIdentifier(const char *);
// [0] init [1] attach [2] started [3] tick [4] key report [5] key report [6] touch report [7] start failed (NativeHidInput's events)
volatile uint32_t native_hid_input_events[8];
volatile int native_hid_input_results[8] = {-1, -1, -1, -1, -1, -1, -1, -1};
volatile uint32_t sirius_hid_stage;
volatile int sirius_hid_load_status = -1, sirius_hid_meta_status = -1;
volatile uint32_t sirius_hid_device_size;
// The class of the event driver that attached below the touch screen device (the live banner's TOUCH row).
char sirius_hid_driver[40];

// Hooks NativeHidInput calls.
void native_hid_input_observed(unsigned event, int result) {
    if (event < 8) {
        native_hid_input_events[event] = native_hid_input_events[event] + 1;
        native_hid_input_results[event] = result;
    }
}
extern volatile unsigned sirius_setup_exec_second __attribute__((weak_import));
extern volatile unsigned sirius_setup_exec_count __attribute__((weak_import));
extern volatile unsigned sirius_springboard_exec_second __attribute__((weak_import));
extern volatile unsigned sirius_springboard_exec_count __attribute__((weak_import));
extern "C" unsigned boot_progress_uptime_seconds(void) __attribute__((weak_import));
extern volatile uint64_t native_legacy_copy_n __attribute__((weak_import));
static volatile uint32_t sirius_home_press_pending;
static unsigned last_auto_setup_count;
static unsigned last_auto_sb_count;
static unsigned auto_home_stage;
static unsigned auto_sb_home_stage;

extern "C" void sirius_request_home_press(void) {
    sirius_home_press_pending = 1;
}

// The keyboard device's Home (Consumer Menu 0x40) presses: triggered either by a bottom-edge swipe/tap or automatically once Setup
// or SpringBoard's initial cover sheet is up.
bool native_hid_input_requested(void) {
    if (__atomic_exchange_n(&sirius_home_press_pending, 0, __ATOMIC_ACQUIRE)) {
        auto_home_stage = 1;
        auto_sb_home_stage = 1;
        IOLog("TOUCH hid: Home press dispatched (gesture)\n");
        return true;
    }
    if (&sirius_setup_exec_count && &sirius_setup_exec_second && &boot_progress_uptime_seconds && sirius_setup_exec_count > 0) {
        if (last_auto_setup_count != sirius_setup_exec_count) {
            last_auto_setup_count = sirius_setup_exec_count;
            auto_home_stage = 0;
        }
        unsigned now = boot_progress_uptime_seconds();
        unsigned start = sirius_setup_exec_second;
        uint64_t copies = &native_legacy_copy_n ? native_legacy_copy_n : 0;
        if (auto_home_stage == 0 && now >= start + 12 && copies >= 60) {
            auto_home_stage = 1;
            IOLog("TOUCH hid: Home press dispatched (auto-hello at %us, copies=%llu)\n", now, (unsigned long long)copies);
            return true;
        }
    } else if (&sirius_springboard_exec_count && &sirius_springboard_exec_second && &boot_progress_uptime_seconds &&
               sirius_springboard_exec_count > 0) {
        if (last_auto_sb_count != sirius_springboard_exec_count) {
            last_auto_sb_count = sirius_springboard_exec_count;
            auto_sb_home_stage = 0;
        }
        unsigned now = boot_progress_uptime_seconds();
        unsigned start = sirius_springboard_exec_second;
        uint64_t copies = &native_legacy_copy_n ? native_legacy_copy_n : 0;
        if (auto_sb_home_stage == 0 && now >= start + 16 && copies >= 20) {
            auto_sb_home_stage = 1;
            IOLog("TOUCH hid: Home press dispatched (auto-lockscreen at %us, copies=%llu)\n", now, (unsigned long long)copies);
            return true;
        }
    }
    return false;
}
// NativeHidInput's hardware-button source (weak_import there, but the static kernel link still needs a definition): this board
// has no button driver yet, so no sink is ever called. The touch screen device does not register one either.
void native_buttons_set_sink(void (*)(void *, uint32_t), void *) {}
}

static IORegistryEntry *findEventDriver(IORegistryEntry *parent, unsigned depth) {
    if (!depth) return nullptr;
    OSIterator *it = parent->getChildIterator(gIOServicePlane);
    if (!it) return nullptr;
    IORegistryEntry *found = nullptr;
    while (OSObject *child = it->getNextObject()) {
        IORegistryEntry *entry = OSDynamicCast(IORegistryEntry, child);
        if (!entry) continue;
        if (entry->metaCast("IOHIDEventDriver")) { found = entry; break; }
        if ((found = findEventDriver(entry, depth - 1))) break;
    }
    it->release();
    return found;
}

// NativeHidInput calls this once a second. backboardd classifies a digitizer by Built-In/DisplayIntegrated on the event service
// it opened and drops digitizer events unless that service has PrimaryUsagePage 0x0D and PrimaryUsage 4 (Touch Screen); publish
// them on the IOHIDEventDriver and its interface once (each setProperty is a registry change notification).
extern "C" __attribute__((noinline)) void native_hid_live_checkpoint(IOService *device) {
    static IORegistryEntry *publishedFor;
    IORegistryEntry *driver = findEventDriver(device, 4);
    if (!driver || publishedFor == driver || !device->getProperty("Built-In")) return;
    publishedFor = driver;
    strlcpy(sirius_hid_driver, driver->getMetaClass()->getClassName(), sizeof(sirius_hid_driver));
    driver->setProperty("Built-In", kOSBooleanTrue);
    driver->setProperty("DisplayIntegrated", kOSBooleanTrue);
    driver->setProperty("PrimaryUsagePage", (unsigned long long)0x0d, 32);
    driver->setProperty("PrimaryUsage", (unsigned long long)4, 32);
    if (IORegistryEntry *interface = driver->getParentEntry(gIOServicePlane)) {
        interface->setProperty("Built-In", kOSBooleanTrue);
        interface->setProperty("DisplayIntegrated", kOSBooleanTrue);
        interface->setProperty("PrimaryUsagePage", (unsigned long long)0x0d, 32);
        interface->setProperty("PrimaryUsage", (unsigned long long)4, 32);
    }
    IOLog("TOUCH hid event driver %s published as the built-in touch screen\n", driver->getMetaClass()->getClassName());
}

static void hidThread(void *, wait_result_t) {
    sirius_hid_stage = 1;
    sirius_hid_load_status = OSKextLoadKextWithIdentifier("com.apple.iokit.IOHIDFamily");
    if (sirius_hid_load_status == 0)
        OSKext::loadKextWithIdentifier("com.apple.iokit.IOHIDEventDriver", true, false, kOSKextExcludeNone, kOSKextExcludeNone,
            nullptr);
    const OSSymbol *name = OSSymbol::withCString("IOHIDDevice");
    const OSMetaClass *meta = name ? OSMetaClass::getMetaClassWithName(name) : nullptr;
    sirius_hid_device_size = meta ? meta->getClassSize() : 0;
    OSSafeReleaseNULL(name);
    sirius_hid_stage = 2;
    IOService *resource = nullptr;
    OSDictionary *match = IOService::serviceMatching("IOHIDResource");
    if (sirius_hid_load_status == 0 && match) resource = IOService::waitForMatchingService(match, 10000000000ULL);
    OSSafeReleaseNULL(match);
    // NativeHidInput is compiled against the original 208-byte IOHIDDevice (scripts/data/hid-device-abi.json).
    if (!resource || sirius_hid_device_size != 208) {
        IOLog("TOUCH hid not started: IOHIDFamily load=%d IOHIDResource=%d IOHIDDevice size=%u\n", sirius_hid_load_status,
            resource != nullptr, sirius_hid_device_size);
        OSSafeReleaseNULL(resource);
        return;
    }
    sirius_hid_stage = 3;
    void *handle = OSMetaClass::preModLoad(g_kernel_kmod_info.name);
    if (handle) {
        native_hid_static_init();
        sirius_hid_meta_status = OSMetaClass::postModLoad(handle);
    }
    bool started = false;
    if (handle && !sirius_hid_meta_status) {
        IOService *touch = hid_mode == 2 ? native_hid_touch_allocate() : native_hid_input_allocate();
        started = touch && touch->metaCast("IOHIDDevice") && touch->init() && touch->attach(resource) && touch->start(resource);
        if (!started && touch) {
            touch->detach(resource);
            OSSafeReleaseNULL(touch);
        }
        // The device lives for the boot: its registry attachment and work loop stay owned by IOKit.
    }
    sirius_hid_stage = started ? 4 : 5;
    IOLog("TOUCH hid %s device %s (metaclass status %d)\n", hid_mode == 2 ? "touch-only" : "keyboard+touch",
        started ? "started on IOHIDResource" : "FAILED to start", sirius_hid_meta_status);
    OSSafeReleaseNULL(resource);
}

// Called from the late display probe (package/LegacyDisplayProbe.cpp); sirius-touch-hid=1 (combined) or 2 (touch-only).
extern "C" void sirius_hid_touch_start(void) {
    unsigned enabled = 0;
    PE_parse_boot_argn("sirius-touch-hid", &enabled, sizeof(enabled));
    if ((enabled != 1 && enabled != 2) || sirius_hid_stage) return;
    hid_mode = enabled;
    thread_t thread = nullptr;
    if (kernel_thread_start(hidThread, nullptr, &thread) == KERN_SUCCESS) thread_deallocate(thread);
}
