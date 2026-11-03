/* JAVA.B22: the B22 JVM as a native SD application.

Classes come from E:\JAVALIB.JAR (the class library) and E:\APP.JAR (the
application, Main-Class from its manifest). The VM runs a bounded slice on
every window timer tick; System.out is shown as a console. */
#include "b22n.h"
#include "jvm.h"
#include "jar.h"

typedef uint32_t u32;

#define SLICE 20000          /* bytecodes per timer tick */
#define TICK_MS 20
#define META_BYTES (256*1024)
#define RESERVE (64*1024)    /* App Heap left to the firmware */
#define LINES 9
#define COLUMNS 28

static const B22Api *api;
static Jvm *vm;
static void *block;
static Jar library, application;
static u32 library_file, application_file;
static int finished;
static char console[LINES][COLUMNS+1];
static u32 row, column;
static int dirty;

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

/* ---------- JVM host ---------- */

static u32 archive_read(void *context, u32 offset, void *buffer, u32 length) {
    u32 handle = (u32)(uintptr_t)context;
    if (api->seek(handle, offset)) return 0;
    u32 n = api->read(handle, buffer, length);
    return n == 0xffffffffu ? 0 : n;
}
static void *archive_alloc(void *context, u32 size) { (void)context; return api->alloc(size); }
static void archive_release(void *context, void *data) { (void)context; api->free(data); }

static int open_archive(Jar *jar, u32 *handle, const char *path) {
    u32 h = api->open(path);
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
static void host_release(void *context, uint8_t *data) { (void)context; api->free(data); }

static void host_write(void *context, int stream, const char *bytes, uint32_t length) {
    (void)context; (void)stream;
    for (u32 i = 0; i < length; i++) {
        unsigned char c = (unsigned char)bytes[i];
        if (c < 0x80) put((char)c);
        else if ((c & 0xc0) == 0xc0) put('?');  /* one mark per non-ASCII character */
    }
}

static uint32_t host_ms(void *context) { (void)context; return api->ms(); }

/* Main-Class from the manifest, as "pkg/Name". */
static int main_class(char *name, u32 capacity) {
    u32 size = 0;
    uint8_t *manifest = jar_read(&application, "META-INF/MANIFEST.MF", &size);
    if (!manifest) return 0;
    static const char key[] = "Main-Class:";
    int found = 0;
    for (u32 i = 0; i+sizeof(key)-1 <= size && !found; i++) {
        if ((i && manifest[i-1] != '\n') ) continue;
        u32 k = 0;
        while (key[k] && manifest[i+k] == (uint8_t)key[k]) k++;
        if (key[k]) continue;
        u32 p = i+k, n = 0;
        while (p < size && manifest[p] == ' ') p++;
        while (p < size && manifest[p] != '\r' && manifest[p] != '\n' && n+1 < capacity) {
            name[n++] = manifest[p] == '.' ? '/' : (char)manifest[p];
            p++;
        }
        name[n] = 0;
        found = n > 0;
    }
    api->free(manifest);
    return found;
}

static void shutdown(void) {
    api->timer(0, 0);
    if (library_file) { jar_close(&library); api->close(library_file); library_file = 0; }
    if (application_file) { jar_close(&application); api->close(application_file); application_file = 0; }
    if (block) { api->free(block); block = 0; }
    vm = 0;
}

static void start(void) {
    static const JvmHost host = {host_read, host_release, host_write, host_ms, 0};
    row = column = 0;
    for (u32 i = 0; i < LINES; i++) console[i][0] = 0;
    finished = 0;
    if (!open_archive(&library, &library_file, "E:\\JAVALIB.JAR")) { say("No E:\\JAVALIB.JAR\n"); finished = 1; return; }
    if (!open_archive(&application, &application_file, "E:\\APP.JAR")) { say("No E:\\APP.JAR\n"); finished = 1; return; }
    char name[128];
    if (!main_class(name, sizeof(name))) { say("No Main-Class\n"); finished = 1; return; }
    u32 available = api->heap_free();
    if (available < META_BYTES+RESERVE+128*1024) { say("Not enough memory\n"); finished = 1; return; }
    u32 bytes = available-RESERVE;
    block = api->alloc(bytes);
    vm = block ? jvm_create(&host, block, bytes, META_BYTES) : 0;
    if (!vm) { say("VM creation failed\n"); finished = 1; return; }
    if (!jvm_start_main(vm, name)) { say("VM: "); say(jvm_error(vm)); put('\n'); finished = 1; return; }
    api->timer(TICK_MS, 1);
}

static void tick(void) {
    if (!vm || finished) return;
    int status = jvm_run(vm, SLICE);
    if (status == JVM_DONE) { say("[finished]\n"); finished = 1; api->timer(0, 0); }
    else if (status == JVM_ERROR) { say("VM: "); say(jvm_error(vm)); put('\n'); finished = 1; api->timer(0, 0); }
}

__attribute__((used, section(".entry")))
u32 b22n_main(const B22Api *program_api, u32 message, u32 parameter) {
    (void)parameter;
    api = program_api;
    if (api->version != B22N_API_VERSION || api->size < sizeof(B22Api)) return 0;
    if (message == B22N_CLOSE) { shutdown(); return 1; }
    if (message == B22N_OPEN) { start(); draw(); return 1; }
    if (message == B22N_TIMER) tick();
    else if (message == B22N_KEY_OK && finished) { api->exit(); return 1; }
    if (dirty || message == B22N_PAINT) draw();
    return 1;
}
