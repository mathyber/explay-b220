/* Object heap: first-fit free list, non-moving mark-sweep collector.

Java fields and stack slots hold 32-bit references, so the VM requires 32-bit
pointers (the host build targets x86 Windows for the same reason). Object fields
are scanned precisely with the class reference map; thread stacks are scanned
conservatively against the bitmap of object starts. */
#include "internal.h"

_Static_assert(sizeof(void *) == 4, "References are 32-bit");

#define SIZE(o) ((o)->info >> 2)
#define MARK 1u
#define NEXT_FREE(o) (*(Object **)FIELDS(o))
#define MIN_GRANULES 2

static u32 granule_index(Jvm *vm, const void *p) { return (u32)((const u8 *)p-vm->heap)/GRANULE; }
static void set_start(Jvm *vm, Object *o) { u32 i = granule_index(vm, o); vm->starts[i/32] |= 1u << (i%32); }
static void clear_start(Jvm *vm, Object *o) { u32 i = granule_index(vm, o); vm->starts[i/32] &= ~(1u << (i%32)); }

int is_object(Jvm *vm, u32 value) {
    const u8 *p = (const u8 *)(uintptr_t)value;
    if (p < vm->heap || p >= vm->heap_end || (p-vm->heap) % GRANULE) return 0;
    u32 i = granule_index(vm, p);
    return (vm->starts[i/32] >> (i%32)) & 1;
}

void push_root(Jvm *vm, Object *o) {
    if (vm->temp_count < MAX_TEMP_ROOTS) vm->temp_roots[vm->temp_count++] = o;
    else vm_fail(vm, "too many temporary roots", 0);
}
void pop_roots(Jvm *vm, u32 count) { vm->temp_count -= count; }

/* The free list is in address order. Objects come from the start of the
   lowest block that fits, so the heap fills densely from the bottom; scratch
   buffers (top) come from the end of the highest one, away from objects that
   stay, and leave one large hole when they go. */
static Object *take(Jvm *vm, u32 g, int top) {
    Object *previous = 0, *chosen = 0, *chosen_previous = 0;
    for (Object *f = vm->free_list; f; previous = f, f = NEXT_FREE(f)) {
        if (SIZE(f) < g) continue;
        chosen = f;
        chosen_previous = previous;
        if (!top) break;
    }
    if (!chosen) return 0;
    Object *f = chosen, *o;
    u32 size = SIZE(f);
    if (size-g < MIN_GRANULES) {
        if (chosen_previous) NEXT_FREE(chosen_previous) = NEXT_FREE(f); else vm->free_list = NEXT_FREE(f);
        o = f;
        g = size;
    } else if (top) {
        f->info = (size-g) << 2;
        o = (Object *)((u8 *)f+(size-g)*GRANULE);
    } else {
        Object *rest = (Object *)((u8 *)f+g*GRANULE);
        rest->cls = 0;
        rest->info = (size-g) << 2;
        NEXT_FREE(rest) = NEXT_FREE(f);
        if (chosen_previous) NEXT_FREE(chosen_previous) = rest; else vm->free_list = rest;
        o = f;
    }
    jvm_memset(o, 0, g*GRANULE);
    o->info = g << 2;
    set_start(vm, o);
    vm->used += g*GRANULE;
    return o;
}

static Object *allocate_at(Jvm *vm, u32 bytes, int top) {
    u32 g = (bytes+GRANULE-1)/GRANULE;
    if (g < MIN_GRANULES) g = MIN_GRANULES;
    Object *o = take(vm, g, top);
    if (!o) { collect(vm); o = take(vm, g, top); }
    return o;
}

static Object *allocate(Jvm *vm, u32 bytes) { return allocate_at(vm, bytes, 0); }

Object *new_object(Jvm *vm, Class *c) {
    Object *o = allocate(vm, c->instance_bytes);
    if (o) o->cls = c;
    return o;
}

u32 element_size(u8 type) {
    switch (type) {
    case 'Z': case 'B': return 1;
    case 'C': case 'S': return 2;
    case 'J': case 'D': return 8;
    default: return 4;
    }
}

Array *new_array(Jvm *vm, Class *c, u32 length) {
    u32 size = element_size(c->element_type);
    if (length > (0x7fffffffu-sizeof(Array))/size) return 0;
    Array *a = (Array *)allocate(vm, sizeof(Array)+length*size);
    if (a) { a->header.cls = c; a->length = length; }
    return a;
}

