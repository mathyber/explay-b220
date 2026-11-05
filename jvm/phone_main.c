/* JAVA.GO1: the GO1 JVM as a native SD application.
   go1-title: Java

Opening it lists the .jar files in E:\JAVA\ and runs the chosen one; with no
files there it runs E:\APP.JAR directly. Classes come from E:\JAVALIB.JAR (the
class library) and the chosen JAR. A manifest with MIDlet-1 starts
go1.Launcher, which runs the MIDlet and owns the screen; otherwise Main-Class
runs with System.out shown as a console. The VM runs a bounded slice on every
window timer tick; phone keys are posted to the MIDP event queue. When the
program ends, the list comes back. */
#include "go1.h"
#include "jvm.h"
#include "jar.h"

typedef uint32_t u32;
typedef uint16_t u16;

#define SLICE 20000          /* bytecodes per timer tick */
#define TICK_MS 20
#define META_BASE (176*1024) /* MIDP library classes, threads, collector tables */
#define HEAP_MIN (128*1024)
#define RESERVE (64*1024)    /* App Heap left to the firmware */
#define LINES 9
#define COLUMNS 28
#define GAMES 48
#define NAME_CHARS 80        /* longer names are skipped */
#define ROWS 6
#define FOLDER "E:\\JAVA\\"
#define TITLE_CHARS 40       /* MIDlet-Name shown in the list */
#define ICON 16
#define ICON_SOURCE_MAX 256  /* larger icons are not decoded */

static const Go1Api *api;
static Jvm *vm;
static void *block;
static Jar library, application;
static u32 library_file, application_file;
static int finished, midp, choosing, from_list;
static char console[LINES][COLUMNS+1];
static u32 row, column;
static int dirty;
static u16 games[GAMES][NAME_CHARS+1];
static u32 game_count, selected;
static Go1FindEntry entry;
/* List details from each JAR's manifest: name and icon (16x16, centred,
   scaled down by area averaging). Allocated while the list is shown and
   freed before a VM starts, so the Java heap does not lose this memory. */
typedef struct GameInfo { u16 title[TITLE_CHARS+1]; u16 pixels[ICON*ICON]; uint8_t alpha[ICON*ICON]; uint8_t has_icon; } GameInfo;
static GameInfo *infos;

static int is_launcher(const char *name) {
    const char *launcher = "go1/Launcher";
    while (*name && *name == *launcher) { name++; launcher++; }
    return *name == *launcher;
}

static u32 widen(u16 *out, const char *ascii) {
    u32 n = 0;
    while (ascii[n]) { out[n] = (unsigned char)ascii[n]; n++; }
    out[n] = 0;
    return n;
}

/* ---------- console ---------- */

static void new_line(void) {
    column = 0;
    if (++row < LINES) return;
    for (u32 i = 1; i < LINES; i++)
        for (u32 j = 0; j <= COLUMNS; j++) console[i-1][j] = console[i][j];
    row = LINES-1;
    for (u32 j = 0; j <= COLUMNS; j++) console[row][j] = 0;
}

static void put(char c) {
    dirty = 1;
    if (c == '\n') { new_line(); return; }
    if (c == '\r') return;
    if (column >= COLUMNS) new_line();
    console[row][column++] = c;
    console[row][column] = 0;
}

static void say(const char *text) { while (*text) put(*text++); }

static void draw(void) {
    api->fill(0, 0, 176, 176, 0);
    for (u32 i = 0; i < LINES; i++) if (console[i][0]) api->text(i*19, console[i], 0xffff);
    dirty = 0;
}

/* ---------- game list ---------- */

static u16 lower(u16 c) { return (c >= 'A' && c <= 'Z') || (c >= 0x410 && c <= 0x42f) ? (u16)(c+32) : c; }

static int before(const u16 *a, const u16 *b) {
    while (*a && lower(*a) == lower(*b)) { a++; b++; }
    return lower(*a) < lower(*b);
}

