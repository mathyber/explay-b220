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

static Object *take(Jvm *vm, u32 g) {
    Object *previous = 0;
    for (Object *f = vm->free_list; f; previous = f, f = NEXT_FREE(f)) {
        u32 size = SIZE(f);
        if (size < g) continue;
        Object *o;
        if (size-g >= MIN_GRANULES) {
            f->info = (size-g) << 2;
            o = (Object *)((u8 *)f+(size-g)*GRANULE);
        } else {
            if (previous) NEXT_FREE(previous) = NEXT_FREE(f); else vm->free_list = NEXT_FREE(f);
            o = f;
            g = size;
        }
        jvm_memset(o, 0, g*GRANULE);
        o->info = g << 2;
        set_start(vm, o);
        vm->used += g*GRANULE;
        return o;
    }
    return 0;
}

static Object *allocate(Jvm *vm, u32 bytes) {
    u32 g = (bytes+GRANULE-1)/GRANULE;
    if (g < MIN_GRANULES) g = MIN_GRANULES;
    Object *o = take(vm, g);
    if (!o) { collect(vm); o = take(vm, g); }
    return o;
}

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

void collect(Jvm *vm) {
    Marker m = {vm, 0, 0};
    for (Thread *t = vm->threads; t; t = t->next) {
        mark(&m, (u32)(uintptr_t)t->object);
        mark(&m, (u32)(uintptr_t)t->exception);
        mark(&m, (u32)(uintptr_t)t->waiting_on);
        for (u32 *s = t->slots; s < t->slots_end; s++) mark(&m, *s);
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
}
