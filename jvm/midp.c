/* MIDP natives: event queue, drawing on the screen or an Image, bitmap fonts,
   PNG decoding and the manifest launcher. */
#include "internal.h"
#include "jar.h"

#define ARG_REF(i) ((Object *)(uintptr_t)args[i])
#define I(i) ((i32)args[i])

/* ---------- events ---------- */

int jvm_post_event(Jvm *vm, int type, int value) {
    u32 next = (vm->event_tail+1) % MAX_EVENTS;
    if (next == vm->event_head) return 0;
    vm->events[vm->event_tail][0] = type;
    vm->events[vm->event_tail][1] = value;
    vm->event_tail = next;
    Thread *t = vm->event_waiter;
    if (t && t->state == THREAD_SLEEPING) t->state = THREAD_RUNNABLE;
    return 1;
}

/* Events.poll(int[2]): an event, or false after putting the thread to sleep
   until the next event (at most 50 ms, so repaints requested by other threads
   are seen promptly). */
static int events_poll(Thread *t, u32 *args, u32 *result) {
    Jvm *vm = thread_vm(t);
    Array *out = (Array *)ARG_REF(0);
    if (!out || out->length < 2) { throw_new(t, "java/lang/IllegalArgumentException", 0); return 1; }
    if (vm->event_head != vm->event_tail) {
        i32 *e = vm->events[vm->event_head];
        ((i32 *)ARRAY_DATA(out))[0] = e[0];
        ((i32 *)ARRAY_DATA(out))[1] = e[1];
        vm->event_head = (vm->event_head+1) % MAX_EVENTS;
        result[0] = 1;
        return 0;
    }
    vm->event_waiter = t;
    t->wake = vm->host.ms(vm->host.context)+50;
    t->state = THREAD_SLEEPING;
    result[0] = 0;
    return 0;
}

static int events_post(Thread *t, u32 *args, u32 *result) {
    (void)result;
    jvm_post_event(thread_vm(t), I(0), I(1));
    return 0;
}

/* ---------- surfaces ---------- */

typedef struct Surface { u16 *pixels; u8 *alpha; i32 width, height; } Surface;
typedef struct Clip { i32 x0, y0, x1, y1; } Clip;  /* x1, y1 exclusive */

static int image_fields(Jvm *vm) {
    if (vm->image_class) return 1;
    Class *c = find_class_c(vm, "javax/microedition/lcdui/Image");
    if (!c) return 0;
    Field *p = find_field_c(c, "pixels", "[S"), *a = find_field_c(c, "alpha", "[B");
    Field *w = find_field_c(c, "width", "I"), *h = find_field_c(c, "height", "I");
    if (!p || !a || !w || !h) { vm_fail(vm, "Image fields missing", 0); return 0; }
    vm->image_pixels = p->offset; vm->image_alpha = a->offset;
    vm->image_width = w->offset; vm->image_height = h->offset;
    vm->image_class = c;
    return 1;
}

#define FIELD(o, offset) (((u32 *)(o))[(offset)/4])

static int surface(Thread *t, Object *target, Surface *s) {
    Jvm *vm = thread_vm(t);
    if (!target) {
        if (!vm->host.screen) { throw_new(t, "java/lang/IllegalStateException", "no display"); return 0; }
        s->pixels = vm->host.screen(vm->host.context);
        s->alpha = 0;
        s->width = JVM_SCREEN_WIDTH;
        s->height = JVM_SCREEN_HEIGHT;
        return s->pixels != 0;
    }
    if (!image_fields(vm)) return 0;
    Array *p = (Array *)(uintptr_t)FIELD(target, vm->image_pixels), *a = (Array *)(uintptr_t)FIELD(target, vm->image_alpha);
    s->width = (i32)FIELD(target, vm->image_width);
    s->height = (i32)FIELD(target, vm->image_height);
    if (!p || p->length < (u32)(s->width*s->height)) { throw_new(t, "java/lang/IllegalStateException", "bad image"); return 0; }
    s->pixels = (u16 *)ARRAY_DATA(p);
    s->alpha = a ? ARRAY_DATA(a) : 0;
    return 1;
}

/* Clip rectangle from x, y, width, height arguments, limited to the surface. */
static Clip clip_of(const Surface *s, i32 x, i32 y, i32 w, i32 h) {
    Clip c = {x, y, x+w, y+h};
    if (c.x0 < 0) c.x0 = 0;
    if (c.y0 < 0) c.y0 = 0;
    if (c.x1 > s->width) c.x1 = s->width;
    if (c.y1 > s->height) c.y1 = s->height;
    return c;
}