static void scan(void) {
    u16 pattern[24];
    widen(pattern, FOLDER "*.jar");
    game_count = selected = 0;
    u32 search = api->find_first(pattern, &entry);
    if (!search) return;
    do {
        u32 length = 0;
        while (entry.name[length]) length++;
        if ((entry.attributes & GO1_ATTR_DIRECTORY) || length > NAME_CHARS) continue;
        /* Insertion keeps the list sorted by name. */
        u32 at = game_count;
        while (at > 0 && before(entry.name, games[at-1])) {
            for (u32 i = 0; i <= NAME_CHARS; i++) games[at][i] = games[at-1][i];
            at--;
        }
        for (u32 i = 0; i <= length; i++) games[at][i] = entry.name[i];
        game_count++;
    } while (game_count < GAMES && api->find_next(search, &entry) == 0);
    api->find_close(search);
}

/* After a MIDlet the whole frame buffer is its picture: clear it. */
static void clear_screen(void) {
    uint16_t *screen = api->screen();
    for (u32 i = 0; i < GO1_SCREEN_WIDTH*GO1_SCREEN_HEIGHT; i++) screen[i] = 0;
    api->flush(0, 0, GO1_SCREEN_WIDTH, GO1_SCREEN_HEIGHT);
}

/* ---------- JVM host ---------- */

static u32 archive_read(void *context, u32 offset, void *buffer, u32 length) {
    u32 handle = (u32)(uintptr_t)context;
    if (api->seek(handle, offset)) return 0;
    u32 n = api->read(handle, buffer, length);
    return n == 0xffffffffu ? 0 : n;
}
/* Once the VM runs, entries (class files, resources) are extracted into its
   heap: outside it only RESERVE is left, less than a large class file. */
static void *archive_alloc(void *context, u32 size) {
    (void)context;
    void *data = vm ? jvm_scratch_alloc(vm, size) : 0;
    return data ? data : api->alloc(size);
}
static void archive_release(void *context, void *data) {
    (void)context;
    if (!vm || !jvm_scratch_release(vm, data)) api->free(data);
}

static int open_archive(Jar *jar, u32 *handle, const u16 *path) {
    u32 h = api->open_wide(path);
    if (!h) return 0;
    u32 size = api->file_size(h);
    JarIo io = {archive_read, archive_alloc, archive_release, (void *)(uintptr_t)h, size};
    if (size == 0xffffffffu || !jar_open(jar, &io)) { api->close(h); return 0; }
    *handle = h;
    return 1;
}

/* ---------- names and icons from the manifests ---------- */

static u32 utf8_to_ucs2(const char *in, u16 *out, u32 capacity) {
    const unsigned char *p = (const unsigned char *)in;
    u32 n = 0;
    while (*p && n < capacity) {
        u32 c = *p++;
        if (c >= 0xe0 && p[0] && p[1]) { c = (c & 15) << 12 | (p[0] & 63) << 6 | (p[1] & 63); p += 2; }
        else if (c >= 0xc0 && p[0]) { c = (c & 31) << 6 | (p[0] & 63); p++; }
        else if (c >= 0x80) c = '?';
        out[n++] = c < 32 ? ' ' : (u16)c;
    }
    out[n] = 0;
    return n;
}

