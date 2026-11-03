/* Native methods, strings, class mirrors, VM creation and the main launcher. */
#include "internal.h"

typedef union { u64 l; double d; u32 w[2]; } Wide;
typedef union { u32 i; float f; } Narrow;

/* ---------- strings ---------- */

static Array *string_chars(Jvm *vm, Object *s, u32 *offset, u32 *count) {
    u32 *fields = (u32 *)s;
    *offset = fields[vm->string_offset/4];
    *count = fields[vm->string_count/4];
    return (Array *)(uintptr_t)fields[vm->string_value/4];
}

/* Modified UTF-8 (class files) to a new java.lang.String. */
Object *new_string(Jvm *vm, const u8 *utf8, u32 length) {
    u32 count = 0;
    for (u32 i = 0; i < length; count++) i += utf8[i] < 0x80 ? 1 : utf8[i] < 0xe0 ? 2 : 3;
    Array *chars = new_array(vm, vm->char_array, count);
    if (!chars) return 0;
    u16 *d = (u16 *)ARRAY_DATA(chars);
    for (u32 i = 0, n = 0; i < length; n++) {
        u8 c = utf8[i];
        if (c < 0x80) { d[n] = c; i++; }
        else if (c < 0xe0) { d[n] = (u16)(((c & 0x1f) << 6) | (utf8[i+1] & 0x3f)); i += 2; }
        else { d[n] = (u16)(((c & 0x0f) << 12) | ((utf8[i+1] & 0x3f) << 6) | (utf8[i+2] & 0x3f)); i += 3; }
    }
    push_root(vm, (Object *)chars);
    Object *s = new_object(vm, vm->string_class);
    pop_roots(vm, 1);
    if (!s) return 0;
    u32 *fields = (u32 *)s;
    fields[vm->string_value/4] = (u32)(uintptr_t)chars;
    fields[vm->string_count/4] = count;
    return s;
}

Object *new_string_c(Jvm *vm, const char *text) { return new_string(vm, (const u8 *)text, jvm_strlen(text)); }

/* UTF-16 to UTF-8 (no surrogate pairing); returns bytes written. */
u32 string_utf8(Jvm *vm, Object *s, char *out, u32 capacity) {
    u32 offset, count, n = 0;
    Array *chars = string_chars(vm, s, &offset, &count);
    const u16 *c = (const u16 *)ARRAY_DATA(chars)+offset;
    for (u32 i = 0; i < count; i++) {
        u16 ch = c[i];
        if (ch < 0x80) { if (n+1 > capacity) break; out[n++] = (char)ch; }
        else if (ch < 0x800) { if (n+2 > capacity) break; out[n++] = (char)(0xc0 | ch >> 6); out[n++] = (char)(0x80 | (ch & 0x3f)); }
        else { if (n+3 > capacity) break; out[n++] = (char)(0xe0 | ch >> 12); out[n++] = (char)(0x80 | ((ch >> 6) & 0x3f)); out[n++] = (char)(0x80 | (ch & 0x3f)); }
    }
    return n;
}

static int string_equal(Jvm *vm, Object *a, Object *b) {
    u32 ao, ac, bo, bc;
    Array *x = string_chars(vm, a, &ao, &ac), *y = string_chars(vm, b, &bo, &bc);
    return ac == bc && !jvm_memcmp((u16 *)ARRAY_DATA(x)+ao, (u16 *)ARRAY_DATA(y)+bo, ac*2);
}

Object *intern(Jvm *vm, Object *s) {
    for (u32 i = 0; i < vm->interned_count; i++) if (string_equal(vm, vm->interned[i], s)) return vm->interned[i];
    if (vm->interned_count >= MAX_INTERNED) { vm_fail(vm, "too many interned strings", 0); return s; }
    vm->interned[vm->interned_count++] = s;
    return s;
}

Object *class_mirror(Jvm *vm, Class *c) {
    if (c->mirror) return c->mirror;
    Object *o = new_object(vm, vm->class_class);
    if (!o) { vm_fail(vm, "out of memory for a class mirror", 0); return 0; }
    ((u32 *)o)[vm->class_vmdata/4] = (u32)(uintptr_t)c;
    c->mirror = o;
    return o;
}

static Class *mirror_class(Jvm *vm, Object *mirror) { return (Class *)(uintptr_t)((u32 *)mirror)[vm->class_vmdata/4]; }

/* ---------- number formatting ---------- */