static u16 rgb565(u32 rgb) { return (u16)(((rgb >> 8) & 0xf800) | ((rgb >> 5) & 0x07e0) | ((rgb >> 3) & 0x001f)); }
static u32 rgb888(u16 c) {
    u32 r = (c >> 11) & 31, g = (c >> 5) & 63, b = c & 31;
    return ((r << 3 | r >> 2) << 16) | ((g << 2 | g >> 4) << 8) | (b << 3 | b >> 2);
}

static u16 blend(u16 under, u16 over, u32 alpha) {
    if (alpha >= 255) return over;
    u32 r = ((over >> 11)*alpha+((under >> 11) & 31)*(255-alpha))/255;
    u32 g = (((over >> 5) & 63)*alpha+((under >> 5) & 63)*(255-alpha))/255;
    u32 b = ((over & 31)*alpha+(under & 31)*(255-alpha))/255;
    return (u16)(r << 11 | g << 5 | b);
}

static inline void plot(Surface *s, const Clip *c, i32 x, i32 y, u16 color) {
    if (x < c->x0 || y < c->y0 || x >= c->x1 || y >= c->y1) return;
    s->pixels[y*s->width+x] = color;
    if (s->alpha) s->alpha[y*s->width+x] = 255;
}

static void span(Surface *s, const Clip *c, i32 y, i32 x0, i32 x1, u16 color) {
    if (y < c->y0 || y >= c->y1) return;
    if (x0 < c->x0) x0 = c->x0;
    if (x1 > c->x1) x1 = c->x1;
    for (i32 x = x0; x < x1; x++) {
        s->pixels[y*s->width+x] = color;
        if (s->alpha) s->alpha[y*s->width+x] = 255;
    }
}

/* Draw.fillRect(target, x, y, w, h, rgb, clip x, y, w, h) */
static int draw_fill_rect(Thread *t, u32 *args, u32 *result) {
    (void)result;
    Surface s;
    if (!surface(t, ARG_REF(0), &s)) return 1;
    Clip c = clip_of(&s, I(6), I(7), I(8), I(9));
    u16 color = rgb565(args[5]);
    for (i32 y = I(2); y < I(2)+I(4); y++) span(&s, &c, y, I(1), I(1)+I(3), color);
    return 0;
}

/* Draw.drawLine(target, x1, y1, x2, y2, rgb, clip...) */
static int draw_line(Thread *t, u32 *args, u32 *result) {
    (void)result;
    Surface s;
    if (!surface(t, ARG_REF(0), &s)) return 1;
    Clip c = clip_of(&s, I(6), I(7), I(8), I(9));
    u16 color = rgb565(args[5]);
    i32 x = I(1), y = I(2), x2 = I(3), y2 = I(4);
    i32 dx = x2 > x ? x2-x : x-x2, dy = y2 > y ? y2-y : y-y2, sx = x < x2 ? 1 : -1, sy = y < y2 ? 1 : -1, error = dx-dy;
    for (u32 guard = 0; guard < 4096; guard++) {
        plot(&s, &c, x, y, color);
        if (x == x2 && y == y2) break;
        i32 e2 = 2*error;
        if (e2 > -dy) { error -= dy; x += sx; }
        if (e2 < dx) { error += dx; y += sy; }
    }
    return 0;
}

/* Draw.fillTriangle(target, x1, y1, x2, y2, x3, y3, rgb, clip...) */
static int draw_fill_triangle(Thread *t, u32 *args, u32 *result) {
    (void)result;
    Surface s;
    if (!surface(t, ARG_REF(0), &s)) return 1;
    Clip c = clip_of(&s, I(8), I(9), I(10), I(11));
    u16 color = rgb565(args[7]);
    i32 x[3] = {I(1), I(3), I(5)}, y[3] = {I(2), I(4), I(6)};
    for (int i = 0; i < 2; i++)
        for (int j = 0; j < 2-i; j++)
            if (y[j] > y[j+1]) { i32 a = y[j]; y[j] = y[j+1]; y[j+1] = a; a = x[j]; x[j] = x[j+1]; x[j+1] = a; }
    for (i32 row = y[0]; row <= y[2]; row++) {
        if (row < c.y0 || row >= c.y1) continue;
        i32 a = y[2] == y[0] ? x[0] : x[0]+(x[2]-x[0])*(row-y[0])/(y[2]-y[0]);
        i32 b;
        if (row < y[1]) b = y[1] == y[0] ? x[1] : x[0]+(x[1]-x[0])*(row-y[0])/(y[1]-y[0]);
        else b = y[2] == y[1] ? x[1] : x[1]+(x[2]-x[1])*(row-y[1])/(y[2]-y[1]);
        if (a > b) { i32 k = a; a = b; b = k; }
        span(&s, &c, row, a, b+1, color);
    }
    return 0;
}