static void fit_icon(const u16 *src, const uint8_t *src_alpha, u32 w, u32 h, GameInfo *info) {
    for (u32 i = 0; i < ICON*ICON; i++) { info->alpha[i] = 0; info->pixels[i] = 0; }
    u32 big = w > h ? w : h, tw = w, th = h;
    if (big > ICON) { tw = (w*ICON+big/2)/big; th = (h*ICON+big/2)/big; }
    if (!tw) tw = 1;
    if (!th) th = 1;
    u32 ox = (ICON-tw)/2, oy = (ICON-th)/2;
    for (u32 ty = 0; ty < th; ty++)
        for (u32 tx = 0; tx < tw; tx++) {
            u32 x0 = tx*w/tw, x1 = (tx+1)*w/tw, y0 = ty*h/th, y1 = (ty+1)*h/th;
            if (x1 <= x0) x1 = x0+1;
            if (y1 <= y0) y1 = y0+1;
            u32 r = 0, g = 0, b = 0, a = 0, count = 0;
            for (u32 y = y0; y < y1; y++)
                for (u32 x = x0; x < x1; x++) {
                    u32 c = src[y*w+x], k = src_alpha ? src_alpha[y*w+x] : 255;
                    u32 cr = c >> 11, cg = (c >> 5) & 63, cb = c & 31;
                    r += ((cr << 3) | (cr >> 2))*k; g += ((cg << 2) | (cg >> 4))*k; b += ((cb << 3) | (cb >> 2))*k;
                    a += k; count++;
                }
            u32 at = (oy+ty)*ICON+ox+tx;
            info->alpha[at] = (uint8_t)(a/count);
            if (a) info->pixels[at] = (u16)((r/a >> 3) << 11 | (g/a >> 2) << 5 | (b/a >> 3));
        }
    info->has_icon = 1;
}

static void decode_icon(Jar *jar, const char *path, GameInfo *info) {
    while (*path == '/') path++;
    u32 size = 0;
    uint8_t *png = *path ? jar_read(jar, path, &size) : 0;
    if (!png) return;
    JvmPngInfo image;
    if (jvm_png_info(png, size, &image) && image.width <= ICON_SOURCE_MAX && image.height <= ICON_SOURCE_MAX) {
        u32 count = image.width*image.height;
        uint8_t *packed = api->alloc(image.packed_size), *raw = api->alloc(image.raw_size);
        u16 *pixels = api->alloc(count*2);
        uint8_t *alpha = image.has_alpha ? api->alloc(count) : 0;
        if (packed && raw && pixels && (alpha || !image.has_alpha) &&
            jvm_png_decode(png, size, packed, raw, pixels, alpha))
            fit_icon(pixels, alpha, image.width, image.height, info);
        if (alpha) api->free(alpha);
        if (pixels) api->free(pixels);
        if (raw) api->free(raw);
        if (packed) api->free(packed);
    }
    archive_release(0, png);
}

static void read_info(u32 index, GameInfo *info) {
    info->title[0] = 0;
    info->has_icon = 0;
    u16 path[NAME_CHARS+12];
    u32 n = widen(path, FOLDER);
    for (u32 i = 0; (path[n+i] = games[index][i]) != 0; i++) {}
    Jar jar;
    u32 handle;
    if (!open_archive(&jar, &handle, path)) return;
    u32 size = 0;
    uint8_t *manifest = jar_read(&jar, "META-INF/MANIFEST.MF", &size);
    if (manifest) {
        char value[128];
        if (jvm_manifest_value(manifest, size, "MIDlet-Name", value, sizeof(value)))
            utf8_to_ucs2(value, info->title, TITLE_CHARS);
        /* MIDlet-Icon, else the icon field of MIDlet-1: "name, icon, class". */
        char icon[96];
        if (!jvm_manifest_value(manifest, size, "MIDlet-Icon", icon, sizeof(icon))) {
            u32 at = 0, out = 0;
            if (jvm_manifest_value(manifest, size, "MIDlet-1", value, sizeof(value))) {
                while (value[at] && value[at] != ',') at++;
                if (value[at] == ',') at++;
                while (value[at] == ' ') at++;
                while (value[at] && value[at] != ',' && out+1 < sizeof(icon)) icon[out++] = value[at++];
                while (out && icon[out-1] == ' ') out--;
            }
            icon[out] = 0;
        }
        archive_release(0, manifest);
        decode_icon(&jar, icon, info);
    }
    jar_close(&jar);
    api->close(handle);
}

