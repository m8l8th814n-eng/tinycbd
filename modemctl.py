#!/usr/bin/env python3
# Bring the modem up and drive it in one process: open every modem channel
# first (so the CP's boot-time indications are queued, not dropped), run
# `tinycbd full`, wait for ONLINE, then run a SIT command sequence while one
# reader thread prints everything that comes back (sitctl.py framing).
#
# Usage (as root, from the tinycbd directory; modemlisten.py stopped):
#   modemctl.py                    full + radio on + reg, then keep listening
#   modemctl.py -i                 same, then a prompt for more sitctl commands
#   modemctl.py --pin              ask for the SIM PIN up front, send it after radio on
#   modemctl.py --no-boot          modem already ONLINE: skip tinycbd
#   modemctl.py -s 0               SIM slot 0 (umts_ipc0); default slot 1
#   modemctl.py --seq "radio on;reg;psreg"   own command sequence
#   modemctl.py --dial NUMBER      dial at the end of the sequence
# Output also goes to ~/modemlog/modemctl-<time>.log (contains ICCID/IMSI:
# keep it local).
import argparse, getpass, os, select, subprocess, sys, threading, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import sitctl  # noqa: E402

STATE = '/sys/devices/platform/cpif/modem_state'
lock = threading.RLock()
poller = select.poll()
logf = None
stop = threading.Event()
seen = []           # (time, kind, id) of everything received, for waits


def out(*a):
    line = ' '.join(str(x) for x in a)
    with lock:
        sys.__stdout__.write(line + '\n')
        sys.__stdout__.flush()
        if logf:
            logf.write(line + '\n')
            logf.flush()


class Tee:
    """route sitctl's print() through out() so it lands in the log too"""
    def write(self, s):
        with lock:
            sys.__stdout__.write(s)
            if logf:
                logf.write(s)

    def flush(self):
        sys.__stdout__.flush()
        if logf:
            logf.flush()


def state():
    try:
        return open(STATE).read().strip()
    except OSError as e:
        return f'? ({e.strerror})'


def open_all(fds, quiet=False):
    # called before boot and again at ONLINE, in case cpif refused an open
    # while the modem was down
    have = set(fds.values())
    for d in sitctl.LISTEN_DEVS:
        if os.path.basename(d) in have:
            continue
        try:
            fd = os.open(d, os.O_RDWR | os.O_NONBLOCK)
        except OSError as e:
            if not quiet:
                out('skip', d, e.strerror)
            continue
        fds[fd] = os.path.basename(d)
        poller.register(fd, select.POLLIN)
    out('open:', ' '.join(sorted(fds.values())))
    return fds


def reader(fds):
    p = poller
    while not stop.is_set():
        if state() != 'ONLINE':
            # cpif reports HUP on every channel until ONLINE; just wait
            time.sleep(0.05)
            continue
        for fd, ev in p.poll(200):
            if ev & select.POLLIN:
                try:
                    data = os.read(fd, 65536)
                except BlockingIOError:
                    continue
                except OSError as e:
                    out('<-', fds[fd], 'read error', e.strerror)
                    continue
                if not data:
                    continue
                if '/dev/' + fds[fd] in sitctl.SIT_DEVS:
                    if len(data) >= 4:
                        typ = int.from_bytes(data[0:2], 'little')
                        mid = int.from_bytes(data[2:4], 'little')
                        seen.append((time.time(), typ, mid))
                    print('<-', fds[fd], end=' ')
                    sitctl.show(data)
                else:
                    print('<-', end=' ')
                    sitctl.show_raw(fds[fd], data)
            elif ev & (select.POLLHUP | select.POLLERR):
                time.sleep(0.05)


def watch_state():
    last = None
    while not stop.is_set():
        s = state()
        if s != last:
            out(time.strftime('%T'), 'modem_state', last, '->', s)
            if last == 'ONLINE' and s != 'ONLINE':
                out('*** modem left ONLINE: reboot + full needed; dmesg for "relink"/"CRASH"')
            last = s
        time.sleep(0.1)


