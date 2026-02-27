#!/usr/bin/env python3
"""Debug script to test Sentinel daemon and SSH monitoring."""

import json
import socket
import sys
import subprocess
from sentinel.config import SOCKET_PATH
from sentinel.watchdog import get_ssh_sessions, get_guest_sessions

print("=" * 60)
print("SENTINEL DEBUG")
print("=" * 60)

# Test 1: Check who output
print("\n[1] Raw 'who' output:")
print("-" * 60)
result = subprocess.run(["who"], capture_output=True, text=True)
print(result.stdout)

# Test 2: Check parsed SSH sessions
print("\n[2] Parsed SSH sessions:")
print("-" * 60)
sessions = get_ssh_sessions()
if sessions:
    for s in sessions:
        print(f"  {s['user']:<15} {s['tty']:<10} from={s['from']}")
else:
    print("  (none detected)")

# Test 3: Check guest sessions
print("\n[3] Guest sessions (non-benjamin):")
print("-" * 60)
guests = get_guest_sessions(sessions, "benjamin")
if guests:
    for g in guests:
        print(f"  {g['user']:<15} {g['tty']:<10} from={g['from']}")
else:
    print("  (none - only owner connected)")

# Test 4: Query daemon
print("\n[4] Daemon status:")
print("-" * 60)
try:
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(3)
    s.connect(SOCKET_PATH)
    s.sendall(json.dumps({"cmd": "status"}).encode())
    data = s.recv(4096).decode()
    s.close()
    status = json.loads(data)
    print(json.dumps(status, indent=2))
except Exception as e:
    print(f"ERROR: {e}")

# Test 5: Check daemon logs
print("\n[5] Daemon logs (last 10 lines):")
print("-" * 60)
result = subprocess.run(
    ["journalctl", "-u", "sentinel", "-n", "10", "--no-pager"],
    capture_output=True, text=True
)
print(result.stdout)

print("=" * 60)
