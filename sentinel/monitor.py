"""
Live-updating terminal monitor for Sentinel daemon state.
Shows current SSH sessions and inference state.
"""

import json
import socket
import sys
import time
from datetime import datetime

from .config import load_config, SOCKET_PATH


# ANSI color codes
class Colors:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    GREEN = "\033[92m"
    RED = "\033[91m"
    YELLOW = "\033[93m"
    CYAN = "\033[96m"
    DIM = "\033[2m"


def get_daemon_status() -> dict:
    """Query daemon for current status via Unix socket."""
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(3)
        sock.connect(SOCKET_PATH)
        sock.sendall(json.dumps({"cmd": "status"}).encode())
        data = sock.recv(4096).decode()
        sock.close()
        return json.loads(data)
    except Exception:
        return None


def format_time(iso_str: str) -> str:
    """Convert ISO datetime to short format."""
    if not iso_str:
        return ""
    try:
        dt = datetime.fromisoformat(iso_str)
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except:
        return iso_str


def clear_screen():
    """Clear terminal screen with ANSI escape."""
    sys.stdout.write("\033[2J\033[H")
    sys.stdout.flush()


def render_monitor(status: dict, owner_user: str):
    """Render the monitor display."""
    clear_screen()

    now = datetime.now().strftime("%a %H:%M:%S")
    print(f"{Colors.BOLD}=== Sentinel Monitor ==={Colors.RESET}   [{now}]")
    print()

    if not status:
        print(f"{Colors.RED}✗ Daemon not running{Colors.RESET}")
        return

    # Inference status
    service = status.get("inference_service", "ollama")
    running = status.get("inference_running", False)
    status_icon = f"{Colors.GREEN}●{Colors.RESET}" if running else f"{Colors.RED}●{Colors.RESET}"
    running_text = "running" if running else "stopped"
    print(f"Inference:  {service} ({status_icon} {running_text})")

    # State
    state = status.get("state", "unknown")
    state_color = Colors.GREEN if state == "idle" else Colors.YELLOW
    print(f"State:      {state_color}{state}{Colors.RESET}")

    if state != "idle":
        lock_holder = status.get("lock_holder", "unknown")
        lock_since = status.get("lock_since")
        if lock_since:
            since_str = format_time(lock_since)
            print(f"Lock:       {lock_holder} (since {since_str})")
        else:
            print(f"Lock:       {lock_holder}")

    print()
    print("SSH Sessions:")
    print("────────────────────────────────────────────")

    sessions = status.get("ssh_sessions", [])
    if not sessions:
        print(f"  {Colors.DIM}(none){Colors.RESET}")
    else:
        for session in sessions:
            user = session.get("user", "?")
            tty = session.get("tty", "?")
            from_host = session.get("from", "?")
            is_owner = user == owner_user

            if is_owner:
                color = Colors.GREEN
                marker = "(owner)"
            else:
                color = Colors.RED
                marker = "[GUEST — inference paused]"

            print(f"  {color}{user:<12}{Colors.RESET} {tty:<8} {from_host:<18} {marker}")

    print()
    print(f"{Colors.DIM}Ctrl+C to exit{Colors.RESET}")


def main():
    config = load_config()
    owner_user = config.watchdog.owner_user
    poll_interval = 2  # Monitor updates every 2 seconds

    print(f"Starting Sentinel Monitor (owner={owner_user})...")
    time.sleep(1)

    try:
        while True:
            status = get_daemon_status()
            render_monitor(status, owner_user)
            time.sleep(poll_interval)
    except KeyboardInterrupt:
        clear_screen()
        print("Monitor stopped.")
        sys.exit(0)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
