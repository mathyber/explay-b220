/* Internal structures of the GO1 Java VM. */
#ifndef JVM_INTERNAL_H
#define JVM_INTERNAL_H

#include <stddef.h>
#include "jvm.h"

typedef uint8_t u8;
typedef uint16_t u16;
typedef uint32_t u32;
typedef int32_t i32;
typedef uint64_t u64;
typedef int64_t i64;

typedef struct Class Class;
typedef struct Method Method;
typedef struct Thread Thread;

/* Strings from class files: a pointer to the u16 big-endian length of a
   CONSTANT_Utf8 entry, followed by its (modified UTF-8) bytes. */
typedef const u8 *Utf8;

/* Every heap block starts with this header. info = size in 8-byte granules << 2
   | mark bit. A free block has cls == 0 and keeps the next free block after it. */
typedef struct Object {
    Class *cls;
    u32 info;
} Object;

typedef struct Array {
    Object header;
    u32 length;
    u32 pad;  /* elements start 8-byte aligned at offset 16 */
} Array;

#define ARRAY_DATA(a) ((u8 *)(a)+sizeof(Array))
#define FIELDS(o) ((u32 *)((u8 *)(o)+sizeof(Object)))

typedef struct Field {
    Class *owner;
    Utf8 name, desc;
    u16 access;
    u32 offset;  /* bytes from the object start, or slot index in statics */
} Field;

typedef struct Handler { u16 start, end, handler, type; } Handler;

/* Natives return 0 (result filled), 1 (exception pending), NATIVE_PUSHED or NATIVE_RETRY. */
typedef int (*Native)(Thread *t, u32 *args, u32 *result);
enum { NATIVE_PUSHED = 2, NATIVE_RETRY = 3 };

struct Method {
    Class *owner;
    Utf8 name, desc;
    u16 access, max_stack, max_locals, arg_slots;
    u16 handler_count, vtable_index;
    u8 return_slots;  /* 0, 1 or 2 */
    u8 return_ref;
    u32 code_length;
    const u8 *code;
    const Handler *handlers;
    Native native;
};

enum { CLASS_LOADED, CLASS_LINKED, CLASS_INITIALIZING, CLASS_INITIALIZED, CLASS_ERROR };

/* Constant pool entry, 8 bytes. Indices a and u.b (class, name-and-type,
   name, descriptor) are read only until the entry is resolved: then u holds
   the class, field, method or string. */
typedef struct Constant {
    u8 tag, resolved;
    u16 a;
    union { u32 value; const u8 *utf8; Class *cls; Field *field; Method *method; Object *string; u16 b; } u;
} Constant;

struct Class {
    Utf8 name;
    Class *super;
    Class **interfaces;
    u16 interface_count, access;
    u16 field_count, method_count, vtable_size, constant_count;
    u8 state, element_type;  /* element_type: 0 for non-arrays, else descriptor char */
    u8 dimensions;
    Class *component;        /* arrays */
    Class *array_of;         /* cache of [this */
    Constant *constants;
    Field *fields;
    Method *methods;
    Method **vtable;
    u32 instance_bytes;      /* including the header */
    u32 *ref_map;            /* bit per 4-byte word of an instance: holds a reference */
    u32 static_slots;
    u32 *statics;
    u32 *static_ref_map;
    Object *mirror;          /* java.lang.Class instance */
    Thread *initializer;
    Class *next;             /* all classes */
    u8 name_buffer[];        /* storage for synthesized names */
};

#define ACC_PUBLIC 0x0001
#define ACC_PRIVATE 0x0002
#define ACC_STATIC 0x0008
#define ACC_FINAL 0x0010
#define ACC_SYNCHRONIZED 0x0020
#define ACC_NATIVE 0x0100
#define ACC_INTERFACE 0x0200
#define ACC_ABSTRACT 0x0400

typedef struct Frame {
    Method *method;
    const u8 *pc;
    u32 *locals;
    u32 *stack;      /* operand stack base */
    u32 *sp;         /* saved operand stack pointer */
    Object *monitor; /* synchronized method lock */
} Frame;

enum { THREAD_NEW, THREAD_RUNNABLE, THREAD_SLEEPING, THREAD_WAITING, THREAD_BLOCKED, THREAD_DEAD };

struct Thread {
    Thread *next;
    Object *object;      /* java.lang.Thread */
    Object *exception;   /* pending exception */
    Object *stack;       /* int[] in the heap holding the slots below */
    u32 *slots, *slots_end;
    Frame *frames;
    u32 depth, max_depth;
    u8 state, interrupted;
    u32 wake;            /* ms for SLEEPING, timed WAITING */
    Object *waiting_on;  /* monitor for WAITING/BLOCKED */
    u32 saved_count;     /* monitor recursion count while waiting */
};

typedef struct Monitor {
    Object *object;
    Thread *owner;
    u32 count;
} Monitor;

#define MAX_MONITORS 64
#define MAX_TEMP_ROOTS 32
#define MAX_INTERNED 1024
#define THREAD_SLOTS_FIRST 256   /* a new Java stack: 1 KB in the heap, */
#define THREAD_SLOTS_MAX 8192    /* doubled on demand up to 32 KB */
#define THREAD_FRAMES 256
#define MAX_EVENTS 64

