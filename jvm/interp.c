/* Bytecode interpreter, exceptions, monitors and the green-thread scheduler. */
#include "internal.h"
#include "fpmath.h"

typedef union { u64 l; i64 s; double d; u32 w[2]; } Wide;
typedef union { u32 i; float f; } Narrow;

static inline u64 get_wide(const u32 *p) { Wide v; v.w[0] = p[0]; v.w[1] = p[1]; return v.l; }
static inline void put_wide(u32 *p, u64 x) { Wide v; v.l = x; p[0] = v.w[0]; p[1] = v.w[1]; }
static inline float as_float(u32 x) { Narrow v; v.i = x; return v.f; }
static inline u32 from_float(float f) { Narrow v; v.f = f; return v.i; }
static inline double as_double(u64 x) { Wide v; v.l = x; return v.d; }
static inline u64 from_double(double d) { Wide v; v.d = d; return v.l; }

static i32 f2i(double v) {
    if (v != v) return 0;
    if (v >= 2147483647.0) return 0x7fffffff;
    if (v <= -2147483648.0) return (i32)0x80000000;
    return (i32)v;
}
static i64 f2l(double v) {
    if (v != v) return 0;
    if (v >= 9223372036854775807.0) return (i64)0x7fffffffffffffffull;
    if (v <= -9223372036854775808.0) return (i64)0x8000000000000000ull;
    return (i64)v;
}
static u32 compare(double a, double b, int nan) { return (u32)(a < b ? -1 : a > b ? 1 : a == b ? 0 : nan); }

/* ---------- threads and monitors ---------- */

Thread *new_thread(Jvm *vm, Object *object) {
    Thread *t = 0;
    for (Thread *k = vm->threads; k; k = k->next) if (k->state == THREAD_DEAD) { t = k; break; }
    if (!t) {
        t = meta_alloc(vm, sizeof(Thread));
        if (!t) return 0;
        t->slots = meta_alloc(vm, THREAD_SLOTS*4);
        t->frames = meta_alloc(vm, THREAD_FRAMES*sizeof(Frame));
        if (!t->slots || !t->frames) return 0;
        t->slots_end = t->slots+THREAD_SLOTS;
        t->max_depth = THREAD_FRAMES;
        t->next = vm->threads;
        vm->threads = t;
    } else jvm_memset(t->slots, 0, THREAD_SLOTS*4);
    t->object = object;
    t->exception = 0;
    t->depth = 0;
    t->state = THREAD_NEW;
    t->interrupted = 0;
    t->waiting_on = 0;
    t->saved_count = 0;
    return t;
}

Monitor *monitor_for(Jvm *vm, Object *o, int create) {
    Monitor *empty = 0;
    for (u32 i = 0; i < MAX_MONITORS; i++) {
        if (vm->monitors[i].object == o) return &vm->monitors[i];
        if (!vm->monitors[i].object && !empty) empty = &vm->monitors[i];
    }
    if (!create) return 0;
    if (!empty) { vm_fail(vm, "too many monitors", 0); return 0; }
    empty->object = o; empty->owner = 0; empty->count = 0;
    return empty;
}

int monitor_enter(Thread *t, Object *o) {
    Monitor *m = monitor_for(thread_vm(t), o, 1);
    if (!m) return 0;
    if (!m->owner) { m->owner = t; m->count = 1; return 1; }
    if (m->owner == t) { m->count++; return 1; }
    t->state = THREAD_BLOCKED;
    t->waiting_on = o;
    t->saved_count = 0;
    return 0;
}

int monitor_exit(Thread *t, Object *o) {
    Monitor *m = monitor_for(thread_vm(t), o, 0);
    if (!m || m->owner != t) { throw_new(t, "java/lang/IllegalMonitorStateException", 0); return 0; }
    if (!--m->count) m->owner = 0;
    return 1;
}

/* ---------- exceptions ---------- */

void throw_object(Thread *t, Object *exception) { t->exception = exception; }

void throw_new(Thread *t, const char *class_name, const char *message) {
    Jvm *vm = thread_vm(t);
    Class *c = find_class_c(vm, class_name);
    if (!c) { vm_fail(vm, "missing exception class", 0); return; }
    Object *e = new_object(vm, c);
    if (!e) { vm_fail(vm, "out of memory creating an exception", 0); return; }
    push_root(vm, e);
    if (message) {
        Object *s = new_string_c(vm, message);
        if (s) FIELDS(e)[(vm->throwable_message-sizeof(Object))/4] = (u32)(uintptr_t)s;
    }
    pop_roots(vm, 1);
    t->exception = e;
}

