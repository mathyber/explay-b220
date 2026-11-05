/* FILES.GO1: checks the GO1 file writing API on the card.
   go1-title: Тест файлов
   Creates E:\GO1TEST\A.TXT, writes and appends, reads it back, renames it to
   B.TXT, deletes it and shows "FILES OK" or the step that failed. OK leaves. */
#include "go1.h"

typedef go1_u32 u32;
typedef unsigned short u16;

static const Go1Api *api;
static const char *result;

static const u16 folder[] = {'E', ':', '\\', 'G', 'O', '1', 'T', 'E', 'S', 'T', 0};
static const u16 first[] = {'E', ':', '\\', 'G', 'O', '1', 'T', 'E', 'S', 'T', '\\', 'A', '.', 'T', 'X', 'T', 0};
static const u16 second[] = {'E', ':', '\\', 'G', 'O', '1', 'T', 'E', 'S', 'T', '\\', 'B', '.', 'T', 'X', 'T', 0};

static const char *check(void) {
    api->make_dir(folder);  /* fails harmlessly when it exists */
    u32 h = api->create(first);
    if (!h) return "create";
    u32 n = api->write(h, "hello", 5);
    api->close(h);
    if (n != 5) return "write";
    h = api->open_append(first);
    if (!h) return "append open";
    n = api->write(h, " world", 6);
    api->close(h);
    if (n != 6) return "append";
    h = api->open_update(first);
    if (!h) return "update open";
    if (api->seek(h, 0) || api->write(h, "J", 1) != 1) { api->close(h); return "update"; }
    api->close(h);
    char text[16];
    h = api->open_wide(first);
    if (!h) return "read open";
    u32 size = api->file_size(h);
    n = api->read(h, text, sizeof(text));
    api->close(h);
    static const char want[] = "Jello world";
    if (size != 11 || n != 11) return "size";
    for (u32 i = 0; i < 11; i++) if (text[i] != want[i]) return "content";
    if (api->rename(first, second)) return "rename";
    if (api->open_wide(first)) return "old name";
    if (api->remove(second)) return "remove";
    h = api->open_wide(second);
    if (h) { api->close(h); return "still there"; }
    return 0;
}

static void draw(void) {
    api->fill(0, 0, 176, 176, 0);
    api->text(40, "FILE API", 0xffe0);
    api->text(80, result ? "FAILED:" : "FILES OK", result ? 0xf800 : 0x07e0);
    if (result) api->text(104, result, 0xffff);
}

__attribute__((used, section(".entry")))
u32 go1_main(const Go1Api *program_api, u32 message, u32 parameter) {
    (void)parameter;
    api = program_api;
    if (api->size < sizeof(Go1Api)) {
        if (message == GO1_OPEN || message == GO1_PAINT) { api->fill(0, 0, 176, 176, 0); api->text(60, "Needs a newer firmware", 0xffff); }
        return 1;
    }
    if (message == GO1_OPEN) { result = check(); draw(); }
    else if (message == GO1_PAINT) draw();
    else if (message == GO1_KEY_OK || message == GO1_KEY_SOFT_RIGHT) api->exit();
    return 1;
}