/* Marking. */
typedef struct Marker { Jvm *vm; u32 depth; int overflow; } Marker;

static void mark(Marker *m, u32 value) {
    Jvm *vm = m->vm;
    if (!value || !is_object(vm, value)) return;
    Object *o = (Object *)(uintptr_t)value;
    if (!o->cls || (o->info & MARK)) return;
    o->info |= MARK;
    if (m->depth < vm->marks_capacity) vm->marks_stack[m->depth++] = value;
    else m->overflow = 1;
}

static void scan(Marker *m, Object *o) {
    Class *c = o->cls;
    if (c->dimensions) {
        if (c->element_type == 'L' || c->element_type == '[') {
            Array *a = (Array *)o;
            u32 *e = (u32 *)ARRAY_DATA(a);
            for (u32 i = 0; i < a->length; i++) mark(m, e[i]);
        }
        return;
    }
    u32 words = c->instance_bytes/4;
    u32 *w = (u32 *)o;
    for (u32 i = 2; i < words; i++)
        if ((c->ref_map[i/32] >> (i%32)) & 1) mark(m, w[i]);
}

static void drain(Marker *m) {
    while (m->depth) scan(m, (Object *)(uintptr_t)m->vm->marks_stack[--m->depth]);
}

/* ---------- local variable liveness ----------
   A caller frame's local is a root only if some path from its return point
   reads it before writing it; a dead one (Opera Mini keeps a Vector filled to
   probe memory in such a local) does not keep objects alive. */

/* Instruction length; 0 for jsr/ret (analysis gives up) and unknown opcodes. */
static u32 instruction_length(const u8 *code, u32 pc, u32 size) {
    u8 op = code[pc];
    if (op == 0xaa || op == 0xab) {
        u32 p = (pc+4) & ~3u;
        if (p+12 > size) return 0;
        if (op == 0xaa) {
            i32 low = (i32)rd32(code+p+4), high = (i32)rd32(code+p+8);
            if (high < low || (u32)(high-low) > 0x4000) return 0;
            return p+12+(u32)(high-low+1)*4-pc;
        }
        u32 pairs = rd32(code+p+4);
        return pairs > 0x4000 ? 0 : p+8+pairs*8-pc;
    }
    if (op == 0xc4) return pc+1 < size && code[pc+1] == 0x84 ? 6 : 4;
    if (op == 0xa8 || op == 0xa9 || op == 0xc9) return 0;
    static const u8 lengths[0xca] = {
        1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1, 2,3,2,3,3,2,2,2,2,2,1,1,1,1,1,1,
        1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1, 1,1,1,1,1,1,2,2,2,2,2,1,1,1,1,1,
        1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1, 1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,
        1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1, 1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,
        1,1,1,1,3,1,1,1,1,1,1,1,1,1,1,1, 1,1,1,1,1,1,1,1,1,3,3,3,3,3,3,3,
        3,3,3,3,3,3,3,3,0,0,0,0,1,1,1,1, 1,1,3,3,3,3,3,3,3,5,5,3,2,3,1,1,
        3,3,1,1,0,4,3,3,5,0};
    return op < 0xca ? lengths[op] : 0;
}

/* Does the instruction at pc read (1) or write (2) local slot l? */
static int local_access(const u8 *code, u32 pc, u32 l) {
    u8 op = code[pc];
    u32 index, wide = 0, store = 0;
    if (op == 0xc4) { op = code[pc+1]; index = rd16(code+pc+2); }
    else if ((op >= 0x15 && op <= 0x19) || (op >= 0x36 && op <= 0x3a) || op == 0x84) index = code[pc+1];
    else if (op >= 0x1a && op <= 0x2d) { index = (u32)(op-0x1a)%4; op = (u8)(0x15+(op-0x1a)/4); }
    else if (op >= 0x3b && op <= 0x4e) { index = (u32)(op-0x3b)%4; op = (u8)(0x36+(op-0x3b)/4); }
    else return 0;
    if (op == 0x84) return index == l;  /* iinc: counted as a read */
    if (op >= 0x36 && op <= 0x3a) { store = 1; op = (u8)(op-0x21); }
    if (op < 0x15 || op > 0x19) return 0;
    wide = op == 0x16 || op == 0x18;
    if (l != index && !(wide && l == index+1)) return 0;
    return store ? 2 : 1;
}

