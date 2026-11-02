/* Native B220 application: uses the stock Sokoban window and GUI calls.
 * All addresses are established for the pinned PS, not a general SDK ABI.
 */
typedef unsigned int u32;
typedef unsigned short u16;
typedef unsigned char u8;

typedef struct {
    u32 magic, score, moves, seed;
    u8 x, y, target_x, target_y;
    u8 hundreds, tens, ones, reserved;
    u32 window, paints;
} Game;
_Static_assert(sizeof(Game)==32, "Game must fit in the original 81-byte board");
#define STATE ((volatile Game *)0x04388f50u)
#define MAGIC 0x43415443u
#define FN(address,type) ((type)(address))
typedef u32 (*Handler)(u32,u32,u32);
typedef void (*Fill)(u32 *,u32,u32,u32);
typedef u32 (*Text)(u32 *,u32,u32,const u16 *,u32,u32,u32,u32,u32,u32);
typedef u32 (*Label)(u32,const void *,u32);

static u32 point(unsigned x,unsigned y) { return (x<<16)|y; }
static void fill(unsigned x,unsigned y,unsigned w,unsigned h,u32 color) {
    u32 dev[2]={0,0};
    FN(0x004c65adu,Fill)(dev,point(x,y),point(x+w-1,y+h-1),color);
}
static void text(unsigned y,unsigned height,const u16 *s,unsigned count,u32 color,unsigned font) {
    u32 dev[2]={0,0};
    FN(0x004cf433u,Text)(dev,point(0,y),point(175,y+height-1),s,count,0,color,font,8,0);
}
static void score_text(void) {
    u16 s[]={'S','C','O','R','E',' ',(u16)('0'+STATE->hundreds),
             (u16)('0'+STATE->tens),(u16)('0'+STATE->ones),0};
    fill(0,26,176,20,0x0841);
    text(26,20,s,9,0xffe0,6);
}
static void cell(unsigned x,unsigned y) {
    unsigned px=16+x*18,py=50+y*18;
    fill(px,py,18,18,0x0841);
    fill(px+1,py+1,16,16,((x+y)&1)?0x2104:0x18c3);
    if (x==STATE->target_x && y==STATE->target_y)
        fill(px+5,py+5,8,8,0xffe0);
    if (x==STATE->x && y==STATE->y) {
        fill(px+3,py+3,12,12,0x07ff);
        fill(px+7,py+7,4,4,0xffff);
    }
}
static void render(void) {
    static const u16 title[]={'C','A','T','C','H',' ','T','H','E',' ','D','O','T',0};
    static const u16 help[]={'A','r','r','o','w','s',':',' ','m','o','v','e',' ',' ','O','K',':',' ','r','e','s','e','t',0};
    fill(0,0,176,196,0x0841);
    text(3,22,title,13,0xffff,6);
    score_text();
    for (unsigned y=0;y<7;y++)
        for (unsigned x=0;x<8;x++) cell(x,y);
    text(179,17,help,23,0xbdf7,0);
    STATE->paints++;
}
static void reset(u32 win) {
    STATE->magic=MAGIC;
    STATE->score=0; STATE->moves=0; STATE->seed=0xb2201234;
    STATE->x=3; STATE->y=3; STATE->target_x=5; STATE->target_y=3;
    STATE->hundreds=0; STATE->tens=0; STATE->ones=0;
    STATE->reserved=0; STATE->window=win; STATE->paints=0;
}
static void collect(void) {
    STATE->score++;
    if (STATE->score==1000) STATE->score=0;
    STATE->ones++;
    if (STATE->ones==10) {
        STATE->ones=0; STATE->tens++;
        if (STATE->tens==10) { STATE->tens=0; STATE->hundreds++; }
        if (STATE->hundreds==10) STATE->hundreds=0;
    }
    STATE->seed=STATE->seed*1664525u+1013904223u;
    STATE->target_x=(STATE->seed>>8)&7;
    STATE->target_y=(STATE->seed>>16)&7;
    if (STATE->target_y==7) STATE->target_y=0;
    if (STATE->target_x==STATE->x && STATE->target_y==STATE->y)
        STATE->target_x=(STATE->target_x+1)&7;
}
static void move(int dx,int dy) {
    int x=(int)STATE->x+dx,y=(int)STATE->y+dy;
    if (x<0 || x>=8 || y<0 || y>=7) return;
    unsigned old_x=STATE->x,old_y=STATE->y;
    STATE->x=(u8)x; STATE->y=(u8)y; STATE->moves++;
    int caught=(x==STATE->target_x && y==STATE->target_y);
    if (caught) collect();
    cell(old_x,old_y);
    if (caught) { cell(STATE->target_x,STATE->target_y); score_text(); }
    cell((unsigned)x,(unsigned)y);
}

__attribute__((used,section(".entry")))
u32 game_handler(u32 win,u32 msg,u32 param) {
    Handler original=FN(0x0060f74fu,Handler);
    if (msg==0xf021) {
        /* Stock open establishes the window, softkeys and game lifecycle.
         * Only afterwards reuse its board allocation for our own state.
         */
        original(win,msg,param);
        reset(win);
        static const u16 empty[]={0};
        struct {const u16 *text;u16 length,padding;} label={empty,0,0};
        FN(0x004c5b69u,Label)(0x0016000c,&label,0);
        render();
        return 1;
    }
    if (msg==0xf025 || msg==0x40000014) {
        if (STATE->magic!=MAGIC) reset(win);
        render(); return 1;
    }
    if (msg==0xf001) return 1; /* Do not pass touch coordinates to the old map. */
    if (STATE->magic==MAGIC) {
        if (msg==0xfa01 || msg==0xfa09) { move(0,-1); return 1; }
        if (msg==0xfa02 || msg==0xfa0f) { move(0,1); return 1; }
        if (msg==0xfa03 || msg==0xfa08) { move(-1,0); return 1; }
        if (msg==0xfa04 || msg==0xfa0d) { move(1,0); return 1; }
        if (msg==0xfa1b) { reset(win); render(); return 1; }
        /* Suppress legacy undo/level actions which interpret the old board. */
        if (msg>=0xfa00 && msg<0xfa17) return 1;
    }
    return original(win,msg,param);
}
