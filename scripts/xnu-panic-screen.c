/* Draw a kernel panic message, with its arguments, on the frame buffer in RED, then reset.
 *
 * Works both after PE_create_console() (when vinfo.v_baseaddr is mapped) and during early boot
 * before arm_vm_init / PE_create_console(): the message and call stack are formatted into a
 * stack buffer while the MMU is on, cleaned to PoC, and rendered either through vinfo.v_baseaddr
 * or by jumping to the 1:1 physical identity mapping in _bootstrap_pagetables, turning the MMU
 * off, and writing directly to the physical framebuffer at 0x9c000000.
 */
typedef unsigned long u64;
typedef unsigned int u32;
typedef unsigned char u8;

struct vc_info {
  u32 v_height, v_width, v_depth, v_rowbytes;
  u64 v_baseaddr;
};

#define SCALE       3
#define COLOR_RED   0xFFFF0000u
#define FB_PHYS     0x9c000000UL
#define FB_WIDTH    1080u
#define FB_HEIGHT   2240u
#define FB_ROWBYTES 4320u
#define MSG_CAP     640u

extern const u64 gFont[];

struct buf_ctx {
  char buf[MSG_CAP];
  u32  len;
  u8  *log;
  u32  logn;
};

#ifndef PANIC_TEXT
#define PANIC_TEXT 0UL
#endif
#define PANIC_TEXT_BYTES 256

__attribute__((noreturn)) static void park (void)
{
  for (;;) {
    __asm__ volatile ("wfe");
  }
}

__attribute__((noinline)) static void put (struct buf_ctx *g, u8 c)
{
  if (g->log && g->logn < PANIC_TEXT_BYTES - 1 && c >= 9 && c <= 126) {
    g->log[g->logn++] = (c < 32) ? ' ' : c;
    g->log[g->logn] = 0;
  }
  if (g->len + 1 < MSG_CAP) {
    g->buf[g->len++] = (char)c;
    g->buf[g->len] = 0;
  }
#if defined(DWC3_STAGE) && DWC3_STAGE && defined(DWC3_PUT) && DWC3_PUT
  if (*(volatile const u32 *)DWC3_STAGE >= 5) {
    if (c == '\n') ((void (*)(u8))(u64)DWC3_PUT)('\r');
    ((void (*)(u8))(u64)DWC3_PUT)(c);
  }
#endif
}

__attribute__((noinline)) static void number (struct buf_ctx *g, u64 value, u32 base, u32 width, int zero)
{
  u8  digits[24];
  u32 n = 0, i;

  do {
    u32 d = (u32)(value % base);
    digits[n++] = (u8)(d < 10 ? '0' + d : 'a' + d - 10);
    value /= base;
  } while (value && n < 24);
  for (i = n; i < width; i++) {
    put (g, zero ? '0' : ' ');
  }
  while (n) {
    put (g, digits[--n]);
  }
}

static int readable (const char *s, const u64 *args)
{
  u64 a = (u64)s, par;
  (void)args;
  if (a < 0xffff000000000000UL) return 0;
  __asm__ volatile ("at s1e1r, %1\n\tisb\n\tmrs %0, par_el1" : "=r" (par) : "r" (a) : "memory");
  return !(par & 1) && ((par >> 60) != 0);
}

static u64 va_to_pa (u64 va)
{
  u64 par;
  __asm__ volatile ("at s1e1r, %1\n\tisb\n\tmrs %0, par_el1" : "=r" (par) : "r" (va) : "memory");
  if (par & 1) return 0;
  return (par & 0x0000fffffffff000UL) | (va & 0xfffUL);
}

static void clean_poc (const void *addr, u64 bytes)
{
  u64 p = (u64)addr & ~63UL;
  u64 end = (u64)addr + bytes;
  for (; p < end; p += 64) {
    __asm__ volatile ("dc civac, %0" :: "r" (p) : "memory");
  }
  __asm__ volatile ("dsb sy" ::: "memory");
}