struct Jvm {
    JvmHost host;
    u8 *meta, *meta_top, *meta_end;  /* metadata: [meta_top, meta_end), growing down */
    u8 *heap, *heap_end;
    u32 *starts;          /* bit per granule: an object starts here */
    u32 *marks_stack;
    u32 marks_capacity;
    Object *free_list;
    u32 used, gc_count, uncaught, peak_live;
    Class *classes;
    Thread *threads, *current;
    u32 turn;  /* bytecodes left in the current thread's turn (0: pick the next thread) */
    u8 halted; /* go1.Launcher.halt(): the MIDlet is finished, other threads or not */
    void *stack_base;  /* C stack at the outermost VM entry: compaction scans it */
    u32 *forward_table;  /* compaction: runs of moved objects (a word per 512 bytes of the largest heap) */
    u32 forward_words;
    u32 compactions, pinned_objects, pinned_bytes;  /* pinned: at the last compaction */
    /* Metadata chunks inside the heap (fixed, always live) when the heap's tail is taken. */
#define MAX_META_CHUNKS 64
    Object *meta_chunks[MAX_META_CHUNKS];
    u32 meta_chunk_count;
    u8 *chunk_top, *chunk_end;
    Monitor monitors[MAX_MONITORS];
    u32 monitors_used;  /* entries with an object */
    Object *temp_roots[MAX_TEMP_ROOTS];
    u32 temp_count;
    Object **interned;
    u32 interned_count;
    int status;
    char error[96];
    /* MIDP event queue and the thread sleeping in Events.poll. */
    i32 events[MAX_EVENTS][2];
    u32 event_head, event_tail;
    Thread *event_waiter;
    Class *image_class;
    u32 image_pixels, image_alpha, image_width, image_height, image_alpha_bits;
    /* Frequently used classes and fields. */
    Class *object_class, *string_class, *class_class, *thread_class, *char_array;
    Class *primitive_arrays[12];
    u32 string_value, string_offset, string_count, class_vmdata, throwable_message, thread_vmdata;
};

#define GRANULE 8

/* class.c */
Class *find_class(Jvm *vm, const u8 *name, u32 length);
Class *find_class_c(Jvm *vm, const char *name);
Class *array_class(Jvm *vm, Class *element);
Class *primitive_array(Jvm *vm, u8 type);
int is_subclass(Class *c, Class *of);
int is_instance(Jvm *vm, Object *o, Class *of);
Method *find_method(Class *c, const char *name, const char *desc);
Method *find_method_utf8(Class *c, Utf8 name, Utf8 desc);
Field *find_field(Class *c, Utf8 name, Utf8 desc);
Field *find_field_c(Class *c, const char *name, const char *desc);
Class *resolve_class(Jvm *vm, Class *from, u16 index);
Field *resolve_field(Jvm *vm, Class *from, u16 index);
Method *resolve_method(Jvm *vm, Class *from, u16 index);
Object *resolve_string(Jvm *vm, Class *from, u16 index);
void *meta_alloc(Jvm *vm, u32 bytes);
int give_heap_tail(Jvm *vm, u32 bytes);
u8 *meta_chunk(Jvm *vm, u32 bytes);  /* a new metadata chunk in the heap; sets chunk_end */
void *meta_bytes(Jvm *vm, u32 bytes);
int utf8_equals(Utf8 a, const char *c);
int utf8_same(Utf8 a, Utf8 b);
u32 utf8_length(Utf8 a);
const char *utf8_bytes(Utf8 a);
void vm_fail(Jvm *vm, const char *message, Utf8 detail);

/* heap.c */
Object *new_object(Jvm *vm, Class *c);
Array *new_array(Jvm *vm, Class *c, u32 length);
u32 element_size(u8 type);
void collect(Jvm *vm);
int is_object(Jvm *vm, u32 value);
void push_root(Jvm *vm, Object *o);
void jvm_fix_object(Object *o);  /* never moved by compaction */
int jvm_compact(Jvm *vm);  /* after collect(): slide live objects together */
void pop_roots(Jvm *vm, u32 count);

/* interp.c */
u32 execute(Jvm *vm, Thread *t, u32 budget);
void throw_new(Thread *t, const char *class_name, const char *message);
void throw_object(Thread *t, Object *exception);
int invoke_method(Thread *t, Method *m, u32 *args);
int initialize_class(Thread *t, Class *c);
Thread *new_thread(Jvm *vm, Object *object);
Monitor *monitor_for(Jvm *vm, Object *o, int create);
int monitor_enter(Thread *t, Object *o);
int monitor_exit(Thread *t, Object *o);

/* midp.c */
Native midp_native_for(Utf8 class_name, Utf8 name, Utf8 desc);
Native io_native_for(Utf8 class_name, Utf8 name, Utf8 desc);

/* native.c */
Native native_for(Jvm *vm, Class *c, Utf8 name, Utf8 desc);
Object *new_string(Jvm *vm, const u8 *utf8, u32 length);
Object *new_string_c(Jvm *vm, const char *text);
Object *intern(Jvm *vm, Object *string);
Object *class_mirror(Jvm *vm, Class *c);
u32 string_utf8(Jvm *vm, Object *string, char *out, u32 capacity);

static inline u32 rd16(const u8 *p) { return ((u32)p[0]<<8)|p[1]; }
static inline u32 rd32(const u8 *p) { return (rd16(p)<<16)|rd16(p+2); }
static inline Jvm *thread_vm(Thread *t);
extern Jvm *jvm_current_vm;
static inline Jvm *thread_vm(Thread *t) { (void)t; return jvm_current_vm; }

void *jvm_memset(void *d, int c, u32 n);
void *jvm_memcpy(void *d, const void *s, u32 n);
int jvm_memcmp(const void *a, const void *b, u32 n);
u32 jvm_strlen(const char *s);

#endif
