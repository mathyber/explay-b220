/* B22V interpreter. All file I/O, allocation and drawing execute in the PS. */
typedef unsigned int u32;
typedef signed int s32;
typedef unsigned short u16;
typedef unsigned char u8;
#define FN(a,t) ((t)(a))
#define MAGIC 0x53444150u
typedef struct {
    u32 magic,code,size,status,loads,pc,r[8],steps,checksum,event,drive,io_status,io_count;
} State;
_Static_assert(sizeof(State)==80,"Must fit the original 81-byte board");
#define S ((volatile State *)0x04388f50u)
typedef u32 (*Handler)(u32,u32,u32);
typedef u32 (*Open)(const u16 *,u32,u32,u32);
typedef u32 (*Size)(u32,u32 *);
typedef u32 (*Read)(u32,void *,u32,u32 *,u32);
typedef u32 (*Close)(u32);
typedef void *(*Alloc)(u32,u32,const char *,u32);
typedef void (*Free)(void *);
typedef void (*Fill)(u32 *,u32,u32,u32);
typedef u32 (*Text)(u32 *,u32,u32,const u16 *,u32,u32,u32,u32,u32,u32);
typedef u32 (*Label)(u32,const void *,u32);

static u32 be16(const u8 *p) { return ((u32)p[0]<<8)|p[1]; }
static u32 be32(const u8 *p) { return (be16(p)<<16)|be16(p+2); }
static u32 point(u32 x,u32 y) { return (x<<16)|y; }
static void fill(u32 x,u32 y,u32 w,u32 h,u32 color) {
    if (x>=176 || y>=176 || !w || !h) return;
    if (w>176-x) w=176-x;
    if (h>176-y) h=176-y;
    u32 dev[2]={0,0};
    FN(0x4c65ad,Fill)(dev,point(x,y),point(x+w-1,y+h-1),color&65535);
}
static void text(u32 y,const char *ascii,u32 color) {
    if (y>156) return;
    u16 buffer[65]; u32 count=0,dev[2]={0,0};
    while (count<64 && ascii[count]) { buffer[count]=(u8)ascii[count]; count++; }
    buffer[count]=0;
    FN(0x4cf433,Text)(dev,point(0,y),point(175,y+19),buffer,count,0,color&65535,6,8,0);
}
static void release(void) {
    if (S->magic==MAGIC && S->code) {
        FN(0x6a5469,Free)((void *)S->code);
        S->code=0;
    }
}
static void error(u32 status) {
    S->status=status;
    fill(0,0,176,176,0);
    text(24,"SD APP ERROR",0xf800);
    char number[]={'0'+(char)status,0};
    text(50,number,0xffff);
    text(90,"APP.B22",0xffe0);
    text(120,"F1: RELOAD",0xffff);
}
static int validate(const u8 *p,u32 size) {
    if (size<16 || be32(p)!=0x42323256 || be16(p+4)!=1 || be16(p+6)!=16 || be16(p+10)) return 0;
    u32 count=be16(p+8),data=16+count*8;
    if (!count || count>256 || data>size || be32(p+12)!=size-data) return 0;
    for (u32 n=0;n<count;n++) {
        const u8 *i=p+16+n*8; u32 op=i[0],a=i[1],b=i[2],c=i[3],v=be32(i+4);
        if (op>13 || a>7 || b>7 || c) return 0;
        if ((op==4 || op==5 || op==6 || op==7 || op==12) && v>=count) return 0;
        if (op==9 && a>3) return 0;
        if (op==10) {
            if (v>=size-data) return 0;
            u32 end=data+v,nchars=0;
            while (end<size && p[end] && nchars<64) {
                if (p[end]<32 || p[end]>126) return 0;
                end++; nchars++;
            }
            if (end>=size || p[end]) return 0;
        }
    }
    return 1;
}
static int load(void) {
    release();
    u32 loads=S->magic==MAGIC?S->loads:0;
    volatile u32 *state=(volatile u32 *)S;
    for (u32 i=0;i<sizeof(State)/4;i++) state[i]=0;
    S->magic=MAGIC; S->loads=loads+1;
    /* E: is the SD volume in this pinned firmware (verified via SFS and SPI). */
    static const u16 path[]={'E',':','\\','A','P','P','.','B','2','2',0};
    S->drive='E';
    u32 handle=FN(0x5edc5f,Open)(path,0x31,0,0);
    if (!handle) { error(1); return 0; }
    u32 size=0,actual=0;
    u32 result=FN(0x5ee59f,Size)(handle,&size);
    S->size=size; S->io_status=result;
    if (result || size<16 || size>4096) {
        FN(0x5ee485,Close)(handle); error(2); return 0;
    }
    u8 *buffer=FN(0x6a5439,Alloc)(size,0,"B22V",0);
    if (!buffer) { FN(0x5ee485,Close)(handle); error(3); return 0; }
    result=FN(0x5ee49f,Read)(handle,buffer,size,&actual,0);
    S->io_status=result; S->io_count=actual;
    u32 closed=FN(0x5ee485,Close)(handle);
    if (result || actual!=size || closed) {
        FN(0x6a5469,Free)(buffer); error(4); return 0;
    }
    if (!validate(buffer,size)) {
        FN(0x6a5469,Free)(buffer); error(5); return 0;
    }
    S->code=(u32)buffer;
    u32 sum=2166136261u;
    for (u32 i=0;i<size;i++) sum=(sum^buffer[i])*16777619u;
    S->checksum=sum; S->status=0;
    return 1;
}
static void run(u32 event) {
    if (!S->code || S->status) return;
    const u8 *p=(const u8 *)S->code;
    u32 count=be16(p+8),data=16+count*8;
    S->event=event;
    for (u32 budget=0;budget<512;budget++) {
        if (S->pc>=count) { error(6); return; }
        const u8 *i=p+16+S->pc*8;
        u32 op=i[0],a=i[1],b=i[2],v=be32(i+4);
        S->pc++; S->steps++;
        switch (op) {
        case 0: S->status=9; return;
        case 1: S->r[a]=v; break;
        case 2: S->r[a]+=v; break;
        case 3: S->r[a]=S->r[b]; break;
        case 4: S->pc=v; break;
        case 5: if (S->r[a]==S->r[b]) S->pc=v; break;
        case 6: if (S->r[a]!=S->r[b]) S->pc=v; break;
        case 7: if ((s32)S->r[a]<(s32)S->r[b]) S->pc=v; break;
        case 8: fill(0,0,176,176,v); break;
        case 9: fill(S->r[a],S->r[a+1],S->r[a+2],S->r[a+3],S->r[a+4]); break;
        case 10: text(S->r[a],(const char *)p+data+v,S->r[b]); break;
        case 11: S->r[a]=event; break;
        case 12: S->pc=v; return;
        case 13: S->pc=0; break;
        default: error(7); return;
        }
    }
    error(8);
}
__attribute__((used,section(".entry")))
u32 game_handler(u32 win,u32 msg,u32 param) {
    Handler original=FN(0x60f74f,Handler);
    if (msg==0xf021) {
        release(); original(win,msg,param);
        static const u16 empty[]={0};
        struct {const u16 *s;u16 length,padding;} label={empty,0,0};
        FN(0x4c5b69,Label)(0x16000c,&label,0);
        if (load()) run(msg);
        return 1;
    }
    if (msg==0xf022) { release(); return original(win,msg,param); }
    if (msg==0xfa16) { if (load()) run(0xf021); return 1; }
    if (msg==0xf024 || msg==0xf025 || msg==0x40000014 || (msg>=0xfa00 && msg<0xfa17) || msg==0xfa1b) {
        run(msg); return 1;
    }
    if (msg==0xf001) return 1;
    return original(win,msg,param);
}