static void free_infos(void) {
    if (infos) { api->free(infos); infos = 0; }
}

static void load_infos(void) {
    free_infos();
    if (!game_count || api->size < sizeof(Go1Api)) return;
    infos = api->alloc(game_count*sizeof(GameInfo));
    if (!infos) return;
    for (u32 i = 0; i < game_count; i++) read_info(i, &infos[i]);
}

static void draw_icon(u32 x, u32 y, const GameInfo *info) {
    u16 *screen = api->screen();
    for (u32 row = 0; row < ICON; row++)
        for (u32 col = 0; col < ICON; col++) {
            u32 i = row*ICON+col, a = info->alpha[i];
            if (!a) continue;
            u16 *target = screen+(y+row)*GO1_SCREEN_WIDTH+x+col, front = info->pixels[i], back = *target;
            if (a < 255) {
                u32 r = ((u32)(front >> 11)*a+(u32)(back >> 11)*(255-a))/255;
                u32 g = ((u32)((front >> 5) & 63)*a+(u32)((back >> 5) & 63)*(255-a))/255;
                u32 b = ((u32)(front & 31)*a+(u32)(back & 31)*(255-a))/255;
                front = (u16)(r << 11 | g << 5 | b);
            }
            *target = front;
        }
    api->flush(x, y, ICON, ICON);
}

static void draw_list(void) {
    static const u16 title[] = {'J', 'a', 'v', 'a', 0};
    api->fill(0, 0, 176, 176, 0);
    api->text_wide(2, title, 0xffe0);
    if (!game_count) {
        api->text(50, "No games", 0xffff);
        api->text(76, "Put .jar files into", 0xffff);
        api->text(96, FOLDER, 0x07ff);
        return;
    }
    u32 first = selected/ROWS*ROWS;
    for (u32 i = 0; i < ROWS && first+i < game_count; i++) {
        u32 y = 28+i*22, current = first+i == selected;
        u16 shown[NAME_CHARS+1];
        u32 n = 0;
        const GameInfo *info = infos ? &infos[first+i] : 0;
        const u16 *name = info && info->title[0] ? info->title : games[first+i];
        while ((shown[n] = name[n]) != 0) n++;
        if (name == games[first+i] && n > 4 && shown[n-4] == '.') shown[n-4] = 0;  /* hide ".jar" */
        if (current) api->fill(0, y-1, 176, 21, 0x2104);
        u32 color = current ? 0x07ff : 0xffff;
        /* Capitals take rows +3..+11 of the text box: +2 centres them in the bar y-1..y+19. */
        if (info) {
            api->text_at(28, y+2, shown, color);
            if (info->has_icon) draw_icon(6, y+1, info);
        } else api->text_wide(y+2, shown, color);
    }
    if (game_count > ROWS) {
        char page[8];
        u32 at = 0, value = first/ROWS+1, pages = (game_count+ROWS-1)/ROWS;
        if (value >= 10) page[at++] = (char)('0'+value/10);
        page[at++] = (char)('0'+value%10);
        page[at++] = '/';
        if (pages >= 10) page[at++] = (char)('0'+pages/10);
        page[at++] = (char)('0'+pages%10);
        page[at] = 0;
        api->text(156, page, 0x8410);
    }
}

static uint8_t *host_read(void *context, const char *path, uint32_t *size) {
    (void)context;
    uint8_t *data = library_file ? jar_read(&library, path, size) : 0;
    if (!data && application_file) data = jar_read(&application, path, size);
    return data;
}
static void host_release(void *context, uint8_t *data) { archive_release(context, data); }

static int host_stream(void *context, const char *path, JarStream *stream, uint32_t *size) {
    (void)context;
    return (library_file && jar_stream_open(&library, path, stream, size)) ||
           (application_file && jar_stream_open(&application, path, stream, size));
}

