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

/* Java stacks are int[] in the heap, so a thread only takes what it uses. A
   stack that is too small is replaced by one twice as large and the frames'
   pointers are moved to it; needed is the number of slots from the start. */
static int grow_stack(Thread *t, u32 needed) {
    Jvm *vm = thread_vm(t);
    u32 old = t->slots ? (u32)(t->slots_end-t->slots) : 0, capacity = old ? old*2 : THREAD_SLOTS_FIRST;
    while (capacity < needed) capacity *= 2;
    if (capacity > THREAD_SLOTS_MAX) return 0;
    Array *a = new_array(vm, primitive_array(vm, 'I'), capacity);
    if (!a) return 0;
    u32 *slots = (u32 *)ARRAY_DATA(a);
    if (old) {
        jvm_memcpy(slots, t->slots, old*4);
        for (u32 i = 0; i < t->depth; i++) {
            Frame *f = &t->frames[i];
            f->locals = slots+(f->locals-t->slots);
            f->stack = slots+(f->stack-t->slots);
            f->sp = slots+(f->sp-t->slots);
        }
    }
    t->stack = &a->header;
    t->slots = slots;
    t->slots_end = slots+capacity;
    return 1;
}

Thread *new_thread(Jvm *vm, Object *object) {
    Thread *t = 0;
    for (Thread *k = vm->threads; k; k = k->next) if (k->state == THREAD_DEAD) { t = k; break; }
    if (!t) {
        t = meta_alloc(vm, sizeof(Thread));
        if (!t) return 0;
        t->frames = meta_alloc(vm, THREAD_FRAMES*sizeof(Frame));
        if (!t->frames) return 0;
        t->max_depth = THREAD_FRAMES;
        t->state = THREAD_DEAD;  /* no stack yet: the collector skips it */
        t->next = vm->threads;
        vm->threads = t;
    }
    t->depth = 0;
    t->stack = 0;
    t->slots = t->slots_end = 0;
    if (!grow_stack(t, THREAD_SLOTS_FIRST)) return 0;
    t->object = object;
    t->exception = 0;
    t->depth = 0;
    t->state = THREAD_NEW;
    t->interrupted = 0;
    t->waiting_on = 0;
    t->saved_count = 0;
    return t;
}

/* The search stops once every entry in use was seen (synchronized methods on
   streams enter and leave a monitor per byte; the whole table was scanned each time). */
Monitor *monitor_for(Jvm *vm, Object *o, int create) {
    Monitor *empty = 0;
    u32 seen = 0;
    for (u32 i = 0; i < MAX_MONITORS; i++) {
        Object *x = vm->monitors[i].object;
        if (x == o && o) return &vm->monitors[i];
        if (x) seen++;
        else if (!empty) empty = &vm->monitors[i];
        if (seen == vm->monitors_used && (empty || !create)) break;
    }
    if (!create || !o) return 0;
    if (!empty) { vm_fail(vm, "too many monitors", 0); return 0; }
    empty->object = o; empty->owner = 0; empty->count = 0;
    vm->monitors_used++;
    return empty;
}

/* One unlock by the owner. The last one frees the table entry unless a thread
   is blocked on or waiting for the object, so the 64 entries only hold
   monitors in use (they used to stay until the next collection). */
static void release_monitor(Jvm *vm, Monitor *m) {
    if (--m->count) return;
    m->owner = 0;
    for (Thread *k = vm->threads; k; k = k->next)
        if (k->waiting_on == m->object && (k->state == THREAD_BLOCKED || k->state == THREAD_WAITING)) return;
    m->object = 0;
    vm->monitors_used--;
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
    release_monitor(thread_vm(t), m);
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

static void report_uncaught(Thread *t, const char *trace, u32 trace_length) {
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
    if (trace_length) vm->host.write(vm->host.context, 2, trace, trace_length);
}

/* Find a handler in the current frames; the top frame's pc is the throwing
   instruction, callers hold the address after their invoke instruction. */
static int handle_exception(Thread *t, int top_is_exact) {
    Jvm *vm = thread_vm(t);
    Object *e = t->exception;
    char trace[256];  /* frames left without a handler, for the report */
    u32 traced = 0;
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
            if (mon && mon->owner == t) release_monitor(vm, mon);
        }
        if ((m->access & ACC_STATIC) && m->name[2] == '<') m->owner->state = CLASS_INITIALIZED;
        if (traced+80 < sizeof(trace)) {
            const char *at = "  at ";
            while (*at) trace[traced++] = *at++;
            for (u32 i = 0; i < utf8_length(m->owner->name) && i < 30; i++) trace[traced++] = utf8_bytes(m->owner->name)[i];
            trace[traced++] = '.';
            for (u32 i = 0; i < utf8_length(m->name) && i < 30; i++) trace[traced++] = utf8_bytes(m->name)[i];
            trace[traced++] = ' ';
            char digits[10];
            u32 v = offset, d = 0;
            do { digits[d++] = (char)('0'+v%10); v /= 10; } while (v);
            while (d) trace[traced++] = digits[--d];
            trace[traced++] = '\n';
        }
        t->depth--;
        top_is_exact = 0;
    }
    report_uncaught(t, trace, traced);
    vm->uncaught++;
    t->exception = 0;
    t->state = THREAD_DEAD;
    return 0;
}

/* ---------- invocation ---------- */

/* Push a frame whose arguments already sit at args (on the caller's stack, or
   at the start of an empty thread stack). Returns 0 with an exception pending. */
