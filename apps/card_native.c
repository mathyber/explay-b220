/* B22N demo: App Heap, file API, relocated data and key handling. */
#include "b22n.h"

typedef b22_u32 u32;

static const char *const labels[] = {"NATIVE B22N", "HEAP 1024 KB", "HEAP FAILED"};
static u32 x = 76, y = 90, presses, heap_ok, list_size;

static void number(char *out, const char *prefix, u32 value) {
    char digits[11];
    u32 count = 0;
    do { digits[count++] = (char)('0'+value%10); value /= 10; } while (value);
    while (*prefix) *out++ = *prefix++;
    while (count) *out++ = digits[--count];
    *out = 0;
}

static u32 heap_test(const B22Api *api) {
    u32 size = 1u << 20;
    unsigned char *block = api->alloc(size);
    if (!block) return 0;
    for (u32 i = 0; i < size; i += 4096) block[i] = (unsigned char)(i >> 12);
    u32 ok = 1;
    for (u32 i = 0; i < size; i += 4096) ok &= block[i] == (unsigned char)(i >> 12);
    api->free(block);
    return ok;
}

static void draw(const B22Api *api) {
    char line[24];
    api->fill(0, 0, 176, 176, 0x0010);
    api->text(8, labels[0], 0xffff);
    api->text(30, labels[heap_ok ? 1 : 2], heap_ok ? 0x07e0 : 0xf800);
    number(line, "APPS.LST ", list_size);
    api->text(52, line, 0xffe0);
    number(line, "KEYS ", presses);
    api->text(150, line, 0xffff);
    api->fill(x, y, 24, 24, 0x07ff);
}

__attribute__((used, section(".entry")))
u32 b22n_main(const B22Api *api, u32 message, u32 parameter) {
    (void)parameter;
    if (api->version != B22N_API_VERSION) return 0;
    if (message == B22N_OPEN) {
        heap_ok = heap_test(api);
        u32 handle = api->open("E:\\APPS.LST");
        list_size = 0;
        if (handle) { list_size = api->file_size(handle); api->close(handle); }
    } else if (message == B22N_KEY_UP && y >= 74) { y -= 4; presses++; }
    else if (message == B22N_KEY_DOWN && y <= 120) { y += 4; presses++; }
    else if (message == B22N_KEY_LEFT && x >= 4) { x -= 4; presses++; }
    else if (message == B22N_KEY_RIGHT && x <= 148) { x += 4; presses++; }
    else if (message == B22N_KEY_OK) { api->exit(); return 1; }
    draw(api);
    return 1;
}
