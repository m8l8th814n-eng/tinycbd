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
#   sitctl.py listen         (all modem channels, header + hexdump)
#   sitctl.py -s 0 ...       send on SIM slot 0 (umts_ipc0); default slot 1
#   SIT_DEV=/dev/xxx sitctl.py ...   send on any channel
#   sitctl.py radio on|off
#   sitctl.py pin            (prompts for the PIN, never echoed or printed)
#   sitctl.py reg
#   sitctl.py dial <number>
#   sitctl.py answer
#   sitctl.py hangup <call id>
import getpass, os, re, select, struct, sys, time

# One RIL instance per SIM slot: umts_ipc0 / umts_ipc1. The physical SIM
# answers on umts_ipc1 (SET_RADIO_POWER err=0, 2026-10-02), so slot 1 is the
# default. -s 0|1 or SIT_DEV=/dev/... picks the channel requests go out on.
# DEV = os.environ.get('SIT_DEV', '/dev/umts_ipc0')
DEV = os.environ.get('SIT_DEV', '/dev/umts_ipc1')
# LISTEN_DEVS = ['/dev/umts_ipc0', '/dev/umts_ipc1']
# Everything the CP sends to userspace; channels that do not exist or cannot
# be opened are skipped. Only the umts_ipc ones carry SIT.
SIT_DEVS = ['/dev/umts_ipc0', '/dev/umts_ipc1']
LISTEN_DEVS = SIT_DEVS + ['/dev/umts_rfs0'] + [f'/dev/oem_ipc{i}' for i in range(8)]

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


SHOW_PRIVATE = os.environ.get('SIT_PRIVATE') == '1'


def hexdump(buf, secret=False):
    if secret:
        print('    <payload hidden>')
        return
    if not SHOW_PRIVATE:
        # ICCID/IMSI/MSISDN as ASCII digit runs: blank them over the whole
        # buffer, not per 16-byte row (a row split leaked them). Shown in
        # both columns as '*'. SIT_PRIVATE=1 shows them.
        buf = re.sub(rb'[0-9]{9,}', lambda m: b'*' * len(m.group()), bytes(buf))
    for o in range(0, len(buf), 16):
        chunk = buf[o:o + 16]
        print(f'    {o:04x}  {chunk.hex(" "):<47}  {mask(chunk)}')


def show_raw(dev, buf):
    print(time.strftime('%T'), dev, f'raw len={len(buf)}', flush=True)
    hexdump(buf)


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
    print(time.strftime('%T'), kind, name(mid), f'(0x{mid:04x}) len={ln}{extra}',
          f'got={len(buf)}', flush=True)
    # txt = '<payload hidden>' if secret else mask(body).strip('.')[:60]
    hexdump(body, secret)
    decode(typ, mid, buf)


# RIL_RegState values (reg state byte of GET_CS/PS_REG_STATE)
REG = {0: 'not registered, not searching', 1: 'registered, home',
       2: 'searching', 3: 'registration denied', 4: 'unknown',
       5: 'registered, roaming', 10: 'emergency only (not searching)',
       12: 'emergency only (searching)', 13: 'emergency only (denied)',
       14: 'emergency only (unknown)'}


def decode(typ, mid, buf):
    # field offsets from ProtocolNet{Voice,Data}RegStateAdapter in
    # vendor.radio.protocol.sit.stream.so (offsets from frame start)
    if typ == 1 and mid == 0x0700 and len(buf) >= 0x11:
        st, rej, rat = buf[0xc], buf[0xd], buf[0xe]
        lac = int.from_bytes(buf[0xf:0x11], 'little')
        print(f'    = CS: {REG.get(st, st)}, reject cause {rej}, rat {rat}, lac 0x{lac:x}')
    elif typ == 1 and mid == 0x0701 and len(buf) >= 0x12:
        st, rej, rat = buf[0xc], buf[0xd], buf[0xf]
        lac = int.from_bytes(buf[0x10:0x12], 'little')
        print(f'    = PS: {REG.get(st, st)}, reject cause {rej}, rat {rat}, lac 0x{lac:x}')
    elif typ == 1 and mid == 0x0801 and len(buf) >= 0xd:
        print('    = radio state', buf[0xc])
    elif typ == 2 and mid == 0x0802 and len(buf) >= 0x9:
        print('    = radio state', buf[0x8], {0: '(off)', 2: '(on)'}.get(buf[0x8], ''))


