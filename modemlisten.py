#!/usr/bin/env python3
# Open the CP's IPC channels as soon as they exist and log every message,
import os, select, time

DEVS = ['/dev/umts_ipc0', '/dev/umts_ipc1', '/dev/umts_rfs0',
        '/dev/oem_ipc0', '/dev/oem_ipc1', '/dev/oem_ipc3', '/dev/umts_dm0']
OUT = os.path.expanduser('~simon/modemlog')
os.makedirs(OUT, exist_ok=True)

fds, logs = {}, {}
p = select.poll()
while True:
    for d in DEVS:
        if d in fds.values():
            continue
        try:
            fd = os.open(d, os.O_RDONLY | os.O_NONBLOCK)
        except OSError:
            continue
        fds[fd] = d
        logs[fd] = open(os.path.join(OUT, os.path.basename(d) + '.log'), 'a')
        p.register(fd, select.POLLIN)
        print(time.strftime('%T'), 'opened', d, flush=True)
    for fd, ev in p.poll(500):
        try:
            data = os.read(fd, 65536)
        except BlockingIOError:
            continue
        if data:
            line = '%s %d %s' % (time.strftime('%T'), len(data), data.hex(' '))
            logs[fd].write(line + '\n')
            logs[fd].flush()
            print(os.path.basename(fds[fd]), line, flush=True)
