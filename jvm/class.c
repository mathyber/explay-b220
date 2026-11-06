/* Class loading, linking and constant pool resolution. */
#include "internal.h"

Jvm *jvm_current_vm;

void *jvm_memset(void *d, int c, u32 n) { u8 *p = d; while (n--) *p++ = (u8)c; return d; }
void *jvm_memcpy(void *d, const void *s, u32 n) {
    u8 *p = d; const u8 *q = s;
    if (p < q) while (n--) *p++ = *q++;
    else { p += n; q += n; while (n--) *--p = *--q; }
    return d;
}
int jvm_memcmp(const void *a, const void *b, u32 n) {
    const u8 *p = a, *q = b;
    for (; n; n--, p++, q++) if (*p != *q) return *p < *q ? -1 : 1;
    return 0;
}
u32 jvm_strlen(const char *s) { u32 n = 0; while (s[n]) n++; return n; }

void vm_fail(Jvm *vm, const char *message, Utf8 detail) {
    if (vm->status == JVM_ERROR) return;
    vm->status = JVM_ERROR;
    u32 n = 0;
    while (message[n] && n < sizeof(vm->error)-1) { vm->error[n] = message[n]; n++; }
    if (detail) {
        u32 length = utf8_length(detail);
        const char *bytes = utf8_bytes(detail);
        if (n < sizeof(vm->error)-2) vm->error[n++] = ' ';
        for (u32 i = 0; i < length && n < sizeof(vm->error)-1; i++) vm->error[n++] = bytes[i];
    }
    vm->error[n] = 0;
}

/* Zeroed metadata, taken downward from the top; align 8 for structures, 1 for
   strings and bytecode. Past the heap's end it takes the heap's free tail. */
#define EXTRA_CHUNK (16*1024)

/* Room in the current chunk (outside the block, or inside the heap). */
static u8 *chunk_room(Jvm *vm, u32 bytes, u32 align) {
    if (!vm->chunk_top) return 0;
    u8 *c = (u8 *)((uintptr_t)(vm->chunk_top+align-1) & ~(uintptr_t)(align-1));
    return c <= vm->chunk_end && (u32)(vm->chunk_end-c) >= bytes ? c : 0;
}

static void *meta_take(Jvm *vm, u32 bytes, u32 align) {
    /* In order: down from the top while that stays above the heap; the current
       chunk; a new chunk of host memory outside the block (the phone: the
       Static Byte Heap); the heap's free tail; a chunk inside the heap (the tail
       is often taken: the class file being loaded sits there, pinned). */
    u8 *p = bytes <= (u32)(vm->meta_top-vm->heap) ?
            (u8 *)((uintptr_t)(vm->meta_top-bytes) & ~(uintptr_t)(align-1)) : 0;
    u8 *c = 0;
    if (p && p >= vm->heap_end) goto top;
    if ((c = chunk_room(vm, bytes, align))) goto chunk;
    u32 size = bytes+align > EXTRA_CHUNK ? bytes+align : EXTRA_CHUNK;
    if (vm->host.alloc_extra && (c = vm->host.alloc_extra(vm->host.context, size))) {
        vm->chunk_end = c+size;
        c = (u8 *)((uintptr_t)(c+align-1) & ~(uintptr_t)(align-1));
        goto chunk;
    }
    if (p && give_heap_tail(vm, (u32)(vm->heap_end-p))) goto top;
    if ((c = meta_chunk(vm, bytes+align))) {
        c = (u8 *)((uintptr_t)(c+align-1) & ~(uintptr_t)(align-1));
        goto chunk;
    }
    vm_fail(vm, "out of metadata memory", 0);
    return 0;
top:
    vm->meta_top = p;
    jvm_memset(p, 0, bytes);
    return p;
chunk:
    vm->chunk_top = c+bytes;
    jvm_memset(c, 0, bytes);
    return c;
}

void *meta_alloc(Jvm *vm, u32 bytes) { return meta_take(vm, bytes, 8); }
void *meta_bytes(Jvm *vm, u32 bytes) { return meta_take(vm, bytes, 1); }

