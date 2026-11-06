/* ZIP central directory lookup and a canonical-Huffman inflater (RFC 1951). */
#include "jar.h"

typedef uint8_t u8;
typedef uint16_t u16;
typedef uint32_t u32;
typedef JarBits Bits;
typedef JarHuffman Huffman;

static u32 le16(const u8 *p) { return p[0] | (u32)p[1] << 8; }
static u32 le32(const u8 *p) { return le16(p) | le16(p+2) << 16; }

/* ---------- inflate ---------- */

/* Next chunk of compressed data from the archive, if the reader has one. */
static int refill(Bits *s) {
    if (!s->io || !s->left) return 0;
    u32 n = s->left < s->chunk_size ? s->left : s->chunk_size;
    if (s->io->read(s->io->context, s->offset, s->chunk, n) != n) return 0;
    s->offset += n;
    s->left -= n;
    s->in = s->chunk;
    s->end = s->chunk+n;
    return 1;
}

static u32 bits(Bits *s, u32 need) {
    while (s->count < need) {
        if (s->in >= s->end && !refill(s)) { s->error = 1; return 0; }
        s->buffer |= (u32)*s->in++ << s->count;
        s->count += 8;
    }
    u32 value = s->buffer & ((1u << need)-1);
    s->buffer >>= need;
    s->count -= need;
    return value;
}

/* Bytes into the bit buffer up to 25+ bits, while input lasts (no error at the end). */
static void fill(Bits *s) {
    while (s->count <= 24) {
        if (s->in >= s->end && !refill(s)) return;
        s->buffer |= (u32)*s->in++ << s->count;
        s->count += 8;
    }
}

static int build(Huffman *h, const u8 *lengths, u32 n) {
    u16 offsets[16];
    for (u32 i = 0; i < 16; i++) h->counts[i] = 0;
    for (u32 i = 0; i < n; i++) h->counts[lengths[i]]++;
    h->counts[0] = 0;
    int left = 1;
    for (u32 len = 1; len < 16; len++) {
        left = left*2-h->counts[len];
        if (left < 0) return 0;  /* over-subscribed */
    }
    offsets[1] = 0;
    for (u32 len = 1; len < 15; len++) offsets[len+1] = (u16)(offsets[len]+h->counts[len]);
    for (u32 i = 0; i < n; i++) if (lengths[i]) h->symbols[offsets[lengths[i]]++] = (u16)i;
    /* Lookup table for short codes: canonical code of each symbol, bit-reversed
       because the stream delivers codes most significant bit first. */
    for (u32 i = 0; i < (1u << JAR_FAST_BITS); i++) h->fast[i] = 0;
    u32 next[16], code = 0;
    next[0] = 0;
    for (u32 len = 1; len < 16; len++) { code = (code+(len > 1 ? h->counts[len-1] : 0)) << 1; next[len] = code; }
    for (u32 i = 0; i < n; i++) {
        u32 len = lengths[i];
        if (!len) continue;
        u32 c = next[len]++;
        if (len > JAR_FAST_BITS) continue;
        u32 reversed = 0;
        for (u32 b = 0; b < len; b++) reversed |= ((c >> b) & 1) << (len-1-b);
        for (u32 f = reversed; f < (1u << JAR_FAST_BITS); f += 1u << len) h->fast[f] = (u16)(len << 9 | i);
    }
    return 1;
}

static int decode(Bits *s, const Huffman *h) {
    if (s->count < JAR_FAST_BITS) fill(s);
    if (s->count >= JAR_FAST_BITS) {
        u32 e = h->fast[s->buffer & ((1u << JAR_FAST_BITS)-1)];
        if (e) {
            s->buffer >>= e >> 9;
            s->count -= e >> 9;
            return (int)(e & 511);
        }
    }
    int code = 0, first = 0, index = 0;
    for (u32 len = 1; len < 16; len++) {
        code |= (int)bits(s, 1);
        if (s->error) return -1;
        int count = h->counts[len];
        if (code-first < count) return h->symbols[index+code-first];
        index += count;
        first = (first+count) << 1;
        code <<= 1;
    }
    s->error = 1;
    return -1;
}

