/* B22 Java VM: a compact CLDC-style interpreter for the Explay B220.

The VM lives in one memory block supplied by the host: class metadata grows from
the start, the garbage-collected object heap fills the rest. Java frames are kept
on per-thread stacks inside that block, so calling Java never recurses on the C
stack. jvm_run() executes a bounded number of bytecodes and returns; all state
stays in the block, which lets the phone port answer every window message quickly.
*/
#ifndef JVM_H
#define JVM_H

#include <stdint.h>

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
} JvmHost;

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
/* Threads that ended with an uncaught exception. */
uint32_t jvm_uncaught(Jvm *vm);

#endif
