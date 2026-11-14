@ Division helpers that return correctly on ARMv4T (the B220's SC6600L core:
@ the PS has no BLX, CLZ or CP15 instruction). Zig's compiler-rt writes these
@ four in assembly and returns with "pop {..., pc}"; on ARMv4T a load into pc
@ does not switch to Thumb, so a Thumb caller went on as ARM code and the phone
@ reset (Long.toString in GAZila, 64-bit arithmetic in the browser). The
@ emulator's ARM926 interworks there and hid it. These definitions are strong,
@ compiler-rt's are weak; the work stays in compiler-rt's C-level functions.
    .syntax unified
    .arm
    .text

    .section .text.__aeabi_uidivmod,"ax",%progbits
    .global __aeabi_uidivmod
    .type __aeabi_uidivmod,%function
__aeabi_uidivmod:            @ r0 = n / d, r1 = n % d
    push {r0, r1, r4, lr}
    bl __aeabi_uidiv
    pop {r1, r2, r4, lr}
    mul r3, r0, r2
    sub r1, r1, r3
    bx lr

    .section .text.__aeabi_idivmod,"ax",%progbits
    .global __aeabi_idivmod
    .type __aeabi_idivmod,%function
__aeabi_idivmod:
    push {r0, r1, r4, lr}
    bl __aeabi_idiv
    pop {r1, r2, r4, lr}
    mul r3, r0, r2
    sub r1, r1, r3
    bx lr

    .section .text.__aeabi_uldivmod,"ax",%progbits
    .global __aeabi_uldivmod
    .type __aeabi_uldivmod,%function
__aeabi_uldivmod:            @ r0:r1 = n / d, r2:r3 = n % d
    push {r4, lr}
    sub sp, sp, #16
    add r4, sp, #8
    str r4, [sp]
    bl __udivmoddi4
    ldr r2, [sp, #8]
    ldr r3, [sp, #12]
    add sp, sp, #16
    pop {r4, lr}
    bx lr

    .section .text.__aeabi_ldivmod,"ax",%progbits
    .global __aeabi_ldivmod
    .type __aeabi_ldivmod,%function
__aeabi_ldivmod:
    push {r4, lr}
    sub sp, sp, #16
    add r4, sp, #8
    str r4, [sp]
    bl __divmoddi4
    ldr r2, [sp, #8]
    ldr r3, [sp, #12]
    add sp, sp, #16
    pop {r4, lr}
    bx lr
