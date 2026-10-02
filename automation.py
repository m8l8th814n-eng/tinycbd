#!/usr/bin/env python3
# Modem bring-up in the right order. Run: sudo ./automation.py
import subprocess, time

STATE = '/sys/devices/platform/cpif/modem_state'

# 1. listen first, so the boot messages are caught
listen = subprocess.Popen(['./sitctl.py', 'listen'])
time.sleep(1)

# 2. boot the modem
subprocess.run(['./tinycbd', 'full'], check=True)

# 3. wait for ONLINE
while open(STATE).read().strip() != 'ONLINE':
    time.sleep(0.5)
time.sleep(3)

# 4. radio on, then ask for registration
subprocess.run(['./sitctl.py', 'radio', 'on'])
time.sleep(5)
subprocess.run(['./sitctl.py', 'reg'])

# 5. keep listening until Ctrl-C
listen.wait()
