/* GO1 Java VM: a compact CLDC-style interpreter for the Explay B220.

The VM lives in one memory block supplied by the host: class metadata grows from
the start, the garbage-collected object heap fills the rest. Java frames are kept
on per-thread stacks inside that block, so calling Java never recurses on the C
stack. jvm_run() executes a bounded number of bytecodes and returns; all state
stays in the block, which lets the phone port answer every window message quickly.
*/
#ifndef JVM_H
#define JVM_H

#include <stdint.h>

struct JarStream;

typedef struct JvmHost {
    /* Read a whole resource such as "java/lang/Object.class"; the buffer is
       released with release(). Returns 0 when it does not exist. */
    uint8_t *(*read)(void *context, const char *path, uint32_t *size);
    void (*release)(void *context, uint8_t *buffer);
    /* Console output (System.out = 1, System.err = 2). */
    void (*write)(void *context, int stream, const char *bytes, uint32_t length);
    /* Milliseconds from an arbitrary epoch. */
    uint32_t (*ms)(void *context);
    void *context;
    /* MIDP display: a JVM_SCREEN_WIDTH x JVM_SCREEN_HEIGHT RGB565 frame buffer,
       and a call that shows a changed rectangle. Both may be 0 without a display. */
    uint16_t *(*screen)(void *context);
    void (*flush)(void *context, int x, int y, int width, int height);
    /* Optional: open a resource for reading on demand (see jar.h); with
       stream 0 only report its size. Returns 0 when it does not exist. */
    int (*stream)(void *context, const char *path, struct JarStream *stream, uint32_t *size);
} JvmHost;

/* Resources at least this large are streamed when the host can; smaller ones
   are read whole. */
#define JVM_STREAM_MIN (40*1024)

#define JVM_SCREEN_WIDTH 176
#define JVM_SCREEN_HEIGHT 220

/* Events for MIDP, delivered in order to the Java event thread. */
enum { JVM_KEY_PRESSED = 1, JVM_KEY_RELEASED = 2, JVM_KEY_REPEATED = 3, JVM_REPAINT = 4 };
/* MIDP key codes (as on Nokia/Sony Ericsson phones). */
enum {
    JVM_KEY_UP = -1, JVM_KEY_DOWN = -2, JVM_KEY_LEFT = -3, JVM_KEY_RIGHT = -4, JVM_KEY_FIRE = -5,
    JVM_KEY_SOFT_LEFT = -6, JVM_KEY_SOFT_RIGHT = -7
};

typedef struct Jvm Jvm;

enum {
    JVM_RUNNING = 0,  /* budget used up, call jvm_run again */
    JVM_IDLE = 1,     /* every thread sleeps or waits; see jvm_next_wake */
    JVM_DONE = 2,     /* no live threads */
    JVM_ERROR = 3     /* fatal VM error; see jvm_error */
};

/* memory: the whole VM block; meta_bytes of it hold class metadata and stacks. */
Jvm *jvm_create(const JvmHost *host, void *memory, uint32_t bytes, uint32_t meta_bytes);
/* Start a thread running public static void main(String[]) of a class ("pkg/Name"). */
int jvm_start_main(Jvm *vm, const char *class_name);
int jvm_run(Jvm *vm, uint32_t budget);
uint32_t jvm_next_wake(Jvm *vm);
const char *jvm_error(Jvm *vm);
/* Diagnostics. */
uint32_t jvm_heap_used(Jvm *vm);
uint32_t jvm_gc_count(Jvm *vm);
/* Largest live heap seen right after a collection. */
uint32_t jvm_heap_peak(Jvm *vm);
/* Size of the largest free block in the heap right now. */
uint32_t jvm_heap_largest_free(Jvm *vm);
uint32_t jvm_meta_used(Jvm *vm);
/* Approximate metadata by kind: thread stacks, classes (count), constant
   pools, methods/fields/vtables, bytecode with handlers. */
#define JVM_META_PARTS 5
void jvm_meta_parts(Jvm *vm, uint32_t parts[JVM_META_PARTS]);
/* Collect garbage, then report every live object: its class name and size in bytes. */
void jvm_heap_walk(Jvm *vm, void (*visit)(void *context, const char *name, uint32_t length, uint32_t bytes), void *context);
/* Threads that ended with an uncaught exception. */
uint32_t jvm_uncaught(Jvm *vm);
/* Scratch memory inside the Java heap for host buffers (class files and
   resources while they are read); 0 when it does not fit. Release returns 0
   for a block that is not scratch memory. */
void *jvm_scratch_alloc(Jvm *vm, uint32_t size);
int jvm_scratch_release(Jvm *vm, void *data);
/* Queue a MIDP event (type, value such as a key code); returns 0 if the queue is full. */
int jvm_post_event(Jvm *vm, int type, int value);
/* Main class for an application JAR, read through host->read: "go1/Launcher"
   when the manifest declares MIDlet-1, otherwise its Main-Class. */
int jvm_main_class(const JvmHost *host, char *name, uint32_t capacity);
/* Value of a manifest attribute ("MIDlet-Name"), continuation lines joined,
   as UTF-8 without surrounding spaces. Returns its length, 0 when missing. */
uint32_t jvm_manifest_value(const uint8_t *manifest, uint32_t size, const char *key, char *out, uint32_t capacity);

/* PNG decoding without a VM (used before one exists, e.g. for list icons).
   jvm_png_info gives the image size and the buffer sizes jvm_png_decode
   needs: packed (IDAT), raw (unpacked rows), width*height pixels and,
   when has_alpha, width*height alpha bytes (alpha may be 0 otherwise). */
typedef struct JvmPngInfo { uint32_t width, height, packed_size, raw_size; int has_alpha; } JvmPngInfo;
int jvm_png_info(const uint8_t *data, uint32_t size, JvmPngInfo *info);
int jvm_png_decode(const uint8_t *data, uint32_t size, uint8_t *packed, uint8_t *raw, uint16_t *pixels, uint8_t *alpha);

#endif
