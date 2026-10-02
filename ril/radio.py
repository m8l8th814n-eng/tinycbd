#!/usr/bin/env python3
import struct, sys
from sit import send

send(0x0800, struct.pack('<IBB', 2 if sys.argv[1] == 'on' else 1, 0, 0))