static void report_uncaught(Thread *t) {
    Jvm *vm = thread_vm(t);
    Object *e = t->exception;
    char text[160];
    u32 n = 0;
    const char *prefix = "Uncaught exception: ";
    while (*prefix) text[n++] = *prefix++;
    u32 length = utf8_length(e->cls->name);
    for (u32 i = 0; i < length && n < 100; i++) text[n++] = utf8_bytes(e->cls->name)[i] == '/' ? '.' : utf8_bytes(e->cls->name)[i];
    Object *message = (Object *)(uintptr_t)FIELDS(e)[(vm->throwable_message-sizeof(Object))/4];
    if (message) {
        text[n++] = ':'; text[n++] = ' ';
        n += string_utf8(vm, message, text+n, sizeof(text)-n-2);
    }
    text[n++] = '\n';
    vm->host.write(vm->host.context, 2, text, n);
}

/* Find a handler in the current frames; the top frame's pc is the throwing
   instruction, callers hold the address after their invoke instruction. */
static int handle_exception(Thread *t, int top_is_exact) {
    Jvm *vm = thread_vm(t);
    Object *e = t->exception;
    while (t->depth) {
        Frame *f = &t->frames[t->depth-1];
        Method *m = f->method;
        u32 offset = (u32)(f->pc-m->code)-(top_is_exact ? 0 : 1);
        for (u32 i = 0; i < m->handler_count; i++) {
            const Handler *h = &m->handlers[i];
            if (offset < h->start || offset >= h->end) continue;
            if (h->type) {
                Class *c = resolve_class(vm, m->owner, h->type);
                if (!c) return 0;
                if (!is_subclass(e->cls, c)) continue;
            }
            f->pc = m->code+h->handler;
            f->sp = f->stack;
            *f->sp++ = (u32)(uintptr_t)e;
            t->exception = 0;
            return 1;
        }
        if (f->monitor) {
            Monitor *mon = monitor_for(vm, f->monitor, 0);
            if (mon && mon->owner == t && !--mon->count) mon->owner = 0;
        }
        if ((m->access & ACC_STATIC) && m->name[2] == '<') m->owner->state = CLASS_INITIALIZED;
        t->depth--;
        top_is_exact = 0;
    }
    report_uncaught(t);
    vm->uncaught++;
    t->exception = 0;
    t->state = THREAD_DEAD;
    return 0;
}

/* ---------- invocation ---------- */

/* Push a frame whose arguments already sit at args (on the caller's stack, or
   at the start of an empty thread stack). Returns 0 with an exception pending. */
static int push_frame(Thread *t, Method *m, u32 *args) {
    if (t->depth >= t->max_depth || args+m->max_locals+m->max_stack+4 > t->slots_end) {
        throw_new(t, "java/lang/StackOverflowError", 0);
        return 0;
    }
    Frame *f = &t->frames[t->depth++];
    f->method = m;
    f->pc = m->code;
    f->locals = args;
    for (u32 i = m->arg_slots; i < m->max_locals; i++) args[i] = 0;
    f->stack = f->sp = args+m->max_locals;
    f->monitor = 0;
    return 1;
}

/* Used by natives and the launcher to start Java code on a thread. */
int invoke_method(Thread *t, Method *m, u32 *args) {
    u32 *base = t->depth ? t->frames[t->depth-1].sp : t->slots;
    for (u32 i = 0; i < m->arg_slots; i++) base[i] = args[i];
    return push_frame(t, m, base);
}

/* Initialise c (superclasses first). Returns 1 when ready, 0 after pushing a
   <clinit> frame: the caller re-executes its instruction once it returns. */
static int ensure_initialized(Thread *t, Class *c, u32 *sp) {
    while (c->state < CLASS_INITIALIZING) {
        Class *k = c;
        while (k->super && k->super->state < CLASS_INITIALIZING) k = k->super;
        k->state = CLASS_INITIALIZING;
        k->initializer = t;
        Method *init = 0;
        for (u32 i = 0; i < k->method_count; i++)
            if (utf8_equals(k->methods[i].name, "<clinit>")) { init = &k->methods[i]; break; }
        if (!init) { k->state = CLASS_INITIALIZED; continue; }
        t->frames[t->depth-1].sp = sp;
        if (!push_frame(t, init, sp)) return -1;
        return 0;
    }
    return 1;
}

/* Initialise c and its superclasses before the current top frame runs: every
   pending <clinit> is pushed above it, the most basic class on top. */
int initialize_class(Thread *t, Class *c) {
    Class *chain[16];
    u32 count = 0;
    for (Class *k = c; k && k->state < CLASS_INITIALIZING && count < 16; k = k->super) chain[count++] = k;
    for (u32 i = 0; i < count; i++) {
        Class *k = chain[i];
        k->state = CLASS_INITIALIZING;
        k->initializer = t;
        Method *init = 0;
        for (u32 j = 0; j < k->method_count; j++)
            if (utf8_equals(k->methods[j].name, "<clinit>")) { init = &k->methods[j]; break; }
        if (!init) { k->state = CLASS_INITIALIZED; continue; }
        u32 *base = t->depth ? t->frames[t->depth-1].sp : t->slots;
        if (!push_frame(t, init, base)) return 0;
    }
    return 1;
}

/* Small cache for invokeinterface and virtual calls through interfaces. */
typedef struct CacheEntry { Class *cls; Method *method, *target; } CacheEntry;
static CacheEntry call_cache[256];

