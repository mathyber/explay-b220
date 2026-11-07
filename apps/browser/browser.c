/* BROWSER.GO1: a simple text browser with HTTPS for the GO1 loader.
   go1-title: Браузер

Packet data and TCP come from the firmware (GO1 API), TLS 1.0-1.2 from
BearSSL with the Mozilla root certificates (tools/build_browser.py), text in
the GO1 JVM bitmap fonts (Latin, Cyrillic). HTML is read as a stream: text,
headings, paragraphs, lists, links, image alt text and forms (sent by GET or
POST); no scripts, styles or pictures. Cookies are kept while the browser
runs; responses may come gzip- or deflate-compressed. A query without a dot
goes to DuckDuckGo Lite.

Keys: up/down - previous/next link or field, else scroll; left/right - page
up/down; OK - open the link, edit or toggle the field, press the button; left
soft key - address; right soft key - back; * - reload; # - start page. The
address and field editor uses multi-tap. */
#include "go1.h"
#include "bearssl.h"

typedef go1_u32 u32;
typedef unsigned short u16;
typedef unsigned char u8;

/* ---------- freestanding C library (BearSSL needs these) ---------- */

void *memcpy(void *dst, const void *src, size_t n) {
    u8 *d = dst; const u8 *s = src;
    if (!(((u32)d | (u32)s) & 3)) {
        u32 *dw = (u32 *)d; const u32 *sw = (const u32 *)s;
        for (; n >= 4; n -= 4) *dw++ = *sw++;
        d = (u8 *)dw; s = (const u8 *)sw;
    }
    while (n--) *d++ = *s++;
    return dst;
}
void *memmove(void *dst, const void *src, size_t n) {
    u8 *d = dst; const u8 *s = src;
    if (d < s) while (n--) *d++ = *s++;
    else { d += n; s += n; while (n--) *--d = *--s; }
    return dst;
}
void *memset(void *dst, int c, size_t n) {
    u8 *d = dst;
    while (n && ((u32)d & 3)) { *d++ = (u8)c; n--; }
    u32 word = (u8)c * 0x01010101u, *dw = (u32 *)d;
    for (; n >= 4; n -= 4) *dw++ = word;
    d = (u8 *)dw;
    while (n--) *d++ = (u8)c;
    return dst;
}
int memcmp(const void *a, const void *b, size_t n) {
    const u8 *x = a, *y = b;
    for (; n; n--, x++, y++) if (*x != *y) return *x < *y ? -1 : 1;
    return 0;
}
size_t strlen(const char *s) { size_t n = 0; while (s[n]) n++; return n; }
/* ARM EABI helpers clang calls for structure copies; Zig's compiler-rt only
   has trap stubs for them (an endless "b ."), these override them. */
void __aeabi_memcpy(void *d, const void *s, size_t n) { memcpy(d, s, n); }
void __aeabi_memcpy4(void *d, const void *s, size_t n) { memcpy(d, s, n); }
void __aeabi_memcpy8(void *d, const void *s, size_t n) { memcpy(d, s, n); }
void __aeabi_memmove(void *d, const void *s, size_t n) { memmove(d, s, n); }
void __aeabi_memmove4(void *d, const void *s, size_t n) { memmove(d, s, n); }
void __aeabi_memmove8(void *d, const void *s, size_t n) { memmove(d, s, n); }
void __aeabi_memset(void *d, size_t n, int c) { memset(d, c, n); }
void __aeabi_memset4(void *d, size_t n, int c) { memset(d, c, n); }
void __aeabi_memset8(void *d, size_t n, int c) { memset(d, c, n); }
void __aeabi_memclr(void *d, size_t n) { memset(d, 0, n); }
void __aeabi_memclr4(void *d, size_t n) { memset(d, 0, n); }
void __aeabi_memclr8(void *d, size_t n) { memset(d, 0, n); }

/* ---------- screen, fonts ---------- */

#define W 176
#define H 220
#define BAR 18
#define TOP BAR
#define BOTTOM (H-BAR)
#define VIEW (BOTTOM-TOP)
#define MARGIN 3
#define WIDTH (W-2*MARGIN)

#define C_BACK 0x0000
#define C_TEXT 0xffff
#define C_HEAD 0xffe0
#define C_LINK 0x5e7f
#define C_GRAY 0x9cf3
#define C_BAR 0x0200
#define C_BAR_TEXT 0xffff
#define C_SELECT 0x03e0

extern const unsigned char font_s_data[], font_m_data[], font_l_data[];
extern const br_x509_trust_anchor browser_anchors[];
extern const unsigned browser_anchor_count;

enum { F_SMALL, F_TEXT, F_HEAD };
static const u8 *fonts[3];
static const Go1Api *api;
static u16 *screen;

static u32 rd16(const u8 *p) { return (u32)p[0] << 8 | p[1]; }
static u32 rd32(const u8 *p) { return rd16(p) << 16 | rd16(p+2); }
static u32 font_height(u32 f) { return fonts[f][3]; }

static const u8 *glyph(const u8 *font, u32 code) {
    u32 lo = 0, hi = rd16(font+6);
    while (lo < hi) {
        u32 mid = (lo+hi)/2;
        const u8 *e = font+8+mid*8;
        u32 c = rd16(e);
        if (c == code) return e;
        if (c < code) lo = mid+1; else hi = mid;
    }
    return code == '?' ? 0 : glyph(font, '?');
}
static u32 char_width(u32 f, u32 code) { const u8 *g = glyph(fonts[f], code); return g ? g[2] : 0; }

static void fill(int x, int y, int w, int h, u32 color) {
    if (x < 0) { w += x; x = 0; }
    if (y < 0) { h += y; y = 0; }
    if (x+w > W) w = W-x;
    if (y+h > H) h = H-y;
    if (w <= 0 || h <= 0) return;
    u32 pair = (color & 0xffff) << 16 | (color & 0xffff);
    for (int j = 0; j < h; j++) {
        u16 *row = screen+(y+j)*W+x;
        int n = w;
        if ((u32)row & 2) { *row++ = (u16)color; n--; }
        u32 *words = (u32 *)row;
        for (; n >= 2; n -= 2) *words++ = pair;
        if (n) *(u16 *)words = (u16)color;
    }
}

/* Draws within rows clip_top..clip_bottom-1; returns the width. */
static u32 draw_chars(u32 f, int x, int y, const u16 *text, u32 n, u32 color, int clip_top, int clip_bottom) {
    const u8 *font = fonts[f];
    u32 count = rd16(font+6), height = font[3], start = x;
    const u8 *bitmaps = font+8+count*8;
    for (u32 i = 0; i < n; i++) {
        const u8 *g = glyph(font, text[i]);
        if (!g) continue;
        u32 width = g[3], row_bytes = (width+7)/8;
        const u8 *bits = bitmaps+rd32(g+4);
        for (u32 row = 0; row < height; row++) {
            int py = y+(int)row;
            if (py < clip_top || py >= clip_bottom) continue;
            for (u32 col = 0; col < width; col++) {
                int px = x+(int)col;
                if (px >= 0 && px < W && (bits[row*row_bytes+col/8] & (0x80 >> (col%8))))
                    screen[py*W+px] = (u16)color;
            }
        }
        x += g[2];
    }
    return (u32)x-start;
}

static u32 ascii(u16 *out, const char *s, u32 capacity) {
    u32 n = 0;
    while (s[n] && n+1 < capacity) { out[n] = (u8)s[n]; n++; }
    out[n] = 0;
    return n;
}
static u32 wlen(const u16 *s) { u32 n = 0; while (s[n]) n++; return n; }
static u32 text_width(u32 f, const u16 *s, u32 n) { u32 w = 0; for (u32 i = 0; i < n; i++) w += char_width(f, s[i]); return w; }

/* ---------- entropy: SHA-256 pool fed by RAM, clocks, key and network timing ---------- */

#ifndef ENTROPY_RAM
#define ENTROPY_RAM ((const void *)0x04080000u)  /* MMI task stacks and queues (tests point it elsewhere) */
#endif
static br_sha256_context pool;
static void stir(const void *data, u32 n) { br_sha256_update(&pool, data, n); }
static void stir_time(void) { u32 t[2] = {api->ms(), api->clock()}; stir(t, sizeof(t)); }

/* ---------- page model ---------- */

#define TEXT_MAX 60000
#define RUN_MAX 9000
#define LINE_MAX 5000
#define LINK_MAX 1200
#define HREF_MAX 40000

typedef struct { u32 start; u16 length, x, link; u8 font; u8 color; } Run;  /* link: index+1, 0 none */
typedef struct { u32 run, y; u16 count, height; } Line;
typedef struct { u32 href, line; u16 field; } Link;  /* field: index+1 for a form field */

static u16 text[TEXT_MAX];
static Run runs[RUN_MAX];
static Line lines[LINE_MAX];
static Link links[LINK_MAX];
static char hrefs[HREF_MAX];
static u32 text_len, run_count, line_count, link_count, href_len, page_height, truncated;
static u16 page_title[48];
static u32 title_len;

/* forms (see the forms section) */
#define FORM_MAX 24
#define FIELD_MAX 160
#define POOL_MAX 24000
#define VALUE_ROOM 200   /* bytes an editable value can grow to */
enum { T_TEXT, T_PASSWORD, T_HIDDEN, T_CHECK, T_RADIO, T_SELECT, T_SUBMIT, T_TEXTAREA };
typedef struct { u32 action; u8 post, has_submit; } Form;   /* action: pool offset ("" = this page) */
typedef struct { u8 type, form, checked; u16 option, option_count; u32 name, value, cap, options; } Field;
static Form forms[FORM_MAX];
static Field fields[FIELD_MAX];
static char fpool[POOL_MAX];   /* names, values, options ("value\0label\0" pairs) */
static u32 form_count, field_count, pool_len, form_open, in_select, in_option, in_textarea;
static char option_value[256], option_label[256], area[1024];
static u32 option_label_len, option_selected, option_has_value, area_len;


/* layout state */
static u32 line_x, line_height, gap_pending, space_pending, at_line_start;
static u32 style_font, style_color, style_link, pre;
static u16 word[48];
static u32 word_len, word_width;

static void forms_reset(void);
static void page_reset(void) {
    text_len = run_count = line_count = link_count = href_len = truncated = title_len = 0;
    page_height = 0;
    line_x = 0; line_height = 0; gap_pending = 0; space_pending = 0; at_line_start = 1;
    style_font = F_TEXT; style_color = 0; style_link = 0; pre = 0;
    word_len = word_width = 0;
    page_title[0] = 0;
    forms_reset();
}

static void open_line(void) {
    if (line_count >= LINE_MAX) { truncated = 1; return; }
    Line *l = &lines[line_count++];
    l->run = run_count; l->count = 0; l->height = (u16)font_height(F_TEXT); l->y = page_height;
    line_x = 0; line_height = l->height;
}

static void close_line(void) {
    if (!line_count) return;
    Line *l = &lines[line_count-1];
    l->height = (u16)line_height;
    page_height = l->y+l->height;
}

static void new_line(void) {
    close_line();
    open_line();
    at_line_start = 1;
    space_pending = 0;
}

static void put_run(const u16 *chars, u32 n, u32 width) {
    if (truncated || !n) return;
    if (!line_count) open_line();
    if (text_len+n > TEXT_MAX) { truncated = 1; return; }
    Line *l = &lines[line_count-1];
    Run *last = l->count ? &runs[run_count-1] : 0;
    if (last && last->font == style_font && last->color == style_color && last->link == style_link &&
        last->start+last->length == text_len) {
        last->length = (u16)(last->length+n);
    } else {
        if (run_count >= RUN_MAX) { truncated = 1; return; }
        Run *r = &runs[run_count++];
        r->start = text_len; r->length = (u16)n; r->x = (u16)line_x;
        r->font = (u8)style_font; r->color = (u8)style_color; r->link = (u16)style_link;
        l->count++;
    }
    memcpy(text+text_len, chars, n*2);
    text_len += n;
    line_x += width;
    u32 h = font_height(style_font);
    if (h > line_height) line_height = h;
    at_line_start = 0;
}