/* Source position in a w x h region for destination (dx, dy) under a MIDP 2
   Sprite transform; transforms 4..7 swap the destination's width and height. */
static void transform_point(i32 transform, i32 dx, i32 dy, i32 w, i32 h, i32 *sx, i32 *sy) {
    switch (transform) {
    case 2: *sx = w-1-dx; *sy = dy; break;            /* TRANS_MIRROR */
    case 1: *sx = dx; *sy = h-1-dy; break;            /* TRANS_MIRROR_ROT180 */
    case 3: *sx = w-1-dx; *sy = h-1-dy; break;        /* TRANS_ROT180 */
    case 4: *sx = dy; *sy = dx; break;                /* TRANS_MIRROR_ROT270 */
    case 5: *sx = dy; *sy = h-1-dx; break;            /* TRANS_ROT90 */
    case 6: *sx = w-1-dy; *sy = dx; break;            /* TRANS_ROT270 */
    case 7: *sx = w-1-dy; *sy = h-1-dx; break;        /* TRANS_MIRROR_ROT90 */
    default: *sx = dx; *sy = dy; break;
    }
}

/* Draw.drawRegion(target, Image source, sx, sy, w, h, transform, x, y, clip...):
   (x, y) is the top-left corner of the transformed region. */
static int draw_region(Thread *t, u32 *args, u32 *result) {
    (void)result;
    Surface d, src;
    if (!surface(t, ARG_REF(0), &d)) return 1;
    if (!ARG_REF(1)) { throw_new(t, "java/lang/NullPointerException", 0); return 1; }
    if (!surface(t, ARG_REF(1), &src)) return 1;
    i32 sx0 = I(2), sy0 = I(3), w = I(4), h = I(5), transform = I(6), x = I(7), y = I(8);
    if (sx0 < 0 || sy0 < 0 || w < 0 || h < 0 || sx0+w > src.width || sy0+h > src.height) {
        throw_new(t, "java/lang/IllegalArgumentException", "region outside image");
        return 1;
    }
    Clip c = clip_of(&d, I(9), I(10), I(11), I(12));
    int swap = transform >= 4;
    i32 dw = swap ? h : w, dh = swap ? w : h;
    /* Clip the destination rectangle once instead of testing every pixel:
       games draw large backgrounds and leave most of them to the clip. */
    i32 dx0 = c.x0-x > 0 ? c.x0-x : 0, dy0 = c.y0-y > 0 ? c.y0-y : 0;
    i32 dx1 = c.x1-x < dw ? c.x1-x : dw, dy1 = c.y1-y < dh ? c.y1-y : dh;
    if (dx0 >= dx1 || dy0 >= dy1) return 0;
    /* Every transform is affine: the source index moves by a fixed step per
       destination pixel and per destination row. */
    i32 px, py, qx, qy, rx, ry;
    transform_point(transform, dx0, dy0, w, h, &px, &py);
    transform_point(transform, dx0+1, dy0, w, h, &qx, &qy);
    transform_point(transform, dx0, dy0+1, w, h, &rx, &ry);
    i32 step = (qy-py)*src.width+(qx-px), row_step = (ry-py)*src.width+(rx-px);
    i32 row = (sy0+py)*src.width+sx0+px, n = dx1-dx0;
    for (i32 dy = dy0; dy < dy1; dy++, row += row_step) {
        u16 *to = &d.pixels[(y+dy)*d.width+x+dx0];
        u8 *to_alpha = d.alpha ? &d.alpha[(y+dy)*d.width+x+dx0] : 0;
        const u16 *from = src.pixels;
        i32 at = row;
        if (!src.alpha) {
            for (i32 i = 0; i < n; i++, at += step) to[i] = from[at];
            if (to_alpha) for (i32 i = 0; i < n; i++) to_alpha[i] = 255;
            continue;
        }
        for (i32 i = 0; i < n; i++, at += step) {
            u32 a = src.alpha[at];
            if (!a) continue;
            to[i] = a >= 255 ? from[at] : blend(to[i], from[at], a);
            if (to_alpha && a > to_alpha[i]) to_alpha[i] = (u8)a;
        }
    }
    return 0;
}

