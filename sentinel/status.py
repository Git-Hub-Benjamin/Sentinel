"""
sentinel-status — show current GPU arbitration state.
"""

import sys
import json
import socket
from .config import SOCKET_PATH


def main():
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.connect(SOCKET_PATH)
        s.sendall(json.dumps({"cmd": "status"}).encode())
        data = s.recv(4096).decode()
        s.close()
        status = json.loads(data)
    except FileNotFoundError:
        print("Sentinel daemon is NOT running")
        sys.exit(1)
    except Exception as e:
        print(f"Could not contact daemon: {e}")
        sys.exit(1)

    state = status["state"]
    state_display = {
        "idle":     "IDLE       — inference running, GPU available",
        "research": "RESEARCH   — inference paused, research workload active",
        "resuming": "RESUMING   — waiting to restart inference",
    }.get(state, state.upper())

    print(f"State:     {state_display}")
    print(f"Service:   {status['inference_service']} ({'running' if status['inference_running'] else 'stopped'})")

    if status.get("lock_holder"):
        print(f"Held by:   {status['lock_holder']}")
        print(f"Since:     {status['lock_since']}")
        print(f"Locks:     {status['lock_count']}")
