#!/usr/bin/env python3
# this one is tested. fail.
import sys
from sit import send

n = sys.argv[1].encode()
send(0x0001, bytes([1, 0, len(n)]) + n.ljust(0x51, b'\0') +
     bytes([0, 0x10 if n.startswith(b'+') else 0x20, 1, 0xff, 0, 0, 0, 0]), wait=30)
