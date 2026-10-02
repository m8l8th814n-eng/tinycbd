#!/usr/bin/env python3
from sit import send

STATE = {0: 'not registered', 1: 'home', 2: 'searching', 3: 'denied', 4: 'unknown',
         5: 'roaming', 10: 'emergency only', 12: 'emergency only, searching',
         13: 'emergency only, denied', 14: 'emergency only, unknown'}
for name, mid in (('cs', 0x0700), ('ps', 0x0701)):
    err, d = send(mid)
    print(name, STATE.get(d[0], d[0]), 'reject', d[1])