def run(frame, wait=5.0, secret=False):
    # write on DEV, but read replies and indications from both slots
    fds = {}
    for d in LISTEN_DEVS + ([DEV] if DEV not in LISTEN_DEVS else []):
        try:
            fds[os.open(d, os.O_RDWR | os.O_NONBLOCK)] = os.path.basename(d)
        except OSError as e:
            if d == DEV:
                raise
            print('skip', d, e.strerror)
    print('listening on', ' '.join(sorted(fds.values())), flush=True)
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
            if ev & (select.POLLHUP | select.POLLERR | select.POLLNVAL):
                # cpif reports HUP once the CP leaves ONLINE (CRASH_EXIT)
                print(time.strftime('%T'), fds[fd], 'hung up (modem not ONLINE)')
                return
            try:
                data = os.read(fd, 65536)
            except BlockingIOError:
                continue
            if not data:
                continue
            if '/dev/' + fds[fd] in SIT_DEVS:
                print('<-', fds[fd], end=' ')
                show(data)
            else:
                print('<-', end=' ')
                show_raw(fds[fd], data)


def build(args, getpin=None):
    """args like ['radio', 'on'] -> (frame, secret, wait); shared with modemctl.py"""
    cmd = args[0]
    if cmd == 'radio':
        on = len(args) > 1 and args[1] == 'on'
        # ProtocolRadioPowerBuilder::BuildRadioPower: u32 1=off 2=on, u8, u8
        return request(0x0800, struct.pack('<IBB', 2 if on else 1, 0, 0)), False, 5
    if cmd == 'pin':
        pin = (getpin or (lambda: getpass.getpass('PIN: ')))().encode()[:8]
        # BuildSimVerifyPin: u8 pin_len, pin[8], u8 aid_len, aid[16]
        payload = bytes([len(pin)]) + pin.ljust(8, b'\0') + bytes([0]) + bytes(16)
        return request(0x0201, payload), True, 5
    if cmd == 'reg':
        return request(0x0700), False, 5
    if cmd == 'psreg':
        return request(0x0701), False, 5
    if cmd == 'radiostate':
        return request(0x0801), False, 5
    if cmd == 'dial':
        num = args[1].encode()[:0x52]
        # BuildDial: u8 calltype (voice=1), u8 a, u8 numlen, num[0x52],
        # u8 0, u8 ton (0x10 '+', else 0x20), u8 1, u8 clir (0xff default),
        # u16 0, u8 0, u8 b
        payload = bytes([1, 0, len(num)]) + num.ljust(0x51, b'\0')
        payload += bytes([0, 0x10 if num.startswith(b'+') else 0x20, 1, 0xff, 0, 0, 0, 0])
        assert 12 + len(payload) == 0x68
        return request(0x0001, payload), False, 15
    if cmd == 'answer':
        return request(0x0004), False, 5
    if cmd == 'hangup':
        # BuildHangup: u32 call id, u32 1
        return request(0x0008, struct.pack('<II', int(args[1]), 1)), False, 5
    raise ValueError(f'unknown command {cmd}')


def main():
    global DEV
    if len(sys.argv) > 2 and sys.argv[1] == '-s':
        DEV = f'/dev/umts_ipc{int(sys.argv[2])}'
        del sys.argv[1:3]
    if len(sys.argv) < 2:
        print(__doc__ if __doc__ else open(__file__).read().split('\nimport')[0])
        sys.exit(1)
    if sys.argv[1] == 'listen':
        run(None, wait=-1)
        return
    try:
        frame, secret, wait = build(sys.argv[1:])
    except (ValueError, IndexError) as e:
        print(e)
        sys.exit(1)
    run(frame, wait=wait, secret=secret)


if __name__ == '__main__':
    main()