static const u16 length_base[29] = {3,4,5,6,7,8,9,10,11,13,15,17,19,23,27,31,35,43,51,59,67,83,99,115,131,163,195,227,258};
static const u8 length_extra[29] = {0,0,0,0,0,0,0,0,1,1,1,1,2,2,2,2,3,3,3,3,4,4,4,4,5,5,5,5,0};
static const u16 distance_base[30] = {1,2,3,4,5,7,9,13,17,25,33,49,65,97,129,193,257,385,513,769,1025,1537,2049,3073,
                                      4097,6145,8193,12289,16385,24577};
static const u8 distance_extra[30] = {0,0,0,0,1,1,2,2,3,3,4,4,5,5,6,6,7,7,8,8,9,9,10,10,11,11,12,12,13,13};

/* Length and distance of a match after length symbol (257..285); 0 on bad data. */
static int match(Bits *s, int symbol, const Huffman *distances, u32 *length, u32 *distance) {
    symbol -= 257;
    if (symbol >= 29) return 0;
    *length = length_base[symbol]+bits(s, length_extra[symbol]);
    int d = decode(s, distances);
    if (d < 0 || d >= 30) return 0;
    *distance = distance_base[d]+bits(s, distance_extra[d]);
    return !s->error;
}

static int codes(Bits *s, const Huffman *literals, const Huffman *distances) {
    for (;;) {
        int symbol = decode(s, literals);
        if (symbol < 0) return 0;
        if (symbol < 256) {
            if (s->written >= s->capacity) return 0;
            s->out[s->written++] = (u8)symbol;
        } else if (symbol == 256) return 1;
        else {
            u32 length, distance;
            if (!match(s, symbol, distances, &length, &distance)) return 0;
            if (distance > s->written || length > s->capacity-s->written) return 0;
            for (u32 i = 0; i < length; i++, s->written++) s->out[s->written] = s->out[s->written-distance];
        }
    }
}

static Huffman fixed_literals, fixed_distances;

static void fixed_tables(void) {
    static int ready;
    if (ready) return;
    u8 lengths[288];
    for (u32 i = 0; i < 144; i++) lengths[i] = 8;
    for (u32 i = 144; i < 256; i++) lengths[i] = 9;
    for (u32 i = 256; i < 280; i++) lengths[i] = 7;
    for (u32 i = 280; i < 288; i++) lengths[i] = 8;
    build(&fixed_literals, lengths, 288);
    for (u32 i = 0; i < 30; i++) lengths[i] = 5;
    build(&fixed_distances, lengths, 30);
    ready = 1;
}

/* Code length tables of a dynamic block. */
static int dynamic_tables(Bits *s, Huffman *literals, Huffman *distances) {
    static const u8 order[19] = {16,17,18,0,8,7,9,6,10,5,11,4,12,3,13,2,14,1,15};
    u32 nlen = bits(s, 5)+257, ndist = bits(s, 5)+1, ncode = bits(s, 4)+4;
    if (s->error || nlen > 286 || ndist > 30) return 0;
    u8 lengths[320];
    for (u32 i = 0; i < 19; i++) lengths[order[i]] = i < ncode ? (u8)bits(s, 3) : 0;
    static Huffman lencode;  /* static: the tables are large for the MMI task's stack */
    if (s->error || !build(&lencode, lengths, 19)) return 0;
    for (u32 i = 0; i < nlen+ndist;) {
        int symbol = decode(s, &lencode);
        if (symbol < 0) return 0;
        if (symbol < 16) { lengths[i++] = (u8)symbol; continue; }
        u8 repeat = 0;
        u32 times;
        if (symbol == 16) {
            if (!i) return 0;
            repeat = lengths[i-1];
            times = 3+bits(s, 2);
        } else times = symbol == 17 ? 3+bits(s, 3) : 11+bits(s, 7);
        if (s->error || i+times > nlen+ndist) return 0;
        while (times--) lengths[i++] = repeat;
    }
    if (!lengths[256]) return 0;
    return build(literals, lengths, nlen) && build(distances, lengths+nlen, ndist);
}

/* Stored block header after the 3 block bits: skip to a byte, LEN and NLEN.
   Whole bytes read ahead by decode() stay in the buffer. */
static int stored_length(Bits *s, u32 *length) {
    s->buffer >>= s->count & 7;
    s->count -= s->count & 7;
    *length = bits(s, 16);
    u32 check = bits(s, 16);
    return !s->error && (*length ^ 0xffff) == check;
}

