/* Template JIT for ARM (the phone's ARM926, ARMv5TE, big-endian).

   A hot method (JIT_THRESHOLD backward jumps and calls) is translated whole,
   each bytecode into a short sequence of ARM instructions. Between blocks the
   operand stack and the locals are in memory exactly as the interpreter keeps
   them, so the interpreter can hand over at the start of any block and take
   back at any instruction:
     r4  operand stack pointer in memory (the next free slot, like sp in execute)
     r5  locals
     r6  JitCtx
     r7  budget left (signed)
     r8  locals of an inlined method whose arguments are in memory
   Inside a block the top of the operand stack is kept by the compiler in
   registers (r0-r3, r12, r9-r11) or as known constants, and goes to memory
   only at block ends, at exits and when registers run out.
   Whatever compiled code does not do itself it leaves to the interpreter: it
   stores the cached stack and stops before that bytecode, returning its
   offset. This covers calls (except inlined ones), returns, allocation,
   monitors, floating point, longs other than moves, and every failing check
   (null, array bounds, division by zero): the check comes before any operand
   is consumed, so the interpreter runs the instruction again and throws exactly
   as it would have. Compiled code never allocates, so the heap cannot move
   under it.
   The budget counts bytecodes as in the interpreter: each block of straight-line
   code takes its length off as it starts; a backward jump with the budget used
   up stops before itself and the thread's turn ends there. */
#include "internal.h"

#if defined(__arm__) && !defined(JVM_NO_JIT)

#define JIT_CHUNK (16*1024)
#define JIT_LIMIT (64*1024)    /* code and maps of all compiled methods */
#define JIT_MAX_CODE 4096      /* longer methods stay interpreted */
#define CACHE 6                /* operand stack entries kept in registers */

/* A cached operand stack entry: a register or a known constant. */
typedef struct Slot { u8 constant, reg; u32 value; } Slot;

typedef struct Emit {
    u32 *out;          /* 0 in the sizing pass */
    u32 cap;           /* words at out */
    u32 n;             /* words emitted */
    u32 stubs;         /* failing-check exits so far */
    u32 stub_base;     /* word index of the first exit (second pass) */
    u16 *map;          /* bytecode offset -> word index (0 = not an instruction) | LEADER */
    const Method *m;
    u32 loc;           /* register holding the locals: r5, r8 in an inlined method */
    int inlined;       /* translating an inlined method's body */
    u32 args;          /* inlined method with its arguments as the bottom cache entries */
    int broken;        /* such an argument had to go to memory: no inlining here */
    Slot cache[CACHE]; /* top of the operand stack, bottom first */
    u32 depth;         /* entries in cache */
    u32 used;          /* registers holding entries or scratch values */
    u32 keep;          /* top entries a register shortage must not spill */
    int bad;
} Emit;

enum { R0, R1, R2, R3, RSP = 4, RLOC = 5, RCTX = 6, RBUD = 7, R8 = 8, R9 = 9, R10 = 10, R11 = 11, R12 = 12, LR = 14, PC = 15 };
enum { EQ, NE, HS, LO, MI, PL, VS, VC, HI, LS, GE, LT, GT, LE, AL };
enum { AND = 0, EOR = 1, SUB = 2, RSB = 3, ADD = 4, CMP = 10, CMN = 11, ORR = 12, MOV = 13, MVN = 15 };
enum { LSL, LSR, ASR };
#define C_AL 0xE0000000u
#define EPILOGUE 4             /* word index of the common exit */
#define LEADER JIT_BLOCK       /* map flag: a block starts here */
#define WORD(x) JIT_WORD(x)

static const u8 POOL[] = {R0, R1, R2, R3, R12, R9, R10, R11};

/* Emit is copied with jvm_memcpy: a plain struct assignment becomes a call to
   __aeabi_memcpy, a trap stub in the toolchain's compiler-rt. */
static void copy_emit(Emit *to, const Emit *from) { jvm_memcpy(to, from, sizeof(Emit)); }

static void emit(Emit *e, u32 w) {
    if (e->out) { if (e->n < e->cap) e->out[e->n] = w; else e->bad = 1; }
    e->n++;
}

/* Immediate operand: v as an 8-bit value rotated right by an even amount. */
static int imm(u32 v, u32 *field) {
    for (u32 r = 0; r < 16; r++) {
        u32 x = r ? (v << (2*r)) | (v >> (32-2*r)) : v;
        if (x < 256) { *field = r << 8 | x; return 1; }
    }
    return 0;
}

static void dp_imm(Emit *e, u32 op, u32 s, u32 rd, u32 rn, u32 field) {
    emit(e, C_AL | 1u << 25 | op << 21 | s << 20 | rn << 16 | rd << 12 | field);
}
static void dp_reg(Emit *e, u32 op, u32 s, u32 rd, u32 rn, u32 rm, u32 type, u32 amount) {
    emit(e, C_AL | op << 21 | s << 20 | rn << 16 | rd << 12 | amount << 7 | type << 5 | rm);
}
static void dp_shift_reg(Emit *e, u32 rd, u32 rm, u32 type, u32 rs) {
    emit(e, C_AL | MOV << 21 | rd << 12 | rs << 8 | type << 5 | 1u << 4 | rm);
}
static void mov(Emit *e, u32 rd, u32 rm) { dp_reg(e, MOV, 0, rd, 0, rm, LSL, 0); }
/* ldr/str (word or byte) with an immediate offset, no writeback. */
static void mem(Emit *e, u32 load, u32 byte, u32 rd, u32 rn, i32 offset) {
    u32 up = offset >= 0, a = (u32)(up ? offset : -offset);
    emit(e, C_AL | 1u << 26 | 1u << 24 | up << 23 | byte << 22 | load << 20 | rn << 16 | rd << 12 | a);
}
#define LDR(rd, rn, o) mem(e, 1, 0, rd, rn, o)
#define STR(rd, rn, o) mem(e, 0, 0, rd, rn, o)
/* ldrh/strh/ldrsb/ldrsh, immediate offset 0..255. kind: 1 = h, 2 = sb, 3 = sh. */
static void mem_half(Emit *e, u32 load, u32 kind, u32 rd, u32 rn, u32 offset) {
    emit(e, C_AL | 1u << 24 | 1u << 23 | 1u << 22 | load << 20 | rn << 16 | rd << 12 |
            (offset >> 4) << 8 | 1u << 7 | kind << 5 | 1u << 4 | (offset & 15));
}
static void push(Emit *e, u32 r) { emit(e, 0xE4840004u | r << 12); }      /* str r, [r4], #4 */
static void pop(Emit *e, u32 r) { emit(e, 0xE5340004u | r << 12); }       /* ldr r, [r4, #-4]! */
static void top(Emit *e, u32 r, u32 below) { LDR(r, RSP, -4*(i32)below); } /* ldr r, [r4, #-4*below] */
static void put(Emit *e, u32 r, u32 below) { STR(r, RSP, -4*(i32)below); }
static void ldm_db(Emit *e, u32 regs, u32 writeback) { emit(e, 0xE9140000u | writeback << 21 | regs); }  /* ldmdb r4{!}, {regs} */
static void stm_ia(Emit *e, u32 regs) { emit(e, 0xE8A40000u | regs); }    /* stmia r4!, {regs} */
static void sp_add(Emit *e, i32 bytes) {
    if (bytes > 0) dp_imm(e, ADD, 0, RSP, RSP, (u32)bytes);
    else if (bytes < 0) dp_imm(e, SUB, 0, RSP, RSP, (u32)-bytes);
}

