/* HELLO.GO1: the smallest useful GO1N program (README).
   go1-title: Привет
   A square moved by the arrows, a counter on the timer, right soft key: exit. */
#include "go1.h"

static const Go1Api *api;
static go1_u32 x = 80, y = 100, ticks;

static void draw(void) {
    char text[] = "ticks 0000";
    go1_u32 n = ticks;
    for (int i = 9; i >= 6; i--) { text[i] = (char)('0'+n%10); n /= 10; }
    api->fill(0, 0, 176, 176, 0x0000);          /* black, RGB565 */
    api->text(10, "HELLO GO1", 0xffe0);         /* centred line at y = 10 */
    api->text(30, text, 0xffff);
    api->fill(x, y, 16, 16, 0x07ff);            /* cyan square */
    api->flush(0, 0, 176, 176);                 /* to the LCD */
}

__attribute__((used, section(".entry")))
go1_u32 go1_main(const Go1Api *program_api, go1_u32 message, go1_u32 parameter) {
    (void)parameter;
    api = program_api;
    if (api->version != GO1_API_VERSION) return 0;
    if (message == GO1_OPEN) api->timer(1000, 1);  /* GO1_TIMER every second */
    else if (message == GO1_TIMER) ticks++;
    else if (message == GO1_CLOSE) return 1;       /* nothing allocated, nothing to free */
    else if (message == GO1_KEY_SOFT_RIGHT || message == GO1_KEY_END) { api->exit(); return 1; }
    else if (GO1_IS_KEY(message) && GO1_KIND(message) != GO1_RELEASE) {
        switch (GO1_CODE(message)) {
        case GO1_UP: if (y >= 30) y -= 4; break;
        case GO1_DOWN: if (y+16 <= 172) y += 4; break;
        case GO1_LEFT: if (x >= 4) x -= 4; break;
        case GO1_RIGHT: if (x+16 <= 172) x += 4; break;
        case GO1_OK: x = 80; y = 100; break;
        }
    }
    draw();
    return 1;
}
