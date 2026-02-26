"""
Polls nvidia-smi to detect CUDA processes that are not the inference service.
Emits events: GPU_TAKEN, GPU_FREE
"""

import subprocess
import time
import logging
from typing import Set, Callable, Optional

log = logging.getLogger("sentinel.watchdog")

# Processes that belong to the inference layer — never treated as research
INFERENCE_PROCESSES = {"ollama", "ollama_llama_se", "ollama_llama_server"}


def get_cuda_processes() -> Set[str]:
    """Returns set of process names currently using the GPU via nvidia-smi pmon."""
    try:
        result = subprocess.run(
            ["nvidia-smi", "pmon", "-c", "1", "-s", "m"],
            capture_output=True, text=True, timeout=10
        )
        procs = set()
        for line in result.stdout.splitlines():
            line = line.strip()
            if line.startswith("#") or not line:
                continue
            parts = line.split()
            # pmon format: gpu  pid  type  fb  sm  mem  enc  dec  jpg  ofa  command
            if len(parts) >= 11:
                pid = parts[1]
                command = parts[10]
                if pid != "-" and command != "-":
                    procs.add(command)
        return procs
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        log.warning(f"nvidia-smi failed: {e}")
        return set()


def get_research_processes(ignored: list[str]) -> Set[str]:
    """Returns CUDA processes that are not inference and not in the ignored list."""
    all_procs = get_cuda_processes()
    ignored_set = INFERENCE_PROCESSES | set(ignored)
    return all_procs - ignored_set


class Watchdog:
    def __init__(self, poll_interval: int, ignored_processes: list[str],
                 on_taken: Callable, on_free: Callable):
        self.poll_interval = poll_interval
        self.ignored_processes = ignored_processes
        self.on_taken = on_taken
        self.on_free = on_free
        self._research_active = False
        self._running = False

    def run(self):
        self._running = True
        log.info(f"Watchdog started, polling every {self.poll_interval}s")
        while self._running:
            procs = get_research_processes(self.ignored_processes)
            if procs and not self._research_active:
                log.info(f"Research processes detected: {procs}")
                self._research_active = True
                self.on_taken(source="watchdog", procs=procs)
            elif not procs and self._research_active:
                log.info("No research processes detected, GPU is free")
                self._research_active = False
                self.on_free(source="watchdog")
            time.sleep(self.poll_interval)

    def stop(self):
        self._running = False
