ENTRY(game_handler)
SECTIONS {
    . = 0x0014d000;
    .payload : {
        KEEP(*(.launch))
        KEEP(*(.entry))
        *(.text*)
        *(.rodata*)
        *(.data*)
    }
    .bss : { *(.bss*) *(COMMON) }
    ASSERT(SIZEOF(.bss) == 0, "Mutable state must use the original game board")
    /DISCARD/ : { *(.ARM.exidx*) *(.ARM.extab*) *(.comment*) *(.note*) }
}