static u32 format_unsigned(char *out, u64 v) {
    char d[24];
    u32 n = 0, k = 0;
    do { d[n++] = (char)('0'+v%10); v /= 10; } while (v);
    while (n) out[k++] = d[--n];
    return k;
}

/* Java-like Double.toString: shortest-ish with up to 15 significant digits. */
static u32 format_double(char *out, double v, int digits) {
    u32 n = 0;
    Wide w; w.d = v;
    if (v != v) { jvm_memcpy(out, "NaN", 3); return 3; }
    if (w.w[0] == 0 && w.w[1] == 0) { jvm_memcpy(out, "0.0", 3); return 3; }
    if (v < 0 || (v == 0 && (w.l >> 63))) { out[n++] = '-'; v = -v; }
    if (v == 0) { jvm_memcpy(out+n, "0.0", 3); return n+3; }
    if (v > 1.7976931348623157e308) { jvm_memcpy(out+n, "Infinity", 8); return n+8; }
    int exponent = 0;
    double scaled = v;
    while (scaled >= 10) { scaled /= 10; exponent++; }
    while (scaled < 1) { scaled *= 10; exponent--; }
    /* digits significant digits, rounded */
    u64 mantissa = 0;
    for (int i = 0; i < digits; i++) { int d = (int)scaled; mantissa = mantissa*10+(u64)d; scaled = (scaled-d)*10; }
    if (scaled >= 5) mantissa++;
    u64 limit = 1;
    for (int i = 0; i < digits; i++) limit *= 10;
    if (mantissa >= limit) { mantissa /= 10; exponent++; }
    char d[24];
    u32 count = format_unsigned(d, mantissa);
    while (count > 1 && d[count-1] == '0') count--;
    if (exponent >= -3 && exponent < 7) {
        if (exponent < 0) {
            out[n++] = '0'; out[n++] = '.';
            for (int i = -1; i > exponent; i--) out[n++] = '0';
            jvm_memcpy(out+n, d, count); n += count;
        } else {
            for (int i = 0; i <= exponent; i++) out[n++] = (u32)i < count ? d[i] : '0';
            out[n++] = '.';
            if ((u32)exponent+1 < count) { jvm_memcpy(out+n, d+exponent+1, count-(u32)exponent-1); n += count-(u32)exponent-1; }
            else out[n++] = '0';
        }
    } else {
        out[n++] = d[0]; out[n++] = '.';
        if (count > 1) { jvm_memcpy(out+n, d+1, count-1); n += count-1; } else out[n++] = '0';
        out[n++] = 'E';
        if (exponent < 0) { out[n++] = '-'; exponent = -exponent; }
        n += format_unsigned(out+n, (u64)exponent);
    }
    return n;
}

/* ---------- natives ---------- */

#define ARG_REF(i) ((Object *)(uintptr_t)args[i])
#define RETURN_REF(o) (result[0] = (u32)(uintptr_t)(o))

static int object_hash(Thread *t, u32 *args, u32 *result) { (void)t; result[0] = args[0] >> 3; return 0; }
static int object_class(Thread *t, u32 *args, u32 *result) {
    Jvm *vm = thread_vm(t);
    RETURN_REF(class_mirror(vm, ARG_REF(0)->cls));
    return 0;
}

static int system_arraycopy(Thread *t, u32 *args, u32 *result) {
    (void)result;
    Array *src = (Array *)ARG_REF(0), *dst = (Array *)ARG_REF(2);
    i32 from = (i32)args[1], to = (i32)args[3], length = (i32)args[4];
    if (!src || !dst) { throw_new(t, "java/lang/NullPointerException", 0); return 1; }
    Class *sc = src->header.cls, *dc = dst->header.cls;
    if (!sc->dimensions || !dc->dimensions || (sc->component == 0) != (dc->component == 0) ||
        (!sc->component && sc->element_type != dc->element_type)) {
        throw_new(t, "java/lang/ArrayStoreException", 0);
        return 1;
    }
    if (length < 0 || from < 0 || to < 0 || (u32)from+(u32)length > src->length || (u32)to+(u32)length > dst->length) {
        throw_new(t, "java/lang/ArrayIndexOutOfBoundsException", 0);
        return 1;
    }
    u32 size = element_size(sc->element_type);
    if (sc->component && !is_subclass(sc->component, dc->component)) {
        u32 *s = (u32 *)ARRAY_DATA(src)+from, *d = (u32 *)ARRAY_DATA(dst)+to;
        for (i32 i = 0; i < length; i++) {
            Object *o = (Object *)(uintptr_t)s[i];
            if (o && !is_subclass(o->cls, dc->component)) { throw_new(t, "java/lang/ArrayStoreException", 0); return 1; }
            d[i] = s[i];
        }
        return 0;
    }
    jvm_memcpy(ARRAY_DATA(dst)+(u32)to*size, ARRAY_DATA(src)+(u32)from*size, (u32)length*size);
    return 0;
}

