#!/usr/bin/env python3
import json, os, struct, subprocess, sys
from sit import send

LPAC = '/usr/local/bin/lpac' if os.path.exists('/usr/local/bin/lpac') else 'lpac'
p = subprocess.Popen([LPAC] + sys.argv[1:], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                     text=True, env=dict(os.environ, LPAC_APDU='stdio', LPAC_HTTP='curl'))
ch = 0
dbg = os.environ.get('DEBUG') == '1'


def reply(ecode, data=''):
    if dbg:
        print('  <<', ecode, data, file=sys.stderr, flush=True)
    p.stdin.write(json.dumps({'type': 'apdu', 'payload': {'ecode': ecode, 'data': data}}) + '\n')
    p.stdin.flush()


for line in p.stdout:
    m = json.loads(line)
    if m.get('type') != 'apdu':
        print(line, end='', flush=True)
        continue
    f, a = m['payload']['func'], m['payload'].get('param')
    if dbg:
        print('>>', f, a, file=sys.stderr, flush=True)
    if f in ('connect', 'disconnect'):
        reply(0)
    elif f == 'logic_channel_open':
        aid = bytes.fromhex(a)
        err, d = send(0x0247, bytes([len(aid)]) + aid.ljust(16, b'\0') + b'\0', quiet=True)
        if dbg:
            print('  sit open err', err, d.hex(' '), file=sys.stderr, flush=True)
        ch = struct.unpack_from('<I', d)[0] if not err else 0
        reply(ch if not err else -1)
    elif f == 'logic_channel_close':
        send(0x020e, struct.pack('<I', int(a, 16)), quiet=True)
        reply(0)
    elif f == 'transmit':
        x = bytes.fromhex(a)
        p3 = x[4] if len(x) > 4 else 0
        data = x[5:5 + p3] if len(x) > 5 else b''
        body = struct.pack('<IIIIIIH', ch, x[0], x[1], x[2], x[3], p3, len(data)) + data
        err, d = send(0x020f, body.ljust(0x26 + 2 * len(data) - 12, b'\0'), quiet=True)
        if dbg:
            print('  sit apdu err', err, d.hex(' '), file=sys.stderr, flush=True)
        if err or len(d) < 4:
            reply(-1)
            continue
        n = struct.unpack_from('<H', d, 2)[0]
        reply(0, (d[4:4 + n] + d[:2]).hex())
sys.exit(p.wait())
