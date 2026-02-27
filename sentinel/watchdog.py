"""
Monitors SSH sessions to detect when non-owner users are connected.
Emits events: GPU_TAKEN (guest connected), GPU_FREE (only owner or no one)
"""

import subprocess
import time
import logging
from typing import List, Dict, Callable, Optional

log = logging.getLogger("sentinel.watchdog")


def get_ssh_sessions() -> List[Dict[str, str]]:
    """
    Returns list of SSH/remote sessions from `who` output.
    Only includes pts/* entries (TTY-based remote sessions).

    Returns: [{"user": str, "tty": str, "from": str, "time": str}]
    """
    try:
        result = subprocess.run(
            ["who"],
            capture_output=True, text=True, timeout=5
        )
        sessions = []
        for line in result.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            # Format: username tty date time [from]
            # Example: benjamin pts/13 2026-02-26 18:48 (144.39.201.185)
            # Example: mark     pts/6  2026-02-26 08:31
            parts = line.split()
            if len(parts) >= 2:
                user = parts[0]
                tty = parts[1]
                # Only include remote TTYs (pts/*)
                if tty.startswith("pts/"):
                    # Extract remote host if present (at the end in parens)
                    from_host = ""
                    time_str = ""
                    if len(parts) >= 4:
                        time_str = f"{parts[2]} {parts[3]}"  # date + time
                        if len(parts) >= 5:
                            # Last part is the from field (in parens)
                            from_host = parts[4].strip("()")
                    sessions.append({
                        "user": user,
                        "tty": tty,
                        "from": from_host,
                        "time": time_str,
                    })
        return sessions
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        log.warning(f"who command failed: {e}")
        return []


def get_guest_sessions(sessions: List[Dict[str, str]], owner_user: str) -> List[Dict[str, str]]:
    """Filter out the owner user, return only guest sessions."""
    return [s for s in sessions if s["user"] != owner_user]


class Watchdog:
    def __init__(self, poll_interval: int, owner_user: str,
                 on_taken: Callable, on_free: Callable,
                 on_sessions_update: Optional[Callable] = None):
        self.poll_interval = poll_interval
        self.owner_user = owner_user
        self.on_taken = on_taken
        self.on_free = on_free
        self.on_sessions_update = on_sessions_update
        self._guests_active = False
        self._running = False
        self._last_sessions = []

    def run(self):
        self._running = True
        log.info(f"SSH watchdog started (owner={self.owner_user}, poll={self.poll_interval}s)")
        while self._running:
            sessions = get_ssh_sessions()
            guests = get_guest_sessions(sessions, self.owner_user)

            # Notify daemon of current sessions
            if self.on_sessions_update:
                self.on_sessions_update(sessions)

            # Check for state change
            if guests and not self._guests_active:
                log.info(f"Guest session(s) detected: {[s['user'] for s in guests]}")
                self._guests_active = True
                guest_users = {s["user"] for s in guests}
                self.on_taken(source="ssh", users=guest_users)
            elif not guests and self._guests_active:
                log.info("No guest sessions, GPU is free")
                self._guests_active = False
                self.on_free(source="ssh")

            self._last_sessions = sessions
            time.sleep(self.poll_interval)

    def stop(self):
        self._running = False