/* Draw.drawRGB(target, int[] rgb, offset, scan, x, y, w, h, processAlpha, clip...) */
static int draw_rgb(Thread *t, u32 *args, u32 *result) {
    (void)result;
    Surface s;
    if (!surface(t, ARG_REF(0), &s)) return 1;
    Array *rgb = (Array *)ARG_REF(1);
    if (!rgb) { throw_new(t, "java/lang/NullPointerException", 0); return 1; }
    i32 offset = I(2), scan = I(3), x = I(4), y = I(5), w = I(6), h = I(7);
    int with_alpha = I(8) != 0;
    if (w <= 0 || h <= 0) return 0;
    i64 first = (i64)offset, last = (i64)offset+(i64)(h-1)*scan+(w-1);
    if (offset < 0 || first < 0 || last >= (i64)rgb->length || (i64)offset+(i64)(h-1)*scan < 0) {
        throw_new(t, "java/lang/ArrayIndexOutOfBoundsException", 0);
        return 1;
    }
    Clip c = clip_of(&s, I(9), I(10), I(11), I(12));
    const u32 *data = (const u32 *)ARRAY_DATA(rgb);
    for (i32 dy = 0; dy < h; dy++) {
        i32 ty = y+dy;
        if (ty < c.y0 || ty >= c.y1) continue;
        for (i32 dx = 0; dx < w; dx++) {
            i32 tx = x+dx;
            if (tx < c.x0 || tx >= c.x1) continue;
            u32 argb = data[offset+dy*scan+dx];
            u32 a = with_alpha ? argb >> 24 : 255;
            if (!a) continue;
            u16 *p = &s.pixels[ty*s.width+tx];
            *p = blend(*p, rgb565(argb), a);
            if (s.alpha && a > s.alpha[ty*s.width+tx]) s.alpha[ty*s.width+tx] = (u8)a;
        }
    }
    return 0;
}

/* Draw.getRGB(Image, int[] out, offset, scan, x, y, w, h) */
static int draw_get_rgb(Thread *t, u32 *args, u32 *result) {
    (void)result;
    Surface s;
    if (!ARG_REF(0) || !ARG_REF(1)) { throw_new(t, "java/lang/NullPointerException", 0); return 1; }
    if (!surface(t, ARG_REF(0), &s)) return 1;
    Array *out = (Array *)ARG_REF(1);
    i32 offset = I(2), scan = I(3), x = I(4), y = I(5), w = I(6), h = I(7);
    if (x < 0 || y < 0 || w < 0 || h < 0 || x+w > s.width || y+h > s.height) {
        throw_new(t, "java/lang/IllegalArgumentException", 0);
        return 1;
    }
    if (w && h && (offset < 0 || (i64)offset+(i64)(h-1)*scan+(w-1) >= (i64)out->length || (i64)offset+(i64)(h-1)*scan < 0)) {
        throw_new(t, "java/lang/ArrayIndexOutOfBoundsException", 0);
        return 1;
    }
    u32 *data = (u32 *)ARRAY_DATA(out);
    for (i32 dy = 0; dy < h; dy++)
        for (i32 dx = 0; dx < w; dx++) {
            u32 at = (u32)((y+dy)*s.width+x+dx);
            u32 a = s.alpha ? s.alpha[at] : 255;
            data[offset+dy*scan+dx] = a << 24 | rgb888(s.pixels[at]);
        }
    return 0;
}

/* ---------- fonts ---------- */

/* Font data (big-endian): "BF", version 1, height, ascent, 0, u16 glyph count,
   then entries {u16 code, u8 advance, u8 width, u32 bitmap offset} sorted by
   code, then bitmaps: height rows of ceil(width/8) bytes, most significant bit first. */
typedef struct Font { const u8 *data; u32 size, height, ascent, count; } Font;

static int font_of(Thread *t, Object *bytes, Font *f) {
    Array *a = (Array *)bytes;
    if (!a || a->length < 8 || ARRAY_DATA(a)[0] != 'B' || ARRAY_DATA(a)[1] != 'F') {
        throw_new(t, "java/lang/IllegalArgumentException", "bad font");
        return 0;
    }
    f->data = ARRAY_DATA(a);
    f->size = a->length;
    f->height = f->data[3];
    f->ascent = f->data[4];
    f->count = rd16(f->data+6);
    if (8+f->count*8 > f->size) { throw_new(t, "java/lang/IllegalArgumentException", "bad font"); return 0; }
    return 1;
}

