/* NETTEST.GO1: packet data, DNS and a plain HTTP request through the GO1 API.
   go1-title: Тест сети
   OK repeats, the right soft key leaves. */
#include "go1.h"

typedef go1_u32 u32;

static const Go1Api *api;
static u32 step, sock, address, ticks, received;
static char line[64], status[40];
static const char request[] = "GET / HTTP/1.0\r\nHost: example.org\r\nConnection: close\r\n\r\n";

static void say(const char *text) {
    u32 i = 0;
    while (text[i] && i < sizeof(status)-1) { status[i] = text[i]; i++; }
    status[i] = 0;
}

static void number(char *out, u32 value) {
    char digits[12];
    u32 n = 0;
    do { digits[n++] = (char)('0'+value%10); value /= 10; } while (value);
    while (n) *out++ = digits[--n];
    *out = 0;
}

static void draw(void) {
    char count[16];
    api->fill(0, 0, 176, 176, 0);
    api->text(10, "NET TEST", 0xffe0);
    api->text(40, status, 0xffff);
    number(count, received);
    api->text(64, count, 0x07ff);
    api->text(90, line, 0xffff);
}

static void begin(void) {
    step = 1; ticks = 0; received = 0; line[0] = 0; sock = 0;
    say("GPRS...");
    api->net_start();
    api->timer(100, 1);
}

static void tick(void) {
    ticks++;
    if (step == 1) {
        if (api->net_state() == GO1_NET_READY) { step = 2; say("DNS..."); }
        else if (ticks > 600) { step = 0; say("GPRS failed"); }
    } else if (step == 2) {
        address = api->dns("example.org");
        if (address) { sock = api->tcp_open(); step = sock ? 3 : 0; say(sock ? "Connecting..." : "No socket"); }
        else if (ticks > 900) { step = 0; say("DNS failed"); }
    } else if (step == 3) {
        u32 r = api->tcp_connect(sock, address, 80);
        if (r == 0) { step = 4; say("Sending..."); }
        else if (r == GO1_ERROR) { step = 0; say("Connect failed"); }
    } else if (step == 4) {
        u32 r = api->tcp_send(sock, request, sizeof(request)-1);
        if (r == sizeof(request)-1) { step = 5; say("Receiving..."); }
        else if (r != GO1_AGAIN) { step = 0; say("Send failed"); }
    } else if (step == 5) {
        char buffer[512];
        for (;;) {
            u32 r = api->tcp_recv(sock, buffer, sizeof(buffer));
            if (r == GO1_AGAIN) break;
            if (r == GO1_ERROR || r == 0) { step = 0; say(r ? "Receive error" : "Done"); break; }
            if (!received) {
                u32 n = 0;
                while (n < r && n < sizeof(line)-1 && buffer[n] != '\r') { line[n] = buffer[n]; n++; }
                line[n] = 0;
            }
            received += r;
        }
    }
    if (!step) {
        api->timer(0, 0);
        if (sock) { api->tcp_close(sock); sock = 0; }
    }
    draw();
}

__attribute__((used, section(".entry")))
u32 go1_main(const Go1Api *program_api, u32 message, u32 parameter) {
    (void)parameter;
    api = program_api;
    if (api->size < sizeof(Go1Api)) {
        if (message == GO1_OPEN || message == GO1_PAINT) { api->fill(0, 0, 176, 176, 0); api->text(60, "Needs a newer firmware", 0xffff); }
        return 1;
    }
    if (message == GO1_OPEN) { begin(); draw(); }
    else if (message == GO1_TIMER) tick();
    else if (message == GO1_PAINT) draw();
    else if (message == GO1_KEY_OK && !step) { begin(); draw(); }
    else if (message == GO1_CLOSE && sock) { api->tcp_close(sock); sock = 0; }
    return 1;
}
