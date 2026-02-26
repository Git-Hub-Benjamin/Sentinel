# Sentinel

GPU arbitration daemon for shared research workstations. Automatically yields inference to research workloads (training, quantizing, FPGA experiments) and resumes when the GPU is free.

## How It Works

- Runs Ollama (LLM inference) in the background when the GPU is idle
- Researchers run workloads via `sentinel-request <command>` — Sentinel pauses inference, hands off the GPU, then resumes automatically
- A background watchdog also detects any unauthorized CUDA process and force-pauses inference as a safety net
- A status CLI and optional web dashboard shows current GPU state

## Components

| File | Purpose |
|---|---|
| `sentinel/daemon.py` | Core arbitration daemon |
| `sentinel/watchdog.py` | nvidia-smi polling, auto-detection |
| `sentinel/request.py` | `sentinel-request` CLI wrapper |
| `sentinel/status.py` | `sentinel-status` CLI |
| `sentinel/config.py` | Configuration |
| `sentinel.service` | systemd unit for the daemon |
| `install.sh` | Install script |

## Install

```bash
git clone git@github.com:Git-Hub-Benjamin/Sentinel.git
cd Sentinel
sudo ./install.sh
```

## Usage

### Researchers — wrap your workload:
```bash
sentinel-request python train.py
sentinel-request python quantize.py --model llama
```
Sentinel pauses inference automatically before your command runs and resumes after.

### Check status:
```bash
sentinel-status
```

### Control daemon:
```bash
sudo systemctl start sentinel
sudo systemctl stop sentinel
sudo systemctl status sentinel
```

## Configuration

Edit `/etc/sentinel/config.toml` after install. Key options:

```toml
[inference]
service = "ollama"          # systemd service to pause/resume
restart_delay = 3           # seconds to wait before resuming after GPU freed

[watchdog]
poll_interval = 5           # seconds between nvidia-smi polls
ignored_processes = []      # extra process names to never treat as research

[web]
enabled = true
port = 8765
```