static int inflate(Bits *s) {
    u32 last;
    do {
        last = bits(s, 1);
        u32 type = bits(s, 2);
        if (s->error) return 0;
        if (type == 0) {
            u32 length;
            if (!stored_length(s, &length) || length > s->capacity-s->written) return 0;
            for (u32 i = 0; i < length; i++) s->out[s->written++] = (u8)bits(s, 8);
            if (s->error) return 0;
        } else if (type == 1) {
            fixed_tables();
            if (!codes(s, &fixed_literals, &fixed_distances)) return 0;
        } else if (type == 2) {
            static Huffman literals, distances;  /* static: see dynamic_tables */
            if (!dynamic_tables(s, &literals, &distances) || !codes(s, &literals, &distances)) return 0;
        } else return 0;
    } while (!last);
    return s->written == s->capacity;
}

int jar_inflate(const u8 *in, u32 in_length, u8 *out, u32 out_length) {
    Bits s = {in, in+in_length, 0, 0, out, 0, out_length, 0, 0, 0, 0, 0, 0};
    return inflate(&s);
}

/* ---------- streaming ---------- */

enum { STREAM_HEADER, STREAM_STORED, STREAM_CODES, STREAM_DONE };

static void emit(JarStream *s, u8 *out, u32 *n, u8 c) {
    out[(*n)++] = c;
    s->window[s->produced++ & 32767] = c;
}

int jar_stream_read(JarStream *s, u8 *out, u32 length) {
    Bits *b = &s->bits;
    u32 n = 0;
    if (s->method == 0) {
        u32 left = s->size-s->produced;
        if (length > left) length = left;
        if (length && s->io->read(s->io->context, s->data+s->produced, out, length) != length) return -1;
        s->produced += length;
        return (int)length;
    }
    while (n < length && !s->error && !b->error) {
        if (s->copy_length) {
            emit(s, out, &n, s->window[(s->produced-s->copy_distance) & 32767]);
            s->copy_length--;
        } else if (s->state == STREAM_STORED) {
            if (!s->stored) { s->state = STREAM_HEADER; continue; }
            u8 c = (u8)bits(b, 8);
            if (b->error) break;
            emit(s, out, &n, c);
            s->stored--;
        } else if (s->state == STREAM_CODES) {
            int symbol = decode(b, s->literals);
            if (symbol < 0) break;
            if (symbol < 256) emit(s, out, &n, (u8)symbol);
            else if (symbol == 256) s->state = STREAM_HEADER;
            else if (!match(b, symbol, s->distances, &s->copy_length, &s->copy_distance) ||
                     s->copy_distance > s->produced || s->copy_distance > 32768) s->error = 1;
        } else if (s->state == STREAM_HEADER) {
            if (s->last) { s->state = STREAM_DONE; continue; }
            s->last = (u8)bits(b, 1);
            u32 type = bits(b, 2);
            if (b->error) break;
            if (type == 0) {
                if (stored_length(b, &s->stored)) s->state = STREAM_STORED; else s->error = 1;
            } else if (type == 1) {
                fixed_tables();
                s->literals = &fixed_literals;
                s->distances = &fixed_distances;
                s->state = STREAM_CODES;
            } else if (type == 2 && dynamic_tables(b, &s->dynamic_literals, &s->dynamic_distances)) {
                s->literals = &s->dynamic_literals;
                s->distances = &s->dynamic_distances;
                s->state = STREAM_CODES;
            } else s->error = 1;
        } else break;
    }
    if (s->error || b->error || s->produced > s->size) return -1;
    return (int)n;
}

/* ---------- archive ---------- */

int jar_open(Jar *jar, const JarIo *io) {
    jar->io = *io;
    jar->directory = 0;
    u32 tail = io->size < 65557 ? io->size : 65557;
    if (tail < 22) return 0;
    u8 *buffer = io->alloc(io->context, tail);
    if (!buffer) return 0;
    u32 start = io->size-tail, at = 0;
    int found = 0;
    if (io->read(io->context, start, buffer, tail) == tail)
        for (at = tail-22+1; at-- > 0;)
            if (le32(buffer+at) == 0x06054b50) { found = 1; break; }
    u32 entries = found ? le16(buffer+at+10) : 0, size = found ? le32(buffer+at+12) : 0, offset = found ? le32(buffer+at+16) : 0;
    io->release(io->context, buffer);
    if (!found || offset > io->size || size > io->size-offset) return 0;
    jar->directory = io->alloc(io->context, size ? size : 1);
    if (!jar->directory || io->read(io->context, offset, jar->directory, size) != size) { jar_close(jar); return 0; }
    jar->directory_size = size;
    jar->entries = entries;
    return 1;
}

