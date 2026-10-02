#!/usr/bin/env python3
# Minimal SIT (Samsung IPC, Pixel RIL protocol) client for /dev/umts_ipc0.
#
# Frame layouts from vendor.radio.protocol.sit.{base,stream}.so (RE/sit/):
#   request    RCM_HEADER     u16 type=0, u16 id, u16 len, u16 token, u32 0
#   response   RCM_HEADER     u16 type=1?, u16 id, u16 len, u16 token, u32 error
#   indication RCM_IND_HEADER u16 type=2, u16 id, u16 len, u16 0
# len is the whole frame including the header, little endian throughout.
#
# Usage (as root, with modemlisten.py stopped so it does not eat replies):
#   sitctl.py listen        (reads umts_ipc0 and umts_ipc1)
#   SIT_DEV=/dev/umts_ipc1 sitctl.py ...   send on slot 1
#   sitctl.py radio on|off
#   sitctl.py pin            (prompts for the PIN, never echoed or printed)
#   sitctl.py reg
#   sitctl.py dial <number>
#   sitctl.py answer
#   sitctl.py hangup <call id>
import getpass, os, re, select, struct, sys, time

# One RIL instance per SIM slot: umts_ipc0 / umts_ipc1. The physical SIM
# reported its ICCID/IMSI on umts_ipc1 here. Pick with SIT_DEV=/dev/umts_ipc1.
DEV = os.environ.get('SIT_DEV', '/dev/umts_ipc0')
LISTEN_DEVS = ['/dev/umts_ipc0', '/dev/umts_ipc1']

NAMES = {
    0x0001: 'SIT_DIAL', 0x0004: 'SIT_ANSWER', 0x0008: 'SIT_HANGUP',
    0x000d: 'SIT_IND_EMERGENCY_CALL_LIST',
    0x0201: 'SIT_VERIFY_SIM_PIN', 0x0203: 'SIT_VERIFY_SIM_PIN2',
    0x0210: 'SIT_IND_SIM_STATUS_CHANGED', 0x0246: 'SIT_IND_SIM_PB_READY',
    0x024e: 'SIT_IND_SIM_SLOT_STATUS_CHANGED',
    0x0700: 'SIT_GET_CS_REG_STATE', 0x0701: 'SIT_GET_PS_REG_STATE',
    0x0800: 'SIT_SET_RADIO_POWER', 0x0801: 'SIT_GET_RADIO_POWER',
    0x0802: 'SIT_IND_RADIO_STATE_CHANGED', 0x0803: 'SIT_IND_RADIO_READY',
}
# full id table, if RE/sit/sit_ids.txt was copied next to this script
_ids = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'sit_ids.txt')
if os.path.exists(_ids):
    for line in open(_ids):
        p = line.split()
        if len(p) == 2:
            NAMES.setdefault(int(p[0], 16), p[1])

_token = int(time.time()) & 0x7fff


def name(i):
    return NAMES.get(i, f'0x{i:04x}')


def mask(raw):
    raw = re.sub(rb'[0-9]{9,}', b'<num>', raw)
    return ''.join(chr(c) if 32 <= c < 127 else '.' for c in raw)


def request(msg_id, payload=b''):
    global _token
    _token = (_token + 1) & 0xffff
    length = 12 + len(payload)
    return struct.pack('<HHHHI', 0, msg_id, length, _token, 0) + payload


def show(buf, secret=False):
    if len(buf) < 8:
        print('short', buf.hex(' '))
        return
    typ, mid, ln = struct.unpack_from('<HHH', buf)
    kind = {0: 'REQ', 1: 'RSP', 2: 'IND'}.get(typ, f'type{typ}')
    if typ == 2:
        body = buf[8:]
        extra = ''
    else:
        tok, err = struct.unpack_from('<HI', buf, 6) if len(buf) >= 12 else (0, 0)
        body = buf[12:]
        extra = f' token={tok} err={err}'
    txt = '<payload hidden>' if secret else mask(body).strip('.')[:60]
    print(time.strftime('%T'), kind, name(mid), f'len={ln}{extra}', txt, flush=True)


def run(frame, wait=5.0, secret=False):
    # write on DEV, but read replies and indications from both slots
    fds = {}
    for d in LISTEN_DEVS:
        fds[os.open(d, os.O_RDWR | os.O_NONBLOCK)] = os.path.basename(d)
    tx = next(fd for fd, n in fds.items() if n == os.path.basename(DEV))
    if frame:
        print('->', os.path.basename(DEV), end=' ')
        show(frame, secret)
        os.write(tx, frame)
    p = select.poll()
    for fd in fds:
        p.register(fd, select.POLLIN)
    end = time.time() + wait
    while wait < 0 or time.time() < end:
        for fd, ev in p.poll(500):
            try:
                data = os.read(fd, 65536)
            except BlockingIOError:
                continue
            if data:
                print('<-', fds[fd], end=' ')
                show(data)


def main():
    if len(sys.argv) < 2:
        print(__doc__ if __doc__ else open(__file__).read().split('\nimport')[0])
        sys.exit(1)
    cmd = sys.argv[1]
    if cmd == 'listen':
        run(None, wait=-1)
    elif cmd == 'radio':
        on = len(sys.argv) > 2 and sys.argv[2] == 'on'
        # ProtocolRadioPowerBuilder::BuildRadioPower: u32 1=off 2=on, u8, u8
        run(request(0x0800, struct.pack('<IBB', 2 if on else 1, 0, 0)))
    elif cmd == 'pin':
        pin = getpass.getpass('PIN: ').encode()[:8]
        # BuildSimVerifyPin: u8 pin_len, pin[8], u8 aid_len, aid[16]
        payload = bytes([len(pin)]) + pin.ljust(8, b'\0') + bytes([0]) + bytes(16)
        run(request(0x0201, payload), secret=True)
    elif cmd == 'reg':
        run(request(0x0700))
    elif cmd == 'dial':
        num = sys.argv[2].encode()[:0x52]
        # BuildDial: u8 calltype (voice=1), u8 a, u8 numlen, num[0x52],
        # u8 0, u8 ton (0x10 '+', else 0x20), u8 1, u8 clir (0xff default),
        # u16 0, u8 0, u8 b
        payload = bytes([1, 0, len(num)]) + num.ljust(0x51, b'\0')
        payload += bytes([0, 0x10 if num.startswith(b'+') else 0x20, 1, 0xff, 0, 0, 0, 0])
        assert 12 + len(payload) == 0x68
        run(request(0x0001, payload), wait=15)
    elif cmd == 'answer':
        run(request(0x0004))
    elif cmd == 'hangup':
        # BuildHangup: u32 call id, u32 1
        run(request(0x0008, struct.pack('<II', int(sys.argv[2]), 1)))
    else:
        print('unknown command', cmd)
        sys.exit(1)


if __name__ == '__main__':
    main()