static int push_frame(Thread *t, Method *m, u32 *args) {
    u32 offset = (u32)(args-t->slots), needed = offset+m->max_locals+m->max_stack+4;
    if (t->depth >= t->max_depth || (t->slots+needed > t->slots_end && !grow_stack(t, needed))) {
        throw_new(t, "java/lang/StackOverflowError", 0);
        return 0;
    }
    args = t->slots+offset;
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
    u32 offset = t->depth ? (u32)(t->frames[t->depth-1].sp-t->slots) : 0;
    if (t->slots+offset+m->arg_slots > t->slots_end && !grow_stack(t, offset+m->arg_slots)) {
        throw_new(t, "java/lang/StackOverflowError", 0);
        return 0;
    }
    u32 *base = t->slots+offset;
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
/* jit.c: compiled code from pc on, until it hands back to the interpreter.
   Inline here (jit_run is the same as a function): only at a block start; sp
   and budget go through copies, since taking their own address would keep them
   in memory all through execute. */
#ifdef __arm__
#define JIT_GO() do { const JitCode *c_ = &vm->jit[m->jit & ~JIT_COMPILED]; \
        u32 at_ = (u32)(pc-m->code); \
        if (at_ < m->code_length && (c_->map[at_] & JIT_BLOCK)) { \
            JitCtx ctx_ = {sp, locals, budget}; \
            pc = m->code+((JitEntry)(uintptr_t)c_->code)(&ctx_, c_->code+JIT_WORD(c_->map[at_])); \
            sp = ctx_.sp; budget = ctx_.budget; } } while (0)
#else
#define JIT_GO() do { u32 *sp_ = sp; i32 budget_ = budget; \
        pc = m->code+jit_run(vm, m, (u32)(pc-m->code), &sp_, locals, &budget_); sp = sp_; budget = budget_; } while (0)
#endif
/* Methods get hot by backward jumps and by calls: past JIT_THRESHOLD of both
   together they are compiled and run compiled from then on. */
#define HOT() do { u32 j_ = m->jit; \
        if (j_ & JIT_COMPILED) JIT_GO(); \
        else if (j_ < JIT_THRESHOLD) m->jit = (u16)(j_+1); \
        else if (j_ == JIT_THRESHOLD) { jit_compile(vm, m); if (m->jit & JIT_COMPILED) JIT_GO(); } } while (0)
/* After a jump: backward jumps count. */
#define BACKEDGE() do { if (pc <= start) HOT(); } while (0)

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

/* Returns the number of bytecodes run (all of limit unless the thread stopped;
   compiled code may run a few more). */
#ifdef JVM_OP_STATS
unsigned long long jvm_op_counts[256];  /* debug builds: bytecodes executed by opcode */
#endif

/* Quick forms (the KVM's "quickening"): after its first run an instruction is
   rewritten in place so it skips resolution and checks.
   - fields: the operand becomes the field's byte offset (instances) or stays
     the constant index whose field is resolved and whose class is initialized
     (statics);
   - calls: the constant is resolved; static calls only once the class is
     initialized and the method is plain (not native, not synchronized);
     a virtual call whose actual target is not plain becomes INVOKEVIRTUAL_SLOW
     for good (the general path, never quickened again). */
enum {
    GETFIELD_Q = 0xd0, GETFIELD_Q_WIDE, PUTFIELD_Q, PUTFIELD_Q_WIDE,
    GETSTATIC_Q, GETSTATIC_Q_WIDE, PUTSTATIC_Q, PUTSTATIC_Q_WIDE,
    INVOKEVIRTUAL_Q, INVOKESTATIC_Q, INVOKESPECIAL_Q, INVOKEVIRTUAL_SLOW
};
#define PLAIN(method) (!((method)->access & (ACC_NATIVE|ACC_SYNCHRONIZED|ACC_ABSTRACT)))
/* Quick calls also take bound natives that are not synchronized. */
#define QUICK_OK(method) (!((method)->access & (ACC_SYNCHRONIZED|ACC_ABSTRACT)) && \
                          (!((method)->access & ACC_NATIVE) || (method)->native))

