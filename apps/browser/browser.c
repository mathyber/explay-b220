/* BROWSER.GO1: a simple text browser with HTTPS for the GO1 loader.
   go1-title: Браузер

Packet data and TCP come from the firmware (GO1 API), TLS 1.0-1.2 from
BearSSL with the Mozilla root certificates (tools/build_browser.py), text in
the GO1 JVM bitmap fonts (Latin, Cyrillic). HTML is read as a stream: text,
headings, paragraphs, lists, links and image alt text; no scripts, styles,
forms or pictures. A query without a dot goes to DuckDuckGo Lite.

Keys: up/down - previous/next link or scroll, left/right - page up/down,
OK - open the link, left soft key - address, right soft key - back,
* - reload, # - start page. The address editor uses multi-tap. */
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
typedef struct { u32 href, line; } Link;

static u16 text[TEXT_MAX];
static Run runs[RUN_MAX];
static Line lines[LINE_MAX];
static Link links[LINK_MAX];
static char hrefs[HREF_MAX];
static u32 text_len, run_count, line_count, link_count, href_len, page_height, truncated;
static u16 page_title[48];
static u32 title_len;

/* layout state */
static u32 line_x, line_height, gap_pending, space_pending, at_line_start;
static u32 style_font, style_color, style_link, pre;
static u16 word[48];
static u32 word_len, word_width;

