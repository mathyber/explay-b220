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

The loader calls the entry for B22N_OPEN after loading and then for each key
and repaint message of the window. Left soft key reloads, right soft key
returns to the list; both are handled by the loader. */
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
#define B22N_KEY_UP 0xfa01u
#define B22N_KEY_DOWN 0xfa02u
#define B22N_KEY_LEFT 0xfa03u
#define B22N_KEY_RIGHT 0xfa04u
#define B22N_KEY_OK 0xfa1bu

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
} B22Api;

typedef b22_u32 (*B22Entry)(const B22Api *api, b22_u32 message, b22_u32 parameter);

#endif