static void format (struct buf_ctx *g, const char *fmt, const u64 *args)
{
  u32 i, used = 0;

  if (!readable(fmt, args)) {
    put(g, '<');
    number(g, (u64)fmt, 16, 16, 1);
    put(g, '>');
    return;
  }
  for (i = 0; i < 2000 && fmt[i]; i++) {
    u8 c = (u8)fmt[i];
    if (c != '%') {
      put (g, c);
      continue;
    }
    u32 width = 0;
    int zero = 0, longs = 0;
    c = (u8)fmt[++i];
    if (c == '0') {
      zero = 1;
      c = (u8)fmt[++i];
    }
    while (c >= '0' && c <= '9') {
      width = width * 10 + (c - '0');
      c = (u8)fmt[++i];
    }
    u32 precision = 4000;
    if (c == '.') {
      precision = 0;
      c = (u8)fmt[++i];
      if (c == '*') {
        precision = (u32)args[used++];
        c = (u8)fmt[++i];
      } else {
        while (c >= '0' && c <= '9') {
          if (precision < 4000) precision = precision * 10 + (c - '0');
          c = (u8)fmt[++i];
        }
      }
      if (precision > 4000) precision = 4000;
    }
    if (width > 256) width = 256;
    while (c == 'l' || c == 'z' || c == 'h') {
      longs += (c != 'h');
      c = (u8)fmt[++i];
    }
    if (c == '%') {
      put (g, '%');
      continue;
    }
    if (c == 0) {
      break;
    }
    u64 v = args[used++];
    if (c == 's') {
      const char *s = (const char *)v;
      u32 k;
      for (k = 0; k < precision; k++) {
        if (!readable (s + k, args)) {
          put (g, '<');
          number (g, (u64)(s + k), 16, 0, 0);
          put (g, '>');
          break;
        }
        u8 ch = (u8)s[k];
        if (!ch) break;
        put (g, ch);
      }
    } else if (c == 'x' || c == 'X') {
      number (g, longs ? v : (v & 0xFFFFFFFFu), 16, width, zero);
    } else if (c == 'u' || c == 'd') {
      number (g, longs ? v : (v & 0xFFFFFFFFu), 10, width, zero);
    } else if (c == 'p') {
      put (g, '0');
      put (g, 'x');
      number (g, v, 16, width, zero);
    } else if (c == 'c') {
      put (g, (u8)(v & 0xff));
    }
  }
}

__attribute__((noinline)) static void text (struct buf_ctx *g, const char *s)
{
  while (*s) {
    put (g, (u8)*s++);
  }
}

__attribute__((noinline)) static void hex (struct buf_ctx *g, const char *label, u64 v)
{
  text (g, label);
  put (g, '0');
  put (g, 'x');
  number (g, v, 16, 16, 1);
}

__attribute__((noinline)) static void render_buffer (u8 *base, u32 w, u32 h, u32 rb, const char *msg, const u64 *font)
{
  const u32 cell = 6 * SCALE, line = 14 * SCALE;
  u32 x = 16, y = h / 8;
  u32 row, col, dy, dx, i;

  volatile u32 *mark = (volatile u32 *)(base + (u64)(h - 1) * rb);
  if (mark[0] == 0xC0FFEE11u && mark[1] == 0x5CEEDA7Au) {
    return;
  }
  for (row = y; row < h; row++) {
    for (col = 0; col < w; col++) {
      *(volatile u32 *)(base + (u64)row * rb + (u64)col * 4) = 0;
    }
  }
  y += 8;
  for (i = 0; i < MSG_CAP && msg[i]; i++) {
    u8 c = (u8)msg[i];
    if (c == '\n') {
      x = 16;
      y += line;
      continue;
    }
    if (c == '\t') {
      x += 4 * cell;
      continue;
    }
    if (c < 32 || c > 126) {
      continue;
    }
    if (x + cell > w - 16) {
      x = 16;
      y += line;
    }
    if (y + line > h) {
      break;
    }
    u64 glyph = font[c - 32];
    u32 top = (u32)(glyph >> 32), bottom = (u32)glyph;
    for (row = 0; row < 12; row++) {
      u32 data = (row < 6) ? (top >> (row * 5)) : (bottom >> ((row - 6) * 5));
      for (col = 0; col < 5; col++) {
        if ((data >> col) & 1) {
          for (dy = 0; dy < SCALE; dy++) {
            for (dx = 0; dx < SCALE; dx++) {
              *(volatile u32 *)(base + (u64)(y + row * SCALE + dy) * rb + (u64)(x + col * SCALE + dx) * 4) = COLOR_RED;
            }
          }
        }
      }
    }
    x += cell;
  }
  mark[0] = 0xC0FFEE11u;
  mark[1] = 0x5CEEDA7Au;
  __asm__ volatile ("dsb sy" ::: "memory");
}