static int system_time(Thread *t, u32 *args, u32 *result) {
    (void)args;
    Jvm *vm = thread_vm(t);
    Wide w; w.l = vm->host.ms(vm->host.context);
    result[0] = w.w[0]; result[1] = w.w[1];
    return 0;
}

static int system_identity(Thread *t, u32 *args, u32 *result) { (void)t; result[0] = args[0] >> 3; return 0; }

static int system_gc(Thread *t, u32 *args, u32 *result) {
    (void)args; (void)result;
    collect(thread_vm(t));
    return 0;
}

static int runtime_free(Thread *t, u32 *args, u32 *result) {
    (void)args;
    Jvm *vm = thread_vm(t);
    Wide w; w.l = (u64)(u32)(vm->heap_end-vm->heap)-vm->used;
    result[0] = w.w[0]; result[1] = w.w[1];
    return 0;
}

static int runtime_total(Thread *t, u32 *args, u32 *result) {
    (void)args;
    Jvm *vm = thread_vm(t);
    Wide w; w.l = (u64)(u32)(vm->heap_end-vm->heap);
    result[0] = w.w[0]; result[1] = w.w[1];
    return 0;
}

static int string_intern(Thread *t, u32 *args, u32 *result) { RETURN_REF(intern(thread_vm(t), ARG_REF(0))); return 0; }

static int print_write(Thread *t, u32 *args, u32 *result) {
    (void)result;
    Jvm *vm = thread_vm(t);
    Object *s = ARG_REF(1);
    if (!s) { vm->host.write(vm->host.context, (int)args[0], "null", 4); return 0; }
    u32 offset, count, n = 0;
    Array *chars = string_chars(vm, s, &offset, &count);
    const u16 *c = (const u16 *)ARRAY_DATA(chars)+offset;
    char buffer[256];
    for (u32 i = 0; i < count; i++) {
        if (n > sizeof(buffer)-4) { vm->host.write(vm->host.context, (int)args[0], buffer, n); n = 0; }
        u16 ch = c[i];
        if (ch < 0x80) buffer[n++] = (char)ch;
        else if (ch < 0x800) { buffer[n++] = (char)(0xc0 | ch >> 6); buffer[n++] = (char)(0x80 | (ch & 0x3f)); }
        else { buffer[n++] = (char)(0xe0 | ch >> 12); buffer[n++] = (char)(0x80 | ((ch >> 6) & 0x3f)); buffer[n++] = (char)(0x80 | (ch & 0x3f)); }
    }
    if (n) vm->host.write(vm->host.context, (int)args[0], buffer, n);
    return 0;
}

static int class_name(Thread *t, u32 *args, u32 *result) {
    Jvm *vm = thread_vm(t);
    Class *c = mirror_class(vm, ARG_REF(0));
    u32 length = utf8_length(c->name);
    char name[256];
    if (length > sizeof(name)) length = sizeof(name);
    for (u32 i = 0; i < length; i++) name[i] = utf8_bytes(c->name)[i] == '/' ? '.' : utf8_bytes(c->name)[i];
    RETURN_REF(new_string(vm, (const u8 *)name, length));
    return 0;
}

static int class_is_instance(Thread *t, u32 *args, u32 *result) {
    Jvm *vm = thread_vm(t);
    Object *o = ARG_REF(1);
    result[0] = o && is_subclass(o->cls, mirror_class(vm, ARG_REF(0)));
    return 0;
}

static int class_is_array(Thread *t, u32 *args, u32 *result) {
    result[0] = mirror_class(thread_vm(t), ARG_REF(0))->dimensions != 0;
    return 0;
}

static int class_is_interface(Thread *t, u32 *args, u32 *result) {
    result[0] = (mirror_class(thread_vm(t), ARG_REF(0))->access & ACC_INTERFACE) != 0;
    return 0;
}

