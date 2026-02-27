#!/usr/bin/env bash
# sentinel-ctl — master control script for the Sentinel/Ollama compute setup
set -euo pipefail

API="http://localhost:8765"
VENV="/home/benjamin/.venv-sentinel"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── colours ────────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'

ok()   { echo -e "${GREEN}✓${RESET} $*"; }
warn() { echo -e "${YELLOW}!${RESET} $*"; }
err()  { echo -e "${RED}✗${RESET} $*"; }
hdr()  { echo -e "\n${BOLD}${CYAN}── $* ${RESET}"; }

need_sudo() {
    if [[ $EUID -ne 0 ]]; then
        echo -e "${YELLOW}(needs sudo)${RESET} running: sudo $0 $*"
        exec sudo "$0" "$@"
    fi
}

usage() {
    echo -e "${BOLD}Usage:${RESET} ./ctl.sh <command>\n"
    echo -e "${BOLD}Sentinel daemon${RESET}"
    echo "  restart          Restart sentinel daemon"
    echo "  start            Start sentinel daemon"
    echo "  stop             Stop sentinel daemon"
    echo "  status           Show sentinel + ollama + GPU + SSH state"
    echo "  logs             Tail sentinel logs (journalctl -f)"
    echo "  monitor          Launch live terminal monitor"
    echo ""
    echo -e "${BOLD}Ollama${RESET}"
    echo "  ollama-start     Start ollama"
    echo "  ollama-stop      Stop ollama"
    echo "  ollama-restart   Restart ollama"
    echo "  models           List loaded + available ollama models"
    echo "  pull <model>     Pull an ollama model (e.g. qwen3.5:35b)"
    echo "  query            Interactive inference test (query-inference.py)"
    echo ""
    echo -e "${BOLD}GPU / inference${RESET}"
    echo "  gpu              Live GPU VRAM and process usage"
    echo "  pause            Manually pause inference (stop ollama)"
    echo "  resume           Manually resume inference (start ollama)"
    echo ""
    echo -e "${BOLD}HTTP API${RESET}"
    echo "  api-status       Hit /capacity and /status on the HTTP API"
    echo "  api-test         Send a test chat completion through the proxy"
    echo ""
    echo -e "${BOLD}Debug${RESET}"
    echo "  debug            Full diagnostic dump (sessions, daemon, GPU)"
    echo "  install          Run install.sh (reinstall / upgrade)"
    echo ""
}

# ── sentinel daemon ────────────────────────────────────────────────────────────

cmd_restart() {
    need_sudo restart
    hdr "Restarting sentinel"
    systemctl restart sentinel
    sleep 2
    systemctl status sentinel --no-pager -l
    ok "Done — API should be up at $API/capacity"
}

cmd_start() {
    need_sudo start
    hdr "Starting sentinel"
    systemctl start sentinel
    sleep 1
    systemctl is-active sentinel && ok "sentinel running" || err "sentinel failed to start"
}

cmd_stop() {
    need_sudo stop
    hdr "Stopping sentinel"
    systemctl stop sentinel
    ok "sentinel stopped"
}

cmd_status() {
    hdr "Sentinel state"
    if sentinel-status 2>/dev/null; then
        true
    else
        err "sentinel daemon not responding"
    fi

    hdr "SSH sessions"
    who | awk '
        /pts\// {
            user=$1; tty=$2; from=$(NF)
            gsub(/[()]/, "", from)
            printf "  %-14s %-10s %s\n", user, tty, from
        }
    ' || true

    hdr "GPU"
    nvidia-smi --query-gpu=name,memory.total,memory.free,memory.used,utilization.gpu \
        --format=csv,noheader 2>/dev/null \
        | awk -F', ' '{printf "  %s\n  VRAM: %s total / %s used / %s free\n  Util: %s\n", $1,$2,$4,$3,$5}' \
        || warn "nvidia-smi not available"

    hdr "Services"
    for svc in sentinel ollama; do
        state=$(systemctl is-active $svc 2>/dev/null || echo "not-found")
        if [[ "$state" == "active" ]]; then
            echo -e "  ${GREEN}●${RESET} $svc"
        else
            echo -e "  ${RED}●${RESET} $svc ($state)"
        fi
    done

    hdr "HTTP API"
    curl -sf "$API/capacity" 2>/dev/null | python3 -m json.tool 2>/dev/null \
        || warn "HTTP API not responding (daemon may need restart)"
}

cmd_logs() {
    hdr "Sentinel logs (Ctrl+C to exit)"
    journalctl -u sentinel -f
}

cmd_monitor() {
    sentinel-monitor
}

# ── ollama ─────────────────────────────────────────────────────────────────────

cmd_ollama_start() {
    need_sudo ollama-start
    systemctl start ollama
    ok "ollama started"
}

cmd_ollama_stop() {
    need_sudo ollama-stop
    systemctl stop ollama
    ok "ollama stopped"
}

cmd_ollama_restart() {
    need_sudo ollama-restart
    systemctl restart ollama
    sleep 1
    ok "ollama restarted"
}

