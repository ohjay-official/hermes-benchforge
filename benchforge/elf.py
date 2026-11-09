"""Read symbol addresses from an AVR ELF file, with no tools beyond the standard library.

BenchForge needs two: where ``main`` starts (to know when the C runtime has finished clearing memory)
and where the Arduino core keeps ``timer0_millis`` (to wind the clock forward for the rollover test).
"""

from __future__ import annotations

import struct


class ElfError(ValueError):
    pass


def symbols(data: bytes) -> dict:
    """name -> (value, size) for every named symbol in a 32-bit little-endian ELF file."""
    if len(data) < 52 or data[:4] != b"\x7fELF":
        raise ElfError("not an ELF file")
    if data[4] != 1 or data[5] != 1:
        raise ElfError("expected a 32-bit little-endian ELF file (AVR)")
    (e_shoff,) = struct.unpack_from("<I", data, 32)
    e_shentsize, e_shnum = struct.unpack_from("<HH", data, 46)
    if e_shentsize != 40 or e_shoff + e_shnum * 40 > len(data):
        raise ElfError("damaged ELF section table")
    sections = [struct.unpack_from("<IIIIIIIIII", data, e_shoff + i * 40) for i in range(e_shnum)]
    out: dict = {}
    for sh in sections:
        sh_type, sh_offset, sh_size, sh_link, sh_entsize = sh[1], sh[4], sh[5], sh[6], sh[9]
        if sh_type != 2 or sh_entsize != 16:          # SHT_SYMTAB
            continue
        if sh_link >= len(sections) or sh_offset + sh_size > len(data):
            raise ElfError("damaged ELF symbol table")
        strtab = sections[sh_link]
        s_off, s_size = strtab[4], strtab[5]
        if s_off + s_size > len(data):
            raise ElfError("damaged ELF string table")
        names = data[s_off:s_off + s_size]
        for i in range(sh_size // 16):
            st_name, st_value, st_size, st_info, _other, _shndx = struct.unpack_from(
                "<IIIBBH", data, sh_offset + i * 16)
            if not st_name or st_name >= len(names):
                continue
            end = names.find(b"\0", st_name)
            name = names[st_name:end if end >= 0 else None].decode("ascii", "replace")
            if name and name not in out:
                out[name] = (st_value, st_size)
    return out


def find(syms: dict, name: str):
    """A symbol by name, allowing the suffixes link-time optimisation adds (``timer0_millis.lto_priv.0``)."""
    if name in syms:
        return syms[name]
    for k, v in syms.items():
        if k.startswith(name + "."):
            return v
    return None


def probes(elf_bytes: bytes) -> dict:
    """The addresses the crash tests need. Missing ones are left out (that test is then skipped)."""
    syms = symbols(elf_bytes)
    out = {}
    main = find(syms, "main")
    if main:
        out["main_pc"] = main[0] // 2                 # flash byte address -> word address (the CPU's PC)
    for key in ("timer0_millis", "timer0_overflow_count"):
        hit = find(syms, key)
        if hit and hit[1] == 4:
            out[key] = hit[0]                         # data addresses carry the 0x800000 SRAM offset
    return out