static Method *interface_target(Class *receiver, Method *m) {
    u32 h = (((u32)(uintptr_t)receiver >> 3) ^ ((u32)(uintptr_t)m >> 2)) & 255;
    CacheEntry *e = &call_cache[h];
    if (e->cls == receiver && e->method == m) return e->target;
    Method *target = find_method_utf8(receiver, m->name, m->desc);
    e->cls = receiver; e->method = m; e->target = target;
    return target;
}

/* ---------- interpreter ---------- */

/* Values are computed before sp moves: PUSH(POP()) must be well defined. */
#define PUSH(v) do { u32 value_ = (u32)(v); *sp++ = value_; } while (0)
#define POP() (*--sp)
#define PUSHW(v) do { u64 wide_ = (u64)(v); put_wide(sp, wide_); sp += 2; } while (0)
#define POPW() (sp -= 2, get_wide(sp))
#define REF(v) ((Object *)(uintptr_t)(v))
#define SAVE() do { f->pc = pc; f->sp = sp; } while (0)
#define RELOAD() do { f = &t->frames[t->depth-1]; m = f->method; pc = f->pc; sp = f->sp; locals = f->locals; \
                      cp = m->owner->constants; } while (0)
#define THROW(cls, msg) do { f->pc = start; f->sp = sp; throw_new(t, cls, msg); goto exception_exact; } while (0)
#define NULL_CHECK(o) do { if (!(o)) THROW("java/lang/NullPointerException", 0); } while (0)
#define CHECK_VM() do { if (vm->status == JVM_ERROR) { SAVE(); return 0; } } while (0)

static Array *check_array(Thread *t, u32 ref, i32 index, const char **error) {
    Array *a = (Array *)(uintptr_t)ref;
    (void)t;
    if (!a) { *error = "java/lang/NullPointerException"; return 0; }
    if (index < 0 || (u32)index >= a->length) { *error = "java/lang/ArrayIndexOutOfBoundsException"; return 0; }
    return a;
}

static Array *multi_array(Jvm *vm, Class *c, u32 *counts, u32 dims) {
    Array *a = new_array(vm, c, counts[0]);
    if (!a || dims == 1) return a;
    push_root(vm, (Object *)a);
    for (u32 i = 0; i < counts[0]; i++) {
        Array *sub = multi_array(vm, c->component, counts+1, dims-1);
        if (!sub) { pop_roots(vm, 1); return 0; }
        ((u32 *)ARRAY_DATA(a))[i] = (u32)(uintptr_t)sub;
    }
    pop_roots(vm, 1);
    return a;
}

