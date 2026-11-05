/* go1.Files and go1.Net natives over the host's JvmIo (files on the card,
   packet data and TCP). Every call returns at once; Java waits with sleep. */
#include "internal.h"

#define ARG_REF(i) ((Object *)(uintptr_t)args[i])
#define I(i) ((i32)args[i])

static const JvmIo *io_of(Thread *t) {
    Jvm *vm = thread_vm(t);
    if (!vm->host.io) throw_new(t, "java/io/IOException", "no file or network access on this host");
    return vm->host.io;
}

static int path_of(Thread *t, Object *s, char *out, u32 capacity) {
    if (!s) { throw_new(t, "java/lang/NullPointerException", 0); return 0; }
    u32 n = string_utf8(thread_vm(t), s, out, capacity-1);
    out[n] = 0;
    return 1;
}

/* Array range check; returns the data pointer or 0 after throwing. */
static u8 *range(Thread *t, Object *array, i32 offset, i32 length) {
    Array *a = (Array *)array;
    if (!a) { throw_new(t, "java/lang/NullPointerException", 0); return 0; }
    if (offset < 0 || length < 0 || (u32)offset+(u32)length > a->length) {
        throw_new(t, "java/lang/IndexOutOfBoundsException", 0);
        return 0;
    }
    return ARRAY_DATA(a)+offset;
}

/* ---------- go1.Files ---------- */

/* open(String path, int mode) -> handle or 0 */
static int files_open(Thread *t, u32 *args, u32 *result) {
    const JvmIo *io = io_of(t);
    char path[300];
    if (!io || !path_of(t, ARG_REF(0), path, sizeof(path))) return 1;
    result[0] = io->open(thread_vm(t)->host.context, path, I(1));
    return 0;
}

/* read(int file, byte[] b, int offset, int length) -> bytes, -1 on error */
static int files_read(Thread *t, u32 *args, u32 *result) {
    const JvmIo *io = io_of(t);
    u8 *data = io ? range(t, ARG_REF(1), I(2), I(3)) : 0;
    if (!data && (!io || I(3))) return 1;
    result[0] = io->read(thread_vm(t)->host.context, args[0], data, (u32)I(3));
    return 0;
}

/* write(int file, byte[] b, int offset, int length) -> bytes, -1 on error */
static int files_write(Thread *t, u32 *args, u32 *result) {
    const JvmIo *io = io_of(t);
    u8 *data = io ? range(t, ARG_REF(1), I(2), I(3)) : 0;
    if (!data && (!io || I(3))) return 1;
    result[0] = io->write(thread_vm(t)->host.context, args[0], data, (u32)I(3));
    return 0;
}

static int files_seek(Thread *t, u32 *args, u32 *result) {
    const JvmIo *io = io_of(t);
    if (!io) return 1;
    result[0] = (u32)io->seek(thread_vm(t)->host.context, args[0], args[1]);
    return 0;
}

static int files_size(Thread *t, u32 *args, u32 *result) {
    const JvmIo *io = io_of(t);
    if (!io) return 1;
    result[0] = io->size(thread_vm(t)->host.context, args[0]);
    return 0;
}

static int files_close(Thread *t, u32 *args, u32 *result) {
    (void)result;
    const JvmIo *io = io_of(t);
    if (!io) return 1;
    io->close(thread_vm(t)->host.context, args[0]);
    return 0;
}

static int files_remove(Thread *t, u32 *args, u32 *result) {
    const JvmIo *io = io_of(t);
    char path[300];
    if (!io || !path_of(t, ARG_REF(0), path, sizeof(path))) return 1;
    result[0] = io->remove(thread_vm(t)->host.context, path) == 0;
    return 0;
}

static int files_make_dir(Thread *t, u32 *args, u32 *result) {
    const JvmIo *io = io_of(t);
    char path[300];
    if (!io || !path_of(t, ARG_REF(0), path, sizeof(path))) return 1;
    result[0] = io->make_dir(thread_vm(t)->host.context, path) == 0;
    return 0;
}

/* ---------- go1.Net ---------- */

static const JvmIo *net_of(Thread *t) {
    const JvmIo *io = io_of(t);
    if (io && !io->net_start) { throw_new(t, "java/io/IOException", "no network on this host"); return 0; }
    return io;
}

