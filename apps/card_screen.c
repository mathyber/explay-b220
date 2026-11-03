/* B22N demo: draw straight into the screen buffer, flush it, animate by timer. */
#include "b22n.h"

typedef b22_u32 u32;
typedef unsigned short u16;

static u32 bar = 100, step = 4;

static void gradient(u16 *screen) {
    u16 red[B22N_SCREEN_WIDTH];
    for (u32 x = 0; x < B22N_SCREEN_WIDTH; x++) red[x] = (u16)((x*31/175) << 11);
    for (u32 y = 0; y < B22N_SCREEN_HEIGHT; y++) {
        u16 green = (u16)(((y*63/219) << 5) | 8);
        for (u32 x = 0; x < B22N_SCREEN_WIDTH; x++) screen[y*B22N_SCREEN_WIDTH+x] = red[x] | green;
    }
}

static void draw_bar(u16 *screen, u32 top, u16 color) {
    for (u32 y = top; y < top+12; y++)
        for (u32 x = 16; x < 160; x++) screen[y*B22N_SCREEN_WIDTH+x] = color;
}

__attribute__((used, section(".entry")))
u32 b22n_main(const B22Api *api, u32 message, u32 parameter) {
    (void)parameter;
    if (api->version != B22N_API_VERSION || api->size < sizeof(B22Api)) return 0;
    u16 *screen = api->screen();
    if (message == B22N_OPEN) api->timer(500, 1);
    if (message == B22N_KEY_OK) { api->exit(); return 1; }
    if (message == B22N_KEY_UP) step = 0;
    if (message == B22N_KEY_DOWN) step = 4;
    if (message == B22N_TIMER) {
        bar += step;
        if (bar > 200) bar = 0;
    }
    gradient(screen);
    draw_bar(screen, bar, 0xffff);
    api->flush(0, 0, B22N_SCREEN_WIDTH, B22N_SCREEN_HEIGHT);
    return 1;
}