static void host_write(void *context, int stream, const char *bytes, uint32_t length) {
    (void)context; (void)stream;
    for (u32 i = 0; i < length; i++) {
        unsigned char c = (unsigned char)bytes[i];
        if (c < 0x80) put((char)c);
        else if ((c & 0xc0) == 0xc0) put('?');  /* one mark per non-ASCII character */
    }
}

static uint32_t host_ms(void *context) { (void)context; return api->ms(); }
static uint16_t *host_screen(void *context) { (void)context; return api->screen(); }
static void host_flush(void *context, int x, int y, int w, int h) { (void)context; api->flush((u32)x, (u32)y, (u32)w, (u32)h); }

/* Phone key code to a MIDP key code (0 when unmapped). */
static int midp_key(u32 code) {
    switch (code) {
    case GO1_UP: return JVM_KEY_UP;
    case GO1_DOWN: return JVM_KEY_DOWN;
    case GO1_LEFT: return JVM_KEY_LEFT;
    case GO1_RIGHT: return JVM_KEY_RIGHT;
    case GO1_OK: return JVM_KEY_FIRE;
    case GO1_SOFT_LEFT: return JVM_KEY_SOFT_LEFT;
    case GO1_SOFT_RIGHT: return JVM_KEY_SOFT_RIGHT;
    case GO1_STAR: return '*';
    case GO1_HASH: return '#';
    case GO1_0: return '0';
    default: return code >= GO1_1 && code <= GO1_9 ? (int)('1'+code-GO1_1) : 0;
    }
}

static void key(u32 message) {
    int code = midp_key(GO1_CODE(message)), type = 0;
    switch (GO1_KIND(message)) {
    case GO1_PRESS: type = JVM_KEY_PRESSED; break;
    case GO1_RELEASE: case GO1_LONG_RELEASE: type = JVM_KEY_RELEASED; break;
    case GO1_REPEAT: type = JVM_KEY_REPEATED; break;
    default: break;
    }
    if (code && type) jvm_post_event(vm, type, code);
}

static void shutdown(void) {
    api->timer(0, 0);
    if (library_file) { jar_close(&library); api->close(library_file); library_file = 0; }
    if (application_file) { jar_close(&application); api->close(application_file); application_file = 0; }
    if (block) { api->free(block); block = 0; }
    vm = 0;
}

static void start(const u16 *path) {
    free_infos();
    static const JvmHost host = {host_read, host_release, host_write, host_ms, 0, host_screen, host_flush, host_stream};
    u16 library_path[20];
    row = column = 0;
    for (u32 i = 0; i < LINES; i++) console[i][0] = 0;
    finished = 0;
    midp = 0;
    choosing = 0;
    widen(library_path, "E:\\JAVALIB.JAR");
    if (!open_archive(&library, &library_file, library_path)) { say("No E:\\JAVALIB.JAR\n"); finished = 1; return; }
    if (!open_archive(&application, &application_file, path)) { say("Cannot open the JAR\n"); finished = 1; return; }
    char name[128];
    if (!jvm_main_class(&host, name, sizeof(name))) { say("No MIDlet-1 or Main-Class\n"); finished = 1; return; }
    midp = is_launcher(name);
    if (midp) say("Loading MIDlet...\n");
    /* Metadata grows with the application's classes (measured: about 1.4 bytes
       per class file byte plus the library); the heap gets the rest. */
    u32 meta = META_BASE+jar_class_bytes(&application)/5*7;
    u32 available = api->heap_free();
    if (available < meta+RESERVE+HEAP_MIN) { say("Not enough memory\n"); finished = 1; return; }
    u32 bytes = available-RESERVE;
    block = api->alloc(bytes);
    vm = block ? jvm_create(&host, block, bytes, meta) : 0;
    if (!vm) { say("VM creation failed\n"); finished = 1; return; }
    if (!jvm_start_main(vm, name)) { say("VM: "); say(jvm_error(vm)); put('\n'); finished = 1; return; }
    api->timer(TICK_MS, 1);
}

