/* Windows test host: b22jvm [-heap KB] [-meta KB] [-cp dir;dir] [-jar app.jar] MainClass */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <windows.h>
#include "jvm.h"
#include "jar.h"

typedef struct Host { char paths[16][260]; int count; FILE *archive; Jar jar; int has_jar; } Host;

static uint32_t archive_read(void *context, uint32_t offset, void *buffer, uint32_t length) {
    Host *h = context;
    if (fseek(h->archive, (long)offset, SEEK_SET)) return 0;
    return (uint32_t)fread(buffer, 1, length, h->archive);
}

static void *archive_alloc(void *context, uint32_t size) { (void)context; return malloc(size); }
static void archive_release(void *context, void *block) { (void)context; free(block); }

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

static void host_release(void *context, uint8_t *buffer) { (void)context; free(buffer); }

static void host_write(void *context, int stream, const char *bytes, uint32_t length) {
    (void)context;
    FILE *f = stream == 2 ? stderr : stdout;
    fwrite(bytes, 1, length, f);
    fflush(f);
}

static uint32_t host_ms(void *context) { (void)context; return (uint32_t)GetTickCount(); }

int main(int argc, char **argv) {
    Host h = {0};
    uint32_t heap_kb = 1024, meta_kb = 384;
    const char *main_class = 0;
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
        else main_class = argv[i];
    }
    if (!main_class) { fprintf(stderr, "usage: b22jvm [-heap KB] [-meta KB] -cp dirs Main\n"); return 64; }
    for (char *p = (char *)main_class; *p; p++) if (*p == '.') *p = '/';
    JvmHost host = {host_read, host_release, host_write, host_ms, &h};
    uint32_t bytes = (heap_kb+meta_kb)*1024;
    void *memory = malloc(bytes);
    Jvm *vm = jvm_create(&host, memory, bytes, meta_kb*1024);
    if (!vm) { fprintf(stderr, "VM creation failed\n"); return 70; }
    if (!jvm_start_main(vm, main_class)) { fprintf(stderr, "VM error: %s\n", jvm_error(vm)); return 70; }
    for (;;) {
        int status = jvm_run(vm, 1000000);
        if (status == JVM_ERROR) { fprintf(stderr, "VM error: %s\n", jvm_error(vm)); return 70; }
        if (status == JVM_DONE) break;
        if (status == JVM_IDLE) {
            uint32_t wake = jvm_next_wake(vm);
            if (wake == 0xffffffffu) { fprintf(stderr, "VM deadlock\n"); return 71; }
            int32_t wait = (int32_t)(wake-host_ms(0));
            if (wait > 0) Sleep((DWORD)wait);
        }
    }
    if (getenv("B22JVM_STATS"))
        fprintf(stderr, "heap used %u, collections %u\n", jvm_heap_used(vm), jvm_gc_count(vm));
    return jvm_uncaught(vm) ? 1 : 0;
}
