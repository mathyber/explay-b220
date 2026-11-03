/* B22N native SD applications: interface between the SD Apps loader and a program.

File (big-endian): 32-byte header, image, relocation table.
  0  'B22N'            16 u32 entry offset in image, Thumb bit set
  4  u16 version 1     20 u32 relocation count
  6  u16 header 32     24 u32 0
  8  u32 image bytes   28 u32 0
  12 u32 bss bytes
Each relocation is a u32 image offset of an aligned word that holds an
image-relative address; the loader adds the load address. The program is
linked at 0 and runs from RAM in the firmware MMI task.

The loader calls the entry for B22N_OPEN after loading and then for each key,
timer and repaint message of the window. A long press of the right soft key
returns to the list; api->exit() does the same from the program. */
#ifndef B22N_H
#define B22N_H

typedef unsigned int b22_u32;

#define B22N_MAGIC 0x4232324eu
#define B22N_VERSION 1
#define B22N_API_VERSION 1
#define B22N_MAX_FILE 0x100000u

/* Window messages passed to the program. */
#define B22N_OPEN 0xf021u
#define B22N_PAINT 0xf024u
#define B22N_TIMER 0xf020u  /* parameter: timer id */

/* Key messages are kind | code. A short press gives PRESS then RELEASE; holding
   gives PRESS, REPEAT..., LONG, REPEAT... and LONG_RELEASE. All keys, including
   the soft keys, go to a native program; a long press of the right soft key
   always returns to the application list. */
#define B22N_PRESS 0xfa00u
#define B22N_RELEASE 0xfb00u
#define B22N_LONG 0xfc00u
#define B22N_LONG_RELEASE 0xfd00u
#define B22N_REPEAT 0xfe00u
#define B22N_KIND(message) ((message) & 0xff00u)
#define B22N_CODE(message) ((message) & 0xffu)
#define B22N_IS_KEY(message) ((message) >= 0xfa00u && (message) < 0xff00u)
enum {
    B22N_UP = 0x01, B22N_DOWN = 0x02, B22N_LEFT = 0x03, B22N_RIGHT = 0x04,
    B22N_1 = 0x08, B22N_2, B22N_3, B22N_4, B22N_5, B22N_6, B22N_7, B22N_8, B22N_9,
    B22N_STAR = 0x11, B22N_0 = 0x12, B22N_HASH = 0x13,
    B22N_SOFT_LEFT = 0x16, B22N_SOFT_RIGHT = 0x17, B22N_OK = 0x1b
};
/* Presses of common keys. */
#define B22N_KEY_UP (B22N_PRESS | B22N_UP)
#define B22N_KEY_DOWN (B22N_PRESS | B22N_DOWN)
#define B22N_KEY_LEFT (B22N_PRESS | B22N_LEFT)
#define B22N_KEY_RIGHT (B22N_PRESS | B22N_RIGHT)
#define B22N_KEY_OK (B22N_PRESS | B22N_OK)

typedef struct B22Api {
    b22_u32 version, size;
    /* Firmware Dynamic App Heap; 0 when no free block fits. The loader checks
       the pool first: the firmware allocator itself reboots on failure. */
    void *(*alloc)(b22_u32 size);
    void (*free)(void *block);
    /* ASCII path such as "E:\\DATA.BIN"; handle or 0. */
    b22_u32 (*open)(const char *path);
    /* Size in bytes, 0xffffffff on error. */
    b22_u32 (*file_size)(b22_u32 handle);
    /* Bytes read, 0xffffffff on error. */
    b22_u32 (*read)(b22_u32 handle, void *buffer, b22_u32 size);
    void (*close)(b22_u32 handle);
    /* 176x176 client area, RGB565; rectangles are clipped. */
    void (*fill)(b22_u32 x, b22_u32 y, b22_u32 width, b22_u32 height, b22_u32 color);
    /* Centered printable ASCII, up to 64 characters, Y = 0..156. */
    void (*text)(b22_u32 y, const char *ascii, b22_u32 color);
    /* Return to the application list after the current call returns. */
    void (*exit)(void);
    /* Largest size alloc() can currently return. */
    b22_u32 (*heap_free)(void);
    /* Whole-screen frame buffer, 176x220 RGB565, row stride 176 pixels.
       Draw into it, then flush the changed rectangle; the firmware sends
       flushed areas to the LCD after the current message returns. */
    unsigned short *(*screen)(void);
    void (*flush)(b22_u32 x, b22_u32 y, b22_u32 width, b22_u32 height);
    /* One window timer: start or restart it for ms milliseconds, once or
       periodically; 0 stops it. Returns the firmware timer id or 0.
       Expiry arrives as B22N_TIMER. The loader stops it when the program ends. */
    b22_u32 (*timer)(b22_u32 ms, b22_u32 periodic);
    /* Milliseconds since power-on (firmware 1 ms tick counter). */
    b22_u32 (*ms)(void);
    /* Set the read position from the start of the file; 0 on success. */
    b22_u32 (*seek)(b22_u32 handle, b22_u32 offset);
} B22Api;

#define B22N_SCREEN_WIDTH 176
#define B22N_SCREEN_HEIGHT 220

typedef b22_u32 (*B22Entry)(const B22Api *api, b22_u32 message, b22_u32 parameter);

#endif