#define LIVE_PENDING 512
static u8 visited[65536/8];
static u32 pending[LIVE_PENDING];

/* Is slot l read on some path from the instructions in starts[] before it is
   written? 1 also when the code is not understood (the safe answer). */
static int local_live(const Method *m, const u32 *starts, u32 start_count, u32 l) {
    const u8 *code = m->code;
    u32 size = m->code_length, count = 0;
    jvm_memset(visited, 0, (size+7)/8);
    for (u32 i = 0; i < start_count; i++) pending[count++] = starts[i];
    while (count) {
        u32 pc = pending[--count];
        if (pc >= size) return 1;
        if (visited[pc/8] >> (pc%8) & 1) continue;
        visited[pc/8] |= (u8)(1u << (pc%8));
        u32 length = instruction_length(code, pc, size);
        if (!length || pc+length > size) return 1;
        /* An exception here goes to every handler that covers it. */
        for (u32 i = 0; i < m->handler_count; i++)
            if (pc >= m->handlers[i].start && pc < m->handlers[i].end) {
                if (count == LIVE_PENDING) return 1;
                pending[count++] = m->handlers[i].handler;
            }
        int access = local_access(code, pc, l);
        if (access == 1) return 1;
        if (access == 2) continue;
        u8 op = code[pc];
        if ((op >= 0xac && op <= 0xb1) || op == 0xbf) continue;  /* return, athrow */
        if (count+2 >= LIVE_PENDING) return 1;
        if (op == 0xa7 || op == 0xc8) {
            pending[count++] = pc+(u32)(op == 0xa7 ? (i32)(int16_t)rd16(code+pc+1) : (i32)rd32(code+pc+1));
            continue;
        }
        if ((op >= 0x99 && op <= 0xa6) || op == 0xc6 || op == 0xc7) pending[count++] = pc+(u32)(i32)(int16_t)rd16(code+pc+1);
        if (op == 0xaa || op == 0xab) {
            u32 p = (pc+4) & ~3u, n = op == 0xaa ? (u32)((i32)rd32(code+p+8)-(i32)rd32(code+p+4)+1) : rd32(code+p+4);
            if (count+n+1 >= LIVE_PENDING) return 1;
            pending[count++] = pc+rd32(code+p);
            for (u32 i = 0; i < n; i++) pending[count++] = pc+rd32(code+p+(op == 0xaa ? 12+i*4 : 12+i*8));
            continue;
        }
        pending[count++] = pc+length;
    }
    return 0;
}

/* Roots of a thread: whole operand area and every local of the top frame
   (its pc may lag behind), operands and live locals of caller frames. Slots
   above the top frame are stale and ignored. */
static void mark_frames(Marker *mk, Thread *t) {
    Jvm *vm = mk->vm;
    for (u32 i = 0; i < t->depth; i++) {
        Frame *f = &t->frames[i];
        const Method *m = f->method;
        int top = i+1 == t->depth;
        u32 *end = top ? f->stack+m->max_stack : f->sp;
        for (u32 *s = f->stack; s < end && s < t->slots_end; s++) mark(mk, *s);
        u32 starts[2], start_count = 0;
        if (!top) {
            /* pc is after the invoke, or at an instruction to run again: start
               at the instruction holding pc-1 and the one holding pc. */
            u32 offset = (u32)(f->pc-m->code);
            for (u32 at = 0, length; at < m->code_length && at <= offset; at += length) {
                length = instruction_length(m->code, at, m->code_length);
                if (!length) { start_count = 0; break; }
                if (offset > 0 && at < offset && offset-1 < at+length) starts[start_count++] = at;
                if (offset < at+length && (!start_count || starts[start_count-1] != at)) starts[start_count++] = at;
            }
            if (!start_count) top = 1;
        }
        for (u32 l = 0; l < m->max_locals; l++) {
            u32 value = f->locals[l];
            if (!value || !is_object(vm, value)) continue;
            if (top || local_live(m, starts, start_count, l)) mark(mk, value);
        }
    }
}

