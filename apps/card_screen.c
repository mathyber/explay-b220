/* GO1N demo: draw straight into the screen buffer, flush it, animate by timer. */
#include "go1.h"

typedef go1_u32 u32;
typedef unsigned short u16;

static u32 bar = 100, step = 4;

static void gradient(u16 *screen) {
    u16 red[GO1_SCREEN_WIDTH];
    for (u32 x = 0; x < GO1_SCREEN_WIDTH; x++) red[x] = (u16)((x*31/175) << 11);
    for (u32 y = 0; y < GO1_SCREEN_HEIGHT; y++) {
        u16 green = (u16)(((y*63/219) << 5) | 8);
        for (u32 x = 0; x < GO1_SCREEN_WIDTH; x++) screen[y*GO1_SCREEN_WIDTH+x] = red[x] | green;
    }
}

static void draw_bar(u16 *screen, u32 top, u16 color) {
    for (u32 y = top; y < top+12; y++)
        for (u32 x = 16; x < 160; x++) screen[y*GO1_SCREEN_WIDTH+x] = color;
}

__attribute__((used, section(".entry")))
u32 go1_main(const Go1Api *api, u32 message, u32 parameter) {
    (void)parameter;
    if (api->version != GO1_API_VERSION || api->size < __builtin_offsetof(Go1Api, alloc_static) || message == GO1_CLOSE) return 0;
    u16 *screen = api->screen();
    if (message == GO1_OPEN) api->timer(500, 1);
    if (message == GO1_KEY_OK || message == GO1_KEY_SOFT_RIGHT) { api->exit(); return 1; }
    if (message == GO1_KEY_UP) step = 0;
    if (message == GO1_KEY_DOWN) step = 4;
    if (message == GO1_TIMER) {
        bar += step;
        if (bar > 200) bar = 0;
    }
    gradient(screen);
    draw_bar(screen, bar, 0xffff);
    api->flush(0, 0, GO1_SCREEN_WIDTH, GO1_SCREEN_HEIGHT);
    return 1;
}
