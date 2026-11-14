/* MEMTEST.GO1: is a large App Heap block stable on the real phone?
   go1-title: Тест памяти
   Takes as much of the App Heap as JAVA.GO1 does (all but 64 KB), then keeps
   rewriting it with a pattern that changes every round and checks on the next
   round that the words are still as written. Something else writing into the
   block (the firmware, a DMA) shows as errors; the phone resetting while the
   block is in use shows as a log without an end. Status every 5 s and every
   error (the first 16) go to E:\MEMTEST.TXT. Right soft key or red key: exit. */
#include "go1.h"

typedef go1_u32 u32;
typedef unsigned short u16;

#define RESERVE (64*1024)
#define WORDS_PER_TICK (16*1024)   /* 64 KB a tick */
#define TICK_MS 20

static const Go1Api *api;
static u32 *block, words, position, round, errors, first_bad, bad_value, bad_expected, last_log, started;

static u32 pattern(u32 index, u32 pass) { return (index*2654435761u) ^ (pass*0x9e3779b9u) ^ 0x5a5a0000u; }

static u32 number(char *out, u32 value, u32 base) {
    char digits[12];
    u32 n = 0, k = 0;
    do { digits[n++] = "0123456789abcdef"[value%base]; value /= base; } while (value);
    while (n) out[k++] = digits[--n];
    return k;
}

static u32 line(char *out, const char *label, u32 value, u32 base) {
    u32 n = 0;
    while (*label) out[n++] = *label++;
    n += number(out+n, value, base);
    out[n] = 0;
    return n;
}

static void log_line(const char *text) {
    static const u16 path[] = {'E', ':', '\\', 'M', 'E', 'M', 'T', 'E', 'S', 'T', '.', 'T', 'X', 'T', 0};
    u32 h = api->open_append(path);
    if (!h) h = api->create(path);
    if (!h) return;
    u32 n = 0;
    while (text[n]) n++;
    api->write(h, text, n);
    api->write(h, "\r\n", 2);
    api->close(h);
}

static void status(const char *event) {
    char text[120];
    u32 n = 0;
    while (*event) text[n++] = *event++;
    n += line(text+n, " ms=", api->ms()-started, 10);
    n += line(text+n, " round=", round, 10);
    n += line(text+n, " errors=", errors, 10);
    n += line(text+n, " bytes=", words*4, 10);
    n += line(text+n, " hf=", api->heap_free(), 10);
    if (errors) {
        n += line(text+n, " at=", (u32)(block+first_bad), 16);
        n += line(text+n, " got=", bad_value, 16);
        n += line(text+n, " want=", bad_expected, 16);
    }
    log_line(text);
}

static void draw(void) {
    char text[40];
    api->fill(0, 0, 176, 176, 0);
    api->text(4, "MEMORY TEST", 0xffe0);
    if (!block) { api->text(40, "No memory", 0xf800); return; }
    line(text, "block KB ", words/256, 10); api->text(30, text, 0xffff);
    line(text, "at ", (u32)block, 16); api->text(50, text, 0xffff);
    line(text, "round ", round, 10); api->text(70, text, 0xffff);
    line(text, "errors ", errors, 10); api->text(90, text, errors ? 0xf800 : 0x07e0);
    line(text, "seconds ", (api->ms()-started)/1000, 10); api->text(110, text, 0xffff);
    api->text(150, "Right soft: exit", 0x07ff);
}

static void start(void) {
    u32 available = api->heap_free();
    words = available > RESERVE+4096 ? (available-RESERVE)/4 : 0;
    block = words ? api->alloc(words*4) : 0;
    round = position = errors = 0;
    started = last_log = api->ms();
    if (!block) { log_line("no memory"); return; }
    status("start");
    api->timer(TICK_MS, 1);
}

static void tick(void) {
    if (!block) return;
    for (u32 k = 0; k < WORDS_PER_TICK; k++) {
        u32 i = position;
        if (round) {
            u32 want = pattern(i, round-1), got = block[i];
            if (got != want) {
                if (!errors) { first_bad = i; bad_value = got; bad_expected = want; }
                errors++;
                if (errors <= 16) {
                    char text[80];
                    u32 n = line(text, "bad at=", (u32)(block+i), 16);
                    n += line(text+n, " got=", got, 16);
                    line(text+n, " want=", want, 16);
                    log_line(text);
                }
            }
        }
        block[i] = pattern(i, round);
        if (++position == words) { position = 0; round++; }
    }
    if (api->ms()-last_log >= 5000) { last_log = api->ms(); status("alive"); }
    draw();
    api->flush(0, 0, 176, 176);
}

static void stop(void) {
    api->timer(0, 0);
    if (block) { status("stop"); api->free(block); block = 0; }
}

__attribute__((used, section(".entry")))
u32 go1_main(const Go1Api *program_api, u32 message, u32 parameter) {
    (void)parameter;
    api = program_api;
    if (api->size < __builtin_offsetof(Go1Api, alloc_static)) return 0;
    if (message == GO1_OPEN) { start(); draw(); }
    else if (message == GO1_TIMER) tick();
    else if (message == GO1_PAINT) draw();
    else if (message == GO1_CLOSE) stop();
    else if (message == GO1_KEY_SOFT_RIGHT || message == GO1_KEY_END) { stop(); api->exit(); }
    return 1;
}