u32 utf8_length(Utf8 a) { return rd16(a); }
const char *utf8_bytes(Utf8 a) { return (const char *)a+2; }
int utf8_equals(Utf8 a, const char *c) {
    u32 n = jvm_strlen(c);
    return rd16(a) == n && !jvm_memcmp(a+2, c, n);
}
int utf8_same(Utf8 a, Utf8 b) { return a == b || (rd16(a) == rd16(b) && !jvm_memcmp(a+2, b+2, rd16(a))); }

static Utf8 make_utf8(Jvm *vm, const u8 *bytes, u32 length) {
    u8 *p = meta_bytes(vm, length+3);  /* length, bytes, NUL */
    if (!p) return 0;
    p[0] = (u8)(length>>8); p[1] = (u8)length;
    jvm_memcpy(p+2, bytes, length);
    return p;
}

/* Count argument slots of a method descriptor; set return slot information. */
static u32 descriptor_slots(Utf8 desc, u8 *return_slots, u8 *return_ref) {
    const u8 *p = desc+3, *end = desc+2+rd16(desc);  /* skip '(' */
    u32 slots = 0;
    while (p < end && *p != ')') {
        if (*p == 'J' || *p == 'D') { slots += 2; p++; continue; }
        slots++;
        while (*p == '[') p++;
        if (*p == 'L') while (p < end && *p != ';') p++;
        p++;
    }
    p++;
    *return_slots = p >= end || *p == 'V' ? 0 : (*p == 'J' || *p == 'D') ? 2 : 1;
    *return_ref = p < end && (*p == 'L' || *p == '[');
    return slots;
}

static int is_reference(Utf8 desc) { u8 c = desc[2]; return c == 'L' || c == '['; }
static int is_wide(Utf8 desc) { u8 c = desc[2]; return c == 'J' || c == 'D'; }

static Class *new_class(Jvm *vm, u32 extra) {
    Class *c = meta_alloc(vm, sizeof(Class)+extra);
    return c;
}

static void add_class(Jvm *vm, Class *c) { c->next = vm->classes; vm->classes = c; }

static int link_class(Jvm *vm, Class *c, Utf8 super_name);

/* Builds a class from a class file, copying only the strings and bytecode it
   keeps (no attributes such as StackMap or LineNumberTable), so the file can
   be released before linking. The super class index and ConstantValue
   indices are returned for finish_class; interfaces[] holds pool indices. */
