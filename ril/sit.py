import os, select, struct, sys, time

DEV = f"/dev/umts_ipc{os.environ.get('SLOT', '1')}"
_tok = int(time.time()) & 0x7fff


def send(mid, payload=b'', wait=10, quiet=False):
    global _tok
    _tok += 1
    fd = os.open(DEV, os.O_RDWR)
    os.write(fd, struct.pack('<HHHHI', 0, mid, 12 + len(payload), _tok, 0) + payload)
    end = time.time() + wait
    while time.time() < end:
        if not select.select([fd], [], [], 0.5)[0]:
            continue
        b = os.read(fd, 65536)
        typ, rid, ln = struct.unpack_from('<HHH', b)
        if typ == 1 and rid == mid:
            err = struct.unpack_from('<I', b, 8)[0]
            if not quiet:
                print(f'err={err}', b[12:].hex(' '))
            os.close(fd)
            return err, b[12:]
    os.close(fd)
    if quiet:
        return -1, b''
    print('no reply')
    sys.exit(1)