__attribute__((noinline, noreturn)) static void psci_reset_or_park (u64 wait_s, void (*poll)(void), u64 mark_fn)
{
  if (wait_s) {
    u64 freq, start, now;
    __asm__ volatile ("mrs %0, cntfrq_el0" : "=r" (freq));
    __asm__ volatile ("isb\n\tmrs %0, cntvct_el0" : "=r" (start));
    if (!freq) freq = 19200000;
    do {
      if (poll) poll();
      __asm__ volatile ("isb\n\tmrs %0, cntvct_el0" : "=r" (now));
    } while (now - start < wait_s * freq);
    if (mark_fn) ((int (*)(void))mark_fn)();
    register u64 x0 __asm__ ("x0") = 0x84000009UL;
    __asm__ volatile ("smc #0" : "+r" (x0) :: "memory", "x1", "x2", "x3");
  }
  park();
}

__attribute__((noinline, noreturn)) static void mmu_off_render_and_reset (
    u64 boot_pt_pa, u64 self_pa, u64 sp_pa, u64 msg_pa, u64 font_pa, u64 wait_s)
{
  __asm__ volatile (
    "msr ttbr0_el1, %0\n\t"
    "isb\n\t"
    "tlbi vmalle1\n\t"
    "dsb ish\n\t"
    "isb\n\t"
    "adr x9, 1f\n\t"
    "and x9, x9, #0xfff\n\t"
    "bic %1, %1, #0xfff\n\t"
    "add x9, %1, x9\n\t"
    "br x9\n\t"
    "1:\n\t"
    "mov sp, %2\n\t"
    "mrs x9, sctlr_el1\n\t"
    "bic x9, x9, #1\n\t"
    "bic x9, x9, #(1 << 2)\n\t"
    "msr sctlr_el1, x9\n\t"
    "isb\n\t"
    : "+r" (boot_pt_pa), "+r" (self_pa), "+r" (sp_pa)
    :
    : "x9", "memory"
  );
  render_buffer((u8 *)FB_PHYS, FB_WIDTH, FB_HEIGHT, FB_ROWBYTES, (const char *)msg_pa, (const u64 *)font_pa);
  psci_reset_or_park(wait_s, 0, 0);
}

