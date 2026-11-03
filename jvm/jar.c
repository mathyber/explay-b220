/* ZIP central directory lookup and a canonical-Huffman inflater (RFC 1951). */
#include "jar.h"

typedef uint8_t u8;
typedef uint16_t u16;
typedef uint32_t u32;

static u32 le16(const u8 *p) { return p[0] | (u32)p[1] << 8; }
static u32 le32(const u8 *p) { return le16(p) | le16(p+2) << 16; }

/* ---------- inflate ---------- */

typedef struct Bits {
    const u8 *in, *end;
    u32 buffer, count;
    u8 *out;
    u32 written, capacity;
    int error;
} Bits;

static u32 bits(Bits *s, u32 need) {
    while (s->count < need) {
        if (s->in >= s->end) { s->error = 1; return 0; }
        s->buffer |= (u32)*s->in++ << s->count;
        s->count += 8;
    }
    u32 value = s->buffer & ((1u << need)-1);
    s->buffer >>= need;
    s->count -= need;
    return value;
}

/* Canonical code: count of codes per length and symbols in code order. */
typedef struct Huffman { u16 counts[16]; u16 symbols[288]; } Huffman;

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
    return 1;
}

static int decode(Bits *s, const Huffman *h) {
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

static int codes(Bits *s, const Huffman *literals, const Huffman *distances) {
    for (;;) {
        int symbol = decode(s, literals);
        if (symbol < 0) return 0;
        if (symbol < 256) {
            if (s->written >= s->capacity) return 0;
            s->out[s->written++] = (u8)symbol;
        } else if (symbol == 256) return 1;
        else {
            symbol -= 257;
            if (symbol >= 29) return 0;
            u32 length = length_base[symbol]+bits(s, length_extra[symbol]);
            int d = decode(s, distances);
            if (d < 0 || d >= 30) return 0;
            u32 distance = distance_base[d]+bits(s, distance_extra[d]);
            if (s->error || distance > s->written || length > s->capacity-s->written) return 0;
            for (u32 i = 0; i < length; i++, s->written++) s->out[s->written] = s->out[s->written-distance];
        }
    }
}

static int fixed_block(Bits *s) {
    static Huffman literals, distances;
    static int ready;
    if (!ready) {
        u8 lengths[288];
        for (u32 i = 0; i < 144; i++) lengths[i] = 8;
        for (u32 i = 144; i < 256; i++) lengths[i] = 9;
        for (u32 i = 256; i < 280; i++) lengths[i] = 7;
        for (u32 i = 280; i < 288; i++) lengths[i] = 8;
        build(&literals, lengths, 288);
        for (u32 i = 0; i < 30; i++) lengths[i] = 5;
        build(&distances, lengths, 30);
        ready = 1;
    }
    return codes(s, &literals, &distances);
}

static int dynamic_block(Bits *s) {
    static const u8 order[19] = {16,17,18,0,8,7,9,6,10,5,11,4,12,3,13,2,14,1,15};
    u32 nlen = bits(s, 5)+257, ndist = bits(s, 5)+1, ncode = bits(s, 4)+4;
    if (s->error || nlen > 286 || ndist > 30) return 0;
    u8 lengths[320];
    for (u32 i = 0; i < 19; i++) lengths[order[i]] = i < ncode ? (u8)bits(s, 3) : 0;
    Huffman lencode, literals, distances;
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
    if (!build(&literals, lengths, nlen) || !build(&distances, lengths+nlen, ndist)) return 0;
    return codes(s, &literals, &distances);
}

int jar_inflate(const u8 *in, u32 in_length, u8 *out, u32 out_length) {
    Bits s = {in, in+in_length, 0, 0, out, 0, out_length, 0};
    u32 last;
    do {
        last = bits(&s, 1);
        u32 type = bits(&s, 2);
        if (s.error) return 0;
        if (type == 0) {
            s.buffer = 0;
            s.count = 0;
            if (s.end-s.in < 4) return 0;
            u32 length = le16(s.in), check = le16(s.in+2);
            s.in += 4;
            if ((length ^ 0xffff) != check || (u32)(s.end-s.in) < length || length > s.capacity-s.written) return 0;
            for (u32 i = 0; i < length; i++) s.out[s.written++] = *s.in++;
        } else if (type == 1) { if (!fixed_block(&s)) return 0; }
        else if (type == 2) { if (!dynamic_block(&s)) return 0; }
        else return 0;
    } while (!last);
    return s.written == out_length;
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

uint8_t *jar_read(Jar *jar, const char *name, uint32_t *size) {
    u32 length = 0;
    while (name[length]) length++;
    const u8 *p = jar->directory, *end = p+jar->directory_size;
    for (u32 e = 0; e < jar->entries && end-p >= 46 && le32(p) == 0x02014b50; e++) {
        u32 method = le16(p+10), packed = le32(p+20), unpacked = le32(p+24);
        u32 name_length = le16(p+28), extra = le16(p+30), comment = le16(p+32), local = le32(p+42);
        if ((u32)(end-p) < 46+name_length) return 0;
        int match = name_length == length;
        for (u32 i = 0; match && i < length; i++) match = p[46+i] == (u8)name[i];
        if (match) {
            u8 header[30];
            if ((method != 0 && method != 8) || jar->io.read(jar->io.context, local, header, 30) != 30 ||
                le32(header) != 0x04034b50) return 0;
            u32 data = local+30+le16(header+26)+le16(header+28);
            if (data > jar->io.size || packed > jar->io.size-data) return 0;
            u8 *out = jar->io.alloc(jar->io.context, unpacked ? unpacked : 1);
            if (!out) return 0;
            if (method == 0) {
                if (packed != unpacked || jar->io.read(jar->io.context, data, out, packed) != packed) {
                    jar->io.release(jar->io.context, out);
                    return 0;
                }
            } else {
                u8 *in = jar->io.alloc(jar->io.context, packed ? packed : 1);
                int ok = in && jar->io.read(jar->io.context, data, in, packed) == packed && jar_inflate(in, packed, out, unpacked);
                if (in) jar->io.release(jar->io.context, in);
                if (!ok) { jar->io.release(jar->io.context, out); return 0; }
            }
            *size = unpacked;
            return out;
        }
        p += 46+name_length+extra+comment;
    }
    return 0;
}
