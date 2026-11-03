/* JAVA.B22: the B22 JVM as a native SD application.

Opening it lists the .jar files in E:\JAVA\ and runs the chosen one; with no
files there it runs E:\APP.JAR directly. Classes come from E:\JAVALIB.JAR (the
class library) and the chosen JAR. A manifest with MIDlet-1 starts
b22.Launcher, which runs the MIDlet and owns the screen; otherwise Main-Class
runs with System.out shown as a console. The VM runs a bounded slice on every
window timer tick; phone keys are posted to the MIDP event queue. When the
program ends, the list comes back. */
#include "b22n.h"
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

static const B22Api *api;
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
static B22FindEntry entry;

static int is_launcher(const char *name) {
    const char *launcher = "b22/Launcher";
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
        if ((entry.attributes & B22N_ATTR_DIRECTORY) || length > NAME_CHARS) continue;
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
        while ((shown[n] = games[first+i][n]) != 0) n++;
        if (n > 4 && shown[n-4] == '.') shown[n-4] = 0;  /* hide ".jar" */
        if (current) api->fill(0, y-1, 176, 21, 0x2104);
        api->text_wide(y, shown, current ? 0x07ff : 0xffff);
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

/* After a MIDlet the whole frame buffer is its picture: clear it. */
static void clear_screen(void) {
    uint16_t *screen = api->screen();
    for (u32 i = 0; i < B22N_SCREEN_WIDTH*B22N_SCREEN_HEIGHT; i++) screen[i] = 0;
    api->flush(0, 0, B22N_SCREEN_WIDTH, B22N_SCREEN_HEIGHT);
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
    case B22N_UP: return JVM_KEY_UP;
    case B22N_DOWN: return JVM_KEY_DOWN;
    case B22N_LEFT: return JVM_KEY_LEFT;
    case B22N_RIGHT: return JVM_KEY_RIGHT;
    case B22N_OK: return JVM_KEY_FIRE;
    case B22N_SOFT_LEFT: return JVM_KEY_SOFT_LEFT;
    case B22N_SOFT_RIGHT: return JVM_KEY_SOFT_RIGHT;
    case B22N_STAR: return '*';
    case B22N_HASH: return '#';
    case B22N_0: return '0';
    default: return code >= B22N_1 && code <= B22N_9 ? (int)('1'+code-B22N_1) : 0;
    }
}

static void key(u32 message) {
    int code = midp_key(B22N_CODE(message)), type = 0;
    switch (B22N_KIND(message)) {
    case B22N_PRESS: type = JVM_KEY_PRESSED; break;
    case B22N_RELEASE: case B22N_LONG_RELEASE: type = JVM_KEY_RELEASED; break;
    case B22N_REPEAT: type = JVM_KEY_REPEATED; break;
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
    draw_list();
}

static void choose(u32 message) {
    u32 kind = B22N_KIND(message), code = B22N_CODE(message);
    if (kind != B22N_PRESS && kind != B22N_REPEAT) return;
    if (code == B22N_SOFT_RIGHT && kind == B22N_PRESS) { api->exit(); return; }
    if (!game_count) return;
    if (code == B22N_UP || code == B22N_2) selected = selected ? selected-1 : game_count-1;
    else if (code == B22N_DOWN || code == B22N_8) selected = selected+1 < game_count ? selected+1 : 0;
    else if ((code == B22N_OK || code == B22N_SOFT_LEFT || code == B22N_5) && kind == B22N_PRESS) { launch(selected); return; }
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
u32 b22n_main(const B22Api *program_api, u32 message, u32 parameter) {
    (void)parameter;
    api = program_api;
    if (api->version != B22N_API_VERSION) return 0;
    /* Older loaders lack file search and UCS-2 calls; text and exit always exist. */
    if (api->size < sizeof(B22Api)) {
        if (message == B22N_OPEN || message == B22N_PAINT) {
            api->fill(0, 0, 176, 176, 0);
            api->text(50, "JAVA.B22 needs", 0xffff);
            api->text(72, "a newer firmware", 0xffff);
            api->text(110, "OK: back", 0x07ff);
        } else if (message == B22N_KEY_OK) api->exit();
        return 1;
    }
    if (message == B22N_CLOSE) { shutdown(); return 1; }
    if (message == B22N_OPEN) { open_list(); return 1; }
    if (choosing) {
        if (B22N_IS_KEY(message)) choose(message);
        else if (message == B22N_PAINT) draw_list();
        return 1;
    }
    if (message == B22N_TIMER) tick();
    else if (B22N_IS_KEY(message) && vm && !finished) key(message);
    else if (message == B22N_KEY_OK && finished) { done(); return 1; }
    else if (message == B22N_PAINT && midp && vm && !finished) jvm_post_event(vm, JVM_REPAINT, 1);
    if (choosing || (midp && !finished)) return 1;  /* the list or the MIDlet owns the screen */
    if (dirty || message == B22N_PAINT) draw();
    return 1;
}