static const u8 *glyph(const Font *f, u32 code) {
    u32 lo = 0, hi = f->count;
    while (lo < hi) {
        u32 mid = (lo+hi)/2;
        const u8 *e = f->data+8+mid*8;
        u32 c = rd16(e);
        if (c == code) return e;
        if (c < code) lo = mid+1; else hi = mid;
    }
    return code == '?' ? 0 : glyph(f, '?');
}

static const u16 *string_range(Thread *t, Object *s, i32 offset, i32 length) {
    Jvm *vm = thread_vm(t);
    if (!s) { throw_new(t, "java/lang/NullPointerException", 0); return 0; }
    u32 *fields = (u32 *)s;
    u32 start = fields[vm->string_offset/4], count = fields[vm->string_count/4];
    if (offset < 0 || length < 0 || (u32)offset+(u32)length > count) {
        throw_new(t, "java/lang/StringIndexOutOfBoundsException", 0);
        return 0;
    }
    Array *chars = (Array *)(uintptr_t)fields[vm->string_value/4];
    return (const u16 *)ARRAY_DATA(chars)+start+offset;
}

/* Draw.textWidth(String, offset, length, byte[] font) */
static int draw_text_width(Thread *t, u32 *args, u32 *result) {
    Font f;
    if (!font_of(t, ARG_REF(3), &f)) return 1;
    const u16 *c = string_range(t, ARG_REF(0), I(1), I(2));
    if (!c) return 1;
    u32 width = 0;
    for (i32 i = 0; i < I(2); i++) { const u8 *g = glyph(&f, c[i]); if (g) width += g[2]; }
    result[0] = width;
    return 0;
}

/* Draw.drawText(target, String, offset, length, x, y (top), rgb, byte[] font, bold, clip...) -> width */
static int draw_text(Thread *t, u32 *args, u32 *result) {
    Surface s;
    Font f;
    if (!surface(t, ARG_REF(0), &s) || !font_of(t, ARG_REF(7), &f)) return 1;
    const u16 *c = string_range(t, ARG_REF(1), I(2), I(3));
    if (!c) return 1;
    Clip clip = clip_of(&s, I(9), I(10), I(11), I(12));
    u16 color = rgb565(args[6]);
    int bold = I(8) != 0;
    i32 x = I(4), y = I(5);
    for (i32 i = 0; i < I(3); i++) {
        const u8 *g = glyph(&f, c[i]);
        if (!g) continue;
        u32 width = g[3], row_bytes = (width+7)/8, offset = rd32(g+4);
        const u8 *bits = f.data+8+f.count*8+offset;
        if (8+f.count*8+offset+row_bytes*f.height > f.size) break;
        for (u32 row = 0; row < f.height; row++)
            for (u32 col = 0; col < width; col++)
                if (bits[row*row_bytes+col/8] & (0x80 >> (col%8))) {
                    plot(&s, &clip, x+(i32)col, y+(i32)row, color);
                    if (bold) plot(&s, &clip, x+(i32)col+1, y+(i32)row, color);
                }
        x += g[2]+bold;
    }
    result[0] = (u32)(x-I(4));
    return 0;
}

static int draw_flush(Thread *t, u32 *args, u32 *result) {
    (void)result;
    Jvm *vm = thread_vm(t);
    if (vm->host.flush) vm->host.flush(vm->host.context, I(0), I(1), I(2), I(3));
    return 0;
}

/* ---------- PNG ---------- */

typedef struct Png {
    u32 width, height, idat;
    u8 depth, color, interlace;
    const u8 *palette, *trns;
    u32 palette_entries, trns_length;
} Png;

