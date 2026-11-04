/* SD Apps: GO1V interpreter and GO1N native loader. File I/O, allocation and drawing execute in the PS. */
#include "go1.h"
typedef unsigned int u32;
typedef signed int s32;
typedef unsigned short u16;
typedef unsigned char u8;
#define FN(a,t) ((t)(a))
#define MAGIC 0x53444150u
/* Allocator heap type: large requests with any other value assert and reboot. */
#define APP_HEAP 0x44444444u
#define EXIT_REQUESTED 10
typedef struct {
    u32 magic,code,size,status,loads,pc,r[8],steps,checksum,event,mode,catalog,io_count;
} State;
#define ICON_PIXELS (GO1_ICON_SIZE*GO1_ICON_SIZE)
typedef struct {
    u32 count,selected; char names[16][13]; u8 has_icon[16];
    u16 titles[16][GO1_TITLE_MAX+1];  /* UTF-16; titles[n][0]==0: show the file name */
    u16 pixels[16][ICON_PIXELS]; u8 alpha[16][ICON_PIXELS];  /* RGB565 as stored (big-endian RAM) */
} Catalog;
#define CAT ((Catalog *)S->catalog)
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
typedef u32 (*Create)(u32,u32);
typedef u32 (*Invalidate)(u32 *,u32,u32);

/* First payload section: replace only the Games menu's launch callback. */
__attribute__((used,section(".launch")))
u32 launch_sd_apps(void) { return FN(0x4d7da3,Create)(0x70d150,0)&255; }

/* Dynamic App Heap is a ThreadX byte pool. A request that does not fit makes
   the firmware allocator assert and reboot instead of returning 0, so every
   allocation first walks the pool for the largest free block (marker 0xFFFFEEEE). */
#define APP_POOL ((volatile u32 *)0x042c4d50u)
#define POOL_MARGIN 128
static u32 largest_free(void) {
    if (APP_POOL[0]!=0x42595445u) return 0;
    u32 start=APP_POOL[6],end=start+APP_POOL[7],block=start,best=0;
    for (u32 n=0;n<4096 && block>=start && block+8<=end;n++) {
        u32 next=*(volatile u32 *)block,mark=*(volatile u32 *)(block+4);
        if (next<=block || next>end) break;
        if (mark==0xffffeeeeu && next-block-8>best) best=next-block-8;
        block=next;
    }
    return best;
}
static void *heap_alloc(u32 size,const char *tag) {
    if (!size || size>0x01000000u || largest_free()<size+POOL_MARGIN) return 0;
    return FN(0x6a5439,Alloc)(size,APP_HEAP,tag,0);
}
static u32 be16(const u8 *p) { return ((u32)p[0]<<8)|p[1]; }
static u32 be32(const u8 *p) { return (be16(p)<<16)|be16(p+2); }
static u32 point(u32 x,u32 y) { return (x<<16)|y; }
static void clear_panel(void) {
    u32 dev[2]={0,0};
    FN(0x4c65ad,Fill)(dev,0,point(175,195),0);
}
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
/* Native mode keeps its window and timer in the unused GO1V registers. */
#define NATIVE_TIMER S->r[6]
#define NATIVE_WINDOW S->r[7]
#define NATIVE_PERIODIC S->r[5]
#define MSG_TIMER 0xf020u  /* parameter points to the expired timer id byte */
typedef u32 (*CreateTimer)(u32,u32,u32);
typedef u32 (*StopTimer)(u32);
static void stop_timer(void) {
    if (S->mode==2 && NATIVE_TIMER) { FN(0x1f5641,StopTimer)(NATIVE_TIMER); NATIVE_TIMER=0; }
}
/* The program may have drawn over the soft key bar: ask MMK for a full
   repaint of the window stack (PostUpdateScreenMsg, 2 = full). */
