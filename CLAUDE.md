# Sentinel — Context for Claude Code

## What This Is
GPU arbitration daemon for a shared university research workstation (USU) with 1x NVIDIA RTX 5090 (32GB GDDR7). The machine runs Ollama for personal LLM inference (Qwen3.5-35B-A3B) but is primarily a research machine for an FPGA project. Sentinel ensures research workloads always get GPU priority over inference.

## Hardware Context
- OS: Linux (Ubuntu expected)
- GPU: NVIDIA RTX 5090, 32GB GDDR7 — single GPU per machine
- There are 2 separate desktops, each with 1x 5090. Sentinel runs independently on each.
- Inference model: Qwen3.5-35B-A3B via Ollama
- Research uses: model training, quantization, FPGA-related ML inference — all via Python/CUDA

## Core Behavior
1. Ollama runs as a systemd service for personal inference
2. When a researcher wraps their workload with `sentinel-request <cmd>`, Sentinel:
   - Stops Ollama immediately
   - Runs the command
   - Restarts Ollama after the command exits (with configurable delay)
3. A background watchdog polls `nvidia-smi pmon` every N seconds — if any CUDA process other than Ollama is detected WITHOUT a sentinel-request lock, inference is force-paused (safety net)
4. When the GPU goes idle again (no research processes), inference is automatically resumed

## Architecture
- `sentinel/daemon.py` — main daemon, Unix socket server, state machine (IDLE/RESEARCH/RESUMING)
- `sentinel/watchdog.py` — nvidia-smi polling thread
- `sentinel/request.py` — `sentinel-request` CLI: acquires lock → runs command → releases lock
- `sentinel/status.py` — `sentinel-status` CLI: shows current state
- `sentinel/config.py` — config loading from /etc/sentinel/config.toml
- `sentinel.service` — systemd unit
- `install.sh` — install script (must run as root)

## Communication
Daemon listens on Unix socket `/var/run/sentinel.sock`.
Messages are JSON: `{"cmd": "acquire"|"release"|"status", "holder": "..."}`

## Key Design Decisions
- Lock counting (reference counting) so multiple stacked sentinel-request calls work correctly
- Watchdog is a safety net — it does NOT use the lock mechanism, it calls force_pause/force_free directly
- Daemon must run as root to control systemd services
- `sentinel-request` can be run by any user (socket is chmod 666)
- No external dependencies — stdlib only, requires Python 3.11+

## What Still Needs Work / Testing on Real Hardware
- Test watchdog detection with actual CUDA workloads (`nvidia-smi pmon` output format may need tuning)
- Test Ollama stop/start timing with real models loaded
- Verify socket permissions work for non-root users
- Consider adding a web status dashboard (config.web.enabled exists but not yet implemented)
- Consider Tailscale integration notes for remote access

## Install
```bash
sudo ./install.sh
```

## Commands
```bash
sentinel-status                        # check current state
sentinel-request python train.py       # wrap a research workload
sudo systemctl stop sentinel           # stop daemon
journalctl -u sentinel -f              # live logs
```
