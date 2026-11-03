/* Floating point helpers without libm (the phone build has no C library). */
#ifndef JVM_FPMATH_H
#define JVM_FPMATH_H

#include <stdint.h>

/* Exact IEEE remainder with the sign of x (Java frem/drem), after musl fmod. */
static inline double jvm_fmod(double x, double y) {
    union { double f; uint64_t i; } ux = {x}, uy = {y};
    int ex = (int)(ux.i >> 52 & 0x7ff), ey = (int)(uy.i >> 52 & 0x7ff);
    uint64_t sx = ux.i >> 63, i, uxi = ux.i;
    if (uy.i << 1 == 0 || y != y || ex == 0x7ff) return (x*y)/(x*y);
    if (uxi << 1 <= uy.i << 1) return uxi << 1 == uy.i << 1 ? 0*x : x;
    if (!ex) { for (i = uxi << 12; i >> 63 == 0; ex--, i <<= 1) {} uxi <<= -ex+1; }
    else { uxi &= (uint64_t)-1 >> 12; uxi |= 1ull << 52; }
    if (!ey) { for (i = uy.i << 12; i >> 63 == 0; ey--, i <<= 1) {} uy.i <<= -ey+1; }
    else { uy.i &= (uint64_t)-1 >> 12; uy.i |= 1ull << 52; }
    for (; ex > ey; ex--) {
        i = uxi-uy.i;
        if (i >> 63 == 0) { if (i == 0) return 0*x; uxi = i; }
        uxi <<= 1;
    }
    i = uxi-uy.i;
    if (i >> 63 == 0) { if (i == 0) return 0*x; uxi = i; }
    for (; uxi >> 52 == 0; uxi <<= 1, ex--) {}
    if (ex > 0) { uxi -= 1ull << 52; uxi |= (uint64_t)ex << 52; }
    else uxi >>= -ex+1;
    uxi |= sx << 63;
    ux.i = uxi;
    return ux.f;
}

#endif