static void flush_word(void) {
    if (!word_len) return;
    u32 space = space_pending && !at_line_start ? char_width(style_font, ' ') : 0;
    if (gap_pending) {
        if (line_count && !at_line_start) new_line();
        /* Move the still empty current line down: the gap goes above it. */
        if (line_count > 1 && !lines[line_count-1].count) lines[line_count-1].y += gap_pending;
        gap_pending = 0;
        space = 0;
    }
    if (line_x+space+word_width > WIDTH && !at_line_start) { new_line(); space = 0; }
    if (space) { u16 sp = ' '; put_run(&sp, 1, space); }
    /* A word wider than the line is cut. */
    u32 start = 0;
    while (start < word_len) {
        u32 n = 0, w = 0;
        while (start+n < word_len && (line_x+w+char_width(style_font, word[start+n]) <= WIDTH || (at_line_start && !n))) {
            w += char_width(style_font, word[start+n]);
            n++;
        }
        put_run(word+start, n, w);
        start += n;
        if (start < word_len) new_line();
    }
    word_len = word_width = 0;
    space_pending = 0;
}

static void emit_char(u32 c) {
    if (c == '\r') return;
    if (pre && c == '\n') { flush_word(); new_line(); return; }
    if (c == ' ' || c == '\t' || c == '\n') {
        flush_word();
        space_pending = 1;
        return;
    }
    if (c == 0xa0) c = ' ';  /* no-break space stays inside the word */
    if (word_len >= sizeof(word)/2) flush_word();
    word[word_len++] = (u16)c;
    word_width += char_width(style_font, c);
}

static void block(u32 gap) {
    flush_word();
    if (line_count && !at_line_start) new_line();
    if (gap && line_count && gap > gap_pending) gap_pending = gap;
}

/* ---------- URLs ---------- */

#define URL_MAX 1600
typedef struct { u32 https, port; char host[128]; char path[URL_MAX]; } Url;
static char current[URL_MAX];
static Url url;  /* of the page being loaded */

static void copy(char *dst, const char *src, u32 capacity) {
    u32 n = 0;
    while (src[n] && n+1 < capacity) { dst[n] = src[n]; n++; }
    dst[n] = 0;
}

static int starts(const char *s, const char *prefix) {
    while (*prefix) { char a = *s++, b = *prefix++; if (a >= 'A' && a <= 'Z') a = (char)(a+32); if (a != b) return 0; }
    return 1;
}

static int parse_url(const char *s, Url *u) {
    u->https = 0;
    if (starts(s, "https://")) { u->https = 1; s += 8; }
    else if (starts(s, "http://")) s += 7;
    else return 0;
    u32 n = 0;
    u->port = u->https ? 443 : 80;
    while (*s && *s != '/' && *s != '?' && *s != '#' && *s != ':' && n+1 < sizeof(u->host)) {
        char c = *s++;
        u->host[n++] = c >= 'A' && c <= 'Z' ? (char)(c+32) : c;
    }
    u->host[n] = 0;
    if (!n) return 0;
    if (*s == ':') {
        u32 port = 0;
        s++;
        while (*s >= '0' && *s <= '9') port = port*10+(u32)(*s++-'0');
        if (port && port < 65536) u->port = port;
    }
    n = 0;
    if (*s != '/') u->path[n++] = '/';
    while (*s && *s != '#' && n+1 < sizeof(u->path)) {
        char c = *s++;
        if (c == ' ') { if (n+4 < sizeof(u->path)) { u->path[n++] = '%'; u->path[n++] = '2'; u->path[n++] = '0'; } }
        else u->path[n++] = c;
    }
    u->path[n] = 0;
    return 1;
}

/* Resolve href against base into out (both absolute http(s) URLs). */
static void resolve(const char *base, const char *href, char *out, u32 capacity) {
    while (*href == ' ') href++;
    if (starts(href, "http://") || starts(href, "https://")) { copy(out, href, capacity); return; }
    u32 n = 0, scheme = 0;
    while (base[scheme] && base[scheme] != ':') scheme++;
    if (href[0] == '/' && href[1] == '/') {
        for (u32 i = 0; i <= scheme && n+1 < capacity; i++) out[n++] = base[i];
        out[n] = 0;
        copy(out+n, href, capacity-n);
        return;
    }
    u32 host_end = scheme+3;
    while (base[host_end] && base[host_end] != '/' && base[host_end] != '?' && base[host_end] != '#') host_end++;
    if (href[0] == '/') {
        for (u32 i = 0; i < host_end && n+1 < capacity; i++) out[n++] = base[i];
        out[n] = 0;
        copy(out+n, href, capacity-n);
        return;
    }
    u32 end = host_end, last_slash = host_end;
    while (base[end] && base[end] != '?' && base[end] != '#') { if (base[end] == '/') last_slash = end; end++; }
    if (href[0] == '?' || href[0] == 0) {
        for (u32 i = 0; i < end && n+1 < capacity; i++) out[n++] = base[i];
    } else {
        for (u32 i = 0; i < last_slash && n+1 < capacity; i++) out[n++] = base[i];
        if (n+1 < capacity) out[n++] = '/';
        /* "./" and "../" segments */
        while (href[0] == '.') {
            if (href[1] == '/') href += 2;
            else if (href[1] == '.' && (href[2] == '/' || !href[2])) {
                href += href[2] ? 3 : 2;
                if (n > host_end+1) { n--; while (n > host_end && out[n-1] != '/') n--; }
            } else break;
        }
    }
    out[n] = 0;
    copy(out+n, href, capacity-n);
}

/* ---------- HTML ---------- */

enum { H_TEXT, H_TAG, H_COMMENT, H_RAW, H_ENTITY };
static u32 html_state, tag_len, raw_kind, entity_len, in_title, quote, list_depth, ordered[8], counter[8];
static char tag[1024];
static char refresh[600];  /* <meta http-equiv="refresh" content="0; url=..."> */
static char entity[12];
static int charset_1251;

static int tag_is(const char *name, const char *want) {
    while (*want) { if (*name++ != *want++) return 0; }
    return !*name;
}

/* Value of attribute `name` inside tag text (lower-case tag text). */
static int attribute(const char *t, const char *name, char *out, u32 capacity) {
    u32 len = (u32)strlen(name);
    for (const char *p = t; *p; p++) {
        if ((p == t || p[-1] == ' ' || p[-1] == '\t' || p[-1] == '\n' || p[-1] == '"' || p[-1] == '\'') && starts(p, name)) {
            const char *q = p+len;
            while (*q == ' ') q++;
            if (*q != '=') continue;
            q++;
            while (*q == ' ') q++;
            char end = 0;
            if (*q == '"' || *q == '\'') end = *q++;
            u32 n = 0;
            while (*q && (end ? *q != end : *q != ' ' && *q != '>') && n+1 < capacity) {
                static const struct { const char *name; char c; } named[] = {
                    {"&amp;", '&'}, {"&lt;", '<'}, {"&gt;", '>'}, {"&quot;", '"'}, {"&#39;", '\''}, {"&#x27;", '\''}};
                int matched = 0;
                if (*q == '&')
                    for (u32 k = 0; k < sizeof(named)/sizeof(named[0]) && !matched; k++)
                        if (starts(q, named[k].name)) { out[n++] = named[k].c; q += strlen(named[k].name); matched = 1; }
                if (!matched) out[n++] = *q++;
            }
            out[n] = 0;
            return 1;
        }
    }
    return 0;
}

static void title_char(u32 c) {
    if (c == '\n' || c == '\t' || c == '\r') c = ' ';
    if (c == ' ' && (!title_len || page_title[title_len-1] == ' ')) return;
    if (title_len+1 < sizeof(page_title)/2) { page_title[title_len++] = (u16)c; page_title[title_len] = 0; }
}

static void put_utf8(char *out, u32 *n, u32 capacity, u32 c);

static void text_char(u32 c) {
    if (in_title) { title_char(c); return; }
    if (in_select) {  /* option labels are not page text */
        if (in_option) {
            if (c == '\n' || c == '\t' || c == '\r') c = ' ';
            if (c != ' ' || option_label_len) put_utf8(option_label, &option_label_len, sizeof(option_label), c);
        }
        return;
    }
    emit_char(c);
}

static void bullet(void) {
    u32 depth = list_depth ? list_depth-1 : 0;
    if (depth > 7) depth = 7;
    u16 mark[8];
    u32 n = 0;
    for (u32 i = 0; i < depth && n < 3; i++) mark[n++] = ' ';
    if (ordered[depth]) {
        u32 v = ++counter[depth];
        if (v >= 10) mark[n++] = (u16)('0'+v/10%10);
        mark[n++] = (u16)('0'+v%10);
        mark[n++] = '.';
    } else mark[n++] = 0x2022;
    for (u32 i = 0; i < n; i++) emit_char(mark[i]);
    flush_word();
    space_pending = 1;
}

/* ---------- forms ---------- */

static u32 widen_utf8(u16 *out, const char *s, u32 capacity);

static void forms_reset(void) {
    form_count = field_count = pool_len = form_open = in_select = in_option = in_textarea = 0;
}

/* A string in the pool with room to grow to `room` bytes; ~0 when full. */
static u32 pool_add(const char *str, u32 room) {
    u32 len = (u32)strlen(str), size = (len > room ? len : room)+1;
    if (pool_len+size > POOL_MAX) return ~0u;
    u32 at = pool_len;
    memcpy(fpool+at, str, len+1);
    pool_len += size;
    return at;
}
static const char *pooled(u32 at) { return at == ~0u ? "" : fpool+at; }

/* An attribute without a value (checked, selected). */
static int flag(const char *t, const char *name) {
    u32 len = (u32)strlen(name);
    for (const char *p = t; *p; p++)
        if ((p == t || p[-1] == ' ' || p[-1] == '\t' || p[-1] == '\n' || p[-1] == '"' || p[-1] == '\'') && starts(p, name)) {
            char c = p[len];
            if (!c || c == ' ' || c == '\t' || c == '\n' || c == '/' || c == '=' || c == '>') return 1;
        }
    return 0;
}

static u32 add_field(u32 type, const char *name, const char *value, u32 room) {
    if (field_count >= FIELD_MAX) return 0;
    Field *f = &fields[field_count];
    f->type = (u8)type; f->form = (u8)form_open; f->checked = 0; f->option = f->option_count = 0; f->options = ~0u;
    f->name = pool_add(name, 0);
    f->value = pool_add(value, room);
    f->cap = room > strlen(value) ? room : (u32)strlen(value);
    if (f->name == ~0u || f->value == ~0u) return 0;
    if (type == T_SUBMIT && form_open) forms[form_open-1].has_submit = 1;
    return ++field_count;
}

/* A field on the page: a link to it, drawn as `chars` placeholder characters
   (draw_field paints the box over them) or as the label (buttons). */
static void show_field(u32 field, const char *label, u32 chars) {
    if (!field || link_count >= LINK_MAX) return;
    flush_word();
    if (space_pending && !at_line_start) {  /* the space before stays outside the field */
        u16 sp = ' ';
        u32 w = char_width(style_font, ' ');
        if (line_x+w <= WIDTH) put_run(&sp, 1, w);
        space_pending = 0;
    }
    u32 saved_link = style_link, saved_color = style_color;
    links[link_count].href = 0;
    links[link_count].line = line_count ? line_count-1 : 0;
    links[link_count].field = (u16)field;
    style_link = ++link_count;
    style_color = 1;
    if (label) {
        u16 wide[64];
        u32 n = widen_utf8(wide, label, 64);
        emit_char('[');
        for (u32 i = 0; i < n; i++) emit_char(wide[i] == ' ' ? 0xa0 : wide[i]);
        emit_char(']');
    } else for (u32 i = 0; i < chars; i++) emit_char('_');
    flush_word();
    space_pending = 1;
    style_link = saved_link; style_color = saved_color;
}

static void option_done(void) {
    if (!in_option) return;
    in_option = 0;
    while (option_label_len && option_label[option_label_len-1] == ' ') option_label_len--;
    option_label[option_label_len] = 0;
    Field *f = &fields[in_select-1];
    const char *value = option_has_value ? option_value : option_label;
    u32 a = pool_add(value, 0), b = pool_add(option_label, 0);
    if (a == ~0u || b == ~0u) return;
    if (!f->option_count) f->options = a;
    if (option_selected) f->option = f->option_count;
    f->option_count++;
}

/* The value (and label) of a select's option `index`. */
static const char *option_at(const Field *f, u32 index, int label) {
    const char *p = pooled(f->options);
    if (f->options == ~0u) return "";
    for (u32 i = 0; i < index; i++) { p += strlen(p)+1; p += strlen(p)+1; }
    return label ? p+strlen(p)+1 : p;
}

