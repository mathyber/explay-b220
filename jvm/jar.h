/* JAR (ZIP) reading with a built-in inflater; no C library needed.

The archive is read through a callback, so only its central directory and the
entry being extracted are held in memory; compressed data is read in small
chunks. Large entries can also be streamed through a 32 KB window. */
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

/* Bit reader over a buffer, refilled from the archive when io is set. */
typedef struct JarBits {
    const uint8_t *in, *end;
    uint32_t buffer, count;
    uint8_t *out;
    uint32_t written, capacity;
    int error;
    const JarIo *io;
    uint32_t offset, left;  /* compressed bytes still in the archive */
    uint8_t *chunk;
    uint32_t chunk_size;
} JarBits;

/* Canonical Huffman code: count of codes per length and symbols in code order.
   fast: codes of up to JAR_FAST_BITS bits by their next input bits (LSB first),
   (length << 9) | symbol, 0 for longer codes. */
#define JAR_FAST_BITS 8
typedef struct JarHuffman { uint16_t counts[16]; uint16_t symbols[288]; uint16_t fast[1 << JAR_FAST_BITS]; } JarHuffman;

/* An entry decompressed on demand: about 34 KB, most of it the window. It
   holds pointers into itself, so it must not move while in use (a byte[] that
   holds one is fixed: jvm_fix_object). */
typedef struct JarStream {
    JarBits bits;
    const JarIo *io;
    uint32_t data, size, produced;
    uint32_t stored, copy_length, copy_distance;
    uint8_t method, state, last, error;
    const JarHuffman *literals, *distances;
    JarHuffman dynamic_literals, dynamic_distances;
    uint8_t chunk[256];
    uint8_t window[32768];
} JarStream;

/* Returns 1 on success. */
int jar_open(Jar *jar, const JarIo *io);
void jar_close(Jar *jar);
/* Extract an entry ("pkg/Name.class", "META-INF/MANIFEST.MF"); the buffer comes
   from io.alloc. Returns 0 if the entry is missing, damaged or uses another method. */
uint8_t *jar_read(Jar *jar, const char *name, uint32_t *size);
/* Raw DEFLATE: returns 1 when exactly out_length bytes were produced. */
int jar_inflate(const uint8_t *in, uint32_t in_length, uint8_t *out, uint32_t out_length);
/* Find an entry for streaming: its uncompressed size in *size, and with a
   stream, prepare it. Returns 0 if missing or not stored/deflated. */
int jar_stream_open(Jar *jar, const char *name, JarStream *stream, uint32_t *size);
/* Next bytes of a stream: count, 0 at the end, -1 on damaged data. */
int jar_stream_read(JarStream *stream, uint8_t *out, uint32_t length);
/* Uncompressed size of all .class entries. */
uint32_t jar_class_bytes(Jar *jar);

#endif
