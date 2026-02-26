"""
sentinel-request — wrap a research workload, acquiring GPU priority before
running and releasing after.

Usage:
    sentinel-request python train.py --epochs 50
    sentinel-request python quantize.py
"""

import os
import sys
import json
import socket
import subprocess
import getpass
from .config import SOCKET_PATH


def _send(msg: dict) -> dict:
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.connect(SOCKET_PATH)
        s.sendall(json.dumps(msg).encode())
        data = s.recv(4096).decode()
        s.close()
        return json.loads(data)
    except FileNotFoundError:
        print("[sentinel] ERROR: Sentinel daemon is not running. Start it with: sudo systemctl start sentinel")
        sys.exit(1)
    except Exception as e:
        print(f"[sentinel] ERROR: Could not contact daemon: {e}")
        sys.exit(1)


def main():
    if len(sys.argv) < 2:
        print("Usage: sentinel-request <command> [args...]")
        sys.exit(1)

    holder = f"{getpass.getuser()}:{' '.join(sys.argv[1:])[:60]}"
    command = sys.argv[1:]

    # Acquire GPU
    resp = _send({"cmd": "acquire", "holder": holder})
    print(f"[sentinel] {resp.get('message', 'GPU acquired')}")

    # Run workload
    exit_code = 0
    try:
        result = subprocess.run(command)
        exit_code = result.returncode
    except KeyboardInterrupt:
        print("\n[sentinel] Interrupted.")
        exit_code = 130
    except FileNotFoundError:
        print(f"[sentinel] Command not found: {command[0]}")
        exit_code = 127
    finally:
        # Always release, even if workload crashed
        resp = _send({"cmd": "release", "holder": holder})
        print(f"[sentinel] {resp.get('message', 'GPU released')}")

    sys.exit(exit_code)