int execute(Jvm *vm, Thread *t, u32 budget) {
    Frame *f; Method *m; const u8 *pc, *start; u32 *sp, *locals; Constant *cp;
    if (!t->depth) { t->state = THREAD_DEAD; return 0; }
    RELOAD();
    if (t->exception) goto exception_caller;
    while (budget--) {
        start = pc;
        u8 op = *pc++;
        switch (op) {
        case 0x00: break;
        case 0x01: PUSH(0); break;
        case 0x02: case 0x03: case 0x04: case 0x05: case 0x06: case 0x07: case 0x08: PUSH((i32)op-3); break;
        case 0x09: case 0x0a: PUSHW((i64)(op-9)); break;
        case 0x0b: case 0x0c: case 0x0d: PUSH(from_float((float)(op-0x0b))); break;
        case 0x0e: case 0x0f: PUSHW(from_double((double)(op-0x0e))); break;
        case 0x10: PUSH((i32)(int8_t)*pc++); break;
        case 0x11: PUSH((i32)(int16_t)rd16(pc)); pc += 2; break;
        case 0x12: case 0x13: {
            u16 index = op == 0x12 ? *pc : (u16)rd16(pc);
            pc += op == 0x12 ? 1 : 2;
            Constant *k = &cp[index];
            if (k->tag == 8) {
                SAVE();
                Object *s = resolve_string(vm, m->owner, index);
                CHECK_VM();
                PUSH((uintptr_t)s);
            } else if (k->tag == 7) {
                SAVE();
                Class *c = resolve_class(vm, m->owner, index);
                CHECK_VM();
                Object *mirror = class_mirror(vm, c);
                CHECK_VM();
                PUSH((uintptr_t)mirror);
            } else PUSH(k->u.value);
            break;
        }
        case 0x14: { Constant *k = &cp[rd16(pc)]; pc += 2; PUSHW(((u64)k->u.value << 32) | k[1].u.value); break; }
        case 0x15: case 0x17: case 0x19: PUSH(locals[*pc++]); break;
        case 0x16: case 0x18: PUSH(locals[*pc]); PUSH(locals[*pc+1]); pc++; break;
        case 0x1a: case 0x1b: case 0x1c: case 0x1d: PUSH(locals[op-0x1a]); break;
        case 0x1e: case 0x1f: case 0x20: case 0x21: PUSH(locals[op-0x1e]); PUSH(locals[op - 0x1e + 1]); break;
        case 0x22: case 0x23: case 0x24: case 0x25: PUSH(locals[op-0x22]); break;
        case 0x26: case 0x27: case 0x28: case 0x29: PUSH(locals[op-0x26]); PUSH(locals[op-0x26+1]); break;
        case 0x2a: case 0x2b: case 0x2c: case 0x2d: PUSH(locals[op-0x2a]); break;
        case 0x2e: case 0x2f: case 0x30: case 0x31: case 0x32: case 0x33: case 0x34: case 0x35: {
            i32 index = (i32)POP();
            const char *error = 0;
            Array *a = check_array(t, POP(), index, &error);
            if (!a) THROW(error, 0);
            u8 *d = ARRAY_DATA(a);
            switch (op) {
            case 0x2e: case 0x30: case 0x32: PUSH(((u32 *)d)[index]); break;
            case 0x2f: case 0x31: PUSHW(get_wide((u32 *)d+index*2)); break;
            case 0x33: PUSH((i32)((int8_t *)d)[index]); break;
            case 0x34: PUSH(((u16 *)d)[index]); break;
            default: PUSH((i32)((int16_t *)d)[index]); break;
            }
            break;
        }
        case 0x36: case 0x38: case 0x3a: locals[*pc++] = POP(); break;
        case 0x37: case 0x39: locals[*pc+1] = POP(); locals[*pc] = POP(); pc++; break;
        case 0x3b: case 0x3c: case 0x3d: case 0x3e: locals[op-0x3b] = POP(); break;
        case 0x3f: case 0x40: case 0x41: case 0x42: locals[op-0x3f+1] = POP(); locals[op-0x3f] = POP(); break;
        case 0x43: case 0x44: case 0x45: case 0x46: locals[op-0x43] = POP(); break;
        case 0x47: case 0x48: case 0x49: case 0x4a: locals[op-0x47+1] = POP(); locals[op-0x47] = POP(); break;
        case 0x4b: case 0x4c: case 0x4d: case 0x4e: locals[op-0x4b] = POP(); break;
        case 0x4f: case 0x50: case 0x51: case 0x52: case 0x53: case 0x54: case 0x55: case 0x56: {
            u64 wide = 0; u32 value = 0;
            if (op == 0x50 || op == 0x52) wide = POPW(); else value = POP();
            i32 index = (i32)POP();
            u32 ref = POP();
            const char *error = 0;
            Array *a = check_array(t, ref, index, &error);
            if (!a) THROW(error, 0);
            u8 *d = ARRAY_DATA(a);
            switch (op) {
            case 0x4f: case 0x51: ((u32 *)d)[index] = value; break;
            case 0x50: case 0x52: put_wide((u32 *)d+index*2, wide); break;
            case 0x53:
                if (value && !is_subclass(REF(value)->cls, a->header.cls->component))
                    THROW("java/lang/ArrayStoreException", 0);
                ((u32 *)d)[index] = value; break;
            case 0x54: ((u8 *)d)[index] = (u8)value; break;
            default: ((u16 *)d)[index] = (u16)value; break;
            }
            break;
        }
        case 0x57: sp--; break;
        case 0x58: sp -= 2; break;
        case 0x59: sp[0] = sp[-1]; sp++; break;
        case 0x5a: { u32 a = sp[-1], b = sp[-2]; sp[-2] = a; sp[-1] = b; sp[0] = a; sp++; break; }
        case 0x5b: { u32 a = sp[-1], b = sp[-2], c = sp[-3]; sp[-3] = a; sp[-2] = c; sp[-1] = b; sp[0] = a; sp++; break; }
        case 0x5c: sp[0] = sp[-2]; sp[1] = sp[-1]; sp += 2; break;
        case 0x5d: { u32 a = sp[-1], b = sp[-2], c = sp[-3]; sp[-3] = b; sp[-2] = a; sp[-1] = c; sp[0] = b; sp[1] = a; sp += 2; break; }
        case 0x5e: { u32 a = sp[-1], b = sp[-2], c = sp[-3], d = sp[-4];
                     sp[-4] = b; sp[-3] = a; sp[-2] = d; sp[-1] = c; sp[0] = b; sp[1] = a; sp += 2; break; }
        case 0x5f: { u32 a = sp[-1]; sp[-1] = sp[-2]; sp[-2] = a; break; }
        case 0x60: { i32 b = (i32)POP(), a = (i32)POP(); PUSH((u32)a+(u32)b); break; }
        case 0x61: { u64 b = POPW(), a = POPW(); PUSHW(a+b); break; }
        case 0x62: { float b = as_float(POP()), a = as_float(POP()); PUSH(from_float(a+b)); break; }
        case 0x63: { double b = as_double(POPW()), a = as_double(POPW()); PUSHW(from_double(a+b)); break; }
        case 0x64: { u32 b = POP(), a = POP(); PUSH(a-b); break; }
        case 0x65: { u64 b = POPW(), a = POPW(); PUSHW(a-b); break; }
        case 0x66: { float b = as_float(POP()), a = as_float(POP()); PUSH(from_float(a-b)); break; }
        case 0x67: { double b = as_double(POPW()), a = as_double(POPW()); PUSHW(from_double(a-b)); break; }
        case 0x68: { u32 b = POP(), a = POP(); PUSH(a*b); break; }
        case 0x69: { u64 b = POPW(), a = POPW(); PUSHW(a*b); break; }
        case 0x6a: { float b = as_float(POP()), a = as_float(POP()); PUSH(from_float(a*b)); break; }
        case 0x6b: { double b = as_double(POPW()), a = as_double(POPW()); PUSHW(from_double(a*b)); break; }
        case 0x6c: case 0x70: {
            i32 b = (i32)POP(), a = (i32)POP();
            if (!b) THROW("java/lang/ArithmeticException", "/ by zero");
            if (b == -1) PUSH(op == 0x6c ? (u32)0-(u32)a : 0);
            else PUSH(op == 0x6c ? a/b : a%b);
            break;
        }
        case 0x6d: case 0x71: {
            i64 b = (i64)POPW(), a = (i64)POPW();
            if (!b) THROW("java/lang/ArithmeticException", "/ by zero");
            if (b == -1) PUSHW(op == 0x6d ? (u64)0-(u64)a : 0);
            else PUSHW(op == 0x6d ? a/b : a%b);
            break;
        }
        case 0x6e: { float b = as_float(POP()), a = as_float(POP()); PUSH(from_float(a/b)); break; }
        case 0x6f: { double b = as_double(POPW()), a = as_double(POPW()); PUSHW(from_double(a/b)); break; }
        case 0x72: { float b = as_float(POP()), a = as_float(POP()); PUSH(from_float((float)jvm_fmod(a, b))); break; }
        case 0x73: { double b = as_double(POPW()), a = as_double(POPW()); PUSHW(from_double(jvm_fmod(a, b))); break; }
        case 0x74: PUSH((u32)0-POP()); break;
        case 0x75: PUSHW((u64)0-POPW()); break;
        case 0x76: PUSH(POP() ^ 0x80000000u); break;
        case 0x77: { u64 v = POPW(); PUSHW(v ^ 0x8000000000000000ull); break; }
        case 0x78: { u32 s = POP() & 31, a = POP(); PUSH(a << s); break; }
        case 0x79: { u32 s = POP() & 63; u64 a = POPW(); PUSHW(a << s); break; }
        case 0x7a: { u32 s = POP() & 31; i32 a = (i32)POP(); PUSH(a >> s); break; }
        case 0x7b: { u32 s = POP() & 63; i64 a = (i64)POPW(); PUSHW(a >> s); break; }
        case 0x7c: { u32 s = POP() & 31, a = POP(); PUSH(a >> s); break; }
        case 0x7d: { u32 s = POP() & 63; u64 a = POPW(); PUSHW(a >> s); break; }
        case 0x7e: { u32 b = POP(), a = POP(); PUSH(a & b); break; }
        case 0x7f: { u64 b = POPW(), a = POPW(); PUSHW(a & b); break; }
        case 0x80: { u32 b = POP(), a = POP(); PUSH(a | b); break; }
        case 0x81: { u64 b = POPW(), a = POPW(); PUSHW(a | b); break; }
        case 0x82: { u32 b = POP(), a = POP(); PUSH(a ^ b); break; }
        case 0x83: { u64 b = POPW(), a = POPW(); PUSHW(a ^ b); break; }
        case 0x84: locals[pc[0]] += (u32)(i32)(int8_t)pc[1]; pc += 2; break;
        case 0x85: PUSHW((u64)(i64)(i32)POP()); break;
        case 0x86: PUSH(from_float((float)(i32)POP())); break;
        case 0x87: PUSHW(from_double((double)(i32)POP())); break;
        case 0x88: { u64 v = POPW(); PUSH((u32)v); break; }
        case 0x89: PUSH(from_float((float)(i64)POPW())); break;
        case 0x8a: PUSHW(from_double((double)(i64)POPW())); break;
        case 0x8b: PUSH(f2i(as_float(POP()))); break;
        case 0x8c: PUSHW(f2l(as_float(POP()))); break;
        case 0x8d: PUSHW(from_double(as_float(POP()))); break;
        case 0x8e: PUSH(f2i(as_double(POPW()))); break;
        case 0x8f: PUSHW(f2l(as_double(POPW()))); break;
        case 0x90: PUSH(from_float((float)as_double(POPW()))); break;
        case 0x91: PUSH((i32)(int8_t)POP()); break;
        case 0x92: PUSH((u16)POP()); break;
        case 0x93: PUSH((i32)(int16_t)POP()); break;
        case 0x94: { i64 b = (i64)POPW(), a = (i64)POPW(); PUSH((u32)(a < b ? -1 : a > b)); break; }
        case 0x95: case 0x96: { float b = as_float(POP()), a = as_float(POP()); PUSH(compare(a, b, op == 0x95 ? -1 : 1)); break; }
        case 0x97: case 0x98: { double b = as_double(POPW()), a = as_double(POPW()); PUSH(compare(a, b, op == 0x97 ? -1 : 1)); break; }
        case 0x99: case 0x9a: case 0x9b: case 0x9c: case 0x9d: case 0x9e: {
            i32 v = (i32)POP();
            int jump = op == 0x99 ? v == 0 : op == 0x9a ? v != 0 : op == 0x9b ? v < 0 : op == 0x9c ? v >= 0 : op == 0x9d ? v > 0 : v <= 0;
            pc = jump ? start+(int16_t)rd16(pc) : pc+2;
            break;
        }
        case 0x9f: case 0xa0: case 0xa1: case 0xa2: case 0xa3: case 0xa4: {
            i32 b = (i32)POP(), a = (i32)POP();
            int jump = op == 0x9f ? a == b : op == 0xa0 ? a != b : op == 0xa1 ? a < b : op == 0xa2 ? a >= b : op == 0xa3 ? a > b : a <= b;
            pc = jump ? start+(int16_t)rd16(pc) : pc+2;
            break;
        }
        case 0xa5: case 0xa6: { u32 b = POP(), a = POP(); pc = (op == 0xa5) == (a == b) ? start+(int16_t)rd16(pc) : pc+2; break; }
        case 0xa7: pc = start+(int16_t)rd16(pc); break;
        case 0xa8: PUSH((u32)(pc+2-m->code)); pc = start+(int16_t)rd16(pc); break;
        case 0xa9: pc = m->code+locals[*pc]; break;
        case 0xaa: {
            const u8 *p = m->code+(((u32)(start-m->code)+4) & ~3u);
            i32 index = (i32)POP(), low = (i32)rd32(p+4), high = (i32)rd32(p+8);
            pc = start+(i32)rd32(index < low || index > high ? p : p+12+(index-low)*4);
            break;
        }
        case 0xab: {
            const u8 *p = m->code+(((u32)(start-m->code)+4) & ~3u);
            i32 key = (i32)POP();
            u32 pairs = rd32(p+4);
            i32 offset = (i32)rd32(p);
            for (u32 lo = 0, hi = pairs; lo < hi;) {
                u32 mid = (lo+hi)/2;
                i32 k = (i32)rd32(p+8+mid*8);
                if (k == key) { offset = (i32)rd32(p+12+mid*8); break; }
                if (k < key) lo = mid+1; else hi = mid;
            }
            pc = start+offset;
            break;
        }
        case 0xac: case 0xad: case 0xae: case 0xaf: case 0xb0: case 0xb1: {
            u32 slots = op == 0xb1 ? 0 : (op == 0xad || op == 0xaf) ? 2 : 1;
            u32 *result = sp-slots;
            if (f->monitor) {
                Monitor *mon = monitor_for(vm, f->monitor, 0);
                if (mon && mon->owner == t && !--mon->count) mon->owner = 0;
            }
            if ((m->access & ACC_STATIC) && m->name[2] == '<') m->owner->state = CLASS_INITIALIZED;
            u32 *target = f->locals;
            for (u32 i = 0; i < slots; i++) target[i] = result[i];
            t->depth--;
            if (!t->depth) { t->state = THREAD_DEAD; return 0; }
            RELOAD();
            sp = target+slots;
            break;
        }
        case 0xb2: case 0xb3: case 0xb4: case 0xb5: {
            u16 index = (u16)rd16(pc);
            pc += 2;
            SAVE();
            Field *fd = resolve_field(vm, m->owner, index);
            CHECK_VM();
            int wide = fd->desc[2] == 'J' || fd->desc[2] == 'D';
            if (op <= 0xb3) {
                int ready = ensure_initialized(t, fd->owner, sp);
                if (ready < 0) goto exception_exact_saved;
                if (!ready) { f->pc = start; RELOAD(); break; }
                u32 *slot = &fd->owner->statics[fd->offset];
                if (op == 0xb2) { if (wide) { PUSH(slot[0]); PUSH(slot[1]); } else PUSH(slot[0]); }
                else if (wide) { slot[1] = POP(); slot[0] = POP(); } else slot[0] = POP();
            } else if (op == 0xb4) {
                Object *o = REF(POP());
                NULL_CHECK(o);
                u32 *slot = (u32 *)((u8 *)o+fd->offset);
                if (wide) { PUSH(slot[0]); PUSH(slot[1]); } else PUSH(slot[0]);
            } else {
                u32 hi = 0, lo = POP();
                if (wide) hi = POP();
                Object *o = REF(POP());
                NULL_CHECK(o);
                u32 *slot = (u32 *)((u8 *)o+fd->offset);
                if (wide) { slot[0] = hi; slot[1] = lo; } else slot[0] = lo;
            }
            break;
        }
        case 0xb6: case 0xb7: case 0xb8: case 0xb9: {
            u16 index = (u16)rd16(pc);
            pc += op == 0xb9 ? 4 : 2;
            SAVE();
            Method *target = resolve_method(vm, m->owner, index);
            CHECK_VM();
            u32 *args = sp-target->arg_slots;
            if (op == 0xb8) {
                int ready = ensure_initialized(t, target->owner, sp);
                if (ready < 0) goto exception_exact_saved;
                if (!ready) { f->pc = start; RELOAD(); break; }
            } else {
                Object *receiver = REF(args[0]);
                NULL_CHECK(receiver);
                if (op == 0xb9 || (target->owner->access & ACC_INTERFACE)) target = interface_target(receiver->cls, target);
                else if (op == 0xb6 && !(target->access & ACC_PRIVATE)) target = receiver->cls->vtable[target->vtable_index];
                if (!target) THROW("java/lang/AbstractMethodError", 0);
            }
            if (target->access & ACC_SYNCHRONIZED) {
                Object *lock = target->access & ACC_STATIC ? class_mirror(vm, target->owner) : REF(args[0]);
                CHECK_VM();
                if (!monitor_enter(t, lock)) { f->pc = start; return 0; }
                if (target->access & ACC_NATIVE) { /* natives are short; release right after */ }
                else {
                    f->pc = pc; f->sp = args;
                    if (!push_frame(t, target, args)) goto exception_caller;
                    t->frames[t->depth-1].monitor = lock;
                    RELOAD();
                    break;
                }
            }
            if (target->access & ACC_NATIVE) {
                if (!target->native) {
                    f->pc = start; f->sp = sp;
                    throw_new(t, "java/lang/UnsatisfiedLinkError", utf8_bytes(target->name));
                    goto exception_exact;
                }
                u32 result[2] = {0, 0};
                f->pc = start; f->sp = sp;
                int status = target->native(t, args, result);
                if (target->access & ACC_SYNCHRONIZED) {
                    Object *lock = target->access & ACC_STATIC ? target->owner->mirror : REF(args[0]);
                    Monitor *mon = monitor_for(vm, lock, 0);
                    if (mon && mon->owner == t && !--mon->count) mon->owner = 0;
                }
                CHECK_VM();
                if (t->exception) goto exception_exact;
                if (status == 2) { RELOAD(); break; }  /* the native pushed a Java frame */
                sp = args;
                for (u32 i = 0; i < target->return_slots; i++) PUSH(result[i]);
                f->pc = pc; f->sp = sp;
                if (t->state != THREAD_RUNNABLE) return 0;
                break;
            }
            if (target->access & ACC_ABSTRACT) THROW("java/lang/AbstractMethodError", 0);
            f->pc = pc; f->sp = args;
            if (!push_frame(t, target, args)) goto exception_caller;
            RELOAD();
            break;
        }
        case 0xba: SAVE(); vm_fail(vm, "invokedynamic is not supported", 0); return 0;
        case 0xbb: {
            u16 index = (u16)rd16(pc);
            pc += 2;
            SAVE();
            Class *c = resolve_class(vm, m->owner, index);
            CHECK_VM();
            int ready = ensure_initialized(t, c, sp);
            if (ready < 0) goto exception_exact_saved;
            if (!ready) { f->pc = start; RELOAD(); break; }
            Object *o = new_object(vm, c);
            if (!o) THROW("java/lang/OutOfMemoryError", 0);
            PUSH((uintptr_t)o);
            break;
        }
        case 0xbc: case 0xbd: {
            i32 count = (i32)POP();
            Class *c;
            SAVE();
            if (op == 0xbc) {
                static const char types[] = "ZCFDBSIJ";
                c = primitive_array(vm, (u8)types[*pc-4]);
                pc++;
            } else {
                Class *element = resolve_class(vm, m->owner, (u16)rd16(pc));
                CHECK_VM();
                c = array_class(vm, element);
                pc += 2;
            }
            CHECK_VM();
            if (count < 0) THROW("java/lang/NegativeArraySizeException", 0);
            Array *a = new_array(vm, c, (u32)count);
            if (!a) THROW("java/lang/OutOfMemoryError", 0);
            PUSH((uintptr_t)a);
            break;
        }
        case 0xbe: { Array *a = (Array *)REF(POP()); NULL_CHECK(a); PUSH(a->length); break; }
        case 0xbf: {
            Object *e = REF(POP());
            NULL_CHECK(e);
            f->pc = start; f->sp = sp;
            t->exception = e;
            goto exception_exact;
        }
        case 0xc0: case 0xc1: {
            u16 index = (u16)rd16(pc);
            pc += 2;
            Object *o = REF(sp[-1]);
            SAVE();
            Class *c = resolve_class(vm, m->owner, index);
            CHECK_VM();
            if (op == 0xc0) { if (o && !is_subclass(o->cls, c)) THROW("java/lang/ClassCastException", 0); }
            else sp[-1] = o && is_subclass(o->cls, c);
            break;
        }
        case 0xc2: case 0xc3: {
            Object *o = REF(POP());
            NULL_CHECK(o);
            if (op == 0xc2) {
                if (!monitor_enter(t, o)) { sp++; f->pc = start; f->sp = sp; return 0; }
            } else if (!monitor_exit(t, o)) goto exception_exact_unsaved;
            break;
        }
        case 0xc4: {
            u8 wide_op = *pc++;
            u32 index = rd16(pc);
            pc += 2;
            switch (wide_op) {
            case 0x15: case 0x17: case 0x19: PUSH(locals[index]); break;
            case 0x16: case 0x18: PUSH(locals[index]); PUSH(locals[index+1]); break;
            case 0x36: case 0x38: case 0x3a: locals[index] = POP(); break;
            case 0x37: case 0x39: locals[index+1] = POP(); locals[index] = POP(); break;
            case 0x84: locals[index] += (u32)(i32)(int16_t)rd16(pc); pc += 2; break;
            case 0xa9: pc = m->code+locals[index]; break;
            default: SAVE(); vm_fail(vm, "bad wide instruction", 0); return 0;
            }
            break;
        }
        case 0xc5: {
            u16 index = (u16)rd16(pc);
            u32 dims = pc[2];
            pc += 3;
            SAVE();
            Class *c = resolve_class(vm, m->owner, index);
            CHECK_VM();
            u32 counts[8];
            if (dims > 8) { vm_fail(vm, "too many array dimensions", 0); return 0; }
            for (u32 i = dims; i--;) {
                i32 n = (i32)POP();
                if (n < 0) THROW("java/lang/NegativeArraySizeException", 0);
                counts[i] = (u32)n;
            }
            f->sp = sp;
            Array *a = multi_array(vm, c, counts, dims);
            CHECK_VM();
            if (!a) THROW("java/lang/OutOfMemoryError", 0);
            PUSH((uintptr_t)a);
            break;
        }
        case 0xc6: case 0xc7: { u32 v = POP(); pc = (op == 0xc6) == !v ? start+(int16_t)rd16(pc) : pc+2; break; }
        case 0xc8: pc = start+(i32)rd32(pc); break;
        case 0xc9: PUSH((u32)(pc+4-m->code)); pc = start+(i32)rd32(pc); break;
        default:
            SAVE();
            vm_fail(vm, "unknown bytecode", 0);
            return 0;
        }
        continue;
exception_exact_unsaved:
        f->pc = start; f->sp = sp;
exception_exact:
exception_exact_saved:
        if (vm->status == JVM_ERROR) return 0;
        if (!handle_exception(t, 1)) return 0;
        RELOAD();
        continue;
exception_caller:
        if (vm->status == JVM_ERROR) return 0;
        if (!handle_exception(t, 0)) return 0;
        RELOAD();
    }
    SAVE();
    return 1;
}