static void constant(Emit *e, u32 rd, u32 v) {
    u32 field;
    if (imm(v, &field)) { dp_imm(e, MOV, 0, rd, 0, field); return; }
    if (imm(~v, &field)) { dp_imm(e, MVN, 0, rd, 0, field); return; }
    int first = 1;
    for (u32 bit = 0; bit < 32;) {
        if (!((v >> bit) & 3)) { bit += 2; continue; }
        u32 piece = (v >> bit) & 255;
        imm(piece << bit, &field);
        dp_imm(e, first ? MOV : ORR, 0, rd, first ? 0 : rd, field);
        first = 0;
        bit += 8;
    }
}

static void branch(Emit *e, u32 cond, u32 to) {
    emit(e, cond << 28 | 0x0A000000u | ((to-(e->n+2)) & 0xFFFFFFu));
}
static void call(Emit *e, const void *function) {
    constant(e, R12, (u32)(uintptr_t)function);
    emit(e, 0xE12FFF3Cu);  /* blx r12 */
}
/* The last emitted instruction made conditional. */
static void cond_last(Emit *e, u32 cond) {
    if (e->out && e->n-1 < e->cap) e->out[e->n-1] = (e->out[e->n-1] & 0x0FFFFFFFu) | cond << 28;
}

/* ---------- the cached top of the operand stack ---------- */

static void release(Emit *e, u32 r) { e->used &= ~(1u << r); }

/* The bottom cached entry to the memory stack. */
static void spill_bottom(Emit *e) {
    if (e->args) e->broken = 1;  /* an inlined method's argument: give up the inlining */
    Slot s = e->cache[0];
    if (s.constant) { constant(e, LR, s.value); push(e, LR); }
    else { push(e, s.reg); release(e, s.reg); }
    for (u32 i = 1; i < e->depth; i++) e->cache[i-1] = e->cache[i];
    e->depth--;
}

/* A free register, spilling the bottom entries if none is left. */
static u32 take(Emit *e) {
    for (;;) {
        for (u32 i = 0; i < sizeof(POOL); i++)
            if (!(e->used & 1u << POOL[i])) { e->used |= 1u << POOL[i]; return POOL[i]; }
        if (e->depth <= e->keep) { e->bad = 1; return R0; }  /* cannot happen: 8 registers, few kept */
        spill_bottom(e);
    }
}

/* Everything cached to memory (block ends, the interpreter's instructions). */
static void flush(Emit *e) { while (e->depth) spill_bottom(e); }

/* Code storing the cached entries without forgetting them (exit paths). */
static void store_cache(Emit *e) {
    for (u32 i = 0; i < e->depth; i++) {
        if (e->cache[i].constant) { constant(e, LR, e->cache[i].value); push(e, LR); }
        else push(e, e->cache[i].reg);
    }
}

static void push_reg(Emit *e, u32 r) {
    if (e->depth == CACHE) spill_bottom(e);
    e->cache[e->depth].constant = 0; e->cache[e->depth].reg = (u8)r;
    e->depth++;
}
static void push_const(Emit *e, u32 v) {
    if (e->depth == CACHE) spill_bottom(e);
    e->cache[e->depth].constant = 1; e->cache[e->depth].value = v;
    e->depth++;
}

/* The top k entries cached in registers: loaded from memory or materialized
   from constants; protected from spilling until the instruction ends. */
static void ensure(Emit *e, u32 k) {
    while (e->depth < k) {
        u32 r = take(e);
        pop(e, r);
        for (u32 i = e->depth; i > 0; i--) e->cache[i] = e->cache[i-1];
        e->cache[0].constant = 0; e->cache[0].reg = (u8)r;
        e->depth++;
    }
    e->keep = k;
    for (u32 j = 0; j < k; j++) {
        if (!e->cache[e->depth-1-j].constant) continue;
        u32 r = take(e);
        Slot *s = &e->cache[e->depth-1-j];
        constant(e, r, s->value);
        s->constant = 0; s->reg = (u8)r;
    }
}
static u32 reg_at(Emit *e, u32 below) { return e->cache[e->depth-below].reg; }

/* The top k entries dropped (in memory or cached). */
static void drop(Emit *e, u32 k) {
    while (k && e->depth) {
        Slot *s = &e->cache[--e->depth];
        if (!s->constant) release(e, s->reg);
        k--;
    }
    sp_add(e, -4*(i32)k);
}

/* The top entry in a register of its own, removed from the stack. */
static u32 pop_reg(Emit *e) {
    ensure(e, 1);
    u32 r = reg_at(e, 1);
    e->depth--;
    return r;
}

/* The top entry when it is a constant. */
static int top_constant(Emit *e, u32 *v) {
    if (!e->depth || !e->cache[e->depth-1].constant) return 0;
    *v = e->cache[e->depth-1].value;
    return 1;
}

/* A copy of an entry (dup, an inlined method reading its argument). */
static void push_copy(Emit *e, Slot s) {
    if (s.constant) { push_const(e, s.value); return; }
    u32 t = take(e);
    mov(e, t, s.reg);
    push_reg(e, t);
}

/* ---------- exits ---------- */

/* Back to the interpreter before the bytecode at `at` (3 words). */
static void exit_at(Emit *e, u32 at) {
    dp_imm(e, MOV, 0, R0, 0, at & 255);
    dp_imm(e, ORR, 0, R0, R0, 12u << 8 | (at >> 8 & 255));  /* (at >> 8) ror 24 = bits 8..15 */
    branch(e, AL, EPILOGUE);
}
/* A failing check: to an exit before `at` with the stack as it was before the
   instruction. Nothing cached: a conditional jump to an exit laid out after the
   body; otherwise the cached entries are stored on the way out, inline. */