static int float_to_bits(Thread *t, u32 *args, u32 *result) { (void)t; result[0] = args[0]; return 0; }
static int double_to_bits(Thread *t, u32 *args, u32 *result) { (void)t; result[0] = args[0]; result[1] = args[1]; return 0; }

static int float_string(Thread *t, u32 *args, u32 *result) {
    Narrow v; v.i = args[0];
    char text[40];
    u32 n = format_double(text, (double)v.f, 7);
    RETURN_REF(new_string(thread_vm(t), (const u8 *)text, n));
    return 0;
}

static int double_string(Thread *t, u32 *args, u32 *result) {
    Wide v; v.w[0] = args[0]; v.w[1] = args[1];
    char text[40];
    u32 n = format_double(text, v.d, 15);
    RETURN_REF(new_string(thread_vm(t), (const u8 *)text, n));
    return 0;
}

static int long_string(Thread *t, u32 *args, u32 *result) {
    Wide v; v.w[0] = args[0]; v.w[1] = args[1];
    char text[24];
    u32 n = 0;
    i64 s = (i64)v.l;
    if (s < 0) { text[n++] = '-'; n += format_unsigned(text+n, (u64)0-(u64)s); }
    else n += format_unsigned(text+n, (u64)s);
    RETURN_REF(new_string(thread_vm(t), (const u8 *)text, n));
    return 0;
}

static int thread_sleep(Thread *t, u32 *args, u32 *result) {
    (void)result;
    Jvm *vm = thread_vm(t);
    Wide v; v.w[0] = args[0]; v.w[1] = args[1];
    i64 ms = (i64)v.l;
    if (ms < 0) { throw_new(t, "java/lang/IllegalArgumentException", "timeout value is negative"); return 1; }
    if (ms > 0x3fffffff) ms = 0x3fffffff;
    t->wake = vm->host.ms(vm->host.context)+(u32)ms;
    t->state = THREAD_SLEEPING;
    return 0;
}

static int thread_yield(Thread *t, u32 *args, u32 *result) {
    (void)args; (void)result;
    t->wake = thread_vm(t)->host.ms(thread_vm(t)->host.context);
    t->state = THREAD_SLEEPING;
    return 0;
}

typedef struct NativeEntry { const char *cls, *name, *desc; Native fn; } NativeEntry;

static const NativeEntry natives[] = {
    {"java/lang/Object", "hashCode", "()I", object_hash},
    {"java/lang/Object", "getClass", "()Ljava/lang/Class;", object_class},
    {"java/lang/System", "arraycopy", "(Ljava/lang/Object;ILjava/lang/Object;II)V", system_arraycopy},
    {"java/lang/System", "currentTimeMillis", "()J", system_time},
    {"java/lang/System", "identityHashCode", "(Ljava/lang/Object;)I", system_identity},
    {"java/lang/System", "gc", "()V", system_gc},
    {"java/lang/Runtime", "freeMemory", "()J", runtime_free},
    {"java/lang/Runtime", "totalMemory", "()J", runtime_total},
    {"java/lang/Runtime", "gc", "()V", system_gc},
    {"java/lang/String", "intern", "()Ljava/lang/String;", string_intern},
    {"java/io/PrintStream", "write0", "(ILjava/lang/String;)V", print_write},
    {"java/lang/Class", "getName", "()Ljava/lang/String;", class_name},
    {"java/lang/Class", "isInstance", "(Ljava/lang/Object;)Z", class_is_instance},
    {"java/lang/Class", "isArray", "()Z", class_is_array},
    {"java/lang/Class", "isInterface", "()Z", class_is_interface},
    {"java/lang/Float", "floatToIntBits", "(F)I", float_to_bits},
    {"java/lang/Float", "intBitsToFloat", "(I)F", float_to_bits},
    {"java/lang/Float", "toString", "(F)Ljava/lang/String;", float_string},
    {"java/lang/Double", "doubleToLongBits", "(D)J", double_to_bits},
    {"java/lang/Double", "longBitsToDouble", "(J)D", double_to_bits},
    {"java/lang/Double", "toString", "(D)Ljava/lang/String;", double_string},
    {"java/lang/Long", "toString", "(J)Ljava/lang/String;", long_string},
    {"java/lang/Thread", "sleep", "(J)V", thread_sleep},
    {"java/lang/Thread", "yield", "()V", thread_yield},
};