typedef u32 (*PostUpdate)(u32);
static void repaint_all(void) { FN(0x4d67d3,PostUpdate)(2); }
static const Go1Api api;
static void release(void) {
    stop_timer();
    /* A native program frees its own allocations on GO1_CLOSE. */
    if (S->magic==MAGIC && S->code && S->mode==2 && S->pc) {
        u32 entry=S->pc;
        S->pc=0;
        FN(entry,Go1Entry)(&api,GO1_CLOSE,0);
    }
    if (S->magic==MAGIC && S->code) {
        FN(0x6a5469,Free)((void *)S->code);
        S->code=0;
    }
}
static void release_all(void) {
    release();
    if (S->magic==MAGIC && S->catalog) {
        FN(0x6a5469,Free)((void *)S->catalog);
        S->catalog=0;
    }
}
static void error(u32 status) {
    S->status=status;
    clear_panel();
    text(24,"SD APP ERROR",0xf800);
    char number[]={'0'+(char)status,0};
    text(50,number,0xffff);
    text(90,S->catalog && CAT->count?CAT->names[CAT->selected]:"SD CARD",0xffe0);
    text(120,"F1: RELOAD",0xffff);
}
static int validate(const u8 *p,u32 size) {
    if (size<16 || size>4096 || be32(p)!=0x474f3156 || be16(p+4)!=1 || be16(p+6)!=16 || be16(p+10)) return 0;
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
static u32 open_card(const char *name) {
    u16 path[16]={'E',':','\\',0};
    u32 i=0;
    while (name[i] && i<12) { path[i+3]=(u8)name[i]; i++; }
    path[i+3]=0;
    return FN(0x5edc5f,Open)(path,0x31,0,0);
}
static u8 *read_file(const char *name,u32 minimum,u32 maximum,u32 *length,u32 *reason) {
    u32 handle=open_card(name);
    if (!handle) { *reason=1; return 0; }
    u32 size=0,actual=0;
    u32 result=FN(0x5ee59f,Size)(handle,&size);
    *length=size;
    if (result || size<minimum || size>maximum) {
        FN(0x5ee485,Close)(handle); *reason=2; return 0;
    }
    u8 *buffer=heap_alloc(size,"GO1V");
    if (!buffer) { FN(0x5ee485,Close)(handle); *reason=3; return 0; }
    result=FN(0x5ee49f,Read)(handle,buffer,size,&actual,0);
    S->io_count=actual;
    u32 closed=FN(0x5ee485,Close)(handle);
    if (result || actual!=size || closed) {
        FN(0x6a5469,Free)(buffer); *reason=4; return 0;
    }
    return buffer;
}
static int filename(const char *s,u32 length) {
    if (length<5 || length>12) return 0;
    u32 dot=length-4;
    if (dot<1 || dot>8 || s[dot]!='.' || s[dot+1]!='G' || s[dot+2]!='O' || s[dot+3]!='1') return 0;
    for (u32 i=0;i<dot;i++)
        if (!((s[i]>='A' && s[i]<='Z') || (s[i]>='0' && s[i]<='9') || s[i]=='_')) return 0;
    return 1;
}
/* Application titles and icons (go1.h), read once per refresh with seek;
   the program itself stays on the card. */
#define LIST_ICON_X 6
#define LIST_TEXT_X 28
#define TEXT_LEFT 3   /* alignment argument of the text call 0x4cf433 (8 centres) */
#define SCREEN_BUFFER ((u16 *)0x04280200u)
typedef u32 (*SeekFile)(u32,u32,u32,u32);
typedef u32 (*InvalidateRect)(u32 *,u32,u32);
static void text_wide_at(u32 x,u32 y,const u16 *wide,u32 count,u32 color,u32 align) {
    if (y>156 || x>=176) return;
    u32 dev[2]={0,0};
    FN(0x4cf433,Text)(dev,point(x,y),point(175,y+19),wide,count,0,color&65535,6,align,0);
}
static void text_at(u32 x,u32 y,const char *ascii,u32 color,u32 align) {
    u16 buffer[65]; u32 count=0;
    while (count<64 && ascii[count]) { buffer[count]=(u8)ascii[count]; count++; }
    buffer[count]=0;
    text_wide_at(x,y,buffer,count,color,align);
}
static u32 read_exact(u32 handle,void *buffer,u32 size) {
    u32 actual=0;
    return !FN(0x5ee49f,Read)(handle,buffer,size,&actual,0) && actual==size;
}
static u32 seek_to(u32 handle,u32 at) { return !FN(0x5ee527,SeekFile)(handle,0,at,0); }
static void load_extras(Catalog *catalog,u32 n) {
    catalog->has_icon[n]=0; catalog->titles[n][0]=0;
    u32 handle=open_card(catalog->names[n]);
    if (!handle) return;
    u8 head[32]; u32 size=0;
    if (!FN(0x5ee59f,Size)(handle,&size) && read_exact(handle,head,32) && be32(head)==GO1_MAGIC) {
        u32 at=be32(head+28);
        u8 two[2];
        if (at>=32 && at+2<=size && seek_to(handle,at) && read_exact(handle,two,2)) {
            u32 count=be16(two);
            u16 *title=catalog->titles[n];
            if (count && count<=GO1_TITLE_MAX && at+2+count*2<=size && read_exact(handle,title,count*2))
                title[count]=0;
            else title[0]=0;
        }
        at=be32(head+24);
        u8 dims[4];
        if (at>=32 && at<size && size-at==GO1_ICON_BYTES && seek_to(handle,at) &&
            read_exact(handle,dims,4) && be16(dims)==GO1_ICON_SIZE && be16(dims+2)==GO1_ICON_SIZE &&
            read_exact(handle,catalog->pixels[n],ICON_PIXELS*2) && read_exact(handle,catalog->alpha[n],ICON_PIXELS))
            catalog->has_icon[n]=1;
    }
    FN(0x5ee485,Close)(handle);
}
static u32 blend(u32 back,u32 front,u32 a) {
    u32 r=(((front>>11)&31)*a+((back>>11)&31)*(255-a))/255;
    u32 g=(((front>>5)&63)*a+((back>>5)&63)*(255-a))/255;
    u32 b=((front&31)*a+(back&31)*(255-a))/255;
    return (r<<11)|(g<<5)|b;
}
static void draw_icon(u32 x,u32 y,const u16 *pixels,const u8 *alpha) {
    for (u32 row=0;row<GO1_ICON_SIZE;row++)
        for (u32 col=0;col<GO1_ICON_SIZE;col++) {
            u32 i=row*GO1_ICON_SIZE+col,a=alpha[i];
            if (!a) continue;
            u16 *target=SCREEN_BUFFER+(y+row)*176+x+col;
            *target=(u16)(a==255?pixels[i]:blend(*target,pixels[i],a));
        }
    u32 dev[2]={0,0};
    FN(0x1d081f,InvalidateRect)(dev,point(x,y),point(x+GO1_ICON_SIZE-1,y+GO1_ICON_SIZE-1));
}
static void menu(void) {
    clear_panel(); text(4,"SD APPS",0xffff);
    if (!S->catalog || !CAT->count) {
        text(54,"NO APPLICATIONS",0xffe0);
        text(84,"APPS.LST",0xffff);
        text(120,"F1: REFRESH",0xffff);
        return;
    }
    u32 first=0;
    while (CAT->selected>=first+6) first+=6;
    for (u32 i=0;i<6 && first+i<CAT->count;i++) {
        u32 n=first+i,selected=n==CAT->selected,y=32+i*24;
        if (selected) fill(0,y,176,20,0x2104);
        u32 color=selected?0x07ff:0xffff,length=0;
        while (length<GO1_TITLE_MAX && CAT->titles[n][length]) length++;
        if (length) text_wide_at(LIST_TEXT_X,y,CAT->titles[n],length,color,TEXT_LEFT);
        else text_at(LIST_TEXT_X,y,CAT->names[n],color,TEXT_LEFT);
        if (CAT->has_icon[n]) draw_icon(LIST_ICON_X,y+2,CAT->pixels[n],CAT->alpha[n]);
    }
}
static void refresh(void) {
    release_all(); S->mode=0; S->status=0;
    Catalog *catalog=heap_alloc(sizeof(Catalog),"GO1V catalog");
    if (!catalog) { S->status=3; menu(); return; }
    catalog->count=0; catalog->selected=0; S->catalog=(u32)catalog;
    u32 size=0,reason=0;
    u8 *data=read_file("APPS.LST",1,224,&size,&reason);
    if (!data) { S->status=reason; menu(); return; }
    u32 pos=0;
    while (pos<size) {
        char name[13]; u32 count=0;
        while (pos<size && data[pos]!='\r' && data[pos]!='\n') {
            if (count>=12) { reason=5; break; }
            name[count++]=(char)data[pos++];
        }
        if (reason) break;
        while (pos<size && (data[pos]=='\r' || data[pos]=='\n')) pos++;
        if (!count) continue;
        name[count]=0;
        if (!filename(name,count) || catalog->count>=16) { reason=5; break; }
        for (u32 j=0;j<=count;j++) catalog->names[catalog->count][j]=name[j];
        catalog->count++;
    }
    FN(0x6a5469,Free)(data);
    if (reason) { catalog->count=0; S->status=reason; }
    for (u32 n=0;n<catalog->count;n++) load_extras(catalog,n);
    menu();
}
static void *api_alloc(u32 size) { return heap_alloc(size,"GO1N"); }
static u32 api_heap_free(void) { u32 free=largest_free(); return free>POOL_MARGIN?free-POOL_MARGIN:0; }
static void api_free(void *block) { if (block) FN(0x6a5469,Free)(block); }
static u32 api_open(const char *ascii) {
    u16 path[64]; u32 i=0;
    while (i<63 && ascii[i]) { path[i]=(u8)ascii[i]; i++; }
    path[i]=0;
    return FN(0x5edc5f,Open)(path,0x31,0,0);
}
static u32 api_size(u32 handle) {
    u32 size=0;
    return FN(0x5ee59f,Size)(handle,&size)?0xffffffffu:size;
}
static u32 api_read(u32 handle,void *buffer,u32 size) {
    u32 actual=0;
    return FN(0x5ee49f,Read)(handle,buffer,size,&actual,0)?0xffffffffu:actual;
}
static void api_close(u32 handle) { FN(0x5ee485,Close)(handle); }
static void api_text(u32 y,const char *ascii,u32 color) { text(y,ascii,color); }
static void api_exit(void) { S->status=EXIT_REQUESTED; }
/* One window timer per program: MMK_CreateWinTimer(window, ms, periodic). */
static u32 api_timer(u32 ms,u32 periodic) {
    stop_timer();
    if (!ms) return 0;
    NATIVE_PERIODIC=periodic?1:0;
    NATIVE_TIMER=FN(0x1f55ef,CreateTimer)(NATIVE_WINDOW,ms,NATIVE_PERIODIC)&255;
    return NATIVE_TIMER;
}
/* Main LCD layer buffer (ROM literal at 0x3aa01c) and the GUI call that merges
   a rectangle into the pending LCD update; both are used by the stock fill. */
#define SCREEN ((unsigned short *)0x04280200u)
static unsigned short *api_screen(void) { return SCREEN; }
static void api_flush(u32 x,u32 y,u32 w,u32 h) {
    if (x>=GO1_SCREEN_WIDTH || y>=GO1_SCREEN_HEIGHT || !w || !h) return;
    if (w>GO1_SCREEN_WIDTH-x) w=GO1_SCREEN_WIDTH-x;
    if (h>GO1_SCREEN_HEIGHT-y) h=GO1_SCREEN_HEIGHT-y;
    u32 dev[2]={0,0};
    FN(0x1d081f,Invalidate)(dev,point(x,y),point(x+w-1,y+h-1));
}
/* RAM word incremented by the 1 ms system tick (measured against virtual time). */
#define MS_COUNTER (*(volatile u32 *)0x042ccec0u)
static u32 api_ms(void) { return MS_COUNTER; }
/* SFS_SetFilePointer(handle, 64-bit offset in r1:r2 (ADS order: high, low), origin 0 = begin). */
typedef u32 (*Seek)(u32,u32,u32,u32);
static u32 api_seek(u32 handle,u32 offset) { return FN(0x5ee527,Seek)(handle,0,offset,0); }
static u32 api_open_wide(const u16 *path) { return FN(0x5edc5f,Open)(path,0x31,0,0); }
static void api_text_wide(u32 y,const u16 *wide,u32 color) {
    if (y>156) return;
    u32 count=0,dev[2]={0,0};
    while (count<64 && wide[count]) count++;
    FN(0x4cf433,Text)(dev,point(0,y),point(175,y+19),wide,count,0,color&65535,6,8,0);
}
/* SFS_FindFirstFile(pattern, data) returns the search handle, 0 when nothing
   matches; SFS_FindNextFile(handle, data) returns 0 on a match. The firmware
   find data is 556 bytes: attributes u16 at 0x14, size at 0x18, long name at 0x1c. */
typedef u32 (*FindFirst)(const u16 *,u8 *);
typedef u32 (*FindNext)(u32,u8 *);
typedef u32 (*FindClose)(u32);
static void found(const u8 *data,Go1FindEntry *entry) {
    entry->attributes=*(const u16 *)(data+0x14);
    entry->size=*(const u32 *)(data+0x18);
    const u16 *name=(const u16 *)(data+0x1c);
    u32 i=0;
    for (;i<GO1_NAME_MAX && name[i];i++) entry->name[i]=name[i];
    entry->name[i]=0;
}
static u32 api_find_first(const u16 *pattern,Go1FindEntry *entry) {
    u32 data[556/4];
    u32 search=FN(0x5ee5c1,FindFirst)(pattern,(u8 *)data);
    if (search) found((u8 *)data,entry);
    return search;
}
static u32 api_find_next(u32 search,Go1FindEntry *entry) {
    u32 data[556/4];
    u32 error=FN(0x5ee8bb,FindNext)(search,(u8 *)data);
    if (!error) found((u8 *)data,entry);
    return error;
}
static void api_find_close(u32 search) { FN(0x5ee94b,FindClose)(search); }
static const Go1Api api={GO1_API_VERSION,sizeof(Go1Api),api_alloc,api_free,api_open,api_size,api_read,
                         api_close,fill,api_text,api_exit,api_heap_free,api_screen,api_flush,
                         api_timer,api_ms,api_seek,api_open_wide,api_text_wide,
                         api_find_first,api_find_next,api_find_close};
/* Relocate a GO1N file in place: the image starts after the 32-byte header,
   .bss follows the image and may overwrite the already applied table.
   Takes ownership of the file buffer and frees it on failure. */
static u8 *reject(u8 *file) { FN(0x6a5469,Free)(file); return 0; }
static u8 *prepare_native(u8 *file,u32 size) {
    if (size<32 || be16(file+4)!=GO1_VERSION || be16(file+6)!=32) return reject(file);
    u32 length=be32(file+8),bss=be32(file+12),entry=be32(file+16),count=be32(file+20);
    u32 icon=be32(file+24),title=be32(file+28);
    if (!length || length>GO1_MAX_FILE || bss>GO1_MAX_FILE || !(entry&1) || (entry&~1u)>=length ||
        count>(GO1_MAX_FILE>>2)) return reject(file);
    /* After the relocations: optional title, then optional icon, then the end. */
    u32 end=32+length+count*4;
    if (title) {
        if (title!=end || end+2>size) return reject(file);
        u32 chars=be16(file+end);
        if (!chars || chars>GO1_TITLE_MAX) return reject(file);
        end+=2+chars*2;
    }
    if (icon) {
        if (icon!=end) return reject(file);
        end+=GO1_ICON_BYTES;
    }
    if (size!=end) return reject(file);
    u32 need=32+length+bss;
    if (need>size) {
        u8 *bigger=heap_alloc(need,"GO1N");
        if (!bigger) return reject(file);
        for (u32 i=0;i<size;i++) bigger[i]=file[i];
        FN(0x6a5469,Free)(file); file=bigger;
    }
    u8 *image=file+32; const u8 *table=image+length;
    for (u32 i=0;i<count;i++) {
        u32 at=be32(table+i*4);
        if (at&3 || at+4>length) return reject(file);
    }
    for (u32 i=0;i<count;i++) {
        u32 *word=(u32 *)(image+be32(table+i*4));
        *word+=(u32)image;
    }
    for (u32 i=0;i<bss;i++) image[length+i]=0;
    S->pc=(u32)image+entry;
    return file;
}
static int load(void) {
    release(); S->mode=1; S->loads++; S->pc=0; S->steps=0; S->status=0;
    S->checksum=0; S->size=0; S->io_count=0;
    for (u32 i=0;i<8;i++) S->r[i]=0;
    clear_panel();
    u32 size=0,reason=0;
    u8 *buffer=read_file(CAT->names[CAT->selected],16,GO1_MAX_FILE,&size,&reason);
    S->size=size;
    if (!buffer) { error(reason); return 0; }
    u32 sum=2166136261u;
    for (u32 i=0;i<size;i++) sum=(sum^buffer[i])*16777619u;
    if (be32(buffer)==GO1_MAGIC) {
        u8 *ready=prepare_native(buffer,size);
        if (!ready) { S->pc=0; error(5); return 0; }
        buffer=ready; S->mode=2;
    } else if (!validate(buffer,size)) {
        FN(0x6a5469,Free)(buffer); error(5); return 0;
    }
    S->code=(u32)buffer;
    S->checksum=sum; S->status=0;
    return 1;
}
static void run(u32 win,u32 event,u32 param) {
    if (!S->code || S->status) return;
    if (S->mode==2) {
        S->event=event; S->steps++; NATIVE_WINDOW=win;
        FN(S->pc,Go1Entry)(&api,event,param);
        if (S->status==EXIT_REQUESTED) { release(); S->mode=0; S->status=0; menu(); repaint_all(); }
        return;
    }
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
        release_all(); original(win,msg,param);
        volatile u32 *state=(volatile u32 *)S;
        for (u32 i=0;i<sizeof(State)/4;i++) state[i]=0;
        S->magic=MAGIC;
        static const u16 empty[]={0};
        struct {const u16 *s;u16 length,padding;} label={empty,0,0};
        FN(0x4c5b69,Label)(0x16000c,&label,0);
        refresh();
        return 1;
    }
    if (msg==0xf022) { release_all(); return original(win,msg,param); }
    if (S->magic==MAGIC && S->mode==0) {
        if (msg==0xfa16) { refresh(); return 1; }
        if (S->catalog && CAT->count) {
            if (msg==0xfa01) { if (CAT->selected) CAT->selected--; menu(); return 1; }
            if (msg==0xfa02) { if (CAT->selected+1<CAT->count) CAT->selected++; menu(); return 1; }
            if (msg==0xfa1b) { if (load()) run(win,0xf021,0); return 1; }
        }
        if (msg==0xf024 || msg==0xf025 || msg==0x40000014) { menu(); return 1; }
        if ((msg>=0xfa00 && msg<0xfa17) || msg==0xfa1b || msg==0xf001) return 1;
        return original(win,msg,param);
    }
    if (S->mode==2 && !S->status) {
        /* Native programs get every key, soft keys included; a long press
           of the right soft key is the loader's way back to the list. */
        if (msg==0xfc17) { release(); S->mode=0; menu(); repaint_all(); return 1; }
        if (msg>=0xfa00 && msg<0xff00) { run(win,msg,0); return 1; }
    }
    if (msg==0xfa17) { u32 native=S->mode==2; release(); S->mode=0; S->status=0; menu(); if (native) repaint_all(); return 1; }
    if (msg==0xfa16) { if (load()) run(win,0xf021,0); return 1; }
    if (msg==0xf024 || msg==0xf025 || msg==0x40000014 || (msg>=0xfa00 && msg<0xfa17) || msg==0xfa1b) {
        if (S->status && S->status<9) error(S->status);
        else run(win,msg,0);
        return 1;
    }
    if (msg==MSG_TIMER && S->mode==2 && NATIVE_TIMER && param && *(const u8 *)param==NATIVE_TIMER) {
        u32 id=NATIVE_TIMER;
        if (!NATIVE_PERIODIC) NATIVE_TIMER=0;  /* the firmware already deleted a one-shot timer */
        run(win,msg,id);
        return 1;
    }
    if (msg==0xf001) return 1;
    return original(win,msg,param);
}