static void exit_if(Emit *e, u32 cond, u32 at) {
    if (!e->depth) {
        if (e->out) {
            Emit stub;  /* only what emit() uses */
            stub.out = e->out; stub.cap = e->cap; stub.bad = 0;
            stub.n = e->stub_base+3*e->stubs;
            exit_at(&stub, at);
            if (stub.bad) e->bad = 1;
        }
        branch(e, cond, e->stub_base+3*e->stubs);
        e->stubs++;
        return;
    }
    u32 skip = e->n;
    emit(e, 0);
    store_cache(e);
    exit_at(e, at);
    if (e->out && skip < e->cap) e->out[skip] = (cond ^ 1) << 28 | 0x0A000000u | ((e->n-(skip+2)) & 0xFFFFFFu);
}

/* The code of an instruction (second pass), or the sizing pass's guess. */
static u32 target(Emit *e, u32 at) {
    if (!e->out) return e->n;
    if (at >= e->m->code_length || !WORD(e->map[at])) { e->bad = 1; return e->n; }
    return WORD(e->map[at]);
}

static i32 s16(const u8 *p) { return (int16_t)(p[0] << 8 | p[1]); }
static i32 s32(const u8 *p) { return (i32)((u32)p[0] << 24 | (u32)p[1] << 16 | (u32)p[2] << 8 | p[3]); }

/* Instruction length, 0 for an unknown opcode. */
static u32 length(const u8 *code, u32 pc, u32 size) {
    u8 op = code[pc];
    if (op == 0xaa || op == 0xab) {
        u32 p = (pc+4) & ~3u;
        if (p+12 > size) return 0;
        if (op == 0xaa) {
            i32 low = s32(code+p+4), high = s32(code+p+8);
            if (high < low || (u32)(high-low) > 16384) return 0;
            return p+12+4*(u32)(high-low+1)-pc;
        }
        u32 pairs = (u32)s32(code+p+4);
        if (pairs > 16384) return 0;
        return p+8+8*pairs-pc;
    }
    if (op == 0xc4) return pc+1 < size && code[pc+1] == 0x84 ? 6 : 4;
    if (op >= 0xd0 && op <= 0xdb) return 3;
    if (op > 0xc9) return 0;
    if (op == 0x10 || op == 0x12 || (op >= 0x15 && op <= 0x19) || (op >= 0x36 && op <= 0x3a) || op == 0xa9 || op == 0xbc) return 2;
    if (op == 0x11 || op == 0x13 || op == 0x14 || op == 0x84 || (op >= 0x99 && op <= 0xa8) ||
        (op >= 0xb2 && op <= 0xb8) || op == 0xbb || op == 0xbd || op == 0xc0 || op == 0xc1 || op == 0xc6 || op == 0xc7) return 3;
    if (op == 0xc5) return 4;
    if (op == 0xb9 || op == 0xba || op == 0xc8 || op == 0xc9) return 5;
    return 1;
}

/* Index of the low word of a long in two stack slots (put_wide keeps memory order). */
static u32 low_word(void) { union { u64 l; u32 w[2]; } v; v.l = 1; return v.w[1] == 1; }

static i32 jit_div(i32 a, i32 b) { return b == -1 ? (i32)(0u-(u32)a) : a/b; }
static i32 jit_rem(i32 a, i32 b) { return b == -1 ? 0 : a%b; }

/* A backward jump stops before itself once the budget is used up (blocks pay as they start). */
static void budget(Emit *e, u32 at) {
    dp_imm(e, CMP, 1, 0, RBUD, 0);
    exit_if(e, LE, at);
}

/* Array element check: exits before `at` when the array is null or the index out of range.
   t receives the length. */
static void array_check(Emit *e, u32 at, u32 array, u32 index, u32 t) {
    dp_imm(e, CMP, 1, 0, array, 0);
    exit_if(e, EQ, at);
    LDR(t, array, (i32)offsetof(Array, length));
    dp_reg(e, CMP, 1, 0, index, t, LSL, 0);
    exit_if(e, HS, at);
}

/* ---------- inlining ---------- */

static int translate(Emit *e, const u8 *code, u32 pc, u32 size);

/* Math.min/max/abs(int), natives in the library: two or three instructions here. */
static int math(Emit *e, const Method *t) {
    if (!utf8_equals(t->owner->name, "java/lang/Math")) return 0;
    if ((utf8_equals(t->name, "min") || utf8_equals(t->name, "max")) && utf8_equals(t->desc, "(II)I")) {
        u32 b = pop_reg(e), a = pop_reg(e);
        dp_reg(e, CMP, 1, 0, a, b, LSL, 0);
        mov(e, a, b);
        cond_last(e, utf8_equals(t->name, "min") ? GT : LT);
        release(e, b);
        push_reg(e, a);
        return 1;
    }
    if (utf8_equals(t->name, "abs") && utf8_equals(t->desc, "(I)I")) {
        u32 r = pop_reg(e);
        dp_imm(e, CMP, 1, 0, r, 0);
        dp_imm(e, RSB, 0, r, r, 0);
        cond_last(e, LT);
        push_reg(e, r);
        return 1;
    }
    return 0;
}

#define INLINE_MAX 48  /* bytecode bytes of a method inlined at its calls */

/* Bytecodes of a method that can take the place of its call: straight-line code
   that cannot throw (no arrays, no division; fields only of its own receiver,
   which the call checked), no locals beyond its arguments, and the return as its
   last instruction. 0 when it cannot. */