Native native_for(Jvm *vm, Class *c, Utf8 name, Utf8 desc) {
    (void)vm;
    for (u32 i = 0; i < sizeof(natives)/sizeof(natives[0]); i++)
        if (utf8_equals(c->name, natives[i].cls) && utf8_equals(name, natives[i].name) && utf8_equals(desc, natives[i].desc))
            return natives[i].fn;
    return 0;
}

/* ---------- VM creation ---------- */

static u32 field_offset(Jvm *vm, Class *c, const char *name, const char *desc) {
    Field *f = c ? find_field_c(c, name, desc) : 0;
    if (!f) { vm_fail(vm, "core field missing", 0); return 0; }
    return f->offset;
}

Jvm *jvm_create(const JvmHost *host, void *memory, uint32_t bytes, uint32_t meta_bytes) {
    u8 *base = (u8 *)(((uintptr_t)memory+7) & ~(uintptr_t)7);
    bytes -= (u32)(base-(u8 *)memory);
    if (meta_bytes < 64*1024 || meta_bytes+64*1024 > bytes) return 0;
    Jvm *vm = (Jvm *)base;
    jvm_memset(vm, 0, sizeof(Jvm));
    jvm_current_vm = vm;
    vm->host = *host;
    vm->meta = vm->meta_top = base+((sizeof(Jvm)+7) & ~7u);
    vm->meta_end = base+meta_bytes;
    vm->heap = vm->meta_end;
    vm->heap_end = vm->heap+((bytes-meta_bytes) & ~(u32)(GRANULE-1));
    u32 heap_bytes = (u32)(vm->heap_end-vm->heap);
    vm->starts = meta_alloc(vm, (heap_bytes/GRANULE+31)/32*4);
    vm->marks_capacity = 4096;
    vm->marks_stack = meta_alloc(vm, vm->marks_capacity*4);
    vm->interned = meta_alloc(vm, MAX_INTERNED*sizeof(Object *));
    if (!vm->starts || !vm->marks_stack || !vm->interned) return 0;
    Object *all = (Object *)vm->heap;
    all->cls = 0;
    all->info = (heap_bytes/GRANULE) << 2;
    *(Object **)FIELDS(all) = 0;
    vm->free_list = all;
    vm->status = JVM_RUNNING;

    vm->object_class = find_class_c(vm, "java/lang/Object");
    vm->string_class = find_class_c(vm, "java/lang/String");
    vm->class_class = find_class_c(vm, "java/lang/Class");
    vm->char_array = primitive_array(vm, 'C');
    Class *throwable = find_class_c(vm, "java/lang/Throwable");
    if (!vm->object_class || !vm->string_class || !vm->class_class || !vm->char_array || !throwable) {
        if (vm->status != JVM_ERROR) vm_fail(vm, "core classes missing", 0);
        return vm;
    }
    vm->string_value = field_offset(vm, vm->string_class, "value", "[C");
    vm->string_offset = field_offset(vm, vm->string_class, "offset", "I");
    vm->string_count = field_offset(vm, vm->string_class, "count", "I");
    vm->class_vmdata = field_offset(vm, vm->class_class, "vmClass", "I");
    vm->throwable_message = field_offset(vm, throwable, "detailMessage", "Ljava/lang/String;");
    return vm;
}

int jvm_start_main(Jvm *vm, const char *class_name) {
    jvm_current_vm = vm;
    if (vm->status == JVM_ERROR) return 0;
    Class *c = find_class_c(vm, class_name);
    if (!c) { if (vm->status != JVM_ERROR) vm_fail(vm, "main class not found", 0); return 0; }
    Method *m = find_method(c, "main", "([Ljava/lang/String;)V");
    if (!m || !(m->access & ACC_STATIC)) { vm_fail(vm, "no static main(String[])", c->name); return 0; }
    Class *array = array_class(vm, vm->string_class);
    Array *arguments = array ? new_array(vm, array, 0) : 0;
    Thread *t = new_thread(vm, 0);
    if (!arguments || !t) { vm_fail(vm, "cannot start main", 0); return 0; }
    u32 args[1] = {(u32)(uintptr_t)arguments};
    if (!invoke_method(t, m, args) || !initialize_class(t, c)) return 0;
    t->state = THREAD_RUNNABLE;
    return 1;
}

const char *jvm_error(Jvm *vm) { return vm->error; }
uint32_t jvm_heap_used(Jvm *vm) { return vm->used; }
uint32_t jvm_gc_count(Jvm *vm) { return vm->gc_count; }
uint32_t jvm_uncaught(Jvm *vm) { return vm->uncaught; }