static int png_parse(const u8 *p, u32 size, Png *png) {
    static const u8 signature[8] = {137, 80, 78, 71, 13, 10, 26, 10};
    if (size < 8 || jvm_memcmp(p, signature, 8)) return 0;
    jvm_memset(png, 0, sizeof(*png));
    for (u32 at = 8; at+12 <= size;) {
        u32 length = rd32(p+at);
        const u8 *type = p+at+4, *data = p+at+8;
        if (length > size-at-12) return 0;
        if (!jvm_memcmp(type, "IHDR", 4) && length >= 13) {
            png->width = rd32(data); png->height = rd32(data+4);
            png->depth = data[8]; png->color = data[9]; png->interlace = data[12];
        } else if (!jvm_memcmp(type, "PLTE", 4)) { png->palette = data; png->palette_entries = length/3; }
        else if (!jvm_memcmp(type, "tRNS", 4)) { png->trns = data; png->trns_length = length; }
        else if (!jvm_memcmp(type, "IDAT", 4)) png->idat += length;
        else if (!jvm_memcmp(type, "IEND", 4)) break;
        at += 12+length;
    }
    if (!png->width || !png->height || png->width > 2048 || png->height > 2048 || png->interlace || !png->idat) return 0;
    u8 c = png->color, d = png->depth;
    int ok = (c == 0 && (d == 1 || d == 2 || d == 4 || d == 8 || d == 16)) || (c == 3 && d <= 8 && png->palette) ||
             ((c == 2 || c == 4 || c == 6) && (d == 8 || d == 16));
    return ok;
}

static u32 png_channels(u8 color) { return color == 2 ? 3 : color == 4 ? 2 : color == 6 ? 4 : 1; }

static u8 paeth(u8 a, u8 b, u8 c) {
    i32 p = (i32)a+b-c, pa = p > a ? p-a : a-p, pb = p > b ? p-b : b-p, pc = p > c ? p-c : c-p;
    return pa <= pb && pa <= pc ? a : pb <= pc ? b : c;
}

/* IDAT payload -> packed, inflate -> raw (height*(row_bytes+1)), unfilter, then
   RGB565 pixels and, when out_alpha is set, 8-bit alpha. Returns 1 on success. */
static int png_decode_core(const u8 *p, u32 size, const Png *ppng, u8 *packed, u8 *raw, u16 *out, u8 *out_alpha) {
    Png png = *ppng;
    u32 channels = png_channels(png.color), bits = channels*png.depth;
    u32 row_bytes = (png.width*bits+7)/8, bpp = bits >= 8 ? bits/8 : 1;
    u32 raw_size = png.height*(row_bytes+1);
    u32 n = 0;
    for (u32 at = 8; at+12 <= size;) {
        u32 length = rd32(p+at);
        if (!jvm_memcmp(p+at+4, "IDAT", 4)) { jvm_memcpy(packed+n, p+at+8, length); n += length; }
        if (!jvm_memcmp(p+at+4, "IEND", 4)) break;
        at += 12+length;
    }
    /* zlib: 2-byte header, deflate data, Adler-32 (not checked). */
    if (!(n > 6 && jar_inflate(packed+2, n-6, raw, raw_size))) return 0;
    u8 *d = raw;
    for (u32 y = 0; y < png.height; y++) {
        u8 *row = d+y*(row_bytes+1)+1, *above = y ? row-(row_bytes+1) : 0, filter = row[-1];
        for (u32 i = 0; i < row_bytes; i++) {
            u8 a = i >= bpp ? row[i-bpp] : 0, b = above ? above[i] : 0, c = above && i >= bpp ? above[i-bpp] : 0;
            switch (filter) {
            case 1: row[i] = (u8)(row[i]+a); break;
            case 2: row[i] = (u8)(row[i]+b); break;
            case 3: row[i] = (u8)(row[i]+((u32)a+b)/2); break;
            case 4: row[i] = (u8)(row[i]+paeth(a, b, c)); break;
            default: break;
            }
        }
    }
    u32 step = png.depth == 16 ? 2 : 1;
    for (u32 y = 0; y < png.height; y++) {
        const u8 *row = d+y*(row_bytes+1)+1;
        for (u32 x = 0; x < png.width; x++) {
            u32 r, g, b, a = 255;
            if (png.depth < 8) {
                u32 bitpos = x*png.depth, shift = 8-png.depth-bitpos%8;
                u32 v = (row[bitpos/8] >> shift) & ((1u << png.depth)-1);
                if (png.color == 3) {
                    if (v >= png.palette_entries) v = 0;
                    r = png.palette[v*3]; g = png.palette[v*3+1]; b = png.palette[v*3+2];
                    if (png.trns && v < png.trns_length) a = png.trns[v];
                } else {
                    r = g = b = v*255/((1u << png.depth)-1);
                    if (png.trns && png.trns_length >= 2 && v == rd16(png.trns)) a = 0;
                }
            } else {
                const u8 *px = row+x*channels*step;
                switch (png.color) {
                case 3:
                    r = px[0] < png.palette_entries ? png.palette[px[0]*3] : 0;
                    g = px[0] < png.palette_entries ? png.palette[px[0]*3+1] : 0;
                    b = px[0] < png.palette_entries ? png.palette[px[0]*3+2] : 0;
                    if (png.trns && px[0] < png.trns_length) a = png.trns[px[0]];
                    break;
                case 0:
                    r = g = b = px[0];
                    if (png.trns && png.trns_length >= 2 && px[0] == rd16(png.trns) >> (step == 2 ? 8 : 0)) a = 0;
                    break;
                case 4: r = g = b = px[0]; a = px[step]; break;
                case 2:
                    r = px[0]; g = px[step]; b = px[2*step];
                    if (png.trns && png.trns_length >= 6 && r == (rd16(png.trns) >> (step == 2 ? 8 : 0)) &&
                        g == (rd16(png.trns+2) >> (step == 2 ? 8 : 0)) && b == (rd16(png.trns+4) >> (step == 2 ? 8 : 0))) a = 0;
                    break;
                default: r = px[0]; g = px[step]; b = px[2*step]; a = px[3*step]; break;
                }
            }
            out[y*png.width+x] = rgb565(r << 16 | g << 8 | b);
            if (out_alpha) out_alpha[y*png.width+x] = (u8)a;
        }
    }
    return 1;
}

