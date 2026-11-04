// Position-independent reset probe: no MMU, no data, no absolute addresses.
// Runs in the zero padding right after the kernel entry (_start is one `b`).
.text
.globl _start
_start:
    mrs     x9, cntfrq_el0          // counter frequency
    movz    x10, #0x3e8             // 1000
    udiv    x9, x9, x10             // ticks per millisecond
    movz    x10, #0x1f40            // 8000 ms
    mul     x9, x9, x10
    mrs     x11, cntvct_el0
    add     x9, x11, x9
1:  mrs     x11, cntvct_el0
    cmp     x11, x9
    b.lo    1b
    movz    x0, #0x0009
    movk    x0, #0x8400, lsl #16    // PSCI SYSTEM_RESET (SMC conduit on this board)
    smc     #0
2:  wfi
    b       2b