u32 execute(Jvm *vm, Thread *t, u32 limit) {
    i32 budget = (i32)limit;  /* compiled code may leave it below zero */
    const i32 initial = budget;
    Frame *f; Method *m; const u8 *pc, *start; u32 *sp, *locals; Constant *cp;
    /* Handler of every opcode (L_default for unused ones): threaded dispatch. */
    static const void *const dispatch[256] = {
        &&L_00, &&L_01, &&L_02, &&L_03,
        &&L_04, &&L_05, &&L_06, &&L_07,
        &&L_08, &&L_09, &&L_0a, &&L_0b,
        &&L_0c, &&L_0d, &&L_0e, &&L_0f,
        &&L_10, &&L_11, &&L_12, &&L_13,
        &&L_14, &&L_15, &&L_16, &&L_17,
        &&L_18, &&L_19, &&L_1a, &&L_1b,
        &&L_1c, &&L_1d, &&L_1e, &&L_1f,
        &&L_20, &&L_21, &&L_22, &&L_23,
        &&L_24, &&L_25, &&L_26, &&L_27,
        &&L_28, &&L_29, &&L_2a, &&L_2b,
        &&L_2c, &&L_2d, &&L_2e, &&L_2f,
        &&L_30, &&L_31, &&L_32, &&L_33,
        &&L_34, &&L_35, &&L_36, &&L_37,
        &&L_38, &&L_39, &&L_3a, &&L_3b,
        &&L_3c, &&L_3d, &&L_3e, &&L_3f,
        &&L_40, &&L_41, &&L_42, &&L_43,
        &&L_44, &&L_45, &&L_46, &&L_47,
        &&L_48, &&L_49, &&L_4a, &&L_4b,
        &&L_4c, &&L_4d, &&L_4e, &&L_4f,
        &&L_50, &&L_51, &&L_52, &&L_53,
        &&L_54, &&L_55, &&L_56, &&L_57,
        &&L_58, &&L_59, &&L_5a, &&L_5b,
        &&L_5c, &&L_5d, &&L_5e, &&L_5f,
        &&L_60, &&L_61, &&L_62, &&L_63,
        &&L_64, &&L_65, &&L_66, &&L_67,
        &&L_68, &&L_69, &&L_6a, &&L_6b,
        &&L_6c, &&L_6d, &&L_6e, &&L_6f,
        &&L_70, &&L_71, &&L_72, &&L_73,
        &&L_74, &&L_75, &&L_76, &&L_77,
        &&L_78, &&L_79, &&L_7a, &&L_7b,
        &&L_7c, &&L_7d, &&L_7e, &&L_7f,
        &&L_80, &&L_81, &&L_82, &&L_83,
        &&L_84, &&L_85, &&L_86, &&L_87,
        &&L_88, &&L_89, &&L_8a, &&L_8b,
        &&L_8c, &&L_8d, &&L_8e, &&L_8f,
        &&L_90, &&L_91, &&L_92, &&L_93,
        &&L_94, &&L_95, &&L_96, &&L_97,
        &&L_98, &&L_99, &&L_9a, &&L_9b,
        &&L_9c, &&L_9d, &&L_9e, &&L_9f,
        &&L_a0, &&L_a1, &&L_a2, &&L_a3,
        &&L_a4, &&L_a5, &&L_a6, &&L_a7,
        &&L_a8, &&L_a9, &&L_aa, &&L_ab,
        &&L_ac, &&L_ad, &&L_ae, &&L_af,
        &&L_b0, &&L_b1, &&L_b2, &&L_b3,
        &&L_b4, &&L_b5, &&L_b6, &&L_b7,
        &&L_b8, &&L_b9, &&L_ba, &&L_bb,
        &&L_bc, &&L_bd, &&L_be, &&L_bf,
        &&L_c0, &&L_c1, &&L_c2, &&L_c3,
        &&L_c4, &&L_c5, &&L_c6, &&L_c7,
        &&L_c8, &&L_c9, &&L_default, &&L_default,
        &&L_default, &&L_default, &&L_default, &&L_default,
        &&L_d0, &&L_d1, &&L_d2, &&L_d3,
        &&L_d4, &&L_d5, &&L_d6, &&L_d7,
        &&L_d8, &&L_d9, &&L_da, &&L_db,
        &&L_default, &&L_default, &&L_default, &&L_default,
        &&L_default, &&L_default, &&L_default, &&L_default,
        &&L_default, &&L_default, &&L_default, &&L_default,
        &&L_default, &&L_default, &&L_default, &&L_default,
        &&L_default, &&L_default, &&L_default, &&L_default,
        &&L_default, &&L_default, &&L_default, &&L_default,
        &&L_default, &&L_default, &&L_default, &&L_default,
        &&L_default, &&L_default, &&L_default, &&L_default,
        &&L_default, &&L_default, &&L_default, &&L_default,
    };
    if (!t->depth) { t->state = THREAD_DEAD; return initial-budget; }
    RELOAD();
    if (t->exception) goto exception_caller;
    while (budget-- > 0) {
        start = pc;
        u8 op = *pc++;
#ifdef JVM_OP_STATS
        jvm_op_counts[op]++;
#endif
        switch (op) {
        case 0x00: L_00: break;
        case 0x01: L_01: PUSH(0); break;
        case 0x02: L_02: case 0x03: L_03: case 0x04: L_04: case 0x05: L_05: case 0x06: L_06: case 0x07: L_07: case 0x08: L_08: PUSH((i32)op-3); break;
        case 0x09: L_09: case 0x0a: L_0a: PUSHW((i64)(op-9)); break;
        case 0x0b: L_0b: case 0x0c: L_0c: case 0x0d: L_0d: PUSH(from_float((float)(op-0x0b))); break;
        case 0x0e: L_0e: case 0x0f: L_0f: PUSHW(from_double((double)(op-0x0e))); break;
        case 0x10: L_10: PUSH((i32)(int8_t)*pc++); break;
        case 0x11: L_11: PUSH((i32)(int16_t)rd16(pc)); pc += 2; break;
        case 0x12: L_12: case 0x13: L_13: {
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
        case 0x14: L_14: { Constant *k = &cp[rd16(pc)]; pc += 2; PUSHW(((u64)k->u.value << 32) | k[1].u.value); break; }
        case 0x15: L_15: case 0x17: L_17: case 0x19: L_19: PUSH(locals[*pc++]); break;
        case 0x16: L_16: case 0x18: L_18: PUSH(locals[*pc]); PUSH(locals[*pc+1]); pc++; break;
        case 0x1a: L_1a: case 0x1b: L_1b: case 0x1c: L_1c: case 0x1d: L_1d: PUSH(locals[op-0x1a]); break;
        case 0x1e: L_1e: case 0x1f: L_1f: case 0x20: L_20: case 0x21: L_21: PUSH(locals[op-0x1e]); PUSH(locals[op - 0x1e + 1]); break;
        case 0x22: L_22: case 0x23: L_23: case 0x24: L_24: case 0x25: L_25: PUSH(locals[op-0x22]); break;
        case 0x26: L_26: case 0x27: L_27: case 0x28: L_28: case 0x29: L_29: PUSH(locals[op-0x26]); PUSH(locals[op-0x26+1]); break;
        case 0x2a: L_2a: case 0x2b: L_2b: case 0x2c: L_2c: case 0x2d: L_2d: PUSH(locals[op-0x2a]); break;
        case 0x2e: L_2e: case 0x2f: L_2f: case 0x30: L_30: case 0x31: L_31: case 0x32: L_32: case 0x33: L_33: case 0x34: L_34: case 0x35: L_35: {
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
        case 0x36: L_36: case 0x38: L_38: case 0x3a: L_3a: locals[*pc++] = POP(); break;
        case 0x37: L_37: case 0x39: L_39: locals[*pc+1] = POP(); locals[*pc] = POP(); pc++; break;
        case 0x3b: L_3b: case 0x3c: L_3c: case 0x3d: L_3d: case 0x3e: L_3e: locals[op-0x3b] = POP(); break;
        case 0x3f: L_3f: case 0x40: L_40: case 0x41: L_41: case 0x42: L_42: locals[op-0x3f+1] = POP(); locals[op-0x3f] = POP(); break;
        case 0x43: L_43: case 0x44: L_44: case 0x45: L_45: case 0x46: L_46: locals[op-0x43] = POP(); break;
        case 0x47: L_47: case 0x48: L_48: case 0x49: L_49: case 0x4a: L_4a: locals[op-0x47+1] = POP(); locals[op-0x47] = POP(); break;
        case 0x4b: L_4b: case 0x4c: L_4c: case 0x4d: L_4d: case 0x4e: L_4e: locals[op-0x4b] = POP(); break;
        case 0x4f: L_4f: case 0x50: L_50: case 0x51: L_51: case 0x52: L_52: case 0x53: L_53: case 0x54: L_54: case 0x55: L_55: case 0x56: L_56: {
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
        case 0x57: L_57: sp--; break;
        case 0x58: L_58: sp -= 2; break;
        case 0x59: L_59: sp[0] = sp[-1]; sp++; break;
        case 0x5a: L_5a: { u32 a = sp[-1], b = sp[-2]; sp[-2] = a; sp[-1] = b; sp[0] = a; sp++; break; }
        case 0x5b: L_5b: { u32 a = sp[-1], b = sp[-2], c = sp[-3]; sp[-3] = a; sp[-2] = c; sp[-1] = b; sp[0] = a; sp++; break; }
        case 0x5c: L_5c: sp[0] = sp[-2]; sp[1] = sp[-1]; sp += 2; break;
        case 0x5d: L_5d: { u32 a = sp[-1], b = sp[-2], c = sp[-3]; sp[-3] = b; sp[-2] = a; sp[-1] = c; sp[0] = b; sp[1] = a; sp += 2; break; }
        case 0x5e: L_5e: { u32 a = sp[-1], b = sp[-2], c = sp[-3], d = sp[-4];
                     sp[-4] = b; sp[-3] = a; sp[-2] = d; sp[-1] = c; sp[0] = b; sp[1] = a; sp += 2; break; }
        case 0x5f: L_5f: { u32 a = sp[-1]; sp[-1] = sp[-2]; sp[-2] = a; break; }
        case 0x60: L_60: { i32 b = (i32)POP(), a = (i32)POP(); PUSH((u32)a+(u32)b); break; }
        case 0x61: L_61: { u64 b = POPW(), a = POPW(); PUSHW(a+b); break; }
        case 0x62: L_62: { float b = as_float(POP()), a = as_float(POP()); PUSH(from_float(a+b)); break; }
        case 0x63: L_63: { double b = as_double(POPW()), a = as_double(POPW()); PUSHW(from_double(a+b)); break; }
        case 0x64: L_64: { u32 b = POP(), a = POP(); PUSH(a-b); break; }
        case 0x65: L_65: { u64 b = POPW(), a = POPW(); PUSHW(a-b); break; }
        case 0x66: L_66: { float b = as_float(POP()), a = as_float(POP()); PUSH(from_float(a-b)); break; }
        case 0x67: L_67: { double b = as_double(POPW()), a = as_double(POPW()); PUSHW(from_double(a-b)); break; }
        case 0x68: L_68: { u32 b = POP(), a = POP(); PUSH(a*b); break; }
        case 0x69: L_69: { u64 b = POPW(), a = POPW(); PUSHW(a*b); break; }
        case 0x6a: L_6a: { float b = as_float(POP()), a = as_float(POP()); PUSH(from_float(a*b)); break; }
        case 0x6b: L_6b: { double b = as_double(POPW()), a = as_double(POPW()); PUSHW(from_double(a*b)); break; }
        case 0x6c: L_6c: case 0x70: L_70: {
            i32 b = (i32)POP(), a = (i32)POP();
            if (!b) THROW("java/lang/ArithmeticException", "/ by zero");
            if (b == -1) PUSH(op == 0x6c ? (u32)0-(u32)a : 0);
            else PUSH(op == 0x6c ? a/b : a%b);
            break;
        }
        case 0x6d: L_6d: case 0x71: L_71: {
            i64 b = (i64)POPW(), a = (i64)POPW();
            if (!b) THROW("java/lang/ArithmeticException", "/ by zero");
            if (b == -1) PUSHW(op == 0x6d ? (u64)0-(u64)a : 0);
            else PUSHW(op == 0x6d ? a/b : a%b);
            break;
        }
        case 0x6e: L_6e: { float b = as_float(POP()), a = as_float(POP()); PUSH(from_float(a/b)); break; }
        case 0x6f: L_6f: { double b = as_double(POPW()), a = as_double(POPW()); PUSHW(from_double(a/b)); break; }
        case 0x72: L_72: { float b = as_float(POP()), a = as_float(POP()); PUSH(from_float((float)jvm_fmod(a, b))); break; }
        case 0x73: L_73: { double b = as_double(POPW()), a = as_double(POPW()); PUSHW(from_double(jvm_fmod(a, b))); break; }
        case 0x74: L_74: PUSH((u32)0-POP()); break;
        case 0x75: L_75: PUSHW((u64)0-POPW()); break;
        case 0x76: L_76: PUSH(POP() ^ 0x80000000u); break;
        case 0x77: L_77: { u64 v = POPW(); PUSHW(v ^ 0x8000000000000000ull); break; }
        case 0x78: L_78: { u32 s = POP() & 31, a = POP(); PUSH(a << s); break; }
        case 0x79: L_79: { u32 s = POP() & 63; u64 a = POPW(); PUSHW(a << s); break; }
        case 0x7a: L_7a: { u32 s = POP() & 31; i32 a = (i32)POP(); PUSH(a >> s); break; }
        case 0x7b: L_7b: { u32 s = POP() & 63; i64 a = (i64)POPW(); PUSHW(a >> s); break; }
        case 0x7c: L_7c: { u32 s = POP() & 31, a = POP(); PUSH(a >> s); break; }
        case 0x7d: L_7d: { u32 s = POP() & 63; u64 a = POPW(); PUSHW(a >> s); break; }
        case 0x7e: L_7e: { u32 b = POP(), a = POP(); PUSH(a & b); break; }
        case 0x7f: L_7f: { u64 b = POPW(), a = POPW(); PUSHW(a & b); break; }
        case 0x80: L_80: { u32 b = POP(), a = POP(); PUSH(a | b); break; }
        case 0x81: L_81: { u64 b = POPW(), a = POPW(); PUSHW(a | b); break; }
        case 0x82: L_82: { u32 b = POP(), a = POP(); PUSH(a ^ b); break; }
        case 0x83: L_83: { u64 b = POPW(), a = POPW(); PUSHW(a ^ b); break; }
        case 0x84: L_84: locals[pc[0]] += (u32)(i32)(int8_t)pc[1]; pc += 2; break;
        case 0x85: L_85: PUSHW((u64)(i64)(i32)POP()); break;
        case 0x86: L_86: PUSH(from_float((float)(i32)POP())); break;
        case 0x87: L_87: PUSHW(from_double((double)(i32)POP())); break;
        case 0x88: L_88: { u64 v = POPW(); PUSH((u32)v); break; }
        case 0x89: L_89: PUSH(from_float((float)(i64)POPW())); break;
        case 0x8a: L_8a: PUSHW(from_double((double)(i64)POPW())); break;
        case 0x8b: L_8b: PUSH(f2i(as_float(POP()))); break;
        case 0x8c: L_8c: PUSHW(f2l(as_float(POP()))); break;
        case 0x8d: L_8d: PUSHW(from_double(as_float(POP()))); break;
        case 0x8e: L_8e: PUSH(f2i(as_double(POPW()))); break;
        case 0x8f: L_8f: PUSHW(f2l(as_double(POPW()))); break;
        case 0x90: L_90: PUSH(from_float((float)as_double(POPW()))); break;
        case 0x91: L_91: PUSH((i32)(int8_t)POP()); break;
        case 0x92: L_92: PUSH((u16)POP()); break;
        case 0x93: L_93: PUSH((i32)(int16_t)POP()); break;
        case 0x94: L_94: { i64 b = (i64)POPW(), a = (i64)POPW(); PUSH((u32)(a < b ? -1 : a > b)); break; }
        case 0x95: L_95: case 0x96: L_96: { float b = as_float(POP()), a = as_float(POP()); PUSH(compare(a, b, op == 0x95 ? -1 : 1)); break; }
        case 0x97: L_97: case 0x98: L_98: { double b = as_double(POPW()), a = as_double(POPW()); PUSH(compare(a, b, op == 0x97 ? -1 : 1)); break; }
        case 0x99: L_99: case 0x9a: L_9a: case 0x9b: L_9b: case 0x9c: L_9c: case 0x9d: L_9d: case 0x9e: L_9e: {
            i32 v = (i32)POP();
            int jump = op == 0x99 ? v == 0 : op == 0x9a ? v != 0 : op == 0x9b ? v < 0 : op == 0x9c ? v >= 0 : op == 0x9d ? v > 0 : v <= 0;
            pc = jump ? start+(int16_t)rd16(pc) : pc+2;
            BACKEDGE();
            break;
        }
        case 0x9f: L_9f: case 0xa0: L_a0: case 0xa1: L_a1: case 0xa2: L_a2: case 0xa3: L_a3: case 0xa4: L_a4: {
            i32 b = (i32)POP(), a = (i32)POP();
            int jump = op == 0x9f ? a == b : op == 0xa0 ? a != b : op == 0xa1 ? a < b : op == 0xa2 ? a >= b : op == 0xa3 ? a > b : a <= b;
            pc = jump ? start+(int16_t)rd16(pc) : pc+2;
            BACKEDGE();
            break;
        }
        case 0xa5: L_a5: case 0xa6: L_a6: { u32 b = POP(), a = POP(); pc = (op == 0xa5) == (a == b) ? start+(int16_t)rd16(pc) : pc+2; BACKEDGE(); break; }
        case 0xa7: L_a7: pc = start+(int16_t)rd16(pc); BACKEDGE(); break;
        case 0xa8: L_a8: PUSH((u32)(pc+2-m->code)); pc = start+(int16_t)rd16(pc); break;
        case 0xa9: L_a9: pc = m->code+locals[*pc]; break;
        case 0xaa: L_aa: {
            const u8 *p = m->code+(((u32)(start-m->code)+4) & ~3u);
            i32 index = (i32)POP(), low = (i32)rd32(p+4), high = (i32)rd32(p+8);
            pc = start+(i32)rd32(index < low || index > high ? p : p+12+(index-low)*4);
            break;
        }
        case 0xab: L_ab: {
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
        case 0xac: L_ac: case 0xad: L_ad: case 0xae: L_ae: case 0xaf: L_af: case 0xb0: L_b0: case 0xb1: L_b1: {
            u32 slots = op == 0xb1 ? 0 : (op == 0xad || op == 0xaf) ? 2 : 1;
            u32 *result = sp-slots;
            if (f->monitor) {
                Monitor *mon = monitor_for(vm, f->monitor, 0);
                if (mon && mon->owner == t) release_monitor(vm, mon);
            }
            if ((m->access & ACC_STATIC) && m->name[2] == '<') m->owner->state = CLASS_INITIALIZED;
            u32 *target = f->locals;
            for (u32 i = 0; i < slots; i++) target[i] = result[i];
            t->depth--;
            if (!t->depth) { t->state = THREAD_DEAD; return initial-budget; }
            RELOAD();
            sp = target+slots;
            if (m->jit & JIT_COMPILED) JIT_GO();  /* back into the caller's compiled loop */
            break;
        }
        case 0xb2: L_b2: case 0xb3: L_b3: case 0xb4: L_b4: case 0xb5: L_b5: {
            u16 index = (u16)rd16(pc);
            pc += 2;
            SAVE();
            Field *fd = resolve_field(vm, m->owner, index);
            CHECK_VM();
            int wide = fd->desc[2] == 'J' || fd->desc[2] == 'D';
            if (op >= 0xb4 && fd->offset < 65536) {
                u8 *code = (u8 *)start;
                code[0] = (u8)(op == 0xb4 ? (wide ? GETFIELD_Q_WIDE : GETFIELD_Q) : (wide ? PUTFIELD_Q_WIDE : PUTFIELD_Q));
                code[1] = (u8)(fd->offset >> 8);
                code[2] = (u8)fd->offset;
            }
            if (op <= 0xb3) {
                int ready = ensure_initialized(t, fd->owner, sp);
                if (ready < 0) goto exception_exact_saved;
                if (!ready) { f->pc = start; RELOAD(); break; }
                if (fd->owner->state == CLASS_INITIALIZED)
                    *(u8 *)start = (u8)(op == 0xb2 ? (wide ? GETSTATIC_Q_WIDE : GETSTATIC_Q) : (wide ? PUTSTATIC_Q_WIDE : PUTSTATIC_Q));
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
        case 0xb6: L_b6: case 0xb7: L_b7: case 0xb8: L_b8: case 0xb9: L_b9: case INVOKEVIRTUAL_SLOW: L_db: {
            int slow = op == INVOKEVIRTUAL_SLOW;
            if (slow) op = 0xb6;
            u16 index = (u16)rd16(pc);
            pc += op == 0xb9 ? 4 : 2;
            SAVE();
            Method *target = resolve_method(vm, m->owner, index);
            CHECK_VM();
            u32 *args = sp-target->arg_slots;
            if (op == 0xb6 && !slow && !(target->owner->access & ACC_INTERFACE) && !(target->access & ACC_PRIVATE))
                *(u8 *)start = INVOKEVIRTUAL_Q;
            else if (op == 0xb7 && PLAIN(target) && !(target->owner->access & ACC_INTERFACE))
                *(u8 *)start = INVOKESPECIAL_Q;
            if (op == 0xb8) {
                int ready = ensure_initialized(t, target->owner, sp);
                if (ready < 0) goto exception_exact_saved;
                if (!ready) { f->pc = start; RELOAD(); break; }
                if (target->owner->state == CLASS_INITIALIZED && QUICK_OK(target)) *(u8 *)start = INVOKESTATIC_Q;
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
                if (!monitor_enter(t, lock)) { f->pc = start; return initial-budget; }
                if (target->access & ACC_NATIVE) { /* natives are short; release right after */ }
                else {
                    f->pc = pc; f->sp = args;
                    if (!push_frame(t, target, args)) goto exception_caller;
                    t->frames[t->depth-1].monitor = lock;
                    RELOAD();
                    HOT();
                    break;
                }
            }
            if (target->access & ACC_NATIVE) {
                if (!target->native) {
                    char name[64];
                    u32 length = utf8_length(target->name);
                    if (length > sizeof(name)-1) length = sizeof(name)-1;
                    jvm_memcpy(name, utf8_bytes(target->name), length);
                    name[length] = 0;
                    f->pc = start; f->sp = sp;
                    throw_new(t, "java/lang/UnsatisfiedLinkError", name);
                    goto exception_exact;
                }
                u32 result[2] = {0, 0};
                f->pc = start; f->sp = sp;
                int status = target->native(t, args, result);
                if (target->access & ACC_SYNCHRONIZED) {
                    Object *lock = target->access & ACC_STATIC ? target->owner->mirror : REF(args[0]);
                    Monitor *mon = monitor_for(vm, lock, 0);
                    if (mon && mon->owner == t) release_monitor(vm, mon);
                }
                CHECK_VM();
                if (t->exception) goto exception_exact;
                /* NATIVE_PUSHED: the native set this frame's sp and pushed a Java
                   frame whose return completes the call. NATIVE_RETRY: it pushed
                   <clinit> frames; the invoke runs again once they return. */
                if (status == NATIVE_PUSHED) { f->pc = pc; RELOAD(); break; }
                if (status == NATIVE_RETRY) { RELOAD(); break; }
                sp = args;
                for (u32 i = 0; i < target->return_slots; i++) PUSH(result[i]);
                f->pc = pc; f->sp = sp;
                if (t->state != THREAD_RUNNABLE) return initial-budget;
                break;
            }
            if (target->access & ACC_ABSTRACT) THROW("java/lang/AbstractMethodError", 0);
            f->pc = pc; f->sp = args;
            if (!push_frame(t, target, args)) goto exception_caller;
            RELOAD();
            HOT();
            break;
        }
        case GETFIELD_Q: L_d0: {
            u32 offset = rd16(pc);
            pc += 2;
            Object *o = REF(sp[-1]);
            NULL_CHECK(o);
            sp[-1] = *(u32 *)((u8 *)o+offset);
            break;
        }
        case GETFIELD_Q_WIDE: L_d1: {
            u32 offset = rd16(pc);
            pc += 2;
            Object *o = REF(POP());
            NULL_CHECK(o);
            u32 *slot = (u32 *)((u8 *)o+offset);
            PUSH(slot[0]); PUSH(slot[1]);
            break;
        }
        case PUTFIELD_Q: L_d2: {
            u32 offset = rd16(pc);
            pc += 2;
            u32 value = POP();
            Object *o = REF(POP());
            NULL_CHECK(o);
            *(u32 *)((u8 *)o+offset) = value;
            break;
        }
        case PUTFIELD_Q_WIDE: L_d3: {
            u32 offset = rd16(pc);
            pc += 2;
            u32 lo = POP(), hi = POP();
            Object *o = REF(POP());
            NULL_CHECK(o);
            u32 *slot = (u32 *)((u8 *)o+offset);
            slot[0] = hi; slot[1] = lo;
            break;
        }
        case GETSTATIC_Q: L_d4: case GETSTATIC_Q_WIDE: L_d5: case PUTSTATIC_Q: L_d6: case PUTSTATIC_Q_WIDE: L_d7: {
            Field *fd = cp[rd16(pc)].u.field;
            pc += 2;
            u32 *slot = &fd->owner->statics[fd->offset];
            if (op == GETSTATIC_Q) PUSH(slot[0]);
            else if (op == GETSTATIC_Q_WIDE) { PUSH(slot[0]); PUSH(slot[1]); }
            else if (op == PUTSTATIC_Q) slot[0] = POP();
            else { slot[1] = POP(); slot[0] = POP(); }
            break;
        }
        case INVOKEVIRTUAL_Q: L_d8: case INVOKESPECIAL_Q: L_da: case INVOKESTATIC_Q: L_d9: {
            Method *target = cp[rd16(pc)].u.method;
            u32 *args = sp-target->arg_slots;
            if (op != INVOKESTATIC_Q) {
                Object *receiver = REF(args[0]);
                NULL_CHECK(receiver);
                if (op == INVOKEVIRTUAL_Q) {
                    target = receiver->cls->vtable[target->vtable_index];
                    if (!target || !QUICK_OK(target)) {
                        /* A synchronized, abstract or unbound override: the general path, for good. */
                        *(u8 *)start = INVOKEVIRTUAL_SLOW;
                        pc = start;
                        break;
                    }
                }
            }
            pc += 2;
            if (target->access & ACC_NATIVE) {
                /* As on the general path. */
                u32 result[2] = {0, 0};
                f->pc = start; f->sp = sp;
                int status = target->native(t, args, result);
                CHECK_VM();
                if (t->exception) goto exception_exact;
                if (status == NATIVE_PUSHED) { f->pc = pc; RELOAD(); break; }
                if (status == NATIVE_RETRY) { RELOAD(); break; }
                sp = args;
                for (u32 i = 0; i < target->return_slots; i++) PUSH(result[i]);
                f->pc = pc; f->sp = sp;
                if (t->state != THREAD_RUNNABLE) return initial-budget;
                break;
            }
            f->pc = pc; f->sp = args;
            if (!push_frame(t, target, args)) goto exception_caller;
            RELOAD();
            HOT();
            break;
        }
        case 0xba: L_ba: SAVE(); vm_fail(vm, "invokedynamic is not supported", 0); return initial-budget;
        case 0xbb: L_bb: {
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
        case 0xbc: L_bc: case 0xbd: L_bd: {
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
            if (!a) {
                /* Say what did not fit: "<descriptor> length <count>". */
                char text[48];
                u32 n = 0, length = utf8_length(c->name);
                for (u32 i = 0; i < length && n < 24; i++) text[n++] = utf8_bytes(c->name)[i];
                const char *word = " length ";
                while (*word) text[n++] = *word++;
                char digits[12];
                u32 d = 0, v = (u32)count;
                do { digits[d++] = (char)('0'+v%10); v /= 10; } while (v);
                while (d) text[n++] = digits[--d];
                text[n] = 0;
                THROW("java/lang/OutOfMemoryError", text);
            }
            PUSH((uintptr_t)a);
            break;
        }
        case 0xbe: L_be: { Array *a = (Array *)REF(POP()); NULL_CHECK(a); PUSH(a->length); break; }
        case 0xbf: L_bf: {
            Object *e = REF(POP());
            NULL_CHECK(e);
            f->pc = start; f->sp = sp;
            t->exception = e;
            goto exception_exact;
        }
        case 0xc0: L_c0: case 0xc1: L_c1: {
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
        case 0xc2: L_c2: case 0xc3: L_c3: {
            Object *o = REF(POP());
            NULL_CHECK(o);
            if (op == 0xc2) {
                if (!monitor_enter(t, o)) { sp++; f->pc = start; f->sp = sp; return initial-budget; }
            } else if (!monitor_exit(t, o)) goto exception_exact_unsaved;
            break;
        }
        case 0xc4: L_c4: {
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
            default: SAVE(); vm_fail(vm, "bad wide instruction", 0); return initial-budget;
            }
            break;
        }
        case 0xc5: L_c5: {
            u16 index = (u16)rd16(pc);
            u32 dims = pc[2];
            pc += 3;
            SAVE();
            Class *c = resolve_class(vm, m->owner, index);
            CHECK_VM();
            u32 counts[8];
            if (dims > 8) { vm_fail(vm, "too many array dimensions", 0); return initial-budget; }
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
        case 0xc6: L_c6: case 0xc7: L_c7: { u32 v = POP(); pc = (op == 0xc6) == !v ? start+(int16_t)rd16(pc) : pc+2; BACKEDGE(); break; }
        case 0xc8: L_c8: pc = start+(i32)rd32(pc); BACKEDGE(); break;
        case 0xc9: L_c9: PUSH((u32)(pc+4-m->code)); pc = start+(i32)rd32(pc); break;
        default: L_default:
            SAVE();
            vm_fail(vm, "unknown bytecode", 0);
            return initial-budget;
        }
        /* Threaded dispatch: straight to the next handler (no bounds check, no
           jump back to the loop head); the loop head only starts the slice. */
        if (budget-- <= 0) break;
        start = pc;
        op = *pc++;
#ifdef JVM_OP_STATS
        jvm_op_counts[op]++;
#endif
        goto *dispatch[op];
exception_exact_unsaved:
        f->pc = start; f->sp = sp;
exception_exact:
exception_exact_saved:
        if (vm->status == JVM_ERROR) return initial-budget;
        if (!handle_exception(t, 1)) return initial-budget;
        RELOAD();
        continue;
exception_caller:
        if (vm->status == JVM_ERROR) return initial-budget;
        if (!handle_exception(t, 0)) return initial-budget;
        RELOAD();
    }
    SAVE();
    return (u32)(initial-budget);
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

/* Bytecodes a thread runs before the next runnable thread gets its turn. Phone
   JVMs switch on a timer of tens of milliseconds; switching every few thousand
   bytecodes exposes races in MIDlets (Opera Mini 4.2 feeds its 4-byte entropy
   buffer from two threads under different locks and overran it at startup with
   4000). A thread that sleeps or waits yields at once. The turn survives across
   jvm_run calls, so callers may run the VM in small budgets. */
#define THREAD_SLICE 16000

/* The runnable thread after `from` in list order (wrapping), or 0. */
static Thread *next_runnable(Jvm *vm, Thread *from) {
    Thread *t = from;
    for (Thread *k = vm->threads; k; k = k->next) {
        t = t && t->next ? t->next : vm->threads;
        if (t->state == THREAD_RUNNABLE) return t;
    }
    return 0;
}

static int run(Jvm *vm, u32 budget);

int jvm_run(Jvm *vm, u32 budget) {
    int here = 0;
    vm->stack_base = &here+1;  /* compaction scans the C stack up to here */
    int status = run(vm, budget);
    vm->stack_base = 0;
    return status;
}

static int run(Jvm *vm, u32 budget) {
    jvm_current_vm = vm;
    if (vm->status == JVM_ERROR) return JVM_ERROR;
    if (vm->halted) return JVM_DONE;
    while (budget) {
        wake_threads(vm);
        Thread *t = vm->current;
        if (!t || t->state != THREAD_RUNNABLE || !vm->turn) {
            t = next_runnable(vm, vm->current);
            if (!t) {
                for (Thread *k = vm->threads; k; k = k->next)
                    if (k->state != THREAD_DEAD) return JVM_IDLE;
                return JVM_DONE;
            }
            vm->turn = THREAD_SLICE;
        }
        vm->current = t;
        /* Charge what ran: a thread that soon sleeps or waits leaves the rest
           of the budget to the others. */
        u32 used = execute(vm, t, budget < vm->turn ? budget : vm->turn);
        if (!used) used = 1;
        budget -= used < budget ? used : budget;
        vm->turn -= used < vm->turn ? used : vm->turn;
        if (t->state != THREAD_RUNNABLE) vm->turn = 0;
        if (vm->status == JVM_ERROR) return JVM_ERROR;
        if (vm->halted) return JVM_DONE;
    }
    return JVM_RUNNING;
}

uint32_t jvm_next_wake(Jvm *vm) {
    u32 best = 0xffffffffu;
    for (Thread *t = vm->threads; t; t = t->next)
        if ((t->state == THREAD_SLEEPING || (t->state == THREAD_WAITING && t->wake)) && t->wake < best) best = t->wake;
    return best;
}

/* Debugging (host profiler): "Class.method" on top of the current thread. */
void jvm_debug_where(Jvm *vm, char *out, uint32_t capacity) {
    Thread *t = vm->current;
    out[0] = 0;
    if (!t || !t->depth || capacity < 4) return;
    Method *m = t->frames[t->depth-1].method;
    uint32_t n = 0, a = utf8_length(m->owner->name), b = utf8_length(m->name);
    for (uint32_t i = 0; i < a && n+2 < capacity; i++) out[n++] = (char)m->owner->name[2+i];
    out[n++] = '.';
    for (uint32_t i = 0; i < b && n+1 < capacity; i++) out[n++] = (char)m->name[2+i];
    uint32_t c = utf8_length(m->desc);
    for (uint32_t i = 0; i < c && n+1 < capacity; i++) out[n++] = (char)m->desc[2+i];
    /* the bytecode length, for the profile */
    char digits[12]; uint32_t d = 0, len = m->code_length;
    do { digits[d++] = (char)('0'+len%10); len /= 10; } while (len);
    if (n+2+d < capacity) { out[n++] = ' '; out[n++] = '#'; while (d) out[n++] = digits[--d]; }
    out[n] = 0;
}

/* Debugging (emulator probes): a line per thread with its state, waiting object
   and frames as "Class.method@offset", innermost first. */
void jvm_debug_threads(Jvm *vm, char *out, uint32_t capacity) {
    uint32_t n = 0;
#define OUT(c) do { if (n+1 < capacity) out[n++] = (char)(c); } while (0)
    /* first line: compiled methods and their bytes */
    uint32_t values[2] = {vm->jit_count, vm->jit_total};
    for (int k = 0; k < 2; k++) {
        char digits[12]; uint32_t d = 0, v = values[k];
        do { digits[d++] = (char)('0'+v%10); v /= 10; } while (v);
        OUT('j'); while (d) OUT(digits[--d]); OUT(' ');
    }
    OUT('\n');
    for (Thread *t = vm->threads; t; t = t->next) {
        OUT('0'+t->state); OUT(t == vm->current ? '*' : ' ');
        uint32_t w = (uint32_t)(uintptr_t)t->waiting_on;
        for (int s = 28; s >= 0; s -= 4) OUT("0123456789abcdef"[(w >> s) & 15]);
        for (uint32_t d = t->depth; d-- > 0 && n+80 < capacity;) {
            Frame *f = &t->frames[d];
            Method *m = f->method;
            OUT(' ');
            for (uint32_t i = 0; i < utf8_length(m->owner->name) && i < 24; i++) OUT(m->owner->name[2+i]);
            OUT('.');
            for (uint32_t i = 0; i < utf8_length(m->name) && i < 16; i++) OUT(m->name[2+i]);
            OUT('@');
            char digits[12]; uint32_t k = 0, v = (uint32_t)(f->pc-m->code);
            do { digits[k++] = (char)('0'+v%10); v /= 10; } while (v);
            while (k) OUT(digits[--k]);
            if (m->jit & JIT_COMPILED) OUT('J');
        }
        OUT('\n');
    }
#undef OUT
    out[n] = 0;
}