int jvm_png_info(const uint8_t *data, uint32_t size, JvmPngInfo *info) {
    Png png;
    if (!png_parse(data, size, &png)) return 0;
    u32 bits = png_channels(png.color)*png.depth;
    info->width = png.width; info->height = png.height;
    info->packed_size = png.idat;
    info->raw_size = png.height*((png.width*bits+7)/8+1);
    info->has_alpha = png.color == 4 || png.color == 6 || png.trns;
    return 1;
}

int jvm_png_decode(const uint8_t *data, uint32_t size, uint8_t *packed, uint8_t *raw, uint16_t *pixels, uint8_t *alpha) {
    Png png;
    return png_parse(data, size, &png) && png_decode_core(data, size, &png, packed, raw, pixels, alpha);
}

/* Draw.decodePng(byte[] data, int offset, int length, Image into) -> success */
static int draw_decode_png(Thread *t, u32 *args, u32 *result) {
    Jvm *vm = thread_vm(t);
    Array *source = (Array *)ARG_REF(0);
    Object *image = ARG_REF(3);
    if (!source || !image) { throw_new(t, "java/lang/NullPointerException", 0); return 1; }
    if (I(1) < 0 || I(2) < 0 || (u32)I(1)+(u32)I(2) > source->length) { throw_new(t, "java/lang/ArrayIndexOutOfBoundsException", 0); return 1; }
    if (!image_fields(vm)) return 1;
    Png png;
    result[0] = 0;
    if (!png_parse(ARRAY_DATA(source)+I(1), (u32)I(2), &png)) return 0;
    u32 bits = png_channels(png.color)*png.depth;
    u32 raw_size = png.height*((png.width*bits+7)/8+1);
    int has_alpha = png.color == 4 || png.color == 6 || png.trns;
    Class *bytes = primitive_array(vm, 'B'), *shorts = primitive_array(vm, 'S');
    Array *packed = new_array(vm, bytes, png.idat);
    if (!packed) { throw_new(t, "java/lang/OutOfMemoryError", 0); return 1; }
    push_root(vm, (Object *)packed);
    Array *raw = new_array(vm, bytes, raw_size);
    if (raw) push_root(vm, (Object *)raw);
    Array *pixels = raw ? new_array(vm, shorts, png.width*png.height) : 0;
    if (pixels) push_root(vm, (Object *)pixels);
    Array *alpha = pixels && has_alpha ? new_array(vm, bytes, png.width*png.height) : 0;
    u32 roots = 1+(raw != 0)+(pixels != 0);
    if (!raw || !pixels || (has_alpha && !alpha)) { pop_roots(vm, roots); throw_new(t, "java/lang/OutOfMemoryError", 0); return 1; }
    /* The source array cannot move: the heap is non-moving. */
    int ok = png_decode_core(ARRAY_DATA(source)+I(1), (u32)I(2), &png, ARRAY_DATA(packed), ARRAY_DATA(raw),
                             (u16 *)ARRAY_DATA(pixels), alpha ? ARRAY_DATA(alpha) : 0);
    pop_roots(vm, roots);
    if (!ok) return 0;
    FIELD(image, vm->image_pixels) = (u32)(uintptr_t)pixels;
    FIELD(image, vm->image_alpha) = (u32)(uintptr_t)alpha;
    FIELD(image, vm->image_width) = png.width;
    FIELD(image, vm->image_height) = png.height;
    result[0] = 1;
    return 0;
}