static void launch(u32 index) {
    u16 path[NAME_CHARS+12];
    u32 n = widen(path, FOLDER);
    for (u32 i = 0; (path[n+i] = games[index][i]) != 0; i++) {}
    start(path);
    draw();
}

/* Program over: back to the list it came from, or to SD Apps. */
static void done(void) {
    shutdown();
    if (!from_list) { api->exit(); return; }
    clear_screen();
    choosing = 1;
    finished = 0;
    midp = 0;
    load_infos();
    draw_list();
}

static void open_list(void) {
    scan();
    from_list = game_count > 0;
    u16 fallback[16];
    widen(fallback, "E:\\APP.JAR");
    u32 old = from_list ? 0 : api->open_wide(fallback);
    if (old) { api->close(old); start(fallback); draw(); return; }
    choosing = 1;
    load_infos();
    draw_list();
}

static void choose(u32 message) {
    u32 kind = GO1_KIND(message), code = GO1_CODE(message);
    if (kind != GO1_PRESS && kind != GO1_REPEAT) return;
    if (code == GO1_SOFT_RIGHT && kind == GO1_PRESS) { api->exit(); return; }
    if (!game_count) return;
    if (code == GO1_UP || code == GO1_2) selected = selected ? selected-1 : game_count-1;
    else if (code == GO1_DOWN || code == GO1_8) selected = selected+1 < game_count ? selected+1 : 0;
    else if ((code == GO1_OK || code == GO1_SOFT_LEFT || code == GO1_5) && kind == GO1_PRESS) { launch(selected); return; }
    else return;
    draw_list();
}

static void tick(void) {
    if (!vm || finished) return;
    int status = jvm_run(vm, SLICE);
    /* An uncaught exception or VM error brings the console back over the MIDlet. */
    if (midp && (status == JVM_ERROR || jvm_uncaught(vm))) { midp = 0; dirty = 1; }
    if (status == JVM_DONE && midp) done();
    else if (status == JVM_DONE) { say("[finished]\n"); finished = 1; api->timer(0, 0); }
    else if (status == JVM_ERROR) { say("VM: "); say(jvm_error(vm)); put('\n'); finished = 1; api->timer(0, 0); }
}

__attribute__((used, section(".entry")))
u32 go1_main(const Go1Api *program_api, u32 message, u32 parameter) {
    (void)parameter;
    api = program_api;
    if (api->version != GO1_API_VERSION) return 0;
    /* Older loaders lack file search and UCS-2 calls; text and exit always exist. */
    if (api->size < __builtin_offsetof(Go1Api, text_at)) {
        if (message == GO1_OPEN || message == GO1_PAINT) {
            api->fill(0, 0, 176, 176, 0);
            api->text(50, "JAVA.GO1 needs", 0xffff);
            api->text(72, "a newer firmware", 0xffff);
            api->text(110, "OK: back", 0x07ff);
        } else if (message == GO1_KEY_OK) api->exit();
        return 1;
    }
    if (message == GO1_CLOSE) { shutdown(); free_infos(); return 1; }
    if (message == GO1_OPEN) { open_list(); return 1; }
    if (choosing) {
        if (GO1_IS_KEY(message)) choose(message);
        else if (message == GO1_PAINT) draw_list();
        return 1;
    }
    if (message == GO1_TIMER) tick();
    else if (GO1_IS_KEY(message) && vm && !finished) key(message);
    else if (message == GO1_KEY_OK && finished) { done(); return 1; }
    else if (message == GO1_PAINT && midp && vm && !finished) jvm_post_event(vm, JVM_REPAINT, 1);
    if (choosing || (midp && !finished)) return 1;  /* the list or the MIDlet owns the screen */
    if (dirty || message == GO1_PAINT) draw();
    return 1;
}