void collect(Jvm *vm) {
    Marker m = {vm, 0, 0};
    for (Thread *t = vm->threads; t; t = t->next) {
        if (t->state == THREAD_DEAD) continue;  /* its stack array becomes garbage */
        mark(&m, (u32)(uintptr_t)t->stack);
        mark(&m, (u32)(uintptr_t)t->object);
        mark(&m, (u32)(uintptr_t)t->exception);
        mark(&m, (u32)(uintptr_t)t->waiting_on);
        mark_frames(&m, t);
        for (u32 i = 0; i < t->depth; i++) mark(&m, (u32)(uintptr_t)t->frames[i].monitor);
        drain(&m);
    }
    for (Class *c = vm->classes; c; c = c->next) {
        mark(&m, (u32)(uintptr_t)c->mirror);
        for (u32 i = 0; i < c->static_slots; i++)
            if ((c->static_ref_map[i/32] >> (i%32)) & 1) mark(&m, c->statics[i]);
        for (u32 i = 1; i < c->constant_count; i++)
            if (c->constants[i].tag == 8 && c->constants[i].resolved) mark(&m, (u32)(uintptr_t)c->constants[i].u.string);
        drain(&m);
    }
    for (u32 i = 0; i < vm->interned_count; i++) mark(&m, (u32)(uintptr_t)vm->interned[i]);
    for (u32 i = 0; i < vm->temp_count; i++) mark(&m, (u32)(uintptr_t)vm->temp_roots[i]);
    for (u32 i = 0; i < MAX_MONITORS; i++)
        if (vm->monitors[i].owner) mark(&m, (u32)(uintptr_t)vm->monitors[i].object);
    drain(&m);
    while (m.overflow) {
        m.overflow = 0;
        for (u8 *p = vm->heap; p < vm->heap_end; p += SIZE((Object *)p)*GRANULE) {
            Object *o = (Object *)p;
            if (o->cls && (o->info & MARK)) { scan(&m, o); drain(&m); }
        }
    }
    /* Sweep, merging neighbouring free blocks. Monitors of dead objects go too. */
    for (u32 i = 0; i < MAX_MONITORS; i++) {
        Object *o = vm->monitors[i].object;
        if (o && !vm->monitors[i].owner) vm->monitors[i].object = 0;
    }
    vm->free_list = 0;
    vm->used = 0;
    Object **tail = &vm->free_list;
    Object *run = 0;
    u32 run_size = 0;
    for (u8 *p = vm->heap; p < vm->heap_end;) {
        Object *o = (Object *)p;
        u32 size = SIZE(o);
        if (o->cls && (o->info & MARK)) {
            o->info &= ~MARK;
            vm->used += size*GRANULE;
            if (run) { run->info = run_size << 2; *tail = run; tail = &NEXT_FREE(run); run = 0; }
        } else {
            if (o->cls) clear_start(vm, o);
            o->cls = 0;
            if (!run) { run = o; run_size = 0; }
            run_size += size;
        }
        p += size*GRANULE;
    }
    if (run) { run->info = run_size << 2; *tail = run; tail = &NEXT_FREE(run); }
    *tail = 0;
    vm->gc_count++;
    if (vm->used > vm->peak_live) vm->peak_live = vm->used;
}

void jvm_heap_walk(Jvm *vm, void (*visit)(void *context, const char *name, uint32_t length, uint32_t bytes), void *context) {
    collect(vm);
    for (u8 *p = vm->heap; p < vm->heap_end; p += SIZE((Object *)p)*GRANULE) {
        Object *o = (Object *)p;
        if (o->cls) visit(context, utf8_bytes(o->cls->name), utf8_length(o->cls->name), SIZE(o)*GRANULE);
    }
}

/* Host buffers inside the heap (class files and resources while they are
   read): a byte[] kept alive as a temporary root until it is released. */
void *jvm_scratch_alloc(Jvm *vm, uint32_t size) {
    Class *bytes = primitive_array(vm, 'B');
    if (!bytes || vm->temp_count >= MAX_TEMP_ROOTS || size > 0x7fffffffu-sizeof(Array)) return 0;
    Array *a = (Array *)allocate_at(vm, sizeof(Array)+size, 1);
    if (!a) return 0;
    a->header.cls = bytes;
    a->length = size;
    vm->temp_roots[vm->temp_count++] = &a->header;
    return ARRAY_DATA(a);
}

int jvm_scratch_release(Jvm *vm, void *data) {
    for (u32 i = vm->temp_count; i-- > 0;)
        if (ARRAY_DATA((Array *)vm->temp_roots[i]) == data) {
            for (u32 j = i+1; j < vm->temp_count; j++) vm->temp_roots[j-1] = vm->temp_roots[j];
            vm->temp_count--;
            return 1;
        }
    return 0;
}
