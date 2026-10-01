#!/usr/bin/env python3
# Decode modemlisten.py logs. Usage: decode.py ~/modemlog/*.log
# umts_* lines: SIT frames  {02 00 | u16 cmd | u32 len | data}
# oem_*  lines: GEMS protobuf, printed as field tree
# Digit runs of 9+ (ICCID, IMSI) are printed as <num>.
import os, re, sys


def mask(raw):
    raw = re.sub(rb'[0-9]{9,}', b'<num>', raw)
    return ''.join(chr(c) if 32 <= c < 127 else '.' for c in raw)


def varint(b, i):
    v = s = 0
    while i < len(b):
        c = b[i]; i += 1
        v |= (c & 0x7f) << s; s += 7
        if not c & 0x80:
            return v, i
    raise ValueError


def proto(b, depth=0):
    out, i = [], 0
    while i < len(b):
        key, i = varint(b, i)
        f, wt = key >> 3, key & 7
        pad = '  ' * depth
        if wt == 0:
            v, i = varint(b, i); out.append(f'{pad}f{f}={v}')
        elif wt == 5:
            v = int.from_bytes(b[i:i + 4], 'little'); i += 4; out.append(f'{pad}f{f}=0x{v:x}')
        elif wt == 1:
            v = int.from_bytes(b[i:i + 8], 'little'); i += 8; out.append(f'{pad}f{f}=0x{v:x}')
        elif wt == 2:
            n, i = varint(b, i); sub = b[i:i + n]; i += n
            if sub and all(32 <= c < 127 for c in sub):
                out.append(f'{pad}f{f}="{mask(sub)}"')
                continue
            try:
                inner = proto(sub, depth + 1)
                out.append(f'{pad}f{f}{{'); out += inner; out.append(f'{pad}}}')
            except Exception:
                out.append(f'{pad}f{f}="{mask(sub)}"')
        else:
            raise ValueError
    return out


for path in sys.argv[1:]:
    name = os.path.basename(path)
    print(f'== {name}')
    for line in open(path):
        p = line.split()
        if len(p) < 3:
            continue
        b = bytes.fromhex(''.join(p[2:]))
        if name.startswith('umts_') and len(b) >= 8 and b[0] == 2:
            cmd = int.from_bytes(b[2:4], 'little')
            ln = int.from_bytes(b[4:8], 'little')
            print(p[0], f'cmd=0x{cmd:04x} len={ln}', mask(b[8:]).strip('.')[:80])
        elif name.startswith('oem_'):
            try:
                print(p[0], ' '.join(proto(b)))
            except Exception:
                print(p[0], 'raw', mask(b)[:80])
        else:
            print(p[0], 'raw', mask(b)[:80])
