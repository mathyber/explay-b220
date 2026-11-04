/* GO1N native SD applications: interface between the SD Apps loader and a program.

Files are E:\NAME.GO1 ("Go-1").
File (big-endian): 32-byte header, image, relocation table, optional title,
optional icon, in this order and nothing after them.
  0  'GO1N'            16 u32 entry offset in image, Thumb bit set
  4  u16 version 1     20 u32 relocation count
  6  u16 header 32     24 u32 icon offset in the file, 0 = no icon
  8  u32 image bytes   28 u32 title offset in the file, 0 = no title
  12 u32 bss bytes
Title: u16 character count 1..GO1_TITLE_MAX, then UTF-16BE characters. The
application list shows it instead of the file name.
Icon: u16 width 16, u16 height 16, 256 RGB565 pixels, then 256 alpha bytes
(0 transparent, 255 opaque), rows top to bottom; shown left of the name.
Each relocation is a u32 image offset of an aligned word that holds an
image-relative address; the loader adds the load address. The program is
linked at 0 and runs from RAM in the firmware MMI task.

The loader calls the entry for GO1_OPEN after loading and then for each key,
timer and repaint message of the window. A long press of the right soft key
returns to the list; api->exit() does the same from the program. */
#ifndef GO1_H
#define GO1_H

typedef unsigned int go1_u32;

#define GO1_MAGIC 0x474f314eu
#define GO1_VERSION 1
#define GO1_API_VERSION 1
#define GO1_MAX_FILE 0x100000u
#define GO1_ICON_SIZE 16
#define GO1_ICON_BYTES (4+GO1_ICON_SIZE*GO1_ICON_SIZE*3)
#define GO1_TITLE_MAX 24

/* Window messages passed to the program. */
#define GO1_OPEN 0xf021u
#define GO1_PAINT 0xf024u
#define GO1_TIMER 0xf020u  /* parameter: timer id */
#define GO1_CLOSE 0xf022u  /* last call before unloading: free your allocations */

/* Key messages are kind | code. A short press gives PRESS then RELEASE; holding
   gives PRESS, REPEAT..., LONG, REPEAT... and LONG_RELEASE. All keys, including
   the soft keys, go to a native program; a long press of the right soft key
   always returns to the application list. */
#define GO1_PRESS 0xfa00u
#define GO1_RELEASE 0xfb00u
#define GO1_LONG 0xfc00u
#define GO1_LONG_RELEASE 0xfd00u
#define GO1_REPEAT 0xfe00u
#define GO1_KIND(message) ((message) & 0xff00u)
#define GO1_CODE(message) ((message) & 0xffu)
#define GO1_IS_KEY(message) ((message) >= 0xfa00u && (message) < 0xff00u)
enum {
    GO1_UP = 0x01, GO1_DOWN = 0x02, GO1_LEFT = 0x03, GO1_RIGHT = 0x04,
    GO1_1 = 0x08, GO1_2, GO1_3, GO1_4, GO1_5, GO1_6, GO1_7, GO1_8, GO1_9,
    GO1_STAR = 0x11, GO1_0 = 0x12, GO1_HASH = 0x13,
    GO1_SOFT_LEFT = 0x16, GO1_SOFT_RIGHT = 0x17, GO1_OK = 0x1b
};
/* Presses of common keys. */
#define GO1_KEY_UP (GO1_PRESS | GO1_UP)
#define GO1_KEY_DOWN (GO1_PRESS | GO1_DOWN)
#define GO1_KEY_LEFT (GO1_PRESS | GO1_LEFT)
#define GO1_KEY_RIGHT (GO1_PRESS | GO1_RIGHT)
#define GO1_KEY_OK (GO1_PRESS | GO1_OK)

#define GO1_ATTR_DIRECTORY 0x10
#define GO1_NAME_MAX 255
typedef struct Go1FindEntry {
    go1_u32 size, attributes;
    unsigned short name[GO1_NAME_MAX+1];  /* long name, without the directory */
} Go1FindEntry;

typedef struct Go1Api {
    go1_u32 version, size;
    /* Firmware Dynamic App Heap; 0 when no free block fits. The loader checks
       the pool first: the firmware allocator itself reboots on failure. */
    void *(*alloc)(go1_u32 size);
    void (*free)(void *block);
    /* ASCII path such as "E:\\DATA.BIN"; handle or 0. */
    go1_u32 (*open)(const char *path);
    /* Size in bytes, 0xffffffff on error. */
    go1_u32 (*file_size)(go1_u32 handle);
    /* Bytes read, 0xffffffff on error. */
    go1_u32 (*read)(go1_u32 handle, void *buffer, go1_u32 size);
    void (*close)(go1_u32 handle);
    /* 176x176 client area, RGB565; rectangles are clipped. */
    void (*fill)(go1_u32 x, go1_u32 y, go1_u32 width, go1_u32 height, go1_u32 color);
    /* Centered printable ASCII, up to 64 characters, Y = 0..156. */
    void (*text)(go1_u32 y, const char *ascii, go1_u32 color);
    /* Return to the application list after the current call returns. */
    void (*exit)(void);
    /* Largest size alloc() can currently return. */
    go1_u32 (*heap_free)(void);
    /* Whole-screen frame buffer, 176x220 RGB565, row stride 176 pixels.
       Draw into it, then flush the changed rectangle; the firmware sends
       flushed areas to the LCD after the current message returns. */
    unsigned short *(*screen)(void);
    void (*flush)(go1_u32 x, go1_u32 y, go1_u32 width, go1_u32 height);
    /* One window timer: start or restart it for ms milliseconds, once or
       periodically; 0 stops it. Returns the firmware timer id or 0.
       Expiry arrives as GO1_TIMER. The loader stops it when the program ends. */
    go1_u32 (*timer)(go1_u32 ms, go1_u32 periodic);
    /* Milliseconds since power-on (firmware 1 ms tick counter). */
    go1_u32 (*ms)(void);
    /* Set the read position from the start of the file; 0 on success. */
    go1_u32 (*seek)(go1_u32 handle, go1_u32 offset);
    /* Fields below exist when api->size covers them (loaders from 2026-11). */
    /* UCS-2 path, for long and non-ASCII names; handle or 0. */
    go1_u32 (*open_wide)(const unsigned short *path);
    /* Like text(), UCS-2, up to 64 characters. */
    void (*text_wide)(go1_u32 y, const unsigned short *text, go1_u32 color);
    /* Directory search with a UCS-2 pattern such as L"E:\\JAVA\\*.jar" (case
       does not matter). find_first fills the first match and returns a search
       handle, 0 when nothing matches; find_next returns 0 while it finds more.
       Close every handle find_first returned. */
    go1_u32 (*find_first)(const unsigned short *pattern, Go1FindEntry *entry);
    go1_u32 (*find_next)(go1_u32 search, Go1FindEntry *entry);
    void (*find_close)(go1_u32 search);
} Go1Api;


#define GO1_SCREEN_WIDTH 176
#define GO1_SCREEN_HEIGHT 220

typedef go1_u32 (*Go1Entry)(const Go1Api *api, go1_u32 message, go1_u32 parameter);

#endif