static u32 simple_callee(const Method *t) {
    if (t->access & (ACC_NATIVE|ACC_SYNCHRONIZED|ACC_ABSTRACT)) return 0;
    if (!t->code || !t->code_length || t->code_length > INLINE_MAX || t->handler_count) return 0;
    if (t->max_locals > t->arg_slots || t->arg_slots > 16) return 0;
    int self = !(t->access & ACC_STATIC);
    u8 kind[16];  /* operand stack: 1 = the receiver */
    u32 depth = 0, count = 0;
    const u8 *c = t->code;
    for (u32 pc = 0; pc < t->code_length;) {
        u8 op = c[pc];
        u32 n = length(c, pc, t->code_length), pops = 0, pushes = 0, pushed = 0;
        if (!n) return 0;
        if ((op >= 0x02 && op <= 0x08) || (op >= 0x0b && op <= 0x0d) || op == 0x10 || op == 0x11) pushes = 1;
        else if (op == 0x12 || op == 0x13) {
            const Constant *k = &t->owner->constants[op == 0x12 ? c[pc+1] : (u32)(c[pc+1] << 8 | c[pc+2])];
            if (k->tag != 3 && k->tag != 4) return 0;
            pushes = 1;
        }
        else if (op == 0x15 || op == 0x17 || op == 0x19) { pushes = 1; pushed = self && op == 0x19 && !c[pc+1]; }
        else if ((op >= 0x1a && op <= 0x1d) || (op >= 0x22 && op <= 0x25) || (op >= 0x2a && op <= 0x2d)) {
            pushes = 1;
            pushed = self && op == 0x2a;
        }
        else if (op == 0x60 || op == 0x64 || op == 0x68 || op == 0x7e || op == 0x80 || op == 0x82 ||
                 op == 0x78 || op == 0x7a || op == 0x7c) { pops = 2; pushes = 1; }
        else if (op == 0x74 || (op >= 0x91 && op <= 0x93)) { pops = 1; pushes = 1; }
        else if (op == 0x59) { if (!depth) return 0; pushes = 1; pushed = kind[depth-1]; }
        else if (op == 0xd0) { if (!depth || kind[depth-1] != 1) return 0; pops = 1; pushes = 1; }
        else if (op == 0xd2) { if (depth < 2 || kind[depth-2] != 1) return 0; pops = 2; }
        else if (op == 0xd4) pushes = 1;
        else if (op == 0xd6) pops = 1;
        else if (op == 0xac || op == 0xae || op == 0xb0 || op == 0xb1) {
            if (pc+n != t->code_length || (op != 0xb1 && !depth)) return 0;
            return count+1;
        }
        else return 0;
        if (depth < pops) return 0;
        depth -= pops;
        for (u32 i = 0; i < pushes; i++) { if (depth >= 16) return 0; kind[depth++] = (u8)pushed; }
        count++;
        pc += n;
    }
    return 0;
}

/* The inlined method's body, its return leaving the result in place of the arguments. */
static int inline_body(Emit *e, const Method *t) {
    const Method *caller = e->m;
    e->m = t; e->inlined = 1;
    int ok = 1;
    u8 ret = 0xb1;
    for (u32 p = 0; p < t->code_length;) {
        u8 o = t->code[p];
        if (o >= 0xac && o <= 0xb1) { ret = o; break; }
        if (!translate(e, t->code, p, t->code_length)) { ok = 0; break; }
        p += length(t->code, p, t->code_length);
    }
    e->m = caller; e->inlined = 0;
    if (!ok) return 0;
    u32 result = ret == 0xb1 ? 0 : pop_reg(e);
    while (e->depth) { Slot *s = &e->cache[--e->depth]; if (!s->constant) release(e, s->reg); }  /* the arguments */
    if (e->args) e->args = 0;
    else mov(e, RSP, R8);
    if (ret != 0xb1) push_reg(e, result);
    return 1;
}

/* A call whose target is simple enough: its body in place of the call. A virtual
   call checks that the receiver's class still calls this method; a null receiver
   leaves the call to the interpreter. The arguments are the inlined method's
   locals: the cached entries themselves when they fit in registers, else the
   memory stack (r8). */
static int inline_call(Emit *e, const u8 *code, u32 pc) {
    u8 op = code[pc];
    if (e->inlined) return 0;
    const Method *t = e->m->owner->constants[code[pc+1] << 8 | code[pc+2]].u.method;
    if (!t) return 0;
    if (op == 0xd9 && (t->access & ACC_NATIVE)) return math(e, t);
    u32 count = simple_callee(t);
    if (!count || ((t->access & ACC_STATIC) != 0) != (op == 0xd9)) return 0;
    if (op == 0xd8 && t->vtable_index >= 1024) return 0;
    u32 args = t->arg_slots;
    if (args && args+t->max_stack+2 <= sizeof(POOL) && args <= CACHE) {
        /* Arguments in registers: everything below them to memory, then they are
           the whole cache and stay there (a spill would end this). */
        ensure(e, args);
        while (e->depth > args) spill_bottom(e);
        e->keep = 0;
        if (op != 0xd9) {
            u32 self = e->cache[0].reg;
            dp_imm(e, CMP, 1, 0, self, 0);
            exit_if(e, EQ, pc);
            if (op == 0xd8) {
                u32 a = take(e), b = take(e);
                LDR(a, self, (i32)offsetof(Object, cls));
                LDR(a, a, (i32)offsetof(Class, vtable));
                LDR(a, a, 4*(i32)t->vtable_index);
                constant(e, b, (u32)(uintptr_t)t);
                dp_reg(e, CMP, 1, 0, a, b, LSL, 0);
                release(e, a); release(e, b);
                exit_if(e, NE, pc);
            }
        }
        e->args = args;
        e->broken = 0;
        int ok = inline_body(e, t);
        if (!ok || e->broken) return 0;
        return (int)(1+count);
    }
    /* Arguments in memory. */
    flush(e);
    if (op != 0xd9) {
        top(e, R0, args);
        dp_imm(e, CMP, 1, 0, R0, 0);
        exit_if(e, EQ, pc);
        if (op == 0xd8) {
            LDR(R1, R0, (i32)offsetof(Object, cls));
            LDR(R1, R1, (i32)offsetof(Class, vtable));
            LDR(R1, R1, 4*(i32)t->vtable_index);
            constant(e, R2, (u32)(uintptr_t)t);
            dp_reg(e, CMP, 1, 0, R1, R2, LSL, 0);
            exit_if(e, NE, pc);
        }
    }
    dp_imm(e, SUB, 0, R8, RSP, 4*args);
    u32 loc = e->loc;
    e->loc = R8;
    int ok = inline_body(e, t);
    e->loc = loc;
    return ok ? (int)(1+count) : 0;
}

/* ---------- instructions ---------- */

/* One instruction; returns the bytecodes it stands for (more than one with an
   inlined call), 0 when it is left to the interpreter. */