/* ---------- scheduler ---------- */

static void wake_threads(Jvm *vm) {
    u32 now = vm->host.ms(vm->host.context);
    for (Thread *t = vm->threads; t; t = t->next) {
        if ((t->state == THREAD_SLEEPING || (t->state == THREAD_WAITING && t->wake)) && (i32)(now-t->wake) >= 0) {
            if (t->state == THREAD_SLEEPING) t->state = THREAD_RUNNABLE;
            else t->state = THREAD_BLOCKED;  /* timed wait over: reacquire the monitor */
        }
        if (t->state == THREAD_BLOCKED) {
            Monitor *m = monitor_for(vm, t->waiting_on, 0);
            if (!m || !m->owner) {
                if (t->saved_count) {
                    if (!m) m = monitor_for(vm, t->waiting_on, 1);
                    if (!m) return;
                    m->owner = t;
                    m->count = t->saved_count;
                    t->saved_count = 0;
                }
                t->waiting_on = 0;
                t->state = THREAD_RUNNABLE;
            }
        }
    }
}

int jvm_run(Jvm *vm, u32 budget) {
    jvm_current_vm = vm;
    if (vm->status == JVM_ERROR) return JVM_ERROR;
    while (budget) {
        wake_threads(vm);
        int any = 0;
        for (Thread *t = vm->threads; t && budget; t = t->next) {
            if (t->state != THREAD_RUNNABLE) continue;
            any = 1;
            u32 slice = budget < 4000 ? budget : 4000;
            vm->current = t;
            execute(vm, t, slice);
            budget -= slice;
            if (vm->status == JVM_ERROR) return JVM_ERROR;
        }
        if (!any) {
            for (Thread *t = vm->threads; t; t = t->next)
                if (t->state != THREAD_DEAD) return JVM_IDLE;
            return JVM_DONE;
        }
    }
    return JVM_RUNNING;
}

uint32_t jvm_next_wake(Jvm *vm) {
    u32 best = 0xffffffffu;
    for (Thread *t = vm->threads; t; t = t->next)
        if ((t->state == THREAD_SLEEPING || (t->state == THREAD_WAITING && t->wake)) && t->wake < best) best = t->wake;
    return best;
}