cmd_models() {
    hdr "Loaded in VRAM (/api/ps)"
    curl -sf http://localhost:11434/api/ps 2>/dev/null \
        | python3 -c "
import sys, json
d = json.load(sys.stdin)
models = d.get('models', [])
if not models:
    print('  (none)')
for m in models:
    vram = m.get('size_vram', 0) // (1024*1024)
    print(f'  {m[\"name\"]:<40} {vram} MB VRAM')
" || warn "ollama not running"

    hdr "Available locally (ollama list)"
    ollama list 2>/dev/null || warn "ollama not running"
}

cmd_pull() {
    local model="${1:-}"
    if [[ -z "$model" ]]; then
        echo "Usage: ./ctl.sh pull <model>"
        echo "Example: ./ctl.sh pull qwen3.5:35b"
        exit 1
    fi
    hdr "Pulling $model"
    # Sentinel must be stopped or inference must be free
    if systemctl is-active sentinel &>/dev/null; then
        state=$(curl -sf "$API/status" 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin)['state'])" 2>/dev/null || echo "unknown")
        if [[ "$state" != "idle" ]]; then
            warn "Sentinel state is '$state' — ollama may be stopped. Pulling anyway..."
        fi
    fi
    ollama pull "$model"
    ok "Pulled $model"
}

cmd_query() {
    python3 "$SCRIPT_DIR/query-inference.py"
}

# ── GPU / inference ────────────────────────────────────────────────────────────

cmd_gpu() {
    hdr "GPU utilisation"
    watch -n 1 'nvidia-smi --query-gpu=name,memory.total,memory.free,memory.used,utilization.gpu,utilization.memory,temperature.gpu --format=csv,noheader \
        | awk -F", " "{printf \"  GPU:   %s\n  VRAM:  %s total  |  %s used  |  %s free\n  Util:  GPU %s   MEM %s   TEMP %s\n\", \$1,\$2,\$4,\$3,\$5,\$6,\$7}"
        echo ""
        echo "  Processes:"
        nvidia-smi pmon -c 1 -s mu 2>/dev/null | grep -v "^#" | grep -v "^$" | awk "{printf \"    pid %-8s  mem %s MiB  cmd %s\n\", \$2,\$4,\$NF}" || echo "    (none)"'
}

cmd_pause() {
    need_sudo pause
    hdr "Pausing inference (stopping ollama)"
    systemctl stop ollama
    ok "ollama stopped — restart with: ./ctl.sh resume"
}

cmd_resume() {
    need_sudo resume
    hdr "Resuming inference (starting ollama)"
    # Also stop sentinel temporarily if it would fight us
    systemctl start ollama
    ok "ollama started"
}

# ── HTTP API ───────────────────────────────────────────────────────────────────

cmd_api_status() {
    hdr "GET /capacity"
    curl -sf "$API/capacity" | python3 -m json.tool || err "API not responding"

    hdr "GET /status"
    curl -sf "$API/status" | python3 -m json.tool || err "API not responding"
}

cmd_api_test() {
    hdr "POST /v1/chat/completions (streaming test)"
    # Pick first available model
    model=$(curl -sf "$API/v1/models" 2>/dev/null \
        | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['data'][0]['id'] if d.get('data') else '')" 2>/dev/null || echo "")

    if [[ -z "$model" ]]; then
        warn "No model available from /v1/models — is ollama running?"
        exit 1
    fi

    echo "Using model: $model"
    echo "Prompt: 'Say hello in exactly five words.'"
    echo ""

    curl -sf "$API/v1/chat/completions" \
        -H "Content-Type: application/json" \
        -d "{
            \"model\": \"$model\",
            \"messages\": [{\"role\": \"user\", \"content\": \"Say hello in exactly five words.\"}],
            \"stream\": false
        }" | python3 -c "
import sys, json
d = json.load(sys.stdin)
if 'error' in d:
    print('ERROR:', d['error'])
else:
    print(d['choices'][0]['message']['content'])
"
}

# ── debug ──────────────────────────────────────────────────────────────────────

cmd_debug() {
    python3 "$SCRIPT_DIR/debug-sentinel.py"
}

cmd_install() {
    need_sudo install
    bash "$SCRIPT_DIR/install.sh"
}

# ── dispatch ───────────────────────────────────────────────────────────────────

case "${1:-}" in
    restart)        cmd_restart ;;
    start)          cmd_start ;;
    stop)           cmd_stop ;;
    status)         cmd_status ;;
    logs)           cmd_logs ;;
    monitor)        cmd_monitor ;;
    ollama-start)   cmd_ollama_start ;;
    ollama-stop)    cmd_ollama_stop ;;
    ollama-restart) cmd_ollama_restart ;;
    models)         cmd_models ;;
    pull)           cmd_pull "${2:-}" ;;
    query)          cmd_query ;;
    gpu)            cmd_gpu ;;
    pause)          cmd_pause ;;
    resume)         cmd_resume ;;
    api-status)     cmd_api_status ;;
    api-test)       cmd_api_test ;;
    debug)          cmd_debug ;;
    install)        cmd_install ;;
    *)              usage ;;
esac
