"""
Sentinel daemon — core arbitration logic.
Manages inference service state and handles lock requests via Unix socket.
"""

import os
import sys
import json
import socket
import signal
import logging
import subprocess
import threading
import time
from datetime import datetime
from typing import Optional

from .config import load_config, SOCKET_PATH, STATE_FILE
from .watchdog import Watchdog

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s"
)
log = logging.getLogger("sentinel.daemon")


class State:
    IDLE = "idle"           # GPU free, inference running
    RESEARCH = "research"   # Research workload active, inference paused
    RESUMING = "resuming"   # Waiting for restart_delay before bringing inference back


class SentinelDaemon:
    def __init__(self):
        self.config = load_config()
        self.state = State.IDLE
        self.lock_holder: Optional[str] = None
        self.lock_since: Optional[str] = None
        self.lock_count = 0  # reference count — multiple research processes can stack
        self.ssh_sessions: list = []
        self._lock = threading.RLock()
        self._running = False

    # ------------------------------------------------------------------ #
    # Inference service control
    # ------------------------------------------------------------------ #

    def _service_running(self) -> bool:
        result = subprocess.run(
            ["systemctl", "is-active", "--quiet", self.config.inference.service]
        )
        return result.returncode == 0

    def pause_inference(self, reason: str = ""):
        if self._service_running():
            log.info(f"Pausing inference service ({reason})")
            subprocess.run(["systemctl", "stop", self.config.inference.service], check=False)
        else:
            log.debug("Inference service already stopped")

    def resume_inference(self):
        delay = self.config.inference.restart_delay
        if delay > 0:
            log.info(f"Waiting {delay}s before resuming inference...")
            time.sleep(delay)
        log.info("Resuming inference service")
        subprocess.run(["systemctl", "start", self.config.inference.service], check=False)

    # ------------------------------------------------------------------ #
    # State transitions
    # ------------------------------------------------------------------ #

    def acquire(self, holder: str) -> dict:
        with self._lock:
            self.lock_count += 1
            if self.state == State.IDLE:
                self.state = State.RESEARCH
                self.lock_holder = holder
                self.lock_since = datetime.now().isoformat()
                self._write_state()
                threading.Thread(target=self.pause_inference, args=(f"lock by {holder}",), daemon=True).start()
                return {"ok": True, "message": f"GPU acquired by {holder}"}
            else:
                # Already in research mode — stack the lock
                return {"ok": True, "message": f"GPU already in research mode, lock stacked (count={self.lock_count})"}

    def release(self, holder: str) -> dict:
        with self._lock:
            self.lock_count = max(0, self.lock_count - 1)
            if self.lock_count == 0 and self.state == State.RESEARCH:
                self.state = State.IDLE
                self.lock_holder = None
                self.lock_since = None
                self._write_state()
                threading.Thread(target=self.resume_inference, daemon=True).start()
                return {"ok": True, "message": "GPU released, resuming inference"}
            return {"ok": True, "message": f"Lock released (remaining count={self.lock_count})"}

    def force_pause(self, holders: set) -> dict:
        """Called by watchdog when SSH guests detected."""
        with self._lock:
            if self.state == State.IDLE:
                self.state = State.RESEARCH
                self.lock_holder = f"ssh:{','.join(sorted(holders))}"
                self.lock_since = datetime.now().isoformat()
                self._write_state()
                threading.Thread(target=self.pause_inference, args=("ssh watchdog detection",), daemon=True).start()
        return {"ok": True}

    def force_free(self) -> dict:
        """Called by watchdog when GPU is clear."""
        with self._lock:
            if self.state == State.RESEARCH and self.lock_count == 0:
                self.state = State.IDLE
                self.lock_holder = None
                self.lock_since = None
                self._write_state()
                threading.Thread(target=self.resume_inference, daemon=True).start()
        return {"ok": True}

    def get_status(self) -> dict:
        with self._lock:
            return {
                "state": self.state,
                "lock_holder": self.lock_holder,
                "lock_since": self.lock_since,
                "lock_count": self.lock_count,
                "inference_service": self.config.inference.service,
                "inference_running": self._service_running(),
                "ssh_sessions": self.ssh_sessions,
                "owner_user": self.config.watchdog.owner_user,
            }

    def _write_state(self):
        try:
            with open(STATE_FILE, "w") as f:
                json.dump(self.get_status(), f)
        except OSError:
            pass

    # ------------------------------------------------------------------ #
    # Unix socket server
    # ------------------------------------------------------------------ #

    def _handle_client(self, conn: socket.socket):
        try:
            data = conn.recv(4096).decode()
            msg = json.loads(data)
            cmd = msg.get("cmd")

            if cmd == "acquire":
                resp = self.acquire(msg.get("holder", "unknown"))
            elif cmd == "release":
                resp = self.release(msg.get("holder", "unknown"))
            elif cmd == "status":
                resp = self.get_status()
            else:
                resp = {"ok": False, "message": f"Unknown command: {cmd}"}

            conn.sendall(json.dumps(resp).encode())
        except Exception as e:
            log.error(f"Error handling client: {e}")
        finally:
            conn.close()

    def _socket_server(self):
        if os.path.exists(SOCKET_PATH):
            os.unlink(SOCKET_PATH)
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(SOCKET_PATH)
        os.chmod(SOCKET_PATH, 0o666)
        server.listen(5)
        server.settimeout(1)
        log.info(f"Listening on {SOCKET_PATH}")
        while self._running:
            try:
                conn, _ = server.accept()
                threading.Thread(target=self._handle_client, args=(conn,), daemon=True).start()
            except socket.timeout:
                continue
        server.close()
        if os.path.exists(SOCKET_PATH):
            os.unlink(SOCKET_PATH)

    # ------------------------------------------------------------------ #
    # Main run loop
    # ------------------------------------------------------------------ #

    def run(self):
        self._running = True

        # Ensure inference is running at startup if GPU is free
        if not self._service_running():
            self.resume_inference()

        # Start watchdog
        watchdog = Watchdog(
            poll_interval=self.config.watchdog.poll_interval,
            owner_user=self.config.watchdog.owner_user,
            on_taken=lambda source, users: self.force_pause(users),
            on_free=lambda source: self.force_free(),
            on_sessions_update=lambda sessions: setattr(self, 'ssh_sessions', sessions),
        )
        wt = threading.Thread(target=watchdog.run, daemon=True)
        wt.start()

        # Handle signals
        def shutdown(sig, frame):
            log.info("Shutting down Sentinel")
            self._running = False
            watchdog.stop()

        signal.signal(signal.SIGTERM, shutdown)
        signal.signal(signal.SIGINT, shutdown)

        # Start socket server (blocks until shutdown)
        self._socket_server()
        log.info("Sentinel daemon stopped")


def main():
    if os.geteuid() != 0:
        print("Sentinel daemon must run as root (needed to control systemd services)")
        sys.exit(1)
    daemon = SentinelDaemon()
    daemon.run()
