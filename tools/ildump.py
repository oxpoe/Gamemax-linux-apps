#!/usr/bin/env python3
"""Dump MSIL of methods in a .NET/mixed-mode assembly with resolved operands.

Usage:
  ildump.py <assembly> list <regex>
  ildump.py <assembly> dump <regex>
  ildump.py <assembly> callers <regex>
"""
import re
import sys

import dnfile
from dncil.cil.body.reader import read_method_body_from_bytes

TBL = {0x01: "TypeRef", 0x02: "TypeDef", 0x04: "Field", 0x06: "MethodDef",
       0x0A: "MemberRef", 0x1B: "TypeSpec", 0x2B: "MethodSpec", 0x70: "String"}
CALLISH = {"call", "callvirt", "ldftn", "newobj", "ldvirtftn", "jmp"}


def type_name(pe, row):
    if row is None:
        return "?"
    for attr in ("TypeName", "Name"):
        if hasattr(row, attr) and getattr(row, attr):
            ns = getattr(row, "TypeNamespace", "") or ""
            return str(f"{ns}.{getattr(row, attr)}").lstrip(".")
    return "?"


def build_maps(pe):
    md = pe.net.mdtables
    names = {}
    for i, row in enumerate(md.MethodDef.rows):
        names[0x06000000 | (i + 1)] = str(row.Name)
    for i, row in enumerate(md.MemberRef.rows):
        parent = "?"
        try:
            parent = type_name(pe, row.Class.row) if row.Class else "?"
        except Exception:
            pass
        names[0x0A000000 | (i + 1)] = f"{parent}::{str(row.Name)}"
    for i, row in enumerate(md.Field.rows):
        names[0x04000000 | (i + 1)] = str(row.Name)
    for tbl, base in (("TypeDef", 0x02000000), ("TypeRef", 0x01000000)):
        for i, row in enumerate(getattr(md, tbl).rows):
            names[base | (i + 1)] = type_name(pe, row)
    return names


def decode(pe, row, maps):
    raw = pe.get_data(row.Rva, 12 + 65536)
    body = read_method_body_from_bytes(raw)
    out = []
    for ins in body.instructions:
        op = getattr(ins.opcode, "name", str(ins.opcode)).lower()
        operand = ins.operand
        val = getattr(operand, "value", operand)
        text = ""
        if isinstance(val, int) and val > 0x02000000:
            text = maps.get(val, f"0x{val:08x}")
            if val >> 24 == 0x70:
                try:
                    text = repr(pe.net.user_strings.get(val & 0xFFFFFF))
                except Exception:
                    text = "<bad string>"
        elif val is not None:
            text = str(val)
        out.append((ins.offset, op, text))
    return out


def main():
    path, cmd, pattern = sys.argv[1], sys.argv[2], sys.argv[3]
    pe = dnfile.dnPE(path)
    maps = build_maps(pe)
    rx = re.compile(pattern)
    md = pe.net.mdtables
    rows = [(0x06000000 | (i + 1), row) for i, row in enumerate(md.MethodDef.rows)]
    targets = [(t, r) for t, r in rows if rx.search(str(r.Name))]
    if cmd == "list":
        for t, r in targets:
            print(f"{t:08x} {str(r.Name)}")
        return
    if cmd == "callers":
        for t, r in rows:
            try:
                for off, op, text in decode(pe, r, maps):
                    if op in CALLISH and rx.search(text):
                        print(f"{str(r.Name)} @il+{off}: {op} {text}")
            except Exception:
                continue
        return
    for t, r in targets:
        print(f"=== {t:08x} {str(r.Name)}")
        try:
            for off, op, text in decode(pe, r, maps):
                print(f"  +{off:4} {op:16} {text}")
        except Exception as e:
            print(f"  <decode failed: {e}>")


if __name__ == "__main__":
    main()
