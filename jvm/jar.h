/* JAR (ZIP) reading with a built-in inflater; no C library needed.

The archive is read through a callback, so only its central directory and the
entry being extracted are held in memory. */
#ifndef JVM_JAR_H
#define JVM_JAR_H

#include <stdint.h>

typedef struct JarIo {
    /* Read length bytes at offset; returns the bytes actually read. */
    uint32_t (*read)(void *context, uint32_t offset, void *buffer, uint32_t length);
    void *(*alloc)(void *context, uint32_t size);
    void (*release)(void *context, void *block);
    void *context;
    uint32_t size;  /* archive size in bytes */
} JarIo;

typedef struct Jar {
    JarIo io;
    uint8_t *directory;
    uint32_t directory_size, entries;
} Jar;

/* Returns 1 on success. */
int jar_open(Jar *jar, const JarIo *io);
void jar_close(Jar *jar);
/* Extract an entry ("pkg/Name.class", "META-INF/MANIFEST.MF"); the buffer comes
   from io.alloc. Returns 0 if the entry is missing, damaged or uses another method. */
uint8_t *jar_read(Jar *jar, const char *name, uint32_t *size);
/* Raw DEFLATE: returns 1 when exactly out_length bytes were produced. */
int jar_inflate(const uint8_t *in, uint32_t in_length, uint8_t *out, uint32_t out_length);

#endif
