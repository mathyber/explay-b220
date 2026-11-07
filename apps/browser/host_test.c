/* BROWSER.GO1 on the PC for tests (tools/build_browser.py build_host): the
   whole browser.c with the GO1 API replaced by Windows sockets and stubs.

   browser_host.exe command...
     load URL        open a page (as from the address editor) and wait for it
     page            print the page: text lines, [n] before link n, {n:value} for fields
     set N TEXT      put TEXT into field link N (as the field editor does)
     ok N            select link N and press OK; wait for any page it loads
     request         print the last request sent
     cookies         print the cookies kept
     status          print the status line */
#include <winsock2.h>
#include <ws2tcpip.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

static unsigned char host_ram[0x10000];
#define ENTROPY_RAM host_ram
#include "go1.h"
#include "bearssl.h"  /* before the renames: its inline functions use the real ones */

/* browser.c brings its own C library functions: renamed here, the host's stay. */
#define memcpy browser_memcpy
#define memmove browser_memmove
#define memset browser_memset
#define memcmp browser_memcmp
#define strlen browser_strlen
#include "browser.c"
#undef memcpy
#undef memmove
#undef memset
#undef memcmp
#undef strlen

static u16 host_screen[W*H];
static SOCKET sockets[16];

static void *h_alloc(go1_u32 size) { return malloc(size); }
static void h_free(void *p) { free(p); }
static go1_u32 h_none_path(const char *path) { (void)path; return 0; }
static go1_u32 h_none_wide(const unsigned short *path) { (void)path; return 0; }
static go1_u32 h_size(go1_u32 h) { (void)h; return GO1_ERROR; }
static go1_u32 h_read(go1_u32 h, void *b, go1_u32 n) { (void)h; (void)b; (void)n; return GO1_ERROR; }
static void h_close(go1_u32 h) { (void)h; }
static void h_fill(go1_u32 x, go1_u32 y, go1_u32 w, go1_u32 h, go1_u32 c) { (void)x; (void)y; (void)w; (void)h; (void)c; }
static void h_text(go1_u32 y, const char *s, go1_u32 c) { (void)y; (void)s; (void)c; }
static int exited;
static void h_exit(void) { exited = 1; }
static go1_u32 h_heap_free(void) { return 4*1024*1024; }
static unsigned short *h_screen(void) { return host_screen; }
static void h_flush(go1_u32 x, go1_u32 y, go1_u32 w, go1_u32 h) { (void)x; (void)y; (void)w; (void)h; }
static go1_u32 h_timer(go1_u32 ms, go1_u32 periodic) { (void)ms; (void)periodic; return 1; }
static go1_u32 h_ms(void) { return (go1_u32)GetTickCount(); }
static go1_u32 h_seek(go1_u32 h, go1_u32 o) { (void)h; (void)o; return GO1_ERROR; }
static void h_text_wide(go1_u32 y, const unsigned short *t, go1_u32 c) { (void)y; (void)t; (void)c; }
static go1_u32 h_find_first(const unsigned short *p, Go1FindEntry *e) { (void)p; (void)e; return 0; }
static go1_u32 h_find_next(go1_u32 s, Go1FindEntry *e) { (void)s; (void)e; return 1; }
static void h_find_close(go1_u32 s) { (void)s; }
static void h_text_at(go1_u32 x, go1_u32 y, const unsigned short *t, go1_u32 c) { (void)x; (void)y; (void)t; (void)c; }
static go1_u32 h_net_start(void) { return 1; }
static go1_u32 h_net_state(void) { return GO1_NET_READY; }
static go1_u32 h_dns(const char *host) {
    struct addrinfo hints = {0}, *result = 0;
    hints.ai_family = AF_INET;
    if (getaddrinfo(host, 0, &hints, &result) || !result) return 0;
    go1_u32 a = ntohl(((struct sockaddr_in *)result->ai_addr)->sin_addr.s_addr);
    freeaddrinfo(result);
    return a;
}
static go1_u32 h_tcp_open(void) {
    for (go1_u32 i = 1; i < 16; i++)
        if (!sockets[i]) { sockets[i] = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP); return sockets[i] == INVALID_SOCKET ? (sockets[i] = 0) : i; }
    return 0;
}
static go1_u32 h_tcp_connect(go1_u32 s, go1_u32 ip, go1_u32 port) {
    struct sockaddr_in a = {0};
    a.sin_family = AF_INET; a.sin_port = htons((u_short)port); a.sin_addr.s_addr = htonl(ip);
    if (connect(sockets[s], (struct sockaddr *)&a, sizeof a)) return GO1_ERROR;
    u_long on = 1;
    ioctlsocket(sockets[s], FIONBIO, &on);
    return 0;
}
static go1_u32 h_tcp_send(go1_u32 s, const void *d, go1_u32 n) {
    int r = send(sockets[s], d, (int)n, 0);
    if (r < 0) return WSAGetLastError() == WSAEWOULDBLOCK ? GO1_AGAIN : GO1_ERROR;
    return (go1_u32)r;
}
static go1_u32 h_tcp_recv(go1_u32 s, void *b, go1_u32 n) {
    int r = recv(sockets[s], b, (int)n, 0);
    if (r < 0) return WSAGetLastError() == WSAEWOULDBLOCK ? GO1_AGAIN : GO1_ERROR;
    return (go1_u32)r;
}
static void h_tcp_close(go1_u32 s) { if (s < 16 && sockets[s]) { closesocket(sockets[s]); sockets[s] = 0; } }
static go1_u32 h_clock(void) { return (go1_u32)(time(0)-315532800); }
static go1_u32 h_write(go1_u32 h, const void *d, go1_u32 n) { (void)h; (void)d; (void)n; return GO1_ERROR; }
static go1_u32 h_rename(const unsigned short *a, const unsigned short *b) { (void)a; (void)b; return 1; }
static go1_u32 h_one_wide(const unsigned short *p) { (void)p; return 1; }

