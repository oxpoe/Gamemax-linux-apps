#!/usr/bin/env python3
"""Find which IL methods reference a string stored in the native data section.

Resolves: string bytes -> RVA -> FieldRva row -> Field token -> IL instructions.
Usage: string_refs.py <assembly> <needle> [--context N]
"""
import re
import struct
import sys

import dnfile

CALLISH = {"call", "callvirt", "ldftn", "newobj", "ldsflda", "ldsfld", "ldstr", "ldtoken"}


def rva_of_offset(pe, off):
    for s in pe.sections:
        start = s.PointerToRawData
        if start <= off < start + s.SizeOfRawData:
            return s.VirtualAddress + (off - start)
    return None


def main():
    path, needle = sys.argv[1], sys.argv[2]
    data = open(path, "rb").read()
    pe = dnfile.dnPE(path)
    hits = []
    for enc in ("ascii", "utf-16-le"):
        b = needle.encode(enc)
        hits += [m.start() for m in re.finditer(re.escape(b), data)]
    rvas = {rva_of_offset(pe, h) for h in hits} - {None}
    print(f"needle {needle!r}: {len(hits)} occurrences, rvas: {[hex(r) for r in rvas]}")

    fields = {}  # field token -> FieldRva rva
    for i, row in enumerate(pe.net.mdtables.FieldRva.rows):
        fields[0x04000000 | (i + 1)] = row.Rva
    matching = {t: r for t, r in fields.items() if r in rvas}
    print("matching field tokens:", {hex(t): hex(r) for t, r in matching.items()})

    names = {}
    for i, row in enumerate(pe.net.mdtables.Field.rows):
        names[0x04000000 | (i + 1)] = str(row.Name)
    # any string whose RVA sits inside the string's own blob (same field)
    related = set(matching)
    for t, r in fields.items():
        if any(r <= rv < r + len(needle) + 64 for rv in rvas):
            related.add(t)
    print("related fields:", {hex(t): names[t] for t in sorted(related)})

    from dncil.cil.body.reader import read_method_body_from_bytes
    maps = {}
    for i, row in enumerate(pe.net.mdtables.MethodDef.rows):
        maps[0x06000000 | (i + 1)] = str(row.Name)
    for i, row in enumerate(pe.net.mdtables.MemberRef.rows):
        maps[0x0A000000 | (i + 1)] = str(row.Name)

    for i, row in enumerate(pe.net.mdtables.MethodDef.rows):
        try:
            raw = pe.get_data(row.Rva, 12 + 65536)
            body = read_method_body_from_bytes(raw)
        except Exception:
            continue
        insns = body.instructions
        for j, ins in enumerate(insns):
            op = getattr(ins.opcode, "name", str(ins.opcode)).lower()
            if op not in CALLISH:
                continue
            val = getattr(ins.operand, "value", ins.operand)
            if val in related:
                print(f"\n{str(row.Name)} @ +{ins.offset}: {op} {names.get(val, val)}")
                for k in insns[max(0, j - 6):j + 10]:
                    kop = getattr(k.opcode, "name", "?").lower()
                    kval = getattr(k.operand, "value", k.operand)
                    txt = maps.get(kval, kval) if isinstance(kval, int) and kval > 0x02000000 else (kval if kval is not None else "")
                    mark = " <<<" if k is ins else ""
                    print(f"    +{k.offset:6} {kop:14} {txt}{mark}")


if __name__ == "__main__":
    main()