static Class *parse_class(Jvm *vm, const u8 *data, u32 size, u32 *super_index_out, u16 **constant_values_out) {
    const u8 *p = data, *end = data+size;
#define NEED(n) do { if ((u32)(end-p) < (u32)(n)) { vm_fail(vm, "truncated class file", 0); return 0; } } while (0)
    NEED(10);
    if (rd32(p) != 0xcafebabe) { vm_fail(vm, "not a class file", 0); return 0; }
    u32 major = rd16(p+6);
    if (major < 45 || major > 52) { vm_fail(vm, "unsupported class version", 0); return 0; }
    u32 count = rd16(p+8);
    p += 10;
    Class *c = new_class(vm, 0);
    if (!c) return 0;
    c->constant_count = (u16)count;
    c->constants = meta_alloc(vm, count*sizeof(Constant));
    if (!c->constants) return 0;
    for (u32 i = 1; i < count; i++) {
        NEED(1);
        Constant *k = &c->constants[i];
        k->tag = *p++;
        switch (k->tag) {
        case 1:
            NEED(2); NEED(2+rd16(p));
            if (!(k->u.utf8 = make_utf8(vm, p+2, rd16(p)))) return 0;
            p += 2+rd16(p);
            break;
        case 3: case 4: NEED(4); k->u.value = rd32(p); p += 4; break;
        case 5: case 6:
            NEED(8); k->u.value = rd32(p); c->constants[i+1].u.value = rd32(p+4); p += 8; i++; break;
        case 7: case 8: NEED(2); k->a = (u16)rd16(p); p += 2; break;
        case 9: case 10: case 11: case 12: NEED(4); k->a = (u16)rd16(p); k->u.b = (u16)rd16(p+2); p += 4; break;
        case 15: NEED(3); p += 3; break;
        case 16: NEED(2); p += 2; break;
        case 18: NEED(4); p += 4; break;
        default: vm_fail(vm, "bad constant pool tag", 0); return 0;
        }
    }
    NEED(8);
    c->access = (u16)rd16(p);
    u32 this_index = rd16(p+2), super_index = rd16(p+4), interfaces = rd16(p+6);
    p += 8;
    c->name = c->constants[c->constants[this_index].a].u.utf8;
    *super_index_out = super_index;
    NEED(interfaces*2);
    c->interface_count = (u16)interfaces;
    c->interfaces = meta_alloc(vm, (interfaces+1)*sizeof(Class *));
    if (!c->interfaces) return 0;
    for (u32 i = 0; i < interfaces; i++) c->interfaces[i] = (Class *)(uintptr_t)rd16(p+i*2);
    p += interfaces*2;

    NEED(2);
    c->field_count = (u16)rd16(p); p += 2;
    c->fields = meta_alloc(vm, (c->field_count+1)*sizeof(Field));
    u16 *constant_values = meta_alloc(vm, (c->field_count+1)*sizeof(u16));
    if (!c->fields || !constant_values) return 0;
    *constant_values_out = constant_values;
    for (u32 i = 0; i < c->field_count; i++) {
        NEED(8);
        Field *f = &c->fields[i];
        f->owner = c;
        f->access = (u16)rd16(p);
        f->name = c->constants[rd16(p+2)].u.utf8;
        f->desc = c->constants[rd16(p+4)].u.utf8;
        u32 attributes = rd16(p+6);
        p += 8;
        for (u32 a = 0; a < attributes; a++) {
            NEED(6);
            Utf8 name = c->constants[rd16(p)].u.utf8;
            u32 length = rd32(p+2);
            NEED(6+length);
            if (utf8_equals(name, "ConstantValue") && length == 2) constant_values[i] = (u16)rd16(p+6);
            p += 6+length;
        }
    }
    NEED(2);
    c->method_count = (u16)rd16(p); p += 2;
    c->methods = meta_alloc(vm, (c->method_count+1)*sizeof(Method));
    if (!c->methods) return 0;
    for (u32 i = 0; i < c->method_count; i++) {
        NEED(8);
        Method *m = &c->methods[i];
        m->owner = c;
        m->access = (u16)rd16(p);
        m->name = c->constants[rd16(p+2)].u.utf8;
        m->desc = c->constants[rd16(p+4)].u.utf8;
        m->arg_slots = (u16)(descriptor_slots(m->desc, &m->return_slots, &m->return_ref)+!(m->access & ACC_STATIC));
        u32 attributes = rd16(p+6);
        p += 8;
        for (u32 a = 0; a < attributes; a++) {
            NEED(6);
            Utf8 name = c->constants[rd16(p)].u.utf8;
            u32 length = rd32(p+2);
            NEED(6+length);
            if (utf8_equals(name, "Code")) {
                const u8 *q = p+6;
                m->max_stack = (u16)rd16(q);
                m->max_locals = (u16)rd16(q+2);
                m->code_length = rd32(q+4);
                if (m->code_length > length-8) { vm_fail(vm, "truncated class file", 0); return 0; }
                u8 *code = meta_bytes(vm, m->code_length ? m->code_length : 1);
                if (!code) return 0;
                jvm_memcpy(code, q+8, m->code_length);
                m->code = code;
                q += 8+m->code_length;
                m->handler_count = (u16)rd16(q);
                Handler *h = meta_alloc(vm, (m->handler_count+1)*sizeof(Handler));
                if (!h) return 0;
                for (u32 e = 0; e < m->handler_count; e++) {
                    const u8 *r = q+2+e*8;
                    h[e].start = (u16)rd16(r); h[e].end = (u16)rd16(r+2);
                    h[e].handler = (u16)rd16(r+4); h[e].type = (u16)rd16(r+6);
                }
                m->handlers = h;
            }
            p += 6+length;
        }
        if (m->max_locals < m->arg_slots) m->max_locals = m->arg_slots;
    }
#undef NEED
    return c;
}

