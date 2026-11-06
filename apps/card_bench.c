/* BENCH.GO1: CPU speed, for calibrating the emulator against the real B220.
   go1-title: Бенчмарк
   Each test runs for about one second by the firmware millisecond counter and
   reports work per millisecond. The same program in the emulator gives the
   scale: phone result / emulator result = how much faster the phone is.
   Results are shown and written to E:\BENCH.TXT. OK runs again. */
#include "go1.h"

typedef go1_u32 u32;
typedef unsigned short u16;

static const Go1Api *api;
static char lines[6][32];
static u32 line_count;

/* Integer work in RAM (Thumb): a mixing loop the compiler cannot remove. */
static u32 __attribute__((noinline)) integer_rounds(u32 rounds, u32 seed) {
    for (u32 i = 0; i < rounds; i++) {
        seed ^= seed << 13;
        seed ^= seed >> 17;
        seed ^= seed << 5;
        seed += i;
    }
    return seed;
}

static u32 buffer_a[1024], buffer_b[1024];

static void __attribute__((noinline)) copy_words(u32 *dst, const u32 *src, u32 count) {
    for (u32 i = 0; i < count; i++) dst[i] = src[i];
}

static void number(char *out, u32 value) {
    char digits[12];
    u32 n = 0;
    do { digits[n++] = (char)('0'+value%10); value /= 10; } while (value);
    while (n) *out++ = digits[--n];
    *out = 0;
}

static void report(const char *label, u32 value, const char *unit) {
    char *line = lines[line_count++];
    u32 n = 0;
    while (*label) line[n++] = *label++;
    number(line+n, value);
    while (line[n]) n++;
    while (*unit && n < 30) line[n++] = *unit++;
    line[n] = 0;
}

/* Calls fn(batch) until about 1000 ms pass; returns batches per second. */
static u32 measure(void (*fn)(u32), u32 batch) {
    u32 start = api->ms(), count = 0;
    while (api->ms()-start < 1000) { fn(batch); count++; }
    u32 elapsed = api->ms()-start;
    return count*1000/(elapsed ? elapsed : 1);
}

static volatile u32 sink;
static void run_integer(u32 rounds) { sink = integer_rounds(rounds, sink | 1); }
static void run_copy(u32 words) { copy_words(buffer_b, buffer_a, words); copy_words(buffer_a, buffer_b, words); }
/* heap_free walks the firmware memory pool in the loader, code that runs from flash. */
static void run_flash(u32 calls) { for (u32 i = 0; i < calls; i++) sink += api->heap_free(); }

static void save(void) {
    static const u16 path[] = {'E', ':', '\\', 'B', 'E', 'N', 'C', 'H', '.', 'T', 'X', 'T', 0};
    if (api->size < __builtin_offsetof(Go1Api, alloc_static)) return;
    u32 h = api->open_append(path);
    if (!h) return;
    for (u32 i = 0; i < line_count; i++) {
        u32 n = 0;
        while (lines[i][n]) n++;
        api->write(h, lines[i], n);
        api->write(h, i+1 < line_count ? "; " : "\r\n", 2);
    }
    api->close(h);
}

static void bench(void) {
    line_count = 0;
    api->fill(0, 0, 176, 176, 0);
    api->text(60, "Measuring...", 0xffff);
    api->flush(0, 0, 176, 220);
    report("int ", measure(run_integer, 1000), "k rounds/s");
    report("copy ", measure(run_copy, 1024)*8, " KB/s");
    report("flash ", measure(run_flash, 10)*10, " calls/s");
    save();
}

static void draw(void) {
    api->fill(0, 0, 176, 176, 0);
    api->text(10, "BENCHMARK", 0xffe0);
    for (u32 i = 0; i < line_count; i++) api->text(40+i*24, lines[i], 0xffff);
    api->text(130, "OK: again", 0x07ff);
    api->text(150, "Right soft: exit", 0x07ff);
}

__attribute__((used, section(".entry")))
u32 go1_main(const Go1Api *program_api, u32 message, u32 parameter) {
    (void)parameter;
    api = program_api;
    if (message == GO1_OPEN || message == GO1_KEY_OK) { bench(); draw(); }
    else if (message == GO1_PAINT) draw();
    else if (message == GO1_KEY_SOFT_RIGHT) api->exit();
    return 1;
}
