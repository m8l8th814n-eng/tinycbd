#!/usr/bin/env python3
import sys
from sit import send


def bcd(num):
    d = num.lstrip('+')
    if len(d) % 2:
        d += 'F'
    return bytes(int(d[i + 1] + d[i], 16) for i in range(0, len(d), 2))


num, text = sys.argv[1], sys.argv[2]
d = num.lstrip('+')
ud = text.encode('utf-16-be')[:140]
tpdu = bytes([0x01, 0x00, len(d), 0x91 if num.startswith('+') else 0x81]) + bcd(num) + \
    bytes([0x00, 0x08, len(ud)]) + ud
p = bytes([0, 1, 0]) + bytes(11) + bytes([len(tpdu)]) + tpdu
send(0x0100, p.ljust(0x10f - 12, b'\0'), wait=60)
