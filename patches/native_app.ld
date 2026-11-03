ENTRY(b22n_main)
SECTIONS {
    . = 0;
    .image : {
        KEEP(*(.entry))
        *(.text*)
        *(.rodata*)
        *(.data*)
        . = ALIGN(4);
    }
    .bss : { *(.bss*) *(COMMON) . = ALIGN(4); }
    /DISCARD/ : { *(.ARM.exidx*) *(.ARM.extab*) *(.ARM.attributes*) *(.comment*) *(.note*) }
}