void jar_close(Jar *jar) {
    if (jar->directory) jar->io.release(jar->io.context, jar->directory);
    jar->directory = 0;
}

/* Locate a stored or deflated entry: method, sizes and the offset of its data. */
static int entry(Jar *jar, const char *name, u32 *method, u32 *packed, u32 *unpacked, u32 *data) {
    u32 length = 0;
    while (name[length]) length++;
    const u8 *p = jar->directory, *end = p+jar->directory_size;
    for (u32 e = 0; e < jar->entries && end-p >= 46 && le32(p) == 0x02014b50; e++) {
        u32 name_length = le16(p+28), extra = le16(p+30), comment = le16(p+32), local = le32(p+42);
        if ((u32)(end-p) < 46+name_length) return 0;
        int same = name_length == length;
        for (u32 i = 0; same && i < length; i++) same = p[46+i] == (u8)name[i];
        if (same) {
            u8 header[30];
            *method = le16(p+10);
            *packed = le32(p+20);
            *unpacked = le32(p+24);
            if ((*method != 0 && *method != 8) || jar->io.read(jar->io.context, local, header, 30) != 30 ||
                le32(header) != 0x04034b50) return 0;
            *data = local+30+le16(header+26)+le16(header+28);
            if (*data > jar->io.size || *packed > jar->io.size-*data) return 0;
            return *method == 8 || *packed == *unpacked;
        }
        p += 46+name_length+extra+comment;
    }
    return 0;
}

uint8_t *jar_read(Jar *jar, const char *name, uint32_t *size) {
    u32 method, packed, unpacked, data;
    if (!entry(jar, name, &method, &packed, &unpacked, &data)) return 0;
    u8 *out = jar->io.alloc(jar->io.context, unpacked ? unpacked : 1);
    if (!out) return 0;
    int ok;
    if (method == 0) ok = jar->io.read(jar->io.context, data, out, packed) == packed;
    else {
        /* Compressed data comes from the archive in chunks: no copy of it all. */
        u8 chunk[256];
        Bits s = {chunk, chunk, 0, 0, out, 0, unpacked, 0, &jar->io, data, packed, chunk, sizeof(chunk)};
        ok = inflate(&s);
    }
    if (!ok) { jar->io.release(jar->io.context, out); return 0; }
    *size = unpacked;
    return out;
}

int jar_stream_open(Jar *jar, const char *name, JarStream *s, uint32_t *size) {
    u32 method, packed, unpacked, data;
    if (!entry(jar, name, &method, &packed, &unpacked, &data)) return 0;
    *size = unpacked;
    if (!s) return 1;
    Bits b = {s->chunk, s->chunk, 0, 0, 0, 0, 0, 0, &jar->io, data, packed, s->chunk, sizeof(s->chunk)};
    s->bits = b;
    s->io = &jar->io;
    s->data = data;
    s->size = unpacked;
    s->produced = s->stored = s->copy_length = s->copy_distance = 0;
    s->method = (u8)method;
    s->state = STREAM_HEADER;
    s->last = s->error = 0;
    return 1;
}

uint32_t jar_class_bytes(Jar *jar) {
    u32 total = 0;
    const u8 *p = jar->directory, *end = p+jar->directory_size;
    for (u32 e = 0; e < jar->entries && end-p >= 46 && le32(p) == 0x02014b50; e++) {
        u32 name_length = le16(p+28);
        if ((u32)(end-p) < 46+name_length) break;
        const u8 *name = p+46;
        if (name_length > 6 && name[name_length-6] == '.' && name[name_length-5] == 'c' && name[name_length-1] == 's')
            total += le32(p+24);
        p += 46+name_length+le16(p+30)+le16(p+32);
    }
    return total;
}