static int net_start(Thread *t, u32 *args, u32 *result) {
    (void)args;
    const JvmIo *io = net_of(t);
    if (!io) return 1;
    result[0] = io->net_start(thread_vm(t)->host.context);
    return 0;
}

static int net_state(Thread *t, u32 *args, u32 *result) {
    (void)args;
    const JvmIo *io = net_of(t);
    if (!io) return 1;
    result[0] = io->net_state(thread_vm(t)->host.context);
    return 0;
}

static int net_dns(Thread *t, u32 *args, u32 *result) {
    const JvmIo *io = net_of(t);
    char host[256];
    if (!io || !path_of(t, ARG_REF(0), host, sizeof(host))) return 1;
    result[0] = io->dns(thread_vm(t)->host.context, host);
    return 0;
}

static int net_open(Thread *t, u32 *args, u32 *result) {
    (void)args;
    const JvmIo *io = net_of(t);
    if (!io) return 1;
    result[0] = io->tcp_open(thread_vm(t)->host.context);
    return 0;
}

/* connect(int socket, int ipv4, int port) -> 0 connected, -2 in progress, -1 failed */
static int net_connect(Thread *t, u32 *args, u32 *result) {
    const JvmIo *io = net_of(t);
    if (!io) return 1;
    result[0] = io->tcp_connect(thread_vm(t)->host.context, args[0], args[1], args[2]);
    return 0;
}

/* send/recv(int socket, byte[] b, int offset, int length) -> bytes, -2 again, -1 error; recv 0 at the end */
static int net_send(Thread *t, u32 *args, u32 *result) {
    const JvmIo *io = net_of(t);
    u8 *data = io ? range(t, ARG_REF(1), I(2), I(3)) : 0;
    if (!data && (!io || I(3))) return 1;
    result[0] = io->tcp_send(thread_vm(t)->host.context, args[0], data, (u32)I(3));
    return 0;
}

static int net_recv(Thread *t, u32 *args, u32 *result) {
    const JvmIo *io = net_of(t);
    u8 *data = io ? range(t, ARG_REF(1), I(2), I(3)) : 0;
    if (!data && (!io || I(3))) return 1;
    result[0] = io->tcp_recv(thread_vm(t)->host.context, args[0], data, (u32)I(3));
    return 0;
}

static int net_close(Thread *t, u32 *args, u32 *result) {
    (void)result;
    const JvmIo *io = net_of(t);
    if (!io) return 1;
    io->tcp_close(thread_vm(t)->host.context, args[0]);
    return 0;
}

typedef struct IoNative { const char *cls, *name, *desc; Native fn; } IoNative;

static const IoNative io_natives[] = {
    {"go1/Files", "open", "(Ljava/lang/String;I)I", files_open},
    {"go1/Files", "read", "(I[BII)I", files_read},
    {"go1/Files", "write", "(I[BII)I", files_write},
    {"go1/Files", "seek", "(II)I", files_seek},
    {"go1/Files", "size", "(I)I", files_size},
    {"go1/Files", "close", "(I)V", files_close},
    {"go1/Files", "remove", "(Ljava/lang/String;)Z", files_remove},
    {"go1/Files", "makeDir", "(Ljava/lang/String;)Z", files_make_dir},
    {"go1/Net", "start", "()I", net_start},
    {"go1/Net", "state", "()I", net_state},
    {"go1/Net", "dns", "(Ljava/lang/String;)I", net_dns},
    {"go1/Net", "open", "()I", net_open},
    {"go1/Net", "connect", "(III)I", net_connect},
    {"go1/Net", "send", "(I[BII)I", net_send},
    {"go1/Net", "recv", "(I[BII)I", net_recv},
    {"go1/Net", "close", "(I)V", net_close},
};

Native io_native_for(Utf8 class_name, Utf8 name, Utf8 desc) {
    for (u32 i = 0; i < sizeof(io_natives)/sizeof(io_natives[0]); i++)
        if (utf8_equals(class_name, io_natives[i].cls) && utf8_equals(name, io_natives[i].name) &&
            utf8_equals(desc, io_natives[i].desc))
            return io_natives[i].fn;
    return 0;
}