def wait_for(typ, mid, since, timeout):
    end = time.time() + timeout
    while time.time() < end:
        if any(t >= since and k == typ and m == mid for t, k, m in seen):
            return True
        time.sleep(0.05)
    return False


def send(fds, txfd, args, pin=None):
    try:
        frame, secret, wait = sitctl.build(args, getpin=(lambda: pin) if pin else None)
    except (ValueError, IndexError) as e:
        out('bad command:', ' '.join(args), e)
        return
    mid = int.from_bytes(frame[2:4], 'little')
    t0 = time.time()
    print('->', fds[txfd], end=' ')
    sitctl.show(frame, secret)
    try:
        os.write(txfd, frame)
    except OSError as e:
        # EPERM here = cpif refused to wake the CP (link stuck)
        out('   write failed:', e.strerror)
        return
    if wait_for(1, mid, t0, wait):
        return
    out(f'   no RSP for {sitctl.name(mid)} within {wait} s')


def main():
    global logf
    ap = argparse.ArgumentParser()
    ap.add_argument('-s', type=int, default=1, help='SIM slot 0/1 (umts_ipc0/1)')
    ap.add_argument('-i', action='store_true', help='prompt for commands afterwards')
    ap.add_argument('--no-boot', action='store_true')
    ap.add_argument('--pin', action='store_true', help='ask for the SIM PIN')
    ap.add_argument('--seq', default='radio on;reg', help='commands, ";"-separated')
    ap.add_argument('--dial', help='dial this number after the sequence')
    a = ap.parse_args()

    if os.geteuid() != 0:
        sys.exit('run with sudo')
    pin = getpass.getpass('PIN: ') if a.pin else None

    logdir = os.path.expanduser('~' + os.environ.get('SUDO_USER', '')) + '/modemlog'
    os.makedirs(logdir, exist_ok=True)
    logpath = f'{logdir}/modemctl-{time.strftime("%Y%m%d-%H%M%S")}.log'
    logf = open(logpath, 'w')
    sys.stdout = Tee()
    out('log', logpath)

    fds = open_all({})
    txdev = f'umts_ipc{a.s}'

    threading.Thread(target=reader, args=(fds,), daemon=True).start()
    threading.Thread(target=watch_state, daemon=True).start()

    if not a.no_boot:
        out(time.strftime('%T'), 'tinycbd full')
        p = subprocess.Popen([os.path.join(HERE, 'tinycbd'), 'full'],
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        for line in p.stdout:
            out('  tinycbd:', line.rstrip())
        if p.wait() != 0:
            out('tinycbd full failed, rc', p.returncode)
            stop.set()
            return 1

    end = time.time() + 30
    while state() != 'ONLINE' and time.time() < end:
        time.sleep(0.1)
    if state() != 'ONLINE':
        out('not ONLINE after 30 s:', state())
        stop.set()
        return 1
    open_all(fds, quiet=True)
    txfd = next((fd for fd, n in fds.items() if n == txdev), None)
    if txfd is None:
        out(f'{txdev} could not be opened')
        stop.set()
        return 1
    time.sleep(2)   # let the boot-time indications arrive first

    seq = [c.split() for c in a.seq.split(';') if c.strip()]
    for i, args in enumerate(seq):
        send(fds, txfd, args)
        if args[:2] == ['radio', 'on']:
            if not wait_for(2, 0x0802, time.time() - 6, 10):
                out('   no RADIO_STATE_CHANGED yet')
            if pin:
                send(fds, txfd, ['pin'], pin)
        time.sleep(1)
    if a.dial:
        send(fds, txfd, ['dial', a.dial])

    if a.i:
        out('commands: radio on|off, radiostate, pin, reg, psreg, dial N, answer, hangup ID, quit')
        while True:
            try:
                line = input('sit> ').strip()
            except EOFError:
                break
            if line in ('q', 'quit', 'exit'):
                break
            if line:
                send(fds, txfd, line.split())
    else:
        out('listening, Ctrl-C to stop')
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
    stop.set()
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        stop.set()