static int translate(Emit *e, const u8 *code, u32 pc, u32 size) {
    const Method *m = e->m;
    u8 op = code[pc];
    const u32 DATA = sizeof(Array), LOW = low_word();
    e->keep = 0;
    (void)size;
    if (op == 0x00) return 1;
    if (op == 0x01) { push_const(e, 0); return 1; }
    if (op >= 0x02 && op <= 0x08) { push_const(e, (u32)(op-3)); return 1; }
    if (op >= 0x0b && op <= 0x0d) { push_const(e, op == 0x0b ? 0 : op == 0x0c ? 0x3f800000u : 0x40000000u); return 1; }
    if (op == 0x10) { push_const(e, (u32)(i32)(int8_t)code[pc+1]); return 1; }
    if (op == 0x11) { push_const(e, (u32)s16(code+pc+1)); return 1; }
    if (op == 0x12 || op == 0x13) {
        u32 index = op == 0x12 ? code[pc+1] : (u32)(code[pc+1] << 8 | code[pc+2]);
        const Constant *k = &m->owner->constants[index];
        if (k->tag != 3 && k->tag != 4) return 0;  /* int, float */
        push_const(e, k->u.value);
        return 1;
    }
    if (op == 0x09 || op == 0x0a) {
        flush(e);
        dp_imm(e, MOV, 0, R0, 0, 0);
        dp_imm(e, MOV, 0, R1, 0, op-9u);
        if (LOW) stm_ia(e, 1u << R0 | 1u << R1); else { push(e, R1); push(e, R0); }
        return 1;
    }
    /* locals */
    {
        i32 slot = -1, words = 1, store = 0;
        if (op >= 0x15 && op <= 0x19) { slot = code[pc+1]; words = op == 0x16 || op == 0x18 ? 2 : 1; }
        else if (op >= 0x1a && op <= 0x2d) { slot = (op-0x1a) & 3; words = (op >= 0x1e && op <= 0x21) || (op >= 0x26 && op <= 0x29) ? 2 : 1; }
        else if (op >= 0x36 && op <= 0x3a) { slot = code[pc+1]; words = op == 0x37 || op == 0x39 ? 2 : 1; store = 1; }
        else if (op >= 0x3b && op <= 0x4e) { slot = (op-0x3b) & 3; words = (op >= 0x3f && op <= 0x42) || (op >= 0x47 && op <= 0x4a) ? 2 : 1; store = 1; }
        if (slot >= 0) {
            if (e->args) {  /* an inlined method reading an argument kept in the cache */
                if (store || words != 1 || (u32)slot >= e->args) { e->broken = 1; return 0; }
                push_copy(e, e->cache[slot]);
                return 1;
            }
            if (words == 1) {
                if (store) { u32 r = pop_reg(e); STR(r, e->loc, 4*slot); release(e, r); }
                else { u32 r = take(e); LDR(r, e->loc, 4*slot); push_reg(e, r); }
            } else {
                flush(e);
                if (store) {
                    ldm_db(e, 1u << R0 | 1u << R1, 1);
                    STR(R0, e->loc, 4*slot); STR(R1, e->loc, 4*slot+4);
                } else {
                    LDR(R0, e->loc, 4*slot); LDR(R1, e->loc, 4*slot+4);
                    stm_ia(e, 1u << R0 | 1u << R1);
                }
            }
            return 1;
        }
    }
    /* array loads: ..., array, index -> ..., value */
    if (op >= 0x2e && op <= 0x35) {
        if (op == 0x2f || op == 0x31) {
            flush(e);
            ldm_db(e, 1u << R0 | 1u << R1, 0);
            array_check(e, pc, R0, R1, R2);
            dp_reg(e, ADD, 0, R0, R0, R1, LSL, 3);
            LDR(R2, R0, (i32)DATA); LDR(R3, R0, (i32)DATA+4);
            put(e, R2, 2); put(e, R3, 1);
            return 1;
        }
        ensure(e, 2);
        u32 a = reg_at(e, 2), i = reg_at(e, 1), t = take(e);
        array_check(e, pc, a, i, t);
        if (op == 0x33) { dp_reg(e, ADD, 0, t, a, i, LSL, 0); mem_half(e, 1, 2, t, t, DATA); }
        else if (op == 0x34 || op == 0x35) { dp_reg(e, ADD, 0, t, a, i, LSL, 1); mem_half(e, 1, op == 0x34 ? 1 : 3, t, t, DATA); }
        else { dp_reg(e, ADD, 0, t, a, i, LSL, 2); LDR(t, t, (i32)DATA); }
        drop(e, 2);
        push_reg(e, t);
        return 1;
    }
    /* array stores: ..., array, index, value -> ... (aastore checks types: interpreter) */
    if (op >= 0x4f && op <= 0x56 && op != 0x53) {
        if (op == 0x50 || op == 0x52) {
            flush(e);
            ldm_db(e, 1u << R0 | 1u << R1 | 1u << R2 | 1u << R3, 0);
            array_check(e, pc, R0, R1, R2);  /* r2 is reloaded below */
            top(e, R2, 2);
            dp_reg(e, ADD, 0, R0, R0, R1, LSL, 3);
            STR(R2, R0, (i32)DATA); STR(R3, R0, (i32)DATA+4);
            sp_add(e, -16);
            return 1;
        }
        ensure(e, 3);
        u32 a = reg_at(e, 3), i = reg_at(e, 2), v = reg_at(e, 1), t = take(e);
        array_check(e, pc, a, i, t);
        if (op == 0x54) { dp_reg(e, ADD, 0, t, a, i, LSL, 0); mem(e, 0, 1, v, t, (i32)DATA); }
        else if (op == 0x55 || op == 0x56) { dp_reg(e, ADD, 0, t, a, i, LSL, 1); mem_half(e, 0, 1, v, t, DATA); }
        else { dp_reg(e, ADD, 0, t, a, i, LSL, 2); STR(v, t, (i32)DATA); }
        release(e, t);
        drop(e, 3);
        return 1;
    }
    switch (op) {
    case 0x57: drop(e, 1); return 1;
    case 0x58: drop(e, 2); return 1;
    case 0x59: ensure(e, 1); push_copy(e, e->cache[e->depth-1]); return 1;
    case 0x5c: {  /* dup2 */
        ensure(e, 2);
        Slot a = e->cache[e->depth-2], b = e->cache[e->depth-1];
        push_copy(e, a);
        push_copy(e, b);
        return 1;
    }
    case 0x5f: {  /* swap */
        ensure(e, 2);
        Slot s = e->cache[e->depth-1];
        e->cache[e->depth-1] = e->cache[e->depth-2];
        e->cache[e->depth-2] = s;
        return 1;
    }
    case 0x5a:
        flush(e);
        ldm_db(e, 1u << R0 | 1u << R1, 0); put(e, R1, 2); put(e, R0, 1); push(e, R1);
        return 1;
    case 0x60: case 0x64: case 0x7e: case 0x80: case 0x82: {
        u32 o = op == 0x60 ? ADD : op == 0x64 ? SUB : op == 0x7e ? AND : op == 0x80 ? ORR : EOR, v, field;
        if (top_constant(e, &v)) {
            u32 alt = o == ADD ? SUB : o == SUB ? ADD : 0;
            if (imm(v, &field) || (alt && imm(0u-v, &field))) {
                if (!imm(v, &field)) { imm(0u-v, &field); o = alt; }
                e->depth--;
                u32 a = pop_reg(e);
                dp_imm(e, o, 0, a, a, field);
                push_reg(e, a);
                return 1;
            }
        }
        u32 b = pop_reg(e), a = pop_reg(e);
        dp_reg(e, o, 0, a, a, b, LSL, 0);
        release(e, b);
        push_reg(e, a);
        return 1;
    }
    case 0x68: {
        u32 b = pop_reg(e), a = pop_reg(e);
        emit(e, C_AL | a << 16 | a << 8 | 0x90u | b);  /* mul a, b, a */
        release(e, b);
        push_reg(e, a);
        return 1;
    }
    case 0x6c: case 0x70:
        flush(e);
        top(e, R1, 1);
        dp_imm(e, CMP, 1, 0, R1, 0);
        exit_if(e, EQ, pc);
        ldm_db(e, 1u << R0 | 1u << R1, 1);
        call(e, op == 0x6c ? (const void *)jit_div : (const void *)jit_rem);
        push(e, R0);
        return 1;
    case 0x74: { u32 r = pop_reg(e); dp_imm(e, RSB, 0, r, r, 0); push_reg(e, r); return 1; }
    case 0x78: case 0x7a: case 0x7c: {
        u32 type = op == 0x78 ? LSL : op == 0x7a ? ASR : LSR, v;
        if (top_constant(e, &v)) {
            e->depth--;
            u32 a = pop_reg(e);
            if (v & 31) dp_reg(e, MOV, 0, a, 0, a, type, v & 31);
            push_reg(e, a);
            return 1;
        }
        u32 b = pop_reg(e), a = pop_reg(e);
        dp_imm(e, AND, 0, b, b, 31);
        dp_shift_reg(e, a, a, type, b);
        release(e, b);
        push_reg(e, a);
        return 1;
    }
    case 0x84: {
        i32 slot = code[pc+1], add = (int8_t)code[pc+2];
        u32 r = take(e);
        LDR(r, e->loc, 4*slot);
        if (add >= 0) dp_imm(e, ADD, 0, r, r, (u32)add); else dp_imm(e, SUB, 0, r, r, (u32)-add);
        STR(r, e->loc, 4*slot);
        release(e, r);
        return 1;
    }
    case 0x85:
        flush(e);
        top(e, R0, 1);
        dp_reg(e, MOV, 0, R1, 0, R0, ASR, 31);
        if (LOW) { put(e, R1, 1); push(e, R0); } else { put(e, R0, 1); push(e, R1); }
        return 1;
    case 0x88: flush(e); ldm_db(e, 1u << R0 | 1u << R1, 1); push(e, LOW ? R1 : R0); return 1;
    case 0x91: case 0x92: case 0x93: {
        u32 bits = op == 0x91 ? 24 : 16, r = pop_reg(e);
        dp_reg(e, MOV, 0, r, 0, r, LSL, bits);
        dp_reg(e, MOV, 0, r, 0, r, op == 0x92 ? LSR : ASR, bits);
        push_reg(e, r);
        return 1;
    }
    case 0x99: case 0x9a: case 0x9b: case 0x9c: case 0x9d: case 0x9e: case 0xc6: case 0xc7: {
        static const u8 cond[] = {EQ, NE, LT, GE, GT, LE};
        u32 to = pc+(u32)s16(code+pc+1);
        if (to <= pc) budget(e, pc);
        u32 r = pop_reg(e);
        flush(e);
        dp_imm(e, CMP, 1, 0, r, 0);
        release(e, r);
        branch(e, op >= 0xc6 ? (op == 0xc6 ? EQ : NE) : cond[op-0x99], target(e, to));
        return 1;
    }
    case 0x9f: case 0xa0: case 0xa1: case 0xa2: case 0xa3: case 0xa4: case 0xa5: case 0xa6: {
        static const u8 cond[] = {EQ, NE, LT, GE, GT, LE, EQ, NE};
        u32 to = pc+(u32)s16(code+pc+1), v, field;
        if (to <= pc) budget(e, pc);
        if (top_constant(e, &v) && (imm(v, &field) || imm(0u-v, &field))) {
            e->depth--;
            u32 a = pop_reg(e);
            flush(e);
            if (imm(v, &field)) dp_imm(e, CMP, 1, 0, a, field);
            else { imm(0u-v, &field); dp_imm(e, CMN, 1, 0, a, field); }
            release(e, a);
        } else {
            u32 b = pop_reg(e), a = pop_reg(e);
            flush(e);
            dp_reg(e, CMP, 1, 0, a, b, LSL, 0);
            release(e, a); release(e, b);
        }
        branch(e, cond[op-0x9f], target(e, to));
        return 1;
    }
    case 0xa7: case 0xc8: {
        u32 to = pc+(op == 0xa7 ? (u32)s16(code+pc+1) : (u32)s32(code+pc+1));
        if (to <= pc) budget(e, pc);
        flush(e);
        branch(e, AL, target(e, to));
        return 1;
    }
    case 0xaa: {
        u32 p = (pc+4) & ~3u, def = pc+(u32)s32(code+p);
        i32 low = s32(code+p+4), high = s32(code+p+8);
        u32 count = (u32)(high-low)+1;
        if (count > 1024) return 0;
        flush(e);
        int back = def <= pc;
        for (u32 i = 0; i < count; i++) if (pc+(u32)s32(code+p+12+4*i) <= pc) back = 1;
        if (back) budget(e, pc);
        pop(e, R0);
        if (low) { constant(e, R1, (u32)low); dp_reg(e, SUB, 0, R0, R0, R1, LSL, 0); }
        constant(e, R1, count-1);
        dp_reg(e, CMP, 1, 0, R0, R1, LSL, 0);
        branch(e, HI, target(e, def));
        dp_reg(e, ADD, 0, PC, PC, R0, LSL, 2);  /* pc reads 8 ahead: the table after the filler */
        mov(e, R0, R0);                         /* filler */
        for (u32 i = 0; i < count; i++) branch(e, AL, target(e, pc+(u32)s32(code+p+12+4*i)));
        return 1;
    }
    case 0xab: {
        u32 p = (pc+4) & ~3u, def = pc+(u32)s32(code+p), pairs = (u32)s32(code+p+4);
        if (pairs > 64) return 0;
        flush(e);
        int back = def <= pc;
        for (u32 i = 0; i < pairs; i++) if (pc+(u32)s32(code+p+12+8*i) <= pc) back = 1;
        if (back) budget(e, pc);
        pop(e, R0);
        for (u32 i = 0; i < pairs; i++) {
            constant(e, R1, (u32)s32(code+p+8+8*i));
            dp_reg(e, CMP, 1, 0, R0, R1, LSL, 0);
            branch(e, EQ, target(e, pc+(u32)s32(code+p+12+8*i)));
        }
        branch(e, AL, target(e, def));
        return 1;
    }
    case 0xbe: {
        ensure(e, 1);
        u32 a = reg_at(e, 1);
        dp_imm(e, CMP, 1, 0, a, 0);
        exit_if(e, EQ, pc);
        LDR(a, a, (i32)offsetof(Array, length));
        return 1;
    }
    case 0xd0: case 0xd1: {  /* GETFIELD_Q(_WIDE): operand = byte offset */
        u32 offset = (u32)(code[pc+1] << 8 | code[pc+2]);
        if (offset+4 >= 4096) return 0;
        if (op == 0xd1) {
            flush(e);
            top(e, R0, 1);
            dp_imm(e, CMP, 1, 0, R0, 0);
            exit_if(e, EQ, pc);
            LDR(R1, R0, (i32)offset); LDR(R2, R0, (i32)offset+4); put(e, R1, 1); push(e, R2);
            return 1;
        }
        ensure(e, 1);
        u32 o = reg_at(e, 1);
        if (!e->inlined) {
            dp_imm(e, CMP, 1, 0, o, 0);
            exit_if(e, EQ, pc);
        }
        LDR(o, o, (i32)offset);
        return 1;
    }
    case 0xd2: case 0xd3: {  /* PUTFIELD_Q(_WIDE) */
        u32 offset = (u32)(code[pc+1] << 8 | code[pc+2]);
        if (offset+4 >= 4096) return 0;
        if (op == 0xd3) {
            flush(e);
            ldm_db(e, 1u << R0 | 1u << R1 | 1u << R2, 0);
            if (!e->inlined) {
                dp_imm(e, CMP, 1, 0, R0, 0);
                exit_if(e, EQ, pc);
            }
            STR(R1, R0, (i32)offset); STR(R2, R0, (i32)offset+4);
            sp_add(e, -12);
            return 1;
        }
        ensure(e, 2);
        u32 o = reg_at(e, 2), v = reg_at(e, 1);
        if (!e->inlined) {
            dp_imm(e, CMP, 1, 0, o, 0);
            exit_if(e, EQ, pc);
        }
        STR(v, o, (i32)offset);
        drop(e, 2);
        return 1;
    }
    case 0xd8: case 0xd9: case 0xda:  /* INVOKEVIRTUAL_Q, INVOKESTATIC_Q, INVOKESPECIAL_Q */
        return inline_call(e, code, pc);
    case 0xd4: case 0xd6: {  /* GETSTATIC_Q, PUTSTATIC_Q: class initialized */
        const Field *fd = m->owner->constants[code[pc+1] << 8 | code[pc+2]].u.field;
        u32 address = (u32)(uintptr_t)&fd->owner->statics[fd->offset];
        if (op == 0xd4) { u32 t = take(e); constant(e, t, address); LDR(t, t, 0); push_reg(e, t); }
        else { u32 v = pop_reg(e), t = take(e); constant(e, t, address); STR(v, t, 0); release(e, t); release(e, v); }
        return 1;
    }
    case 0xd5: case 0xd7: {  /* the wide forms */
        const Field *fd = m->owner->constants[code[pc+1] << 8 | code[pc+2]].u.field;
        flush(e);
        constant(e, R3, (u32)(uintptr_t)&fd->owner->statics[fd->offset]);
        if (op == 0xd5) { LDR(R0, R3, 0); LDR(R1, R3, 4); stm_ia(e, 1u << R0 | 1u << R1); }
        else { ldm_db(e, 1u << R0 | 1u << R1, 1); STR(R0, R3, 0); STR(R1, R3, 4); }
        return 1;
    }
    }
    return 0;
}