static void form_tag(const char *name, int closing) {
    char value[600], type[24], fname[96];
    if (tag_is(name, "form")) {
        if (closing) {
            if (form_open && !forms[form_open-1].has_submit)
                show_field(add_field(T_SUBMIT, "", "", 0), "Отправить", 0);
            form_open = 0;
            return;
        }
        if (form_count >= FORM_MAX) { form_open = 0; return; }
        Form *f = &forms[form_count];
        if (!attribute(tag, "action", value, sizeof(value))) value[0] = 0;
        f->action = pool_add(value, 0);
        f->post = attribute(tag, "method", type, sizeof(type)) && starts(type, "post");
        f->has_submit = 0;
        form_open = ++form_count;
        return;
    }
    if (!attribute(tag, "name", fname, sizeof(fname))) fname[0] = 0;
    if (tag_is(name, "input") && !closing) {
        if (!attribute(tag, "type", type, sizeof(type))) copy(type, "text", sizeof(type));
        if (!attribute(tag, "value", value, sizeof(value))) value[0] = 0;
        if (starts(type, "hidden")) add_field(T_HIDDEN, fname, value, 0);
        else if (starts(type, "submit") || starts(type, "image")) {
            const char *label = value[0] ? value : "Отправить";
            if (starts(type, "image") && attribute(tag, "alt", value, sizeof(value)) && value[0]) label = value;
            show_field(add_field(T_SUBMIT, fname, starts(type, "image") ? "" : value, 0), label, 0);
        } else if (starts(type, "checkbox") || starts(type, "radio")) {
            u32 field = add_field(starts(type, "radio") ? T_RADIO : T_CHECK, fname, value[0] ? value : "on", 0);
            if (field) fields[field-1].checked = (u8)flag(tag, "checked");
            show_field(field, 0, 3);
        } else if (starts(type, "button") || starts(type, "reset") || starts(type, "file")) {
            /* scripts or files: nothing to do here */
        } else show_field(add_field(starts(type, "password") ? T_PASSWORD : T_TEXT, fname, value, VALUE_ROOM), 0, 14);
        return;
    }
    if (tag_is(name, "button")) {
        if (closing) { if (style_link && links[style_link-1].field) { flush_word(); style_link = 0; style_color = 0; } return; }
        if (attribute(tag, "type", type, sizeof(type)) && !starts(type, "submit")) return;
        if (!attribute(tag, "value", value, sizeof(value))) value[0] = 0;
        u32 field = add_field(T_SUBMIT, fname, value, 0);
        if (!field || link_count >= LINK_MAX) return;
        flush_word();
        links[link_count].href = 0;
        links[link_count].line = line_count ? line_count-1 : 0;
        links[link_count].field = (u16)field;
        style_link = ++link_count;
        style_color = 1;
        return;
    }
    if (tag_is(name, "select")) {
        if (closing) {
            option_done();
            if (in_select) show_field(in_select, 0, 14);
            in_select = 0;
            return;
        }
        in_select = add_field(T_SELECT, fname, "", 0);
        return;
    }
    if (tag_is(name, "option") && in_select) {
        option_done();
        if (closing) return;
        in_option = 1;
        option_label_len = 0;
        option_has_value = (u32)attribute(tag, "value", option_value, sizeof(option_value));
        option_selected = (u32)flag(tag, "selected");
        return;
    }
}

/* The text of a textarea, read raw up to its closing tag. */
static void textarea_done(void) {
    /* the raw text ends with the closing tag's "</textarea" (10 characters) */
    area_len = area_len >= 10 ? area_len-10 : 0;
    area[area_len] = 0;
    u32 at = 0;
    while (area[at] == '\r' || area[at] == '\n') at++;  /* the newline after <textarea> */
    fields[in_textarea-1].value = pool_add(area+at, VALUE_ROOM);
    fields[in_textarea-1].cap = VALUE_ROOM > area_len ? VALUE_ROOM : area_len;
    show_field(in_textarea, 0, 14);
    in_textarea = 0;
}

static int form_name(const char *name) {
    return tag_is(name, "form") || tag_is(name, "input") || tag_is(name, "button") || tag_is(name, "select") ||
           tag_is(name, "option") || tag_is(name, "textarea");
}

static void handle_tag(void) {
    tag[tag_len] = 0;
    int closing = tag[0] == '/';
    char name[16];
    u32 n = 0;
    const char *p = tag+closing;
    while (*p && *p != ' ' && *p != '/' && *p != '\n' && *p != '\t' && n+1 < sizeof(name)) {
        char c = *p++;
        name[n++] = c >= 'A' && c <= 'Z' ? (char)(c+32) : c;
    }
    name[n] = 0;
    if (!closing && (tag_is(name, "script") || tag_is(name, "style") || tag_is(name, "svg") ||
                     tag_is(name, "template") || tag_is(name, "textarea"))) {
        html_state = H_RAW;
        tag_len = 0;
        /* Index into raw_end: script, style, svg, template, -, textarea (kept as the field's text). */
        raw_kind = tag_is(name, "script") ? 1 : tag_is(name, "style") ? 2 : tag_is(name, "svg") ? 3 :
                   tag_is(name, "template") ? 4 : 6;
        if (raw_kind == 6) {
            char fname[96];
            if (!attribute(tag, "name", fname, sizeof(fname))) fname[0] = 0;
            in_textarea = add_field(T_TEXTAREA, fname, "", 0);
            area_len = 0;
        }
        return;
    }
    if (form_name(name)) {
        if (tag_is(name, "form")) block(5);
        form_tag(name, closing);
        return;
    }
    if (tag_is(name, "title")) {
        if (!closing && !in_title) { title_len = 0; page_title[0] = 0; }  /* replaces the host shown while loading */
        in_title = !closing;
        return;
    }
    if (tag_is(name, "meta")) {
        char value[600], equiv[16];
        if (attribute(tag, "charset", value, sizeof(value)) || attribute(tag, "content", value, sizeof(value)))
            for (char *v = value; *v; v++) if (starts(v, "1251")) charset_1251 = 1;
        if (attribute(tag, "http-equiv", equiv, sizeof(equiv)) && starts(equiv, "refresh") &&
            attribute(tag, "content", value, sizeof(value))) {
            for (char *v = value; *v; v++)
                if (starts(v, "url=")) {
                    v += 4;
                    if (*v == '\'' || *v == '"') v++;
                    copy(refresh, v, sizeof(refresh));
                    u32 len = (u32)strlen(refresh);
                    if (len && (refresh[len-1] == '\'' || refresh[len-1] == '"')) refresh[len-1] = 0;
                    break;
                }
        }
        return;
    }
    if (tag_is(name, "br")) { flush_word(); new_line(); return; }
    if (name[0] == 'h' && name[1] >= '1' && name[1] <= '6' && !name[2]) {
        block(6);
        style_font = closing ? F_TEXT : F_HEAD;
        style_color = closing ? 0 : 2;
        return;
    }
    if (tag_is(name, "p") || tag_is(name, "blockquote") || tag_is(name, "table") ||
        tag_is(name, "hr") || tag_is(name, "figure")) { block(5); return; }
    if (tag_is(name, "pre")) { block(5); pre = !closing; return; }
    if (tag_is(name, "ul") || tag_is(name, "ol")) {
        block(3);
        if (!closing && list_depth < 8) { ordered[list_depth] = name[0] == 'o'; counter[list_depth] = 0; list_depth++; }
        else if (closing && list_depth) list_depth--;
        return;
    }
    if (tag_is(name, "li")) { block(0); if (!closing) bullet(); return; }
    if (tag_is(name, "div") || tag_is(name, "tr") || tag_is(name, "dt") || tag_is(name, "dd") ||
        tag_is(name, "section") || tag_is(name, "article") || tag_is(name, "header") || tag_is(name, "footer") ||
        tag_is(name, "nav") || tag_is(name, "main") || tag_is(name, "aside") || tag_is(name, "figcaption") ||
        tag_is(name, "address") || tag_is(name, "center") || tag_is(name, "dl") || tag_is(name, "caption")) {
        block(0);
        return;
    }
    if (tag_is(name, "td") || tag_is(name, "th")) { flush_word(); space_pending = 1; return; }
    if (tag_is(name, "a")) {
        flush_word();
        char href[600];
        if (closing) { style_link = 0; style_color = style_font == F_HEAD ? 2 : 0; return; }
        if (attribute(tag, "href", href, sizeof(href)) && !starts(href, "javascript:") && !starts(href, "mailto:") &&
            !starts(href, "tel:") && href[0] != '#' && link_count < LINK_MAX) {
            u32 len = (u32)strlen(href);
            if (href_len+len+1 <= HREF_MAX) {
                memcpy(hrefs+href_len, href, len+1);
                links[link_count].href = href_len;
                links[link_count].line = line_count ? line_count-1 : 0;
                links[link_count].field = 0;
                href_len += len+1;
                style_link = ++link_count;
                style_color = 1;
            }
        }
        return;
    }
    if (tag_is(name, "img") && !closing) {
        char alt[80];
        if (attribute(tag, "alt", alt, sizeof(alt)) && alt[0]) {
            u32 saved = style_color;
            if (!style_link) style_color = 3;
            emit_char('[');
            for (const char *a = alt; *a; a++) emit_char((u8)*a);
            emit_char(']');
            flush_word();
            style_color = saved;
        }
        return;
    }
}

static int after_equals(void) {
    u32 i = tag_len;
    while (i && (tag[i-1] == ' ' || tag[i-1] == '\n' || tag[i-1] == '\t')) i--;
    return i && tag[i-1] == '=';
}

static const char *const raw_end[] = {"", "</script", "</style", "</svg", "</template", "", "</textarea"};

static void entity_done(int terminated) {
    entity[entity_len] = 0;
    u32 c = 0;
    if (entity[0] == '#') {
        if (entity[1] == 'x' || entity[1] == 'X') {
            for (u32 i = 2; entity[i]; i++) {
                char d = entity[i];
                c = c*16+(u32)(d >= 'a' ? d-'a'+10 : d >= 'A' ? d-'A'+10 : d-'0');
            }
        } else for (u32 i = 1; entity[i]; i++) c = c*10+(u32)(entity[i]-'0');
    } else {
        static const struct { const char *name; u16 code; } names[] = {
            {"amp", '&'}, {"lt", '<'}, {"gt", '>'}, {"quot", '"'}, {"apos", '\''}, {"nbsp", 0xa0},
            {"mdash", 0x2014}, {"ndash", 0x2013}, {"laquo", 0xab}, {"raquo", 0xbb}, {"copy", 0xa9},
            {"hellip", 0x2026}, {"rsquo", 0x2019}, {"lsquo", 0x2018}, {"ldquo", 0x201c}, {"rdquo", 0x201d},
            {"bull", 0x2022}, {"middot", 0xb7}, {"reg", 0xae}, {"deg", 0xb0}, {"euro", 0x20ac}, {"times", 0xd7},
            {"numero", 0x2116}, {"shy", 0}};
        for (u32 i = 0; i < sizeof(names)/sizeof(names[0]); i++)
            if (tag_is(entity, names[i].name)) { c = names[i].code ? names[i].code : 0xffff; break; }
    }
    if (c == 0xffff) return;  /* soft hyphen */
    if (c && c < 0x10000) { text_char(c); if (!terminated) {} return; }
    text_char('&');
    for (u32 i = 0; i < entity_len; i++) text_char((u8)entity[i]);
    if (terminated) text_char(';');
}