/* x0 = fmt as for panic(); x1 is set by the stub to the entry stack pointer. */
__attribute__((noreturn, used, section(".text.b_main"))) void panic_screen_main (const char *fmt, const u64 *args)
{
  volatile struct vc_info *v = (volatile struct vc_info *)VINFO;
  struct buf_ctx g __attribute__((aligned(64)));

  __asm__ volatile ("msr daifset, #15");
#ifdef REFRESH
  if (REFRESH) {
    ((void (*)(void))(u64)REFRESH)();
  }
#endif
  g.len = 0;
  g.buf[0] = 0;
  g.log = 0;
  g.logn = 0;
  if (PANIC_TEXT && *(volatile u8 *)PANIC_TEXT == 0) {
    g.log = (u8 *)PANIC_TEXT;
  }
  text(&g, "PANIC: ");
  if (fmt) {
    format(&g, fmt, args);
  }
  g.log = 0;

  /* Walk up to 6 stack frames while MMU is on so the exact panic caller in _arm_init is shown. */
  {
    u64 fp = (u64)__builtin_frame_address(0);
    u64 lr = (u64)__builtin_return_address(0);
    hex(&g, "\nLR0=", lr);
    for (int depth = 1; depth <= 5; depth++) {
      if (!readable((const char *)fp, args)) {
        break;
      }
      u64 next_fp = *(const volatile u64 *)fp;
      if (!next_fp || next_fp == fp || (next_fp & 0xf)) {
        break;
      }
      fp = next_fp;
      if (!readable((const char *)fp, args) || !readable((const char *)(fp + 8), args)) {
        break;
      }
      u64 next_lr = *(const volatile u64 *)(fp + 8);
      text(&g, "\nLR");
      put(&g, (u8)('0' + depth));
      hex(&g, "=", next_lr);
    }
  }
  {
    u64 esr, elr, far;
    __asm__ volatile ("mrs %0, esr_el1" : "=r" (esr));
    __asm__ volatile ("mrs %0, elr_el1" : "=r" (elr));
    __asm__ volatile ("mrs %0, far_el1" : "=r" (far));
    hex(&g, "\nESR=", esr);
    hex(&g, "\nELR=", elr);
    hex(&g, "\nFAR=", far);
  }
  (void)MAX_MEM;
  (void)PAGE_TOTAL;
  (void)PAGE_FREE;

  void (*dwc3_poll)(void) = 0;
#if defined(DWC3_STAGE) && DWC3_STAGE && defined(DWC3_POLL) && DWC3_POLL
  if (*(volatile const u32 *)DWC3_STAGE >= 5) {
    dwc3_poll = (void (*)(void))(u64)DWC3_POLL;
  }
#endif

  if (v->v_baseaddr != 0 && v->v_depth == 32) {
    render_buffer((u8 *)v->v_baseaddr, v->v_width, v->v_height, v->v_rowbytes, g.buf, gFont);
#ifdef PANIC_FLUSH
    ((void (*)(void))PANIC_FLUSH)();
#endif
    psci_reset_or_park(
#if defined(PANIC_RESET_S) && PANIC_RESET_S
      (u64)PANIC_RESET_S,
#else
      0ULL,
#endif
      dwc3_poll,
#if defined(PANIC_RESET_MARK) && PANIC_RESET_MARK
      (u64)PANIC_RESET_MARK
#else
      0ULL
#endif
    );
  }

  /* Early panic before PE_create_console() mapped vinfo.v_baseaddr: clean the formatted
   * buffer and font to physical DRAM, switch to the 1:1 bootstrap identity map, disable
   * the MMU, and draw the red text directly onto the physical framebuffer at 0x9c000000. */
  clean_poc(&g, sizeof(g));
  clean_poc(gFont, 96 * sizeof(u64));
  {
    u64 boot_pt_pa = va_to_pa((u64)BOOT_PT);
    u64 self_pa = va_to_pa((u64)&mmu_off_render_and_reset);
    u64 sp_pa = va_to_pa((u64)&g);
    u64 msg_pa = va_to_pa((u64)g.buf);
    u64 font_pa = va_to_pa((u64)gFont);
    if (boot_pt_pa && self_pa && sp_pa && msg_pa && font_pa) {
      mmu_off_render_and_reset(boot_pt_pa, self_pa, sp_pa & ~15UL, msg_pa, font_pa,
#if defined(PANIC_RESET_S) && PANIC_RESET_S
                               (u64)PANIC_RESET_S
#else
                               0ULL
#endif
      );
    }
  }
  park();
}

/* Entry from panic(): keep the stack pointer, which addresses the variadic arguments. */
__attribute__((naked, noreturn, section(".text.a_entry"))) void panic_screen (const char *fmt)
{
  __asm__ volatile ("mov x1, sp\n\tb panic_screen_main");
}

/* The real panic_trap_to_debugger signature takes (fmt, va_list *, ...).
 * Darwin ARM64 va_list is a stack pointer; its address is passed in x1.
 */
__attribute__((naked, noreturn, section(".text.a_common"))) void panic_screen_common (void)
{
  __asm__ volatile ("ldr x1, [x1]\n\tb panic_screen_main");
}

#include "Font.h"
__attribute__((section(".text.z_font"))) const u64 gFont[] = GLYPH_FONT;