/* ---------- methods ---------- */

static int is_jump(u8 op) { return (op >= 0x99 && op <= 0xa7) || op == 0xaa || op == 0xab || op == 0xc6 || op == 0xc7 || op == 0xc8; }

static void lead(Emit *e, u32 at) { if (at < e->m->code_length) e->map[at] |= LEADER; }

/* A copy for trying instructions out: nothing written, nothing cached. */
static void dry_copy(Emit *dry, const Emit *e) {
    copy_emit(dry, e);
    dry->out = 0;
}
static void dry_reset(Emit *dry) {
    dry->n = 0; dry->stubs = 0; dry->depth = 0; dry->used = 0; dry->args = 0; dry->keep = 0; dry->inlined = 0;
}

/* Blocks start at jump targets and after jumps and after whatever compiled code
   leaves to the interpreter (calls: the interpreter comes back after them). */
static void mark_leaders(Emit *e) {
    const Method *m = e->m;
    const u8 *code = m->code;
    Emit dry;
    dry_copy(&dry, e);
    lead(e, 0);
    for (u32 pc = 0; pc < m->code_length;) {
        u32 n = length(code, pc, m->code_length);
        if (!n || pc+n > m->code_length) { e->bad = 1; return; }
        u8 op = code[pc];
        if ((op >= 0x99 && op <= 0xa7) || op == 0xc6 || op == 0xc7) lead(e, pc+(u32)s16(code+pc+1));
        else if (op == 0xc8) lead(e, pc+(u32)s32(code+pc+1));
        else if (op == 0xaa || op == 0xab) {
            u32 p = (pc+4) & ~3u;
            lead(e, pc+(u32)s32(code+p));
            if (op == 0xaa) {
                u32 count = (u32)(s32(code+p+8)-s32(code+p+4))+1;
                for (u32 i = 0; i < count; i++) lead(e, pc+(u32)s32(code+p+12+4*i));
            } else {
                u32 pairs = (u32)s32(code+p+4);
                for (u32 i = 0; i < pairs; i++) lead(e, pc+(u32)s32(code+p+12+8*i));
            }
        }
        dry_reset(&dry);
        /* After an inlined virtual or special call too: its check may send the call
           to the interpreter, which comes back after it. Static ones cannot fail. */
        if (is_jump(op) || op == 0xd8 || op == 0xda || !translate(&dry, code, pc, m->code_length)) lead(e, pc+n);
        pc += n;
    }
}