static Go1Api host_api;

static void wait_page(void) {
    for (int i = 0; i < 20000 && loading(); i++) {
        go1_main(&host_api, GO1_TIMER, 0);
        Sleep(1);
    }
}

static void print_utf8(const u16 *s, u32 n) {
    for (u32 i = 0; i < n; i++) {
        u32 c = s[i];
        if (c < 0x80) putchar((int)c);
        else if (c < 0x800) { putchar(0xc0 | (c >> 6)); putchar(0x80 | (c & 63)); }
        else { putchar(0xe0 | (c >> 12)); putchar(0x80 | ((c >> 6) & 63)); putchar(0x80 | (c & 63)); }
    }
}

static void print_page(void) {
    for (u32 i = 0; i < line_count; i++) {
        const Line *l = &lines[i];
        for (u32 r = 0; r < l->count; r++) {
            const Run *run = &runs[l->run+r];
            if (run->link && links[run->link-1].field) {
                const Field *f = &fields[links[run->link-1].field-1];
                if (f->type != T_SUBMIT) {
                    if (text[run->start] == ' ') putchar(' ');
                    printf("{%u:", run->link);
                    if (f->type == T_CHECK || f->type == T_RADIO) printf("%s", f->checked ? "x" : " ");
                    else printf("%s", f->type == T_SELECT ? option_at(f, f->option, 1) : pooled(f->value));
                    printf("}");
                    continue;
                }
            }
            if (run->link && (r == 0 || runs[l->run+r-1].link != run->link)) printf("[%u]", run->link);
            print_utf8(text+run->start, run->length);
        }
        putchar('\n');
    }
}

int main(int argc, char **argv) {
    WSADATA wsa;
    WSAStartup(MAKEWORD(2, 2), &wsa);
    Go1Api *a = &host_api;
    a->version = GO1_API_VERSION; a->size = sizeof(Go1Api);
    a->alloc = h_alloc; a->free = h_free; a->open = h_none_path; a->file_size = h_size; a->read = h_read; a->close = h_close;
    a->fill = h_fill; a->text = h_text; a->exit = h_exit; a->heap_free = h_heap_free; a->screen = h_screen; a->flush = h_flush;
    a->timer = h_timer; a->ms = h_ms; a->seek = h_seek; a->open_wide = h_none_wide; a->text_wide = h_text_wide;
    a->find_first = h_find_first; a->find_next = h_find_next; a->find_close = h_find_close; a->text_at = h_text_at;
    a->net_start = h_net_start; a->net_state = h_net_state; a->dns = h_dns; a->tcp_open = h_tcp_open;
    a->tcp_connect = h_tcp_connect; a->tcp_send = h_tcp_send; a->tcp_recv = h_tcp_recv; a->tcp_close = h_tcp_close;
    a->clock = h_clock; a->create = h_none_wide; a->open_update = h_none_wide; a->open_append = h_none_wide;
    a->write = h_write; a->remove = h_one_wide; a->make_dir = h_one_wide; a->rename = h_rename; a->alloc_static = h_alloc;
    go1_main(a, GO1_OPEN, 0);
    for (int i = 1; i < argc; i++) {
        const char *c = argv[i];
        if (!strcmp(c, "load") && i+1 < argc) { load(argv[++i], 1); wait_page(); }
        else if (!strcmp(c, "page")) print_page();
        else if (!strcmp(c, "set") && i+2 < argc) {
            u32 n = (u32)atoi(argv[++i]);
            const char *v = argv[++i];
            if (n >= 1 && n <= link_count && links[n-1].field) {
                Field *f = &fields[links[n-1].field-1];
                copy(fpool+f->value, v, f->cap+1);
            } else printf("no field %u\n", n);
        } else if (!strcmp(c, "ok") && i+1 < argc) {
            selected = (u32)atoi(argv[++i]);
            go1_main(a, GO1_KEY_OK, 0);
            wait_page();
        } else if (!strcmp(c, "request")) { fwrite(request, 1, request_len, stdout); putchar('\n'); }
        else if (!strcmp(c, "cookies")) {
            for (u32 k = 0; k < COOKIE_MAX; k++)
                if (cookies[k].used) printf("%s=%s domain=%s path=%s%s\n", cookies[k].name, cookies[k].value, cookies[k].domain,
                                            cookies[k].path, cookies[k].host_only ? " host" : "");
        } else if (!strcmp(c, "status")) { print_utf8(status, wlen(status)); putchar('\n'); }
        printf("--\n");
        fflush(stdout);
    }
    return 0;
}