static void html_char(u32 c) {
    switch (html_state) {
    case H_TEXT:
        if (c == '<') { html_state = H_TAG; tag_len = 0; quote = 0; return; }
        if (c == '&') { html_state = H_ENTITY; entity_len = 0; return; }
        text_char(c);
        return;
    case H_ENTITY:
        if (c == ';') { entity_done(1); html_state = H_TEXT; return; }
        if (entity_len+1 < sizeof(entity) && ((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') || (c == '#' && !entity_len))) {
            entity[entity_len++] = (char)c;
            return;
        }
        entity_done(0);
        html_state = H_TEXT;
        html_char(c);
        return;
    case H_TAG:
        if (tag_len == 3 && tag[0] == '!' && tag[1] == '-' && tag[2] == '-') { html_state = H_COMMENT; tag_len = 0; }
        else if (quote) { if (c == quote) quote = 0; }
        else if ((c == '"' || c == '\'') && after_equals()) quote = c;
        else if (c == '>') { if (tag[0] != '!' && tag[0] != '?') handle_tag(); if (html_state == H_TAG) html_state = H_TEXT; return; }
        if (html_state == H_TAG && tag_len+1 < sizeof(tag)) tag[tag_len++] = (char)(c < 128 ? c : '?');
        return;
    case H_COMMENT:
        tag[tag_len%3] = (char)c;
        tag_len++;
        if (c == '>' && tag_len >= 3 && tag[(tag_len-2)%3] == '-' && tag[(tag_len-3)%3] == '-') html_state = H_TEXT;
        return;
    case H_RAW: {
        const char *end = raw_end[raw_kind];
        if (raw_kind == 6 && in_textarea) put_utf8(area, &area_len, sizeof(area), c);
        u32 len = (u32)strlen(end);
        char lower = (char)(c >= 'A' && c <= 'Z' ? c+32 : c);
        if (lower == end[tag_len]) {
            tag_len++;
            if (tag_len == len) {
                html_state = H_TAG; tag_len = 0; quote = 0; tag[0] = 0;
                if (raw_kind == 6 && in_textarea) textarea_done();
            }
        }
        else tag_len = lower == '<' ? 1 : 0;
        return;
    }
    }
}

/* ---------- text decoding: UTF-8 or windows-1251 ---------- */

static const u16 cp1251_high[64] = {
    0x0402, 0x0403, 0x201a, 0x0453, 0x201e, 0x2026, 0x2020, 0x2021, 0x20ac, 0x2030, 0x0409, 0x2039, 0x040a, 0x040c, 0x040b, 0x040f,
    0x0452, 0x2018, 0x2019, 0x201c, 0x201d, 0x2022, 0x2013, 0x2014, '?', 0x2122, 0x0459, 0x203a, 0x045a, 0x045c, 0x045b, 0x045f,
    0x00a0, 0x040e, 0x045e, 0x0408, 0x00a4, 0x0490, 0x00a6, 0x00a7, 0x0401, 0x00a9, 0x0404, 0x00ab, 0x00ac, 0x00ad, 0x00ae, 0x0407,
    0x00b0, 0x00b1, 0x0406, 0x0456, 0x0491, 0x00b5, 0x00b6, 0x00b7, 0x0451, 0x2116, 0x0454, 0x00bb, 0x0458, 0x0405, 0x0455, 0x0457};
static u32 utf8_need, utf8_code, plain;

static void decoded(u32 c) {
    if (plain) { if (c == '\t') c = ' '; emit_char(c); }
    else html_char(c);
}

static void body_byte(u32 b) {
    if (charset_1251) { decoded(b < 0x80 ? b : b >= 0xc0 ? 0x410+b-0xc0 : cp1251_high[b-0x80]); return; }
    if (utf8_need) {
        if ((b & 0xc0) == 0x80) {
            utf8_code = utf8_code << 6 | (b & 0x3f);
            if (--utf8_need == 0) decoded(utf8_code < 0x10000 ? utf8_code : '?');
            return;
        }
        utf8_need = 0;
        decoded('?');
    }
    if (b < 0x80) decoded(b);
    else if ((b & 0xe0) == 0xc0) { utf8_need = 1; utf8_code = b & 0x1f; }
    else if ((b & 0xf0) == 0xe0) { utf8_need = 2; utf8_code = b & 0x0f; }
    else if ((b & 0xf8) == 0xf0) { utf8_need = 3; utf8_code = b & 0x07; }
    else decoded('?');
}

static void feed_string(const char *s) { while (*s) body_byte((u8)*s++); }

/* UTF-8 literal -> UTF-16, returns the length. */
static u32 widen_utf8(u16 *out, const char *s, u32 capacity) {
    const u8 *p = (const u8 *)s;
    u32 n = 0;
    while (*p && n+1 < capacity) {
        u32 c = *p;
        if (c >= 0xe0 && p[1] && p[2]) { c = (u32)(p[0] & 15) << 12 | (u32)(p[1] & 63) << 6 | (p[2] & 63); p += 3; }
        else if (c >= 0xc0 && p[1]) { c = (u32)(p[0] & 31) << 6 | (p[1] & 63); p += 2; }
        else p++;
        out[n++] = (u16)c;
    }
    out[n] = 0;
    return n;
}

/* ---------- status line ---------- */

static u16 status[64];
static void set_status(const char *s) { widen_utf8(status, s, sizeof(status)/2); }
static void append_status_number(u32 value, const char *suffix) {
    u32 n = wlen(status);
    char digits[12];
    u32 d = 0;
    do { digits[d++] = (char)('0'+value%10); value /= 10; } while (value);
    while (d && n+1 < sizeof(status)/2) status[n++] = (u16)digits[--d];
    status[n] = 0;
    u16 tail[16];
    u32 k = widen_utf8(tail, suffix, 16);
    for (u32 i = 0; i < k && n+1 < sizeof(status)/2; i++) status[n++] = tail[i];
    status[n] = 0;
}

/* ---------- cookies: kept while the browser runs ---------- */

#define COOKIE_MAX 48
typedef struct { char domain[64], path[64], name[64], value[320]; u8 used, host_only, secure; } Cookie;
static Cookie cookies[COOKIE_MAX];

static int same(const char *a, const char *b) { while (*a && *a == *b) { a++; b++; } return *a == *b; }

/* host is domain or a subdomain of it */
static int under(const char *host, const char *domain) {
    u32 h = (u32)strlen(host), d = (u32)strlen(domain);
    if (!d || h < d) return 0;
    if (h == d) return same(host, domain);
    return host[h-d-1] == '.' && same(host+h-d, domain);
}

static void trim_copy(char *out, const char *from, const char *to, u32 capacity) {
    while (from < to && (*from == ' ' || *from == '\t')) from++;
    while (to > from && (to[-1] == ' ' || to[-1] == '\t' || to[-1] == '\r')) to--;
    u32 n = 0;
    while (from < to && n+1 < capacity) out[n++] = *from++;
    out[n] = 0;
}

/* The year in an Expires date ("Thu, 01 Jan 1970 00:00:00 GMT"), 0 if none. */
static u32 expires_year(const char *v) {
    for (; *v; v++)
        if (v[0] >= '0' && v[0] <= '9' && v[1] >= '0' && v[1] <= '9' && v[2] >= '0' && v[2] <= '9' &&
            v[3] >= '0' && v[3] <= '9' && !(v[4] >= '0' && v[4] <= '9'))
            return (u32)((v[0]-'0')*1000+(v[1]-'0')*100+(v[2]-'0')*10+(v[3]-'0'));
    return 0;
}

/* Set-Cookie from the page being loaded (url). */
static void cookie_set(const char *line) {
    const char *p = line, *semi;
    for (semi = p; *semi && *semi != ';'; semi++) {}
    const char *eq = p;
    while (eq < semi && *eq != '=') eq++;
    if (eq == semi) return;
    Cookie c;
    trim_copy(c.name, p, eq, sizeof(c.name));
    trim_copy(c.value, eq+1, semi, sizeof(c.value));
    if (!c.name[0]) return;
    copy(c.domain, url.host, sizeof(c.domain));
    c.host_only = 1; c.secure = 0;
    /* default path: the request path up to its last slash */
    u32 n = 0, last = 0;
    while (url.path[n] && url.path[n] != '?' && n+1 < sizeof(c.path)) { c.path[n] = url.path[n]; if (url.path[n] == '/') last = n; n++; }
    c.path[last ? last : 1] = 0;
    if (!last) c.path[0] = '/';
    int remove = 0;
    for (p = *semi ? semi+1 : semi; *p;) {
        for (semi = p; *semi && *semi != ';'; semi++) {}
        char attr[96];
        trim_copy(attr, p, semi, sizeof(attr));
        if (starts(attr, "domain=")) {
            const char *d = attr+7;
            while (*d == '.') d++;
            char lower[64];
            u32 k = 0;
            for (; d[k] && k+1 < sizeof(lower); k++) lower[k] = d[k] >= 'A' && d[k] <= 'Z' ? (char)(d[k]+32) : d[k];
            lower[k] = 0;
            if (!under(url.host, lower)) return;  /* another site's domain */
            copy(c.domain, lower, sizeof(c.domain));
            c.host_only = 0;
        } else if (starts(attr, "path=") && attr[5] == '/') copy(c.path, attr+5, sizeof(c.path));
        else if (starts(attr, "max-age=")) { const char *v = attr+8; if (*v == '-' || *v == '0') remove = 1; }
        else if (starts(attr, "expires=")) {
            u32 year = expires_year(attr+8), now = api->clock();
            if (year && now && year < 1980+now/31556952) remove = 1;
        } else if (same(attr, "secure") || same(attr, "Secure")) c.secure = 1;
        p = *semi ? semi+1 : semi;
    }
    Cookie *slot = 0;
    for (u32 i = 0; i < COOKIE_MAX; i++)
        if (cookies[i].used && same(cookies[i].name, c.name) && same(cookies[i].domain, c.domain) && same(cookies[i].path, c.path))
            slot = &cookies[i];
    if (remove) { if (slot) slot->used = 0; return; }
    if (!slot) for (u32 i = 0; i < COOKIE_MAX && !slot; i++) if (!cookies[i].used) slot = &cookies[i];
    if (!slot) slot = &cookies[api->ms() % COOKIE_MAX];  /* full: one goes */
    c.used = 1;
    *slot = c;
}

/* "Cookie: ..." for the page being loaded, appended to out. */
static void add(char *out, u32 *n, u32 capacity, const char *s);
static void cookie_header(char *out, u32 *n, u32 capacity) {
    int any = 0;
    for (u32 i = 0; i < COOKIE_MAX; i++) {
        const Cookie *c = &cookies[i];
        if (!c->used || (c->secure && !url.https)) continue;
        if (c->host_only ? !same(url.host, c->domain) : !under(url.host, c->domain)) continue;
        if (!starts(url.path, c->path)) continue;
        add(out, n, capacity, any ? "; " : "Cookie: ");
        add(out, n, capacity, c->name);
        add(out, n, capacity, "=");
        add(out, n, capacity, c->value);
        any = 1;
    }
    if (any) add(out, n, capacity, "\r\n");
}

/* ---------- gzip and deflate (whole response, then inflated into the page) ---------- */

#define GZ_IN (64*1024)
static u8 *gz_in, *gz_window;   /* from the heap when the browser opens; 0: compression not asked for */
static u32 gz_len, gz_overflow, encoding;  /* encoding: 0 identity, 1 gzip, 2 deflate */
static u32 in_pos, bit_buffer, bit_count, out_count, inflate_error;

static void body_byte(u32 b);
static u32 bits(u32 need) {
    while (bit_count < need) {
        if (in_pos >= gz_len) { inflate_error = 1; return 0; }
        bit_buffer |= (u32)gz_in[in_pos++] << bit_count;
        bit_count += 8;
    }
    u32 v = bit_buffer & ((1u << need)-1);
    bit_buffer >>= need;
    bit_count -= need;
    return v;
}

static void out_byte(u32 b) {
    gz_window[out_count++ & 32767] = (u8)b;
    if (!truncated) body_byte(b);
}

typedef struct { u16 count[16], symbol[320]; } Huffman;

static int huffman(Huffman *h, const u8 *lengths, u32 n) {
    u16 offs[16];
    for (u32 i = 0; i < 16; i++) h->count[i] = 0;
    for (u32 i = 0; i < n; i++) h->count[lengths[i]]++;
    if (h->count[0] == n) return 0;
    int left = 1;
    for (u32 i = 1; i < 16; i++) { left <<= 1; left -= h->count[i]; if (left < 0) return -1; }
    offs[1] = 0;
    for (u32 i = 1; i < 15; i++) offs[i+1] = (u16)(offs[i]+h->count[i]);
    for (u32 i = 0; i < n; i++) if (lengths[i]) h->symbol[offs[lengths[i]]++] = (u16)i;
    return left;
}

static int decode(const Huffman *h) {
    int code = 0, first = 0, index = 0;
    for (u32 len = 1; len < 16; len++) {
        code |= (int)bits(1);
        int count = h->count[len];
        if (code-count < first) return h->symbol[index+(code-first)];
        index += count; first += count;
        first <<= 1; code <<= 1;
        if (inflate_error) return -1;
    }
    return -1;
}

static const u16 length_base[29] = {3, 4, 5, 6, 7, 8, 9, 10, 11, 13, 15, 17, 19, 23, 27, 31, 35, 43, 51, 59, 67, 83, 99, 115, 131, 163, 195, 227, 258};
static const u8 length_extra[29] = {0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 2, 2, 2, 2, 3, 3, 3, 3, 4, 4, 4, 4, 5, 5, 5, 5, 0};
static const u16 dist_base[30] = {1, 2, 3, 4, 5, 7, 9, 13, 17, 25, 33, 49, 65, 97, 129, 193, 257, 385, 513, 769, 1025, 1537, 2049, 3073,
                                  4097, 6145, 8193, 12289, 16385, 24577};
static const u8 dist_extra[30] = {0, 0, 0, 0, 1, 1, 2, 2, 3, 3, 4, 4, 5, 5, 6, 6, 7, 7, 8, 8, 9, 9, 10, 10, 11, 11, 12, 12, 13, 13};

static int codes(const Huffman *lengths, const Huffman *distances) {
    for (;;) {
        int symbol = decode(lengths);
        if (symbol < 0 || inflate_error) return -1;
        if (symbol < 256) { out_byte((u32)symbol); if (truncated) return 0; continue; }
        if (symbol == 256) return 0;
        symbol -= 257;
        if (symbol >= 29) return -1;
        u32 len = length_base[symbol]+bits(length_extra[symbol]);
        int d = decode(distances);
        if (d < 0 || d >= 30) return -1;
        u32 dist = dist_base[d]+bits(dist_extra[d]);
        if (inflate_error || dist > out_count || dist > 32768) return -1;
        while (len--) out_byte(gz_window[(out_count-dist) & 32767]);
        if (truncated) return 0;
    }
}

static Huffman fixed_lengths, fixed_distances, dyn_lengths, dyn_distances;

static int inflate_blocks(void) {
    static int fixed_ready;
    if (!fixed_ready) {
        u8 l[288];
        u32 i = 0;
        for (; i < 144; i++) l[i] = 8;
        for (; i < 256; i++) l[i] = 9;
        for (; i < 280; i++) l[i] = 7;
        for (; i < 288; i++) l[i] = 8;
        huffman(&fixed_lengths, l, 288);
        for (i = 0; i < 30; i++) l[i] = 5;
        huffman(&fixed_distances, l, 30);
        fixed_ready = 1;
    }
    u32 last;
    do {
        last = bits(1);
        u32 type = bits(2);
        if (inflate_error) return -1;
        if (type == 0) {
            bit_buffer = 0; bit_count = 0;
            if (in_pos+4 > gz_len) return -1;
            u32 len = gz_in[in_pos] | (u32)gz_in[in_pos+1] << 8;
            in_pos += 4;
            if (in_pos+len > gz_len) return -1;
            while (len--) { out_byte(gz_in[in_pos++]); if (truncated) return 0; }
        } else if (type == 1) {
            if (codes(&fixed_lengths, &fixed_distances)) return -1;
        } else if (type == 2) {
            static const u8 order[19] = {16, 17, 18, 0, 8, 7, 9, 6, 10, 5, 11, 4, 12, 3, 13, 2, 14, 1, 15};
            u8 l[320];
            u32 nlen = bits(5)+257, ndist = bits(5)+1, ncode = bits(4)+4;
            if (nlen > 286 || ndist > 30) return -1;
            for (u32 i = 0; i < 19; i++) l[order[i]] = (u8)(i < ncode ? bits(3) : 0);
            if (huffman(&dyn_lengths, l, 19) != 0) return -1;
            for (u32 i = 0; i < nlen+ndist;) {
                int symbol = decode(&dyn_lengths);
                if (symbol < 0) return -1;
                if (symbol < 16) { l[i++] = (u8)symbol; continue; }
                u32 len = 0, repeat;
                if (symbol == 16) { if (!i) return -1; len = l[i-1]; repeat = 3+bits(2); }
                else if (symbol == 17) repeat = 3+bits(3);
                else repeat = 11+bits(7);
                if (i+repeat > nlen+ndist) return -1;
                while (repeat--) l[i++] = (u8)len;
            }
            if (!l[256]) return -1;
            if (huffman(&dyn_lengths, l, nlen) < 0 || huffman(&dyn_distances, l+nlen, ndist) < 0) return -1;
            if (codes(&dyn_lengths, &dyn_distances)) return -1;
        } else return -1;
        if (truncated) return 0;
    } while (!last);
    return 0;
}

/* The compressed body into the page. */
static void inflate_body(void) {
    in_pos = 0; bit_buffer = bit_count = out_count = inflate_error = 0;
    if (encoding == 1) {
        if (gz_len < 18 || gz_in[0] != 0x1f || gz_in[1] != 0x8b || gz_in[2] != 8) return;
        u32 flags = gz_in[3];
        in_pos = 10;
        if (flags & 4) in_pos += 2+(gz_in[in_pos] | (u32)gz_in[in_pos+1] << 8);
        if (flags & 8) { while (in_pos < gz_len && gz_in[in_pos]) in_pos++; in_pos++; }
        if (flags & 16) { while (in_pos < gz_len && gz_in[in_pos]) in_pos++; in_pos++; }
        if (flags & 2) in_pos += 2;
        if (in_pos >= gz_len) return;
    } else if (gz_len >= 2 && (gz_in[0] & 15) == 8 && !((gz_in[0] << 8 | gz_in[1]) % 31)) in_pos = 2;  /* zlib, else raw deflate */
    inflate_blocks();
}

/* ---------- HTTP ---------- */

enum { R_HEADERS, R_BODY, R_CHUNK_SIZE, R_CHUNK_DATA, R_CHUNK_END, R_DONE };
static u32 http_state, status_code, chunked, content_length, has_length, body_received, header_len, chunk_left;
static u32 chunk_digits, chunk_extension, unsupported;
static char header[8192];
static char location[600];
static char content_type[64];

static void http_reset(void) {
    http_state = R_HEADERS; status_code = 0; chunked = 0; content_length = 0; has_length = 0;
    body_received = 0; header_len = 0; chunk_left = 0; chunk_digits = 0; chunk_extension = 0;
    location[0] = 0; content_type[0] = 0; unsupported = 0; refresh[0] = 0;
    encoding = 0; gz_len = 0; gz_overflow = 0;
    html_state = H_TEXT; in_title = 0; list_depth = 0; charset_1251 = 0; utf8_need = 0; plain = 0;
}

static int header_is(const char *line, const char *name) { return starts(line, name) && line[strlen(name)] == ':'; }

static void header_value(const char *line, char *out, u32 capacity) {
    while (*line && *line != ':') line++;
    if (*line) line++;
    while (*line == ' ') line++;
    copy(out, line, capacity);
}

static void headers_done(void) {
    header[header_len] = 0;
    char *line = header, value[64];
    const char *p = line;
    while (*p && *p != ' ') p++;
    while (*p == ' ') p++;
    while (*p >= '0' && *p <= '9') status_code = status_code*10+(u32)(*p++-'0');
    while (*line) {
        char *end = line;
        while (*end && *end != '\n') end++;
        char saved = *end;
        *end = 0;
        if (end > line && end[-1] == '\r') end[-1] = 0;
        if (header_is(line, "location")) header_value(line, location, sizeof(location));
        else if (header_is(line, "set-cookie")) { const char *v = line+11; while (*v == ' ') v++; cookie_set(v); }
        else if (header_is(line, "content-encoding")) {
            header_value(line, value, sizeof(value));
            if (starts(value, "gzip") || starts(value, "x-gzip")) encoding = 1;
            else if (starts(value, "deflate")) encoding = 2;
            else if (value[0] && !starts(value, "identity")) unsupported = 1;
        }
        else if (header_is(line, "content-type")) {
            header_value(line, content_type, sizeof(content_type));
            for (char *c = content_type; *c; c++) if (*c >= 'A' && *c <= 'Z') *c = (char)(*c+32);
        } else if (header_is(line, "transfer-encoding")) {
            header_value(line, value, sizeof(value));
            if (starts(value, "chunked")) chunked = 1;
        } else if (header_is(line, "content-length")) {
            header_value(line, value, sizeof(value));
            for (const char *v = value; *v >= '0' && *v <= '9'; v++) content_length = content_length*10+(u32)(*v-'0');
            has_length = 1;
        }
        *end = saved;
        line = saved ? end+1 : end;
    }
    int html = !content_type[0];
    for (const char *c = content_type; *c; c++) {
        if (starts(c, "html")) html = 1;
        if (starts(c, "1251")) charset_1251 = 1;
    }
    if (!html && starts(content_type, "text/")) { plain = 1; pre = 1; }
    else if (!html && status_code < 300) unsupported = 1;
    http_state = chunked ? R_CHUNK_SIZE : R_BODY;
}

static void body(const u8 *p, u32 n) {
    body_received += n;
    if (unsupported || (status_code >= 300 && status_code < 400)) return;
    if (encoding) {  /* inflated when it has all arrived */
        if (!gz_in) { unsupported = 1; return; }
        u32 room = GZ_IN-gz_len;
        if (n > room) { gz_overflow = 1; n = room; }
        memcpy(gz_in+gz_len, p, n);
        gz_len += n;
        return;
    }
    for (u32 i = 0; i < n && !truncated; i++) body_byte(p[i]);
}

/* Returns 1 when the whole response has arrived. */
static int http_bytes(const u8 *p, u32 n) {
    for (u32 i = 0; i < n; i++) {
        u8 b = p[i];
        switch (http_state) {
        case R_HEADERS:
            if (header_len+1 < sizeof(header)) header[header_len++] = (char)b;
            if ((header_len >= 4 && !memcmp(header+header_len-4, "\r\n\r\n", 4)) ||
                (header_len >= 2 && header[header_len-1] == '\n' && header[header_len-2] == '\n')) {
                headers_done();
                if (http_state == R_BODY && has_length && !content_length) { http_state = R_DONE; return 1; }
            }
            break;
        case R_BODY: {
            u32 take = n-i;
            if (has_length && take > content_length-body_received) take = content_length-body_received;
            body(p+i, take);
            i += take-1;
            if (has_length && body_received >= content_length) { http_state = R_DONE; return 1; }
            break;
        }
        case R_CHUNK_SIZE:
            if (b == '\n') {
                if (chunk_digits && !chunk_left) { http_state = R_DONE; return 1; }
                if (chunk_digits) http_state = R_CHUNK_DATA;
                chunk_digits = 0; chunk_extension = 0;
            } else if (b == ';') chunk_extension = 1;
            else if (b != '\r' && !chunk_extension) {
                u32 v = b >= '0' && b <= '9' ? b-'0' : b >= 'a' && b <= 'f' ? b-'a'+10u : b >= 'A' && b <= 'F' ? b-'A'+10u : 16;
                if (v < 16 && chunk_digits < 8) { chunk_left = chunk_left*16+v; chunk_digits++; }
            }
            break;
        case R_CHUNK_DATA: {
            u32 take = n-i < chunk_left ? n-i : chunk_left;
            body(p+i, take);
            i += take-1;
            chunk_left -= take;
            if (!chunk_left) http_state = R_CHUNK_END;
            break;
        }
        case R_CHUNK_END:
            if (b == '\n') { http_state = R_CHUNK_SIZE; chunk_digits = 0; chunk_left = 0; }
            break;
        default:
            return 1;
        }
    }
    return http_state == R_DONE;
}

/* ---------- connection ---------- */

enum { N_IDLE, N_PDP, N_DNS, N_CONNECT, N_IO, N_DONE };
static u32 net_step, net_since, sock, address, use_tls, request_len, request_sent, redirects, received_total;
static char request[6144];
static br_ssl_client_context sc;
static br_x509_minimal_context xc;
static u8 tls_in[BR_SSL_BUFSIZE_INPUT];
static u8 tls_out[4096+85];
static u32 tls_ready;

static void add(char *out, u32 *n, u32 capacity, const char *s) { while (*s && *n+1 < capacity) out[(*n)++] = *s++; out[*n] = 0; }

/* A form sent by POST: the body for the next load, then for its request. */
static char post_body[2048];
static u32 post_len, post_pending, post_now;

static void add_number(char *out, u32 *n, u32 capacity, u32 v) {
    char digits[12];
    u32 d = 0;
    do { digits[d++] = (char)('0'+v%10); v /= 10; } while (v);
    while (d && *n+1 < capacity) out[(*n)++] = digits[--d];
    out[*n] = 0;
}

static void build_request(void) {
    u32 n = 0;
    request[0] = 0;
    add(request, &n, sizeof(request), post_now ? "POST " : "GET ");
    add(request, &n, sizeof(request), url.path);
    add(request, &n, sizeof(request), " HTTP/1.1\r\nHost: ");
    add(request, &n, sizeof(request), url.host);
    if (url.port != (url.https ? 443u : 80u)) {
        char port[8], digits[6];
        u32 v = url.port, d = 0, k = 1;
        do { digits[d++] = (char)('0'+v%10); v /= 10; } while (v);
        port[0] = ':';
        while (d) port[k++] = digits[--d];
        port[k] = 0;
        add(request, &n, sizeof(request), port);
    }
    add(request, &n, sizeof(request), "\r\nUser-Agent: Mozilla/5.0 (Linux; Mobile) GO1Browser/0.2 (Explay B220)\r\n"
        "Accept: text/html,text/plain;q=0.9,*/*;q=0.5\r\nAccept-Language: ru,en;q=0.7\r\n");
    add(request, &n, sizeof(request), gz_in ? "Accept-Encoding: gzip, deflate\r\n" : "Accept-Encoding: identity\r\n");
    cookie_header(request, &n, sizeof(request));
    if (post_now) {
        add(request, &n, sizeof(request), "Content-Type: application/x-www-form-urlencoded\r\nContent-Length: ");
        add_number(request, &n, sizeof(request), post_len);
        add(request, &n, sizeof(request), "\r\n");
    }
    add(request, &n, sizeof(request), "Connection: close\r\n\r\n");
    if (post_now && n+post_len < sizeof(request)) { memcpy(request+n, post_body, post_len); n += post_len; }
    request_len = n;
    request_sent = 0;
}

static u32 parse_ipv4(const char *s) {
    u32 value = 0, parts = 0, part = 0, digits = 0;
    for (;; s++) {
        if (*s >= '0' && *s <= '9') { part = part*10+(u32)(*s-'0'); digits++; if (part > 255) return 0; }
        else if ((*s == '.' || !*s) && digits) { value = value << 8 | part; parts++; part = digits = 0; if (!*s) break; }
        else return 0;
    }
    return parts == 4 ? value : 0;
}

static const char *tls_error(int e) {
    switch (e) {
    case BR_ERR_X509_EXPIRED: return "Сертификат просрочен (дата?)";
    case BR_ERR_X509_NOT_TRUSTED: return "Сертификат не доверенный";
    case BR_ERR_X509_BAD_SERVER_NAME: return "Сертификат другого сайта";
    case BR_ERR_BAD_VERSION: case BR_ERR_BAD_CIPHER_SUITE: return "Сайт требует TLS 1.3";
    default: return 0;
    }
}

#define CUSTOM_STATUS ((const char *)1)
static void finish(const char *error);
static void load(const char *address_text, int remember);

static void start_tls(void) {
    /* Days since 0000-01-01 (BearSSL); the RTC counts from 1980-01-01 local time. */
    u32 now = api->clock();
    br_x509_minimal_set_time(&xc, 723180+now/86400, now%86400);
    br_ssl_engine_set_buffers_bidi(&sc.eng, tls_in, sizeof(tls_in), tls_out, sizeof(tls_out));
    u8 seed[32];
    stir_time();
    br_sha256_context snapshot = pool;
    br_sha256_out(&snapshot, seed);
    stir(seed, sizeof(seed));
    br_ssl_engine_inject_entropy(&sc.eng, seed, sizeof(seed));
    br_ssl_client_reset(&sc, url.host, 0);
}

/* Moves bytes; returns 0 to wait for the next tick. */
static int io_plain(void) {
    if (request_sent < request_len) {
        u32 r = api->tcp_send(sock, request+request_sent, request_len-request_sent);
        if (r == GO1_AGAIN) return 0;
        if (r == GO1_ERROR || r == 0) { finish("Ошибка отправки"); return 0; }
        request_sent += r;
        return 1;
    }
    u8 buffer[1024];
    u32 r = api->tcp_recv(sock, buffer, sizeof(buffer));
    if (r == GO1_AGAIN) return 0;
    if (r == GO1_ERROR) { finish(received_total ? 0 : "Ошибка приёма"); return 0; }
    if (r == 0) { finish(http_state == R_HEADERS ? "Сервер закрыл соединение" : 0); return 0; }
    received_total += r;
    stir(&r, sizeof(r));
    if (http_bytes(buffer, r)) { finish(0); return 0; }
    return 1;
}

static int io_tls(void) {
    br_ssl_engine_context *e = &sc.eng;
    unsigned state = br_ssl_engine_current_state(e);
    size_t len;
    if (state & BR_SSL_CLOSED) {
        int err = br_ssl_engine_last_error(e);
        if (!err || http_state != R_HEADERS) { finish(0); return 0; }
        const char *message = tls_error(err);
        if (message) finish(message);
        else { set_status("Ошибка TLS "); append_status_number((u32)err, ""); finish(CUSTOM_STATUS); }
        return 0;
    }
    if (state & BR_SSL_SENDREC) {
        unsigned char *buf = br_ssl_engine_sendrec_buf(e, &len);
        u32 r = api->tcp_send(sock, buf, (u32)len);
        if (r == GO1_AGAIN) return 0;
        if (r == GO1_ERROR || r == 0) { finish("Ошибка отправки"); return 0; }
        br_ssl_engine_sendrec_ack(e, r);
        return 1;
    }
    if (state & BR_SSL_RECVAPP) {
        unsigned char *buf = br_ssl_engine_recvapp_buf(e, &len);
        received_total += (u32)len;
        int done = http_bytes(buf, (u32)len);
        br_ssl_engine_recvapp_ack(e, len);
        if (done) { finish(0); return 0; }
        return 1;
    }
    if ((state & BR_SSL_SENDAPP) && request_sent < request_len) {
        tls_ready = 1;
        unsigned char *buf = br_ssl_engine_sendapp_buf(e, &len);
        u32 n = request_len-request_sent;
        if (n > len) n = (u32)len;
        memcpy(buf, request+request_sent, n);
        br_ssl_engine_sendapp_ack(e, n);
        request_sent += n;
        if (request_sent == request_len) br_ssl_engine_flush(e, 0);
        return 1;
    }
    if (state & BR_SSL_RECVREC) {
        unsigned char *buf = br_ssl_engine_recvrec_buf(e, &len);
        u32 r = api->tcp_recv(sock, buf, (u32)len);
        if (r == GO1_AGAIN) return 0;
        if (r == GO1_ERROR || r == 0) {
            finish(http_state != R_HEADERS ? 0 : r ? "Ошибка приёма" : "Сервер закрыл соединение");
            return 0;
        }
        stir(&r, sizeof(r));
        br_ssl_engine_recvrec_ack(e, r);
        return 1;
    }
    return 0;
}

static void net_tick(void) {
    u32 now = api->ms(), waited = now-net_since;
    switch (net_step) {
    case N_PDP:
        if (api->net_state() == GO1_NET_READY) { net_step = N_DNS; net_since = now; set_status("Поиск сайта…"); }
        else if (waited > 90000) finish("Нет подключения GPRS");
        break;
    case N_DNS:
        address = parse_ipv4(url.host);
        if (!address) address = api->dns(url.host);
        if (address) {
            stir_time();
            sock = api->tcp_open();
            if (!sock) { finish("Нет свободного сокета"); break; }
            net_step = N_CONNECT; net_since = now;
            set_status("Соединение…");
        } else if (waited > 30000) finish("Сайт не найден");
        break;
    case N_CONNECT: {
        u32 r = api->tcp_connect(sock, address, url.port);
        if (r == 0) {
            stir_time();
            build_request();
            if (use_tls) { set_status("Защищённое соединение…"); start_tls(); }
            else set_status("Загрузка…");
            net_step = N_IO; net_since = now;
        } else if (r == GO1_ERROR) finish("Не удалось соединиться");
        else if (waited > 30000) finish("Сайт не отвечает");
        break;
    }
    case N_IO:
        for (u32 i = 0; i < 48 && net_step == N_IO; i++)
            if (!(use_tls ? io_tls() : io_plain())) break;
        if (net_step == N_IO) {
            if (tls_ready || !use_tls) { set_status("Загрузка "); append_status_number(received_total/1024, " КБ"); }
            if (api->ms()-net_since > 120000) finish("Слишком долго");
        }
        break;
    default:
        break;
    }
}

/* ---------- history, pages ---------- */

#define HISTORY 12
static char history[HISTORY][URL_MAX];
static u32 history_count;
static u32 scroll, selected, editing;

static const char start_page[] =
    "<title>Браузер GO1</title><h1>Браузер GO1</h1>"
    "<p>Левая экранная — адрес или поиск, правая — назад. Вверх/вниз — ссылки, влево/вправо — страницы. * — обновить, 0 — в закладки, # — сюда.</p>"
    "<ul><li><a href=\"https://lite.duckduckgo.com/lite/\">DuckDuckGo Lite</a> — поиск"
    "<li><a href=\"https://ru.m.wikipedia.org/\">Википедия</a>"
    "<li><a href=\"https://text.npr.org/\">NPR Text</a>"
    "<li><a href=\"https://lite.cnn.com/\">CNN Lite</a>"
    "<li><a href=\"https://news.ycombinator.com/\">Hacker News</a>"
    "<li><a href=\"https://example.org/\">example.org</a>"
    "<li><a href=\"http://info.cern.ch/\">Первый сайт (CERN)</a></ul>";

static void show_internal(const char *html, const char *address_text) {
    page_reset();
    http_reset();
    feed_string(html);
    flush_word();
    close_line();
    copy(current, address_text, sizeof(current));
    scroll = 0; selected = 0;
    status[0] = 0;
}

/* ---------- card files: TLS seed and bookmarks (E:\BROWSER) ---------- */

static const u16 folder_path[] = {'E', ':', '\\', 'B', 'R', 'O', 'W', 'S', 'E', 'R', 0};
static const u16 seed_path[] = {'E', ':', '\\', 'B', 'R', 'O', 'W', 'S', 'E', 'R', '\\', 'S', 'E', 'E', 'D', '.', 'B', 'I', 'N', 0};
static const u16 marks_path[] = {'E', ':', '\\', 'B', 'R', 'O', 'W', 'S', 'E', 'R', '\\',
                                 'B', 'O', 'O', 'K', 'M', 'A', 'R', 'K', 'S', '.', 'T', 'X', 'T', 0};
#define MARKS_MAX 4096
static char marks[MARKS_MAX];
static u32 marks_len;
static char start_html[MARKS_MAX*2+1024];

/* Older loaders cannot write files: the browser then keeps nothing between runs. */
static int can_write(void) { return api->size >= __builtin_offsetof(Go1Api, alloc_static); }

/* The seed file carries entropy from earlier runs into the pool; it is
   rewritten at once (and after pages) so a value is never reused. */
static void seed_load(void) {
    u32 h = api->open_wide(seed_path);
    if (!h) return;
    u8 seed[32];
    u32 n = api->read(h, seed, sizeof(seed));
    api->close(h);
    if (n == sizeof(seed)) stir(seed, sizeof(seed));
}

static void seed_store(void) {
    if (!can_write()) return;
    u8 seed[32];
    stir_time();
    br_sha256_context snapshot = pool;
    br_sha256_update(&snapshot, "GO1 seed file", 13);  /* not the value given to TLS */
    br_sha256_out(&snapshot, seed);
    api->make_dir(folder_path);
    u32 h = api->create(seed_path);
    if (h) { api->write(h, seed, sizeof(seed)); api->close(h); }
}

static void marks_load(void) {
    marks_len = 0;
    marks[0] = 0;
    u32 h = api->open_wide(marks_path);
    if (!h) return;
    u32 n = api->read(h, marks, MARKS_MAX-1);
    api->close(h);
    marks_len = n == GO1_ERROR ? 0 : n;
    marks[marks_len] = 0;
}

static void put_utf8(char *out, u32 *n, u32 capacity, u32 c) {
    if (c < 0x80) { if (*n+1 < capacity) out[(*n)++] = (char)c; }
    else if (c < 0x800) { if (*n+2 < capacity) { out[(*n)++] = (char)(0xc0 | c >> 6); out[(*n)++] = (char)(0x80 | (c & 63)); } }
    else if (*n+3 < capacity) {
        out[(*n)++] = (char)(0xe0 | c >> 12);
        out[(*n)++] = (char)(0x80 | (c >> 6 & 63));
        out[(*n)++] = (char)(0x80 | (c & 63));
    }
    out[*n] = 0;
}

/* Line "URL<tab>title" appended to BOOKMARKS.TXT (UTF-8). */
static void marks_add(void) {
    if (starts(current, "about:")) return;
    if (!can_write()) { set_status("Прошивка не умеет писать"); return; }
    char line[800];
    u32 n = 0;
    add(line, &n, sizeof(line), current);
    add(line, &n, sizeof(line), "\t");
    for (u32 i = 0; page_title[i] && n+5 < sizeof(line); i++) {
        u32 c = page_title[i];
        put_utf8(line, &n, sizeof(line), c == '\t' || c == '\n' ? ' ' : c);
    }
    add(line, &n, sizeof(line), "\n");
    if (marks_len+n >= MARKS_MAX) { set_status("Закладок слишком много"); return; }
    api->make_dir(folder_path);
    u32 h = api->open_append(marks_path);
    u32 written = h ? api->write(h, line, n) : GO1_ERROR;
    if (h) api->close(h);
    if (written != n) { set_status("Не удалось сохранить"); return; }
    marks_load();
    set_status("Добавлено в закладки");
}

static void add_escaped(char *out, u32 *n, u32 capacity, const char *s, u32 length) {
    for (u32 i = 0; i < length; i++) {
        char c = s[i];
        if (c == '&') add(out, n, capacity, "&amp;");
        else if (c == '<') add(out, n, capacity, "&lt;");
        else if (c == '>') add(out, n, capacity, "&gt;");
        else if (c == '"') add(out, n, capacity, "&quot;");
        else if (*n+1 < capacity) { out[(*n)++] = c; out[*n] = 0; }
    }
}

static const char *start_page_html(void) {
    u32 n = 0;
    start_html[0] = 0;
    add(start_html, &n, sizeof(start_html), start_page);
    if (!marks_len) return start_html;
    add(start_html, &n, sizeof(start_html), "<h2>Закладки</h2><ul>");
    for (const char *line = marks; *line;) {
        const char *end = line, *tab = 0;
        while (*end && *end != '\n') { if (*end == '\t' && !tab) tab = end; end++; }
        const char *url_end = tab ? tab : end, *title = tab ? tab+1 : end;
        if (url_end > line) {
            add(start_html, &n, sizeof(start_html), "<li><a href=\"");
            add_escaped(start_html, &n, sizeof(start_html), line, (u32)(url_end-line));
            add(start_html, &n, sizeof(start_html), "\">");
            if (end > title) add_escaped(start_html, &n, sizeof(start_html), title, (u32)(end-title));
            else add_escaped(start_html, &n, sizeof(start_html), line, (u32)(url_end-line));
            add(start_html, &n, sizeof(start_html), "</a>");
        }
        line = *end ? end+1 : end;
    }
    add(start_html, &n, sizeof(start_html), "</ul>");
    return start_html;
}

static void finish(const char *error) {
    api->timer(editing ? 100 : 0, editing ? 1 : 0);
    if (sock) { api->tcp_close(sock); sock = 0; }
    net_step = N_DONE;
    if (!error && refresh[0] && redirects < 6 && status_code < 300) copy(location, refresh, sizeof(location));
    if (!error && ((status_code >= 300 && status_code < 400) || refresh[0]) && location[0] && redirects < 6) {
        static char next[URL_MAX];
        resolve(current, location, next, sizeof(next));
        redirects++;
        load(next, 0);
        return;
    }
    if (encoding && gz_len && !unsupported) {
        inflate_body();
        if (gz_overflow) truncated = 1;
    }
    flush_word();
    if (error) {
        if (error != CUSTOM_STATUS) set_status(error);
        if (!text_len) {
            /* Keep the message on an otherwise empty page. */
            style_color = 3;
            for (const u16 *s = status; *s; s++) emit_char(*s);
            flush_word();
        }
    } else if (unsupported) {
        set_status("Не страница: ");
        u32 n = wlen(status);
        for (const char *c = content_type; *c && *c != ';' && n+1 < sizeof(status)/2; c++) status[n++] = (u16)(u8)*c;
        status[n] = 0;
    } else if (status_code >= 400) {
        set_status("Ошибка сервера ");
        append_status_number(status_code, "");
    } else if (truncated) set_status("Страница обрезана: велика");
    else status[0] = 0;
    close_line();
    if (!error && use_tls) seed_store();
}

static void load(const char *address_text, int remember) {
    if (sock) { api->tcp_close(sock); sock = 0; }
    if (remember && current[0]) {
        if (history_count == HISTORY) {
            for (u32 i = 1; i < HISTORY; i++) copy(history[i-1], history[i], sizeof(history[0]));
            history_count--;
        }
        copy(history[history_count++], current, sizeof(history[0]));
    }
    if (remember) redirects = 0;
    post_now = post_pending;  /* a form sent by POST; redirects after it are GET */
    post_pending = 0;
    if (starts(address_text, "about:start")) { show_internal(start_page_html(), "about:start"); net_step = N_IDLE; return; }
    if (address_text != current) copy(current, address_text, sizeof(current));
    page_reset();
    http_reset();
    scroll = 0; selected = 0;
    tls_ready = 0; received_total = 0;
    if (!parse_url(current, &url)) { finish("Неверный адрес"); return; }
    use_tls = url.https;
    for (const char *h = url.host; *h && title_len+1 < sizeof(page_title)/2; h++) page_title[title_len++] = (u16)(u8)*h;
    page_title[title_len] = 0;
    set_status("Подключение к сети…");
    net_step = N_PDP;
    net_since = api->ms();
    api->net_start();
    api->timer(50, 1);
}

/* ---------- drawing ---------- */

static const u32 palette[4] = {C_TEXT, C_LINK, C_HEAD, C_GRAY};

static void draw_label(int y, const char *utf8, int align_right) {
    u16 label[32];
    u32 n = widen_utf8(label, utf8, 32);
    int x = align_right ? W-MARGIN-(int)text_width(F_SMALL, label, n) : MARGIN;
    draw_chars(F_SMALL, x, y, label, n, C_BAR_TEXT, 0, H);
}

static void draw_bars(const char *left, const char *right, const u16 *top) {
    fill(0, 0, W, BAR, C_BAR);
    if (top) {
        u32 n = wlen(top), k = 0, width = 0;
        while (k < n && width+char_width(F_SMALL, top[k]) < W-2*MARGIN) width += char_width(F_SMALL, top[k++]);
        draw_chars(F_SMALL, MARGIN, 2, top, k, C_BAR_TEXT, 0, H);
    }
    fill(0, BOTTOM, W, BAR, C_BAR);
    draw_label(BOTTOM+2, left, 0);
    draw_label(BOTTOM+2, right, 1);
}

/* A form field over its placeholder run: a box with the value, a check box,
   a radio button, the chosen option. */
static void draw_field(const Field *f, const Run *run, int x, int y, u32 fh, int chosen) {
    u32 skip = 0;
    while (skip < run->length && text[run->start+skip] == ' ') { x += (int)char_width(run->font, ' '); skip++; }
    int w = (int)text_width(run->font, text+run->start+skip, run->length-skip);
    int top = y < TOP ? TOP : y, bottom = y+(int)fh > BOTTOM ? BOTTOM : y+(int)fh;
    if (bottom <= top) return;
    fill(x, top, w, bottom-top, chosen ? C_SELECT : 0x2945);
    u16 shown[64];
    u32 n = 0;
    if (f->type == T_CHECK || f->type == T_RADIO) {
        shown[0] = f->checked ? (f->type == T_CHECK ? 'x' : 0x2022) : ' ';
        n = 1;
        x += (w-(int)char_width(run->font, shown[0]))/2;
    } else {
        const char *value = f->type == T_SELECT ? option_at(f, f->option, 1) : pooled(f->value);
        n = widen_utf8(shown, value, 60);
        if (f->type == T_PASSWORD) for (u32 i = 0; i < n; i++) shown[i] = '*';
        for (u32 i = 0; i < n; i++) if (shown[i] == '\n' || shown[i] == '\r' || shown[i] == '\t') shown[i] = ' ';
        x += 2;
        u32 room = (u32)(w-4-(f->type == T_SELECT ? 8 : 0)), width = 0, k = 0;
        while (k < n && width+char_width(run->font, shown[k]) <= room) width += char_width(run->font, shown[k++]);
        n = k;
        if (f->type == T_SELECT) { u16 mark = 'v'; draw_chars(run->font, x+w-10, y, &mark, 1, C_GRAY, top, bottom); }
    }
    draw_chars(run->font, x, y, shown, n, C_TEXT, top, bottom);
}

static void draw_page(void) {
    fill(0, TOP, W, VIEW, C_BACK);
    for (u32 i = 0; i < line_count; i++) {
        const Line *l = &lines[i];
        if (l->y+l->height <= scroll) continue;
        if (l->y >= scroll+VIEW) break;
        for (u32 r = 0; r < l->count; r++) {
            const Run *run = &runs[l->run+r];
            u32 fh = font_height(run->font);
            int x = MARGIN+run->x, y = TOP+(int)l->y-(int)scroll+(int)(l->height-fh);
            u32 color = palette[run->color & 3];
            if (run->link && links[run->link-1].field && fields[links[run->link-1].field-1].type != T_SUBMIT) {
                draw_field(&fields[links[run->link-1].field-1], run, x, y, fh, run->link == selected);
                continue;
            }
            if (run->link && run->link == selected) {
                u32 w = text_width(run->font, text+run->start, run->length);
                int top = y < TOP ? TOP : y, bottom = y+(int)fh > BOTTOM ? BOTTOM : y+(int)fh;
                if (bottom > top) fill(x-1, top, (int)w+2, bottom-top, C_SELECT);
                color = C_TEXT;
            }
            draw_chars(run->font, x, y, text+run->start, run->length, color, TOP, BOTTOM);
        }
    }
    if (page_height > VIEW) {
        u32 bar = VIEW*VIEW/page_height, at = scroll*VIEW/page_height;
        if (bar < 8) bar = 8;
        if (at+bar > VIEW) at = VIEW-bar;
        fill(W-2, TOP, 2, VIEW, 0x2104);
        fill(W-2, TOP+(int)at, 2, (int)bar, C_SELECT);
    }
    const u16 *top = status[0] ? status : page_title[0] ? page_title : 0;
    u16 address_wide[64];
    if (!top) { ascii(address_wide, current, 64); top = address_wide; }
    draw_bars("Адрес", history_count ? "Назад" : "Выход", top);
}

/* ---------- address editor ---------- */


static int loading(void);
static char edit[400];
static u32 edit_len, cursor, tap_key, tap_index, tap_time, digits_mode, edit_field;  /* edit_field: index+1 when editing a field */
static const char *const keys[11] = {" 0", "./:-_1", "abc2", "def3", "ghi4", "jkl5", "mno6", "pqrs7", "tuv8", "wxyz9", "?=&%+@!~*#"};

static void edit_insert(char c) {
    if (edit_len+1 >= sizeof(edit)) return;
    memmove(edit+cursor+1, edit+cursor, edit_len-cursor+1);
    edit[cursor++] = c;
    edit_len++;
}

static void draw_edit(void) {
    fill(0, TOP, W, VIEW, C_BACK);
    static const char *const help[] = {"1: . / : - _    0: пробел", "*: ? = & % +   #: abc/123", "Вверх — стереть всё"};
    u16 line[48];
    u32 n = widen_utf8(line, edit_field ? "Поле формы" : "Адрес или поиск", 48);
    int y = TOP+4, x = MARGIN;
    draw_chars(F_SMALL, MARGIN, y, line, n, C_GRAY, TOP, BOTTOM);
    y += 16;
    u32 lh = font_height(F_TEXT);
    fill(0, y-2, W, 4*(int)lh+4, 0x2104);
    for (u32 i = 0; i <= edit_len; i++) {
        u16 c = i < edit_len ? (u16)(u8)edit[i] : ' ';
        u32 w = char_width(F_TEXT, c);
        if (x+(int)w > W-MARGIN) { x = MARGIN; y += (int)lh; }
        if (i == cursor) fill(x, y, 1, (int)lh, tap_key ? C_HEAD : C_TEXT);
        if (i < edit_len) draw_chars(F_TEXT, x, y, &c, 1, C_TEXT, TOP, BOTTOM);
        x += (int)w;
    }
    y = BOTTOM-3*15-4;
    for (u32 i = 0; i < 3; i++) {
        n = widen_utf8(line, help[i], 48);
        draw_chars(F_SMALL, MARGIN, y+(int)i*15, line, n, C_GRAY, TOP, BOTTOM);
    }
    draw_bars(edit_field ? "Готово" : "Перейти", edit_len ? "Стереть" : "Отмена", 0);

    u16 mode[4] = {digits_mode ? '1' : 'a', digits_mode ? '2' : 'b', digits_mode ? '3' : 'c', 0};
    draw_chars(F_SMALL, W/2-10, BOTTOM+2, mode, 3, C_BAR_TEXT, 0, H);
}

static void field_done(void) {
    edit[edit_len] = 0;
    Field *f = &fields[edit_field-1];
    char *value = fpool+f->value;
    if (f->value != ~0u) copy(value, edit, f->cap+1);
    edit_field = 0;
    editing = 0;
    if (!loading()) api->timer(0, 0);
}

static void go(void) {
    edit[edit_len] = 0;
    if (edit_field) { field_done(); return; }
    const char *s = edit;
    while (*s == ' ') s++;
    if (!*s) return;
    int has_dot = 0, has_space = 0, has_scheme = 0;
    for (const char *c = s; *c; c++) {
        if (*c == '.') has_dot = 1;
        if (*c == ' ') has_space = 1;
        if (*c == ':' && c[1] == '/') has_scheme = 1;
    }
    char target[600];
    u32 n = 0;
    target[0] = 0;
    if (has_scheme) copy(target, s, sizeof(target));
    else if (has_dot && !has_space) { add(target, &n, sizeof(target), "https://"); add(target, &n, sizeof(target), s); }
    else {
        static const char hex[] = "0123456789ABCDEF";
        add(target, &n, sizeof(target), "https://lite.duckduckgo.com/lite/?q=");
        for (const u8 *c = (const u8 *)s; *c && n+4 < sizeof(target); c++) {
            if ((*c >= 'a' && *c <= 'z') || (*c >= 'A' && *c <= 'Z') || (*c >= '0' && *c <= '9') || *c == '-' || *c == '.') target[n++] = (char)*c;
            else if (*c == ' ') target[n++] = '+';
            else { target[n++] = '%'; target[n++] = hex[*c >> 4]; target[n++] = hex[*c & 15]; }
        }
        target[n] = 0;
    }
    editing = 0;
    load(target, 1);
}

static int loading(void) { return net_step >= N_PDP && net_step <= N_IO; }

static int edit_key(u32 kind, u32 code) {
    if (kind != GO1_PRESS && kind != GO1_REPEAT) return 0;
    int digit = code >= GO1_1 && code <= GO1_9 ? (int)(code-GO1_1+1) : code == GO1_0 ? 0 : -1;
    u32 slot = digit >= 0 ? (u32)digit : code == GO1_STAR ? 10 : 99;
    if (code == GO1_HASH && kind == GO1_PRESS) { digits_mode = !digits_mode; tap_key = 0; return 1; }
    if (slot < 11 && kind == GO1_PRESS) {
        u32 now = api->ms();
        if (digits_mode && slot < 10) { edit_insert((char)('0'+slot)); tap_key = 0; return 1; }
        const char *set = keys[slot];
        u32 count = (u32)strlen(set);
        if (tap_key == slot+1 && now-tap_time < 1000 && cursor) {
            tap_index = (tap_index+1)%count;
            edit[cursor-1] = set[tap_index];
        } else {
            tap_index = 0;
            edit_insert(set[0]);
        }
        tap_key = slot+1;
        tap_time = now;
        return 1;
    }
    tap_key = 0;
    if (code == GO1_LEFT && cursor) cursor--;
    else if (code == GO1_RIGHT && cursor < edit_len) cursor++;
    else if (code == GO1_UP) { edit_len = cursor = 0; edit[0] = 0; }  /* clear */
    else if (code == GO1_DOWN) cursor = edit_len;
    else if (code == GO1_SOFT_RIGHT) {
        if (!edit_len) { editing = 0; edit_field = 0; if (!loading()) api->timer(0, 0); return 1; }
        if (cursor) { memmove(edit+cursor-1, edit+cursor, edit_len-cursor+1); cursor--; edit_len--; }
    } else if ((code == GO1_OK || code == GO1_SOFT_LEFT) && kind == GO1_PRESS) go();
    return 1;
}

static void open_editor(void) {
    editing = 1;
    edit_field = 0;
    if (starts(current, "about:")) copy(edit, "https://", sizeof(edit));
    else copy(edit, current, sizeof(edit));
    edit_len = cursor = (u32)strlen(edit);
    tap_key = 0;
    api->timer(loading() ? 50 : 100, 1);
}

/* ---------- sending a form ---------- */

static void add_encoded(char *out, u32 *n, u32 capacity, const char *s) {
    static const char hex[] = "0123456789ABCDEF";
    for (const u8 *c = (const u8 *)s; *c && *n+4 < capacity; c++) {
        if ((*c >= 'a' && *c <= 'z') || (*c >= 'A' && *c <= 'Z') || (*c >= '0' && *c <= '9') ||
            *c == '-' || *c == '_' || *c == '.' || *c == '*') out[(*n)++] = (char)*c;
        else if (*c == ' ') out[(*n)++] = '+';
        else { out[(*n)++] = '%'; out[(*n)++] = hex[*c >> 4]; out[(*n)++] = hex[*c & 15]; }
    }
    out[*n] = 0;
}

static void add_pair(char *out, u32 *n, u32 capacity, const char *name, const char *value) {
    if (!name[0]) return;
    if (*n) add(out, n, capacity, "&");
    add_encoded(out, n, capacity, name);
    add(out, n, capacity, "=");
    add_encoded(out, n, capacity, value);
}

/* The form of `button` sent as application/x-www-form-urlencoded (UTF-8). */
static void submit(u32 button) {
    const Field *b = &fields[button-1];
    if (!b->form) return;
    static char query[2048];
    u32 n = 0;
    query[0] = 0;
    for (u32 i = 0; i < field_count; i++) {
        const Field *f = &fields[i];
        if (f->form != b->form) continue;
        const char *name = pooled(f->name);
        switch (f->type) {
        case T_TEXT: case T_PASSWORD: case T_HIDDEN: case T_TEXTAREA: add_pair(query, &n, sizeof(query), name, pooled(f->value)); break;
        case T_CHECK: case T_RADIO: if (f->checked) add_pair(query, &n, sizeof(query), name, pooled(f->value)); break;
        case T_SELECT: if (f->option_count) add_pair(query, &n, sizeof(query), name, option_at(f, f->option, 0)); break;
        case T_SUBMIT: if (i == button-1) add_pair(query, &n, sizeof(query), name, pooled(f->value)); break;
        }
    }
    const Form *form = &forms[b->form-1];
    static char target[URL_MAX];
    resolve(current, pooled(form->action), target, sizeof(target));
    if (form->post) {
        copy(post_body, query, sizeof(post_body));
        post_len = (u32)strlen(post_body);
        post_pending = 1;
    } else {
        u32 end = 0;
        while (target[end] && target[end] != '?' && target[end] != '#') end++;
        target[end] = 0;
        add(target, &end, sizeof(target), "?");
        add(target, &end, sizeof(target), query);
    }
    load(target, 1);
}

/* OK on a field: edit it, toggle it, take the next option, or send the form. */
static void field_key(u32 field) {
    Field *f = &fields[field-1];
    switch (f->type) {
    case T_TEXT: case T_PASSWORD: case T_TEXTAREA:
        editing = 1;
        edit_field = field;
        copy(edit, pooled(f->value), sizeof(edit));
        edit_len = cursor = (u32)strlen(edit);
        tap_key = 0;
        api->timer(loading() ? 50 : 100, 1);
        break;
    case T_CHECK: f->checked = !f->checked; break;
    case T_RADIO:
        for (u32 i = 0; i < field_count; i++)
            if (fields[i].type == T_RADIO && fields[i].form == f->form && same(pooled(fields[i].name), pooled(f->name))) fields[i].checked = 0;
        f->checked = 1;
        break;
    case T_SELECT: if (f->option_count) f->option = (u16)((f->option+1) % f->option_count); break;
    case T_SUBMIT: submit(field); break;
    }
}

/* ---------- page keys ---------- */

static u32 link_y(u32 link) { return lines[links[link-1].line].y; }

static void ensure_selected_visible(void) {
    if (!selected) return;
    u32 y = link_y(selected), h = lines[links[selected-1].line].height;
    if (y < scroll) scroll = y;
    else if (y+h > scroll+VIEW) scroll = y+h-VIEW+4;
}

static u32 max_scroll(void) { return page_height > VIEW ? page_height-VIEW+8 : 0; }
static int on_screen(u32 link) { return link >= 1 && link <= link_count && link_y(link) >= scroll && link_y(link) < scroll+VIEW-8; }

static int page_key(u32 kind, u32 code) {
    if (kind != GO1_PRESS && kind != GO1_REPEAT) return 0;
    if (code == GO1_DOWN || code == GO1_8) {
        /* The next link on screen, else scroll down. */
        if (on_screen(selected+1)) { selected++; ensure_selected_visible(); }
        else {
            u32 target = scroll+VIEW/3;
            scroll = target > max_scroll() ? max_scroll() : target;
            if (on_screen(selected+1)) selected++;
            else if (!on_screen(selected)) selected = 0;
        }
    } else if (code == GO1_UP || code == GO1_2) {
        if (selected > 1 && on_screen(selected-1)) { selected--; ensure_selected_visible(); }
        else if (scroll) {
            scroll = scroll > VIEW/3 ? scroll-VIEW/3 : 0;
            if (selected > 1 && on_screen(selected-1)) selected--;
            else if (!on_screen(selected)) selected = 0;
        } else selected = 0;
    } else if (code == GO1_RIGHT || code == GO1_6) {
        u32 target = scroll+VIEW-20;
        scroll = target > max_scroll() ? max_scroll() : target;
        selected = 0;
    } else if (code == GO1_LEFT || code == GO1_4) {
        scroll = scroll > VIEW-20 ? scroll-(VIEW-20) : 0;
        selected = 0;
    } else if (kind != GO1_PRESS) return 0;
    else if ((code == GO1_OK || code == GO1_5) && selected) {
        if (links[selected-1].field) field_key(links[selected-1].field);
        else {
            static char next[URL_MAX];
            resolve(current, hrefs+links[selected-1].href, next, sizeof(next));
            load(next, 1);
        }
    } else if (code == GO1_SOFT_LEFT) open_editor();
    else if (code == GO1_SOFT_RIGHT) {
        if (history_count) {
            static char back[URL_MAX];
            copy(back, history[--history_count], sizeof(back));
            redirects = 0;
            load(back, 0);
        } else api->exit();
    } else if (code == GO1_STAR && !starts(current, "about:")) {
        static char again[URL_MAX];
        copy(again, current, sizeof(again));
        redirects = 0;
        load(again, 0);
    } else if (code == GO1_HASH) load("about:start", 1);
    else if (code == GO1_0) marks_add();
    return 1;
}

static u32 last_draw;
static void draw(void) {
    last_draw = api->ms();
    if (editing) draw_edit(); else draw_page();

    api->flush(0, 0, W, H);
}

__attribute__((used, section(".entry")))
u32 go1_main(const Go1Api *program_api, u32 message, u32 parameter) {
    (void)parameter;
    api = program_api;
    if (api->size < __builtin_offsetof(Go1Api, alloc_static)) {
        if (message == GO1_OPEN || message == GO1_PAINT) {
            api->fill(0, 0, 176, 176, 0);
            api->text(60, "Needs a newer firmware", 0xffff);
        }
        return 1;
    }
    if (message == GO1_OPEN) {
        fonts[F_SMALL] = font_s_data; fonts[F_TEXT] = font_m_data; fonts[F_HEAD] = font_l_data;
        screen = api->screen();
        br_sha256_init(&pool);
        stir(ENTROPY_RAM, 0x10000);  /* MMI task stacks and queues */
        stir_time();
        br_ssl_client_init_full(&sc, &xc, browser_anchors, browser_anchor_count);
        seed_load();
        seed_store();
        marks_load();
        /* Buffers for compressed pages; without them the server is asked for plain ones. */
        gz_in = api->alloc(GZ_IN);
        gz_window = gz_in ? api->alloc(32768) : 0;
        if (!gz_window && gz_in) { api->free(gz_in); gz_in = 0; }
        history_count = 0; editing = 0; net_step = N_IDLE; sock = 0;
        show_internal(start_page_html(), "about:start");
        draw();
        return 1;
    }
    if (message == GO1_CLOSE) {
        api->timer(0, 0);
        if (sock) { api->tcp_close(sock); sock = 0; }
        if (gz_in) { api->free(gz_in); api->free(gz_window); gz_in = gz_window = 0; }
        return 1;
    }
    if (message == GO1_TIMER) {
        /* Redraw only on change: a full frame is costly, and the timer runs every 50-100 ms. */
        u32 before = wlen(status)+status[0]+net_step*7+line_count;
        int was_loading = loading();
        if (was_loading) net_tick();
        int changed = before != wlen(status)+status[0]+net_step*7+line_count || was_loading != loading();
        if (editing && tap_key && api->ms()-tap_time >= 1000) { tap_key = 0; changed = 1; }
        if (changed && (!loading() || api->ms()-last_draw >= 300)) draw();
        return 1;
    }
    if (GO1_IS_KEY(message)) {
        u32 t[2] = {api->ms(), message};
        stir(t, sizeof(t));
        int changed = editing ? edit_key(GO1_KIND(message), GO1_CODE(message)) : page_key(GO1_KIND(message), GO1_CODE(message));
        if (changed) draw();
        return 1;
    }
    if (message == GO1_PAINT) draw();
    return 1;
}