/* Bytecodes compiled code runs from a block start to the next block. */
static u32 block_cost(Emit *e, u32 from) {
    const Method *m = e->m;
    u32 cost = 0;
    Emit dry;
    dry_copy(&dry, e);
    for (u32 pc = from; pc < m->code_length;) {
        if (pc != from && (e->map[pc] & LEADER)) break;
        dry_reset(&dry);
        cost += (u32)translate(&dry, m->code, pc, m->code_length);
        pc += length(m->code, pc, m->code_length);
    }
    return cost;
}

/* The whole method: prologue, epilogue, body; then the exits of failing checks,
   written as the checks are emitted. */
static void translate_method(Emit *e) {
    const Method *m = e->m;
    const u8 *code = m->code;
    e->n = 0; e->stubs = 0; e->depth = 0; e->used = 0; e->args = 0;
    emit(e, 0xE92D4FF8u);  /* push {r3-r11, lr} (8-byte aligned stack for calls) */
    emit(e, 0xE1A06000u);  /* mov r6, r0 */
    emit(e, 0xE89600B0u);  /* ldm r6, {r4, r5, r7} */
    emit(e, 0xE12FFF11u);  /* bx r1 */
    emit(e, 0xE88600B0u);  /* EPILOGUE: stm r6, {r4, r5, r7} */
    emit(e, 0xE8BD8FF8u);  /* pop {r3-r11, pc} */
    for (u32 pc = 0; pc < m->code_length;) {
        u32 n = length(code, pc, m->code_length);
        if (!n || pc+n > m->code_length) { e->bad = 1; return; }
        if (e->map[pc] & LEADER) flush(e);  /* falling into a block: nothing cached at its start */
        if (!e->out) e->map[pc] = (u16)((e->map[pc] & LEADER) | e->n);
        else if (WORD(e->map[pc]) != e->n) { e->bad = 1; return; }
        if (e->map[pc] & LEADER) {
            /* The block pays for its bytecodes as it starts: the turn of the thread
               is counted in bytecodes run, as in the interpreter. */
            for (u32 cost = block_cost(e, pc); cost; cost -= cost > 255 ? 255 : cost)
                dp_imm(e, SUB, 0, RBUD, RBUD, cost > 255 ? 255 : cost);
        }
        u32 n0 = e->n, stubs0 = e->stubs, depth0 = e->depth, used0 = e->used;
        Slot cache0[CACHE];
        for (u32 i = 0; i < depth0; i++) cache0[i] = e->cache[i];
        if (!translate(e, code, pc, m->code_length)) {
            e->n = n0; e->stubs = stubs0; e->depth = depth0; e->used = used0;
            for (u32 i = 0; i < depth0; i++) e->cache[i] = cache0[i];
            e->args = 0; e->keep = 0; e->inlined = 0; e->loc = RLOC;
            flush(e);
            exit_at(e, pc);
        }
        pc += n;
    }
    if (!e->out) e->stub_base = e->n;
    else if (e->n != e->stub_base) e->bad = 1;
    e->n = e->stub_base+3*e->stubs;
}

