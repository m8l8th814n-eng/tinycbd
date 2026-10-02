#!/usr/bin/env python3
import sys
from sit import send

pin = sys.argv[1].encode()[:8]
send(0x0201, bytes([len(pin)]) + pin.ljust(8, b'\0') + bytes(17))
