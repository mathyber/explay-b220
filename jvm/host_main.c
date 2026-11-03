/* Windows test host: b22jvm [-heap KB] [-meta KB] [-cp dir;dir] [-jar app.jar] MainClass */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <windows.h>
#include "jvm.h"
#include "jar.h"

typedef struct Host { char paths[16][260]; int count; FILE *archive; Jar jar; int has_jar; Jvm *vm; } Host;

static uint32_t archive_read(void *context, uint32_t offset, void *buffer, uint32_t length) {
    Host *h = context;
    if (fseek(h->archive, (long)offset, SEEK_SET)) return 0;
    return (uint32_t)fread(buffer, 1, length, h->archive);
}

/* Like the phone: once the VM runs, JAR entries are extracted into its heap. */
static void *archive_alloc(void *context, uint32_t size) {
    Host *h = context;
    void *data = h->vm ? jvm_scratch_alloc(h->vm, size) : 0;
    return data ? data : malloc(size);
}
static void archive_release(void *context, void *block) {
    Host *h = context;
    if (!h->vm || !jvm_scratch_release(h->vm, block)) free(block);
}

static uint8_t *host_read(void *context, const char *name, uint32_t *size) {
    Host *h = context;
    for (int i = 0; i < h->count; i++) {
        char path[600];
        snprintf(path, sizeof(path), "%s/%s", h->paths[i], name);
        FILE *f = fopen(path, "rb");
        if (!f) continue;
        fseek(f, 0, SEEK_END);
        long length = ftell(f);
        fseek(f, 0, SEEK_SET);
        uint8_t *data = malloc(length > 0 ? (size_t)length : 1);
        size_t got = fread(data, 1, (size_t)length, f);
        fclose(f);
        if (got != (size_t)length) { free(data); return 0; }
        *size = (uint32_t)length;
        return data;
    }
    return h->has_jar ? jar_read(&h->jar, name, size) : 0;
}

static void host_release(void *context, uint8_t *buffer) { archive_release(context, buffer); }

/* Large resources of the application JAR are streamed (class path folders are read whole). */
static int host_stream(void *context, const char *path, JarStream *stream, uint32_t *size) {
    Host *h = context;
    return h->has_jar && jar_stream_open(&h->jar, path, stream, size);
}

static void host_write(void *context, int stream, const char *bytes, uint32_t length) {
    (void)context;
    FILE *f = stream == 2 ? stderr : stdout;
    fwrite(bytes, 1, length, f);
    fflush(f);
}

static uint32_t host_ms(void *context) { (void)context; return (uint32_t)GetTickCount(); }

static uint16_t frame[JVM_SCREEN_WIDTH*JVM_SCREEN_HEIGHT];
static uint16_t *host_screen(void *context) { (void)context; return frame; }
static void host_flush(void *context, int x, int y, int w, int h) { (void)context; (void)x; (void)y; (void)w; (void)h; }

/* Screen as a binary PPM. */
static int save_frame(const char *path) {
    FILE *f = fopen(path, "wb");
    if (!f) return 0;
    fprintf(f, "P6 %d %d 255\n", JVM_SCREEN_WIDTH, JVM_SCREEN_HEIGHT);
    for (int i = 0; i < JVM_SCREEN_WIDTH*JVM_SCREEN_HEIGHT; i++) {
        uint16_t c = frame[i];
        unsigned char rgb[3] = {(unsigned char)((c >> 11) << 3), (unsigned char)(((c >> 5) & 63) << 2), (unsigned char)((c & 31) << 3)};
        fwrite(rgb, 1, 3, f);
    }
    fclose(f);
    return 1;
}

typedef struct Key { uint32_t at; int code; int released; } Key;

typedef struct Usage { char name[64]; uint32_t bytes, count; } Usage;
static Usage usage[256];
static uint32_t usage_count;

static void count_object(void *context, const char *name, uint32_t length, uint32_t bytes) {
    (void)context;
    if (length > sizeof(usage[0].name)-1) length = sizeof(usage[0].name)-1;
    uint32_t i = 0;
    while (i < usage_count && (strlen(usage[i].name) != length || memcmp(usage[i].name, name, length))) i++;
    if (i == usage_count) {
        if (usage_count == sizeof(usage)/sizeof(usage[0])) return;
        memcpy(usage[i].name, name, length);
        usage[i].name[length] = 0;
        usage_count++;
    }
    usage[i].bytes += bytes;
    usage[i].count++;
}

static int by_bytes(const void *a, const void *b) {
    uint32_t x = ((const Usage *)a)->bytes, y = ((const Usage *)b)->bytes;
    return x < y ? 1 : x > y ? -1 : 0;
}

