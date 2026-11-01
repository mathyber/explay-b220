# Reference source

`unpac.c` was downloaded for inspecting the PAC structure. It is not compiled or run.

- Source: https://github.com/ilyakurdyukov/spreadtrum_flash/blob/main/unpac/unpac.c
- Raw URL: https://raw.githubusercontent.com/ilyakurdyukov/spreadtrum_flash/main/unpac/unpac.c
- Upstream license: https://github.com/ilyakurdyukov/spreadtrum_flash/blob/main/LICENSE
- Retrieved for this investigation on 2026-10-31; the URL tracks a branch rather than a pinned commit. The local file is the inspected snapshot.

The Python parser in `tools/analyze_firmware.py` follows the observed field layout. Unlike the current upstream unpacker, it explicitly supports the old `BP_R1.0.0` zero-magic/zero-CRC header in the supplied archive and reports those checksums as absent.
