/* Thumb-1 / ARMv4T, big-endian, linked at 0x00152f2a.
 * tools/build_key_marker.py emits the reviewed encodings directly; this
 * source documents the five instructions and the position-dependent literal.
 * Native function 0x001a1236 has already pushed {r3,r4,r5,lr}.
 */
.syntax unified
.arch armv4t
.thumb
.global key_marker
.thumb_func
key_marker:
    ldr  r0, marker          /* 4802: aligned(PC+4) + 8 */
    str  r0, [sp]           /* 9000: allocated native output-pointer local */
marker_checkpoint:          /* 0x00152f2e: inspect [sp] here */
    lsls r5, r1, #16        /* 040d: first displaced instruction */
    movs r0, #0             /* 2000: second displaced instruction */
    bx   lr                 /* 4770: back to 0x001a123c (Thumb) */
.balign 4
marker:
    .word 0x42323230        /* ASCII B220 in big-endian memory */