/* Resolves interfaces and the super class (which may load more classes),
   links, then applies ConstantValue attributes. */
static Class *finish_class(Jvm *vm, Class *c, u32 super_index, const u16 *constant_values) {
    add_class(vm, c);
    for (u32 i = 0; i < c->interface_count; i++) {
        u32 index = (u32)(uintptr_t)c->interfaces[i];
        c->interfaces[i] = resolve_class(vm, c, (u16)index);
        if (!c->interfaces[i]) return 0;
    }
    Utf8 super_name = super_index ? c->constants[c->constants[super_index].a].u.utf8 : 0;
    if (!link_class(vm, c, super_name)) return 0;
    /* ConstantValue attributes initialise static finals before <clinit>. */
    for (u32 i = 0; i < c->field_count; i++) {
        Field *f = &c->fields[i];
        u32 index = constant_values[i];
        if (!(f->access & ACC_STATIC) || !index) continue;
        Constant *k = &c->constants[index];
        if (k->tag == 8) {
            Object *s = resolve_string(vm, c, (u16)index);
            if (!s) return 0;
            c->statics[f->offset] = (u32)(uintptr_t)s;
        } else if (k->tag == 5 || k->tag == 6) {
            u64 v = ((u64)k->u.value << 32) | c->constants[index+1].u.value;
            jvm_memcpy(&c->statics[f->offset], &v, 8);
        } else c->statics[f->offset] = k->u.value;
    }
    return c;
}

static int link_class(Jvm *vm, Class *c, Utf8 super_name) {
    if (super_name) {
        c->super = find_class(vm, super_name+2, rd16(super_name));
        if (!c->super) { vm_fail(vm, "superclass not found", super_name); return 0; }
    }
    /* Instance layout. */
    u32 bytes = c->super ? c->super->instance_bytes : sizeof(Object);
    u32 statics = 0;
    for (u32 i = 0; i < c->field_count; i++) {
        Field *f = &c->fields[i];
        u32 size = is_wide(f->desc) ? 8 : 4;
        if (f->access & ACC_STATIC) { f->offset = statics; statics += size/4; }
        else { f->offset = bytes; bytes += size; }
    }
    c->instance_bytes = bytes;
    u32 words = (bytes+3)/4, map_words = (words+31)/32;
    c->ref_map = meta_alloc(vm, map_words*4+4);
    c->static_slots = statics;
    c->statics = meta_alloc(vm, statics*4+8);
    c->static_ref_map = meta_alloc(vm, ((statics+31)/32)*4+4);
    if (!c->ref_map || !c->statics || !c->static_ref_map) return 0;
    if (c->super) jvm_memcpy(c->ref_map, c->super->ref_map, ((c->super->instance_bytes/4+31)/32)*4);
    for (u32 i = 0; i < c->field_count; i++) {
        Field *f = &c->fields[i];
        if (!is_reference(f->desc)) continue;
        if (f->access & ACC_STATIC) c->static_ref_map[f->offset/32] |= 1u << (f->offset%32);
        else { u32 w = f->offset/4; c->ref_map[w/32] |= 1u << (w%32); }
    }
    /* Virtual table: inherited entries, overrides by name and descriptor. */
    u32 inherited = c->super ? c->super->vtable_size : 0, size = inherited;
    Method **table = meta_alloc(vm, (inherited+c->method_count+1)*sizeof(Method *));
    if (!table) return 0;
    if (inherited) jvm_memcpy(table, c->super->vtable, inherited*sizeof(Method *));
    for (u32 i = 0; i < c->method_count; i++) {
        Method *m = &c->methods[i];
        if ((m->access & (ACC_STATIC|ACC_PRIVATE)) || (rd16(m->name) && m->name[2] == '<')) continue;
        u32 slot = size;
        for (u32 j = 0; j < inherited; j++)
            if (utf8_same(table[j]->name, m->name) && utf8_same(table[j]->desc, m->desc)) { slot = j; break; }
        table[slot] = m;
        m->vtable_index = (u16)slot;
        if (slot == size) size++;
    }
    c->vtable = table;
    c->vtable_size = (u16)size;
    for (u32 i = 0; i < c->method_count; i++) {
        Method *m = &c->methods[i];
        if (m->access & ACC_NATIVE) m->native = native_for(vm, c, m->name, m->desc);
    }
    c->state = CLASS_LINKED;
    return 1;
}