/* ---------- registry ---------- */

typedef struct Entry { const char *cls, *name, *desc; Native fn; } Entry;

static const Entry entries[] = {
    {"go1/Events", "poll", "([I)Z", events_poll},
    {"go1/Events", "post", "(II)V", events_post},
    {"go1/Draw", "fillRect", "(Ljava/lang/Object;IIIIIIIII)V", draw_fill_rect},
    {"go1/Draw", "drawLine", "(Ljava/lang/Object;IIIIIIIII)V", draw_line},
    {"go1/Draw", "fillTriangle", "(Ljava/lang/Object;IIIIIIIIIII)V", draw_fill_triangle},
    {"go1/Draw", "drawRegion", "(Ljava/lang/Object;Ljava/lang/Object;IIIIIIIIIII)V", draw_region},
    {"go1/Draw", "drawRGB", "(Ljava/lang/Object;[IIIIIIIZIIII)V", draw_rgb},
    {"go1/Draw", "getRGB", "(Ljava/lang/Object;[IIIIIII)V", draw_get_rgb},
    {"go1/Draw", "textWidth", "(Ljava/lang/String;II[B)I", draw_text_width},
    {"go1/Draw", "drawText", "(Ljava/lang/Object;Ljava/lang/String;IIIII[BZIIII)I", draw_text},
    {"go1/Draw", "flush", "(IIII)V", draw_flush},
    {"go1/Draw", "decodePng", "([BIILjava/lang/Object;)Z", draw_decode_png},
};

Native midp_native_for(Utf8 class_name, Utf8 name, Utf8 desc) {
    for (u32 i = 0; i < sizeof(entries)/sizeof(entries[0]); i++)
        if (utf8_equals(class_name, entries[i].cls) && utf8_equals(name, entries[i].name) && utf8_equals(desc, entries[i].desc))
            return entries[i].fn;
    return 0;
}

/* ---------- launcher ---------- */

int jvm_main_class(const JvmHost *host, char *name, uint32_t capacity) {
    u32 size = 0;
    u8 *manifest = host->read(host->context, "META-INF/MANIFEST.MF", &size);
    if (!manifest) return 0;
    int found = 0;
    /* MIDlet-1 wins over Main-Class wherever it appears. */
    for (u32 i = 0; i < size && !found; i++)
        if ((!i || manifest[i-1] == '\n') && size-i >= 9 && !jvm_memcmp(manifest+i, "MIDlet-1:", 9)) {
            const char *launcher = "go1/Launcher";
            u32 n = jvm_strlen(launcher);
            if (n < capacity) { jvm_memcpy(name, launcher, n+1); found = 1; }
        }
    for (u32 i = 0; i < size && !found; i++) {
        if (i && manifest[i-1] != '\n') continue;
        if (size-i >= 11 && !jvm_memcmp(manifest+i, "Main-Class:", 11)) {
            u32 p = i+11, n = 0;
            while (p < size && manifest[p] == ' ') p++;
            while (p < size && manifest[p] != '\r' && manifest[p] != '\n' && n+1 < capacity) {
                name[n++] = manifest[p] == '.' ? '/' : (char)manifest[p];
                p++;
            }
            name[n] = 0;
            found = n > 0;
        }
    }
    host->release(host->context, manifest);
    return found;
}

uint32_t jvm_manifest_value(const uint8_t *manifest, uint32_t size, const char *key, char *out, uint32_t capacity) {
    u32 key_length = jvm_strlen(key), n = 0;
    if (!capacity) return 0;
    for (u32 i = 0; i < size; i++) {
        if ((i && manifest[i-1] != '\n') || size-i < key_length+1 || jvm_memcmp(manifest+i, key, key_length) ||
            manifest[i+key_length] != ':') continue;
        u32 p = i+key_length+1;
        while (p < size && manifest[p] == ' ') p++;
        for (;;) {
            while (p < size && manifest[p] != '\r' && manifest[p] != '\n') {
                if (n+1 < capacity) out[n++] = (char)manifest[p];
                p++;
            }
            if (p < size && manifest[p] == '\r') p++;
            if (p < size && manifest[p] == '\n') p++;
            if (p < size && manifest[p] == ' ') { p++; continue; }  /* continuation line */
            break;
        }
        while (n && out[n-1] == ' ') n--;
        break;
    }
    out[n] = 0;
    return n;
}
