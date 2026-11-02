/* Thumb-1 / ARMv4T, big-endian, linked at 0x004220a4.
 * build_ui_label.py emits these reviewed encodings directly.
 * Called by the original BL at 0x005b7dd0, in the label paint context.
 */
.syntax unified
.arch armv4t
.thumb
.global ui_label
.thumb_func
ui_label:
    push {r4}
    ldr  r4, original_time_text
    cmp  r3, r4
    bne  native_call
    ldr  r3, replacement_text
    movs r4, #8
    str  r4, [sp, #4]       /* Native outgoing length at original SP. */
native_call:
    ldr  r4, native_draw
    mov  ip, r4
    pop  {r4}               /* Restore SP before forwarding stack arguments. */
    bx   ip                 /* Tail-call original GUI text renderer. */
.balign 4
original_time_text:
    .word 0x001294d8
native_draw:
    .word 0x004cf433
replacement_text:
    .word 0x00422880        /* Separate UTF-16 pool after another JPEG EOI. */