static void page_reset(void) {
    text_len = run_count = line_count = link_count = href_len = truncated = title_len = 0;
    page_height = 0;
    line_x = 0; line_height = 0; gap_pending = 0; space_pending = 0; at_line_start = 1;
    style_font = F_TEXT; style_color = 0; style_link = 0; pre = 0;
    word_len = word_width = 0;
    page_title[0] = 0;
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

typedef struct { u32 https, port; char host[128]; char path[600]; } Url;
static char current[800];

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

static void text_char(u32 c) {
    if (in_title) { title_char(c); return; }
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
                     tag_is(name, "template") || tag_is(name, "select") || tag_is(name, "textarea"))) {
        html_state = H_RAW;
        tag_len = 0;
        /* Index into raw_end: script, style, svg, template, select, textarea. */
        raw_kind = tag_is(name, "script") ? 1 : tag_is(name, "style") ? 2 : tag_is(name, "svg") ? 3 :
                   tag_is(name, "template") ? 4 : tag_is(name, "select") ? 5 : 6;
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
    if (tag_is(name, "p") || tag_is(name, "blockquote") || tag_is(name, "table") || tag_is(name, "form") ||
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
    if (tag_is(name, "input") && !closing) {
        char value[48], type[16];
        if (attribute(tag, "type", type, sizeof(type)) && starts(type, "hidden")) return;
        if (attribute(tag, "value", value, sizeof(value)) && value[0]) {
            u32 saved = style_color;
            style_color = 3;
            emit_char('[');
            for (const char *a = value; *a; a++) emit_char((u8)*a);
            emit_char(']');
            flush_word();
            style_color = saved;
        }
    }
}

static int after_equals(void) {
    u32 i = tag_len;
    while (i && (tag[i-1] == ' ' || tag[i-1] == '\n' || tag[i-1] == '\t')) i--;
    return i && tag[i-1] == '=';
}

static const char *const raw_end[] = {"", "</script", "</style", "</svg", "</template", "</select", "</textarea"};

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
        u32 len = (u32)strlen(end);
        char lower = (char)(c >= 'A' && c <= 'Z' ? c+32 : c);
        if (lower == end[tag_len]) { tag_len++; if (tag_len == len) { html_state = H_TAG; tag_len = 0; quote = 0; tag[0] = 0; } }
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

/* ---------- HTTP ---------- */

enum { R_HEADERS, R_BODY, R_CHUNK_SIZE, R_CHUNK_DATA, R_CHUNK_END, R_DONE };
static u32 http_state, status_code, chunked, content_length, has_length, body_received, header_len, chunk_left;
static u32 chunk_digits, chunk_extension, unsupported;
static char header[4096];
static char location[600];
static char content_type[64];

static void http_reset(void) {
    http_state = R_HEADERS; status_code = 0; chunked = 0; content_length = 0; has_length = 0;
    body_received = 0; header_len = 0; chunk_left = 0; chunk_digits = 0; chunk_extension = 0;
    location[0] = 0; content_type[0] = 0; unsupported = 0; refresh[0] = 0;
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
static Url url;
static char request[1200];
static br_ssl_client_context sc;
static br_x509_minimal_context xc;
static u8 tls_in[BR_SSL_BUFSIZE_INPUT];
static u8 tls_out[4096+85];
static u32 tls_ready;

static void add(char *out, u32 *n, u32 capacity, const char *s) { while (*s && *n+1 < capacity) out[(*n)++] = *s++; out[*n] = 0; }

static void build_request(void) {
    u32 n = 0;
    request[0] = 0;
    add(request, &n, sizeof(request), "GET ");
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
    add(request, &n, sizeof(request), "\r\nUser-Agent: Mozilla/5.0 (Linux; Mobile) GO1Browser/0.1 (Explay B220)\r\n"
        "Accept: text/html,text/plain;q=0.9,*/*;q=0.5\r\nAccept-Language: ru,en;q=0.7\r\n"
        "Accept-Encoding: identity\r\nConnection: close\r\n\r\n");
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
static char history[HISTORY][600];
static u32 history_count;
static u32 scroll, selected, editing;

static const char start_page[] =
    "<title>Браузер GO1</title><h1>Браузер GO1</h1>"
    "<p>Левая экранная — адрес или поиск, правая — назад. Вверх/вниз — ссылки, влево/вправо — страницы. * — обновить, # — сюда.</p>"
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

static void finish(const char *error) {
    api->timer(editing ? 100 : 0, editing ? 1 : 0);
    if (sock) { api->tcp_close(sock); sock = 0; }
    net_step = N_DONE;
    if (!error && refresh[0] && redirects < 6 && status_code < 300) copy(location, refresh, sizeof(location));
    if (!error && ((status_code >= 300 && status_code < 400) || refresh[0]) && location[0] && redirects < 6) {
        char next[600];
        resolve(current, location, next, sizeof(next));
        redirects++;
        load(next, 0);
        return;
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
    if (starts(address_text, "about:start")) { show_internal(start_page, "about:start"); net_step = N_IDLE; return; }
    copy(current, address_text, sizeof(current));
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


static char edit[400];
static u32 edit_len, cursor, tap_key, tap_index, tap_time, digits_mode;
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
    u32 n = widen_utf8(line, "Адрес или поиск", 48);
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
    draw_bars("Перейти", edit_len ? "Стереть" : "Отмена", 0);

    u16 mode[4] = {digits_mode ? '1' : 'a', digits_mode ? '2' : 'b', digits_mode ? '3' : 'c', 0};
    draw_chars(F_SMALL, W/2-10, BOTTOM+2, mode, 3, C_BAR_TEXT, 0, H);
}

static void go(void) {
    edit[edit_len] = 0;
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
        if (!edit_len) { editing = 0; if (!loading()) api->timer(0, 0); return 1; }
        if (cursor) { memmove(edit+cursor-1, edit+cursor, edit_len-cursor+1); cursor--; edit_len--; }
    } else if ((code == GO1_OK || code == GO1_SOFT_LEFT) && kind == GO1_PRESS) go();
    return 1;
}

static void open_editor(void) {
    editing = 1;
    if (starts(current, "about:")) copy(edit, "https://", sizeof(edit));
    else copy(edit, current, sizeof(edit));
    edit_len = cursor = (u32)strlen(edit);
    tap_key = 0;
    api->timer(loading() ? 50 : 100, 1);
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
        char next[600];
        resolve(current, hrefs+links[selected-1].href, next, sizeof(next));
        load(next, 1);
    } else if (code == GO1_SOFT_LEFT) open_editor();
    else if (code == GO1_SOFT_RIGHT) {
        if (history_count) {
            char back[600];
            copy(back, history[--history_count], sizeof(back));
            redirects = 0;
            load(back, 0);
        } else api->exit();
    } else if (code == GO1_STAR && !starts(current, "about:")) {
        char again[800];
        copy(again, current, sizeof(again));
        redirects = 0;
        load(again, 0);
    } else if (code == GO1_HASH) load("about:start", 1);
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
    if (api->size < sizeof(Go1Api)) {
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
        stir((const void *)0x04080000u, 0x10000);  /* MMI task stacks and queues */
        stir_time();
        br_ssl_client_init_full(&sc, &xc, browser_anchors, browser_anchor_count);
        history_count = 0; editing = 0; net_step = N_IDLE; sock = 0;
        show_internal(start_page, "about:start");
        draw();
        return 1;
    }
    if (message == GO1_CLOSE) {
        api->timer(0, 0);
        if (sock) { api->tcp_close(sock); sock = 0; }
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
