#!/usr/bin/env python3
# alpine based: apk add zbar
import subprocess, sys

s = sys.argv[1]
if not s.startswith('LPA:'):
    s = subprocess.run(['zbarimg', '-q', '--raw', s], capture_output=True, text=True).stdout.strip()
f = s.split('$')
print('activation code:', s)
print('smdp:', f[1])
print('matching id:', f[2] if len(f) > 2 else '')
