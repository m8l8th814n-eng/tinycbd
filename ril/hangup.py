#!/usr/bin/env python3
import struct, sys
from sit import send

send(0x0008, struct.pack('<II', int(sys.argv[1]) if len(sys.argv) > 1 else 1, 1))