static Class *load_class(Jvm *vm, const u8 *name, u32 length) {
    char path[256];
    if (length+7 > sizeof(path)) return 0;
    jvm_memcpy(path, name, length);
    jvm_memcpy(path+length, ".class", 7);
    u32 size = 0;
    u8 *raw = vm->host.read(vm->host.context, path, &size);
    if (!raw) return 0;
    u32 super_index = 0;
    u16 *constant_values = 0;
    Class *c = parse_class(vm, raw, size, &super_index, &constant_values);
    vm->host.release(vm->host.context, raw);
    return c ? finish_class(vm, c, super_index, constant_values) : 0;
}

static Class *make_array_class(Jvm *vm, const u8 *name, u32 length) {
    Class *c = new_class(vm, 0);
    if (!c) return 0;
    c->name = make_utf8(vm, name, length);
    c->super = vm->object_class;
    c->interfaces = meta_alloc(vm, sizeof(Class *));
    c->access = ACC_PUBLIC|ACC_FINAL|ACC_ABSTRACT;
    c->element_type = name[1];
    c->dimensions = 1;
    if (name[1] == 'L') c->component = find_class(vm, name+2, length-3);
    else if (name[1] == '[') c->component = find_class(vm, name+1, length-1);
    if ((name[1] == 'L' || name[1] == '[') && !c->component) return 0;
    if (c->component && c->component->dimensions) c->dimensions = (u8)(c->component->dimensions+1);
    c->instance_bytes = sizeof(Array);
    c->ref_map = meta_alloc(vm, 4);
    c->vtable = vm->object_class->vtable;
    c->vtable_size = vm->object_class->vtable_size;
    c->state = CLASS_INITIALIZED;
    add_class(vm, c);
    return c;
}

Class *find_class(Jvm *vm, const u8 *name, u32 length) {
    for (Class *c = vm->classes; c; c = c->next)
        if (rd16(c->name) == length && !jvm_memcmp(c->name+2, name, length)) return c;
    if (vm->status == JVM_ERROR) return 0;
    if (length && name[0] == '[') return make_array_class(vm, name, length);
    return load_class(vm, name, length);
}

Class *find_class_c(Jvm *vm, const char *name) { return find_class(vm, (const u8 *)name, jvm_strlen(name)); }

Class *array_class(Jvm *vm, Class *element) {
    if (element->array_of) return element->array_of;
    u32 length = rd16(element->name);
    u8 *name = meta_alloc(vm, length+3);
    if (!name) return 0;
    name[0] = '[';
    if (element->dimensions) { jvm_memcpy(name+1, element->name+2, length); length += 1; }
    else { name[1] = 'L'; jvm_memcpy(name+2, element->name+2, length); name[length+2] = ';'; length += 3; }
    element->array_of = find_class(vm, name, length);
    return element->array_of;
}

Class *primitive_array(Jvm *vm, u8 type) {
    static const char types[] = "ZCFDBSIJ";
    for (u32 i = 0; i < 8; i++) if ((u8)types[i] == type) {
        if (!vm->primitive_arrays[i]) {
            u8 name[2] = {'[', type};
            vm->primitive_arrays[i] = find_class(vm, name, 2);
        }
        return vm->primitive_arrays[i];
    }
    return 0;
}

static int implements(Class *c, Class *interface) {
    for (; c; c = c->super)
        for (u32 i = 0; i < c->interface_count; i++)
            if (c->interfaces[i] == interface || implements(c->interfaces[i], interface)) return 1;
    return 0;
}

int is_subclass(Class *c, Class *of) {
    if (c == of) return 1;
    if (of->dimensions) {
        if (!c->dimensions || c->element_type != of->element_type) return 0;
        if (!of->component) return 1;  /* primitive arrays match by element type */
        return c->component && is_subclass(c->component, of->component);
    }
    if (of->access & ACC_INTERFACE) return implements(c, of);
    for (Class *s = c->super; s; s = s->super) if (s == of) return 1;
    return 0;
}