/* B22JVM_STATS: totals; B22JVM_HEAP: live objects by class after a collection. */
static void stats(Jvm *vm) {
    if (getenv("B22JVM_STATS")) {
        uint32_t parts[JVM_META_PARTS];
        jvm_meta_parts(vm, parts);
        fprintf(stderr, "heap used %u, collections %u, peak live %u, largest free %u, metadata used %u\n", jvm_heap_used(vm),
                jvm_gc_count(vm), jvm_heap_peak(vm), jvm_heap_largest_free(vm), jvm_meta_used(vm));
        fprintf(stderr, "metadata: threads %u, %u classes: constants %u, methods/fields %u, bytecode %u\n",
                parts[0], parts[1], parts[2], parts[3], parts[4]);
    }
    if (!getenv("B22JVM_HEAP")) return;
    jvm_heap_walk(vm, count_object, 0);
    qsort(usage, usage_count, sizeof(usage[0]), by_bytes);
    fprintf(stderr, "live heap %u bytes\n", jvm_heap_used(vm));
    for (uint32_t i = 0; i < usage_count && i < 15; i++)
        fprintf(stderr, "%9u %6u %s\n", usage[i].bytes, usage[i].count, usage[i].name);
}

int main(int argc, char **argv) {
    Host h = {0};
    uint32_t heap_kb = 1024, meta_kb = 512, run_ms = 0;
    const char *main_class = 0, *shot = 0;
    Key keys[64];
    int key_count = 0;
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "-cp") && i+1 < argc) {
            char *list = argv[++i];
            for (char *part = strtok(list, ";"); part && h.count < 16; part = strtok(0, ";"))
                snprintf(h.paths[h.count++], sizeof(h.paths[0]), "%s", part);
        } else if (!strcmp(argv[i], "-jar") && i+1 < argc) {
            h.archive = fopen(argv[++i], "rb");
            if (!h.archive) { fprintf(stderr, "cannot open %s\n", argv[i]); return 66; }
            fseek(h.archive, 0, SEEK_END);
            JarIo io = {archive_read, archive_alloc, archive_release, &h, (uint32_t)ftell(h.archive)};
            if (!jar_open(&h.jar, &io)) { fprintf(stderr, "not a JAR: %s\n", argv[i]); return 66; }
            h.has_jar = 1;
        } else if (!strcmp(argv[i], "-heap") && i+1 < argc) heap_kb = (uint32_t)atoi(argv[++i]);
        else if (!strcmp(argv[i], "-meta") && i+1 < argc) meta_kb = (uint32_t)atoi(argv[++i]);
        else if (!strcmp(argv[i], "-run") && i+1 < argc) run_ms = (uint32_t)atoi(argv[++i]);
        else if (!strcmp(argv[i], "-shot") && i+1 < argc) shot = argv[++i];
        else if (!strcmp(argv[i], "-keys") && i+1 < argc) {
            /* time:code,... -> press at time, release 30 ms later */
            for (char *part = strtok(argv[++i], ","); part && key_count < 62; part = strtok(0, ",")) {
                char *colon = strchr(part, ':');
                if (!colon) continue;
                uint32_t at = (uint32_t)atoi(part);
                int code = atoi(colon+1);
                keys[key_count++] = (Key){at, code, 0};
                keys[key_count++] = (Key){at+30, code, 1};
            }
        } else main_class = argv[i];
    }
    JvmHost host = {host_read, host_release, host_write, host_ms, &h, host_screen, host_flush, host_stream};
    char manifest_main[128];
    if (!main_class && jvm_main_class(&host, manifest_main, sizeof(manifest_main))) main_class = manifest_main;
    if (!main_class) { fprintf(stderr, "usage: b22jvm [-heap KB] [-meta KB] [-cp dirs] [-jar app.jar] [-run ms] [-keys ms:code,...] [-shot file.ppm] [Main]\n"); return 64; }
    for (char *p = (char *)main_class; *p; p++) if (*p == '.') *p = '/';
    uint32_t bytes = (heap_kb+meta_kb)*1024;
    void *memory = malloc(bytes);
    Jvm *vm = jvm_create(&host, memory, bytes, meta_kb*1024);
    h.vm = vm;
    if (!vm) { fprintf(stderr, "VM creation failed\n"); return 70; }
    if (!jvm_start_main(vm, main_class)) { fprintf(stderr, "VM error: %s\n", jvm_error(vm)); return 70; }
    uint32_t start = host_ms(0);
    int next_key = 0;
    for (;;) {
        uint32_t elapsed = host_ms(0)-start;
        while (next_key < key_count && keys[next_key].at <= elapsed) {
            jvm_post_event(vm, keys[next_key].released ? JVM_KEY_RELEASED : JVM_KEY_PRESSED, keys[next_key].code);
            next_key++;
        }
        if (run_ms && elapsed >= run_ms) break;
        int status = jvm_run(vm, 200000);
        if (status == JVM_ERROR) { fprintf(stderr, "VM error: %s\n", jvm_error(vm)); stats(vm); return 70; }
        if (status == JVM_DONE) break;
        if (status == JVM_IDLE) {
            uint32_t wake = jvm_next_wake(vm);
            if (wake == 0xffffffffu && !run_ms) { fprintf(stderr, "VM deadlock\n"); return 71; }
            int32_t wait = (int32_t)(wake-host_ms(0));
            if (wait > 5 || wake == 0xffffffffu) wait = 5;
            if (wait > 0) Sleep((DWORD)wait);
        }
    }
    if (shot && !save_frame(shot)) { fprintf(stderr, "cannot write %s\n", shot); return 73; }
    stats(vm);
    return jvm_uncaught(vm) ? 1 : 0;
}