/* Data cache to memory, instruction cache emptied: the core then fetches the new code. */
static int sync_code(const void *start, u32 bytes) {
    u32 cpsr;
    __asm__ volatile("mrs %0, cpsr" : "=r"(cpsr));
    if ((cpsr & 0x1f) == 0x10) return 0;  /* user mode: no cache maintenance */
    for (uintptr_t p = (uintptr_t)start & ~(uintptr_t)31; p < (uintptr_t)start+bytes; p += 32)
        __asm__ volatile("mcr p15, 0, %0, c7, c10, 1" : : "r"(p) : "memory");  /* clean D line */
    __asm__ volatile("mcr p15, 0, %0, c7, c10, 4" : : "r"(0) : "memory");      /* drain write buffer */
    __asm__ volatile("mcr p15, 0, %0, c7, c5, 0" : : "r"(0) : "memory");       /* invalidate I cache */
    return 1;
}

static void *jit_memory(Jvm *vm, u32 bytes) {
    bytes = (bytes+7) & ~7u;
    if (vm->jit_total+bytes > JIT_LIMIT) return 0;
    if (!vm->jit_top || (u32)(vm->jit_end-vm->jit_top) < bytes) {
        u32 size = bytes > JIT_CHUNK ? bytes : JIT_CHUNK;
        if (!vm->host.alloc_extra) return 0;
        u8 *c = vm->host.alloc_extra(vm->host.context, size);
        if (!c) return 0;
        vm->jit_top = (u8 *)(((uintptr_t)c+7) & ~(uintptr_t)7);
        vm->jit_end = c+size;
        if ((u32)(vm->jit_end-vm->jit_top) < bytes) return 0;
    }
    void *p = vm->jit_top;
    vm->jit_top += bytes;
    vm->jit_total += bytes;
    return p;
}

void jit_compile(Jvm *vm, Method *m) {
    m->jit = JIT_NEVER;
    if (vm->jit_count >= JIT_METHODS || !m->code_length || m->code_length > JIT_MAX_CODE) return;
    if (m->max_locals > 1000) return;
    u32 map_bytes = (m->code_length*2+7) & ~7u;
    /* Sizing pass, the map straight into its final place. */
    u16 *map = jit_memory(vm, map_bytes);
    if (!map) return;
    jvm_memset(map, 0, map_bytes);
    Emit e;
    jvm_memset(&e, 0, sizeof e);
    e.map = map; e.m = m; e.loc = RLOC;
    mark_leaders(&e);
    if (e.bad) return;
    translate_method(&e);
    if (e.bad || e.n > 32767) return;
    u32 words = e.n;
    u32 *code = jit_memory(vm, words*4);
    if (!code) return;
    e.out = code;
    e.cap = words;
    translate_method(&e);
    if (e.bad || e.n != words) return;
    if (!sync_code(code, words*4)) return;
    vm->jit[vm->jit_count].code = code;
    vm->jit[vm->jit_count].map = map;
    m->jit = (u16)(JIT_COMPILED | vm->jit_count++);
}

/* Compiled code from the block starting at `at`; the offset where it stopped.
   Anywhere else (not a block start) the interpreter carries on. */
u32 jit_run(Jvm *vm, Method *m, u32 at, u32 **sp, u32 *locals, i32 *budget) {
    const JitCode *c = &vm->jit[m->jit & ~JIT_COMPILED];
    if (at >= m->code_length || !(c->map[at] & LEADER)) return at;
    JitCtx ctx = {*sp, locals, *budget};
    at = ((JitEntry)(uintptr_t)c->code)(&ctx, c->code+WORD(c->map[at]));
    *sp = ctx.sp;
    *budget = ctx.budget;
    return at;
}

#else

void jit_compile(Jvm *vm, Method *m) { (void)vm; m->jit = JIT_NEVER; }
u32 jit_run(Jvm *vm, Method *m, u32 at, u32 **sp, u32 *locals, i32 *budget) {
    (void)vm; (void)m; (void)sp; (void)locals; (void)budget;
    return at;
}

#endif