int is_instance(Jvm *vm, Object *o, Class *of) { (void)vm; return o && is_subclass(o->cls, of); }

Method *find_method_utf8(Class *c, Utf8 name, Utf8 desc) {
    for (Class *k = c; k; k = k->super)
        for (u32 i = 0; i < k->method_count; i++)
            if (utf8_same(k->methods[i].name, name) && utf8_same(k->methods[i].desc, desc)) return &k->methods[i];
    for (Class *k = c; k; k = k->super)
        for (u32 i = 0; i < k->interface_count; i++) {
            Method *m = find_method_utf8(k->interfaces[i], name, desc);
            if (m) return m;
        }
    return 0;
}

Method *find_method(Class *c, const char *name, const char *desc) {
    for (Class *k = c; k; k = k->super)
        for (u32 i = 0; i < k->method_count; i++)
            if (utf8_equals(k->methods[i].name, name) && utf8_equals(k->methods[i].desc, desc)) return &k->methods[i];
    return 0;
}

Field *find_field(Class *c, Utf8 name, Utf8 desc) {
    for (Class *k = c; k; k = k->super) {
        for (u32 i = 0; i < k->field_count; i++)
            if (utf8_same(k->fields[i].name, name) && utf8_same(k->fields[i].desc, desc)) return &k->fields[i];
        for (u32 i = 0; i < k->interface_count; i++) {
            Field *f = find_field(k->interfaces[i], name, desc);
            if (f) return f;
        }
    }
    return 0;
}

Field *find_field_c(Class *c, const char *name, const char *desc) {
    for (Class *k = c; k; k = k->super)
        for (u32 i = 0; i < k->field_count; i++)
            if (utf8_equals(k->fields[i].name, name) && utf8_equals(k->fields[i].desc, desc)) return &k->fields[i];
    return 0;
}

Class *resolve_class(Jvm *vm, Class *from, u16 index) {
    Constant *k = &from->constants[index];
    if (k->resolved) return k->u.cls;
    Utf8 name = from->constants[k->a].u.utf8;
    Class *c = find_class(vm, name+2, rd16(name));
    if (!c) { vm_fail(vm, "class not found", name); return 0; }
    k->u.cls = c;
    k->resolved = 1;
    return c;
}

Field *resolve_field(Jvm *vm, Class *from, u16 index) {
    Constant *k = &from->constants[index];
    if (k->resolved) return k->u.field;
    Class *c = resolve_class(vm, from, k->a);
    if (!c) return 0;
    Constant *nat = &from->constants[k->u.b];
    Utf8 name = from->constants[nat->a].u.utf8, desc = from->constants[nat->u.b].u.utf8;
    Field *f = find_field(c, name, desc);
    if (!f) { vm_fail(vm, "field not found", name); return 0; }
    k->u.field = f;
    k->resolved = 1;
    return f;
}

Method *resolve_method(Jvm *vm, Class *from, u16 index) {
    Constant *k = &from->constants[index];
    if (k->resolved) return k->u.method;
    Class *c = resolve_class(vm, from, k->a);
    if (!c) return 0;
    Constant *nat = &from->constants[k->u.b];
    Utf8 name = from->constants[nat->a].u.utf8, desc = from->constants[nat->u.b].u.utf8;
    Method *m = find_method_utf8(c, name, desc);
    if (!m && (c->access & ACC_INTERFACE)) m = find_method_utf8(vm->object_class, name, desc);
    if (!m) { vm_fail(vm, "method not found", name); return 0; }
    k->u.method = m;
    k->resolved = 1;
    return m;
}

Object *resolve_string(Jvm *vm, Class *from, u16 index) {
    Constant *k = &from->constants[index];
    if (k->resolved) return k->u.string;
    Utf8 text = from->constants[k->a].u.utf8;
    Object *s = new_string(vm, text+2, rd16(text));
    if (!s) return 0;
    s = intern(vm, s);
    k->u.string = s;
    k->resolved = 1;
    return s;
}
