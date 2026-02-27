#!/usr/bin/env bash
set -e

if [[ $EUID -ne 0 ]]; then
    echo "Run as root: sudo ./install.sh"
    exit 1
fi

VENV_PATH="/home/benjamin/.venv-sentinel"

echo "[sentinel] Creating virtual environment..."
python3 -m venv $VENV_PATH
source $VENV_PATH/bin/activate
pip install --upgrade pip setuptools wheel > /dev/null

echo "[sentinel] Installing Sentinel in venv..."
pip install -e . --quiet
deactivate

echo "[sentinel] Creating command-line wrappers in /usr/local/bin..."
for cmd in sentinel-daemon sentinel-request sentinel-status sentinel-monitor; do
    cat > /usr/local/bin/$cmd << EOF
#!/bin/bash
$VENV_PATH/bin/$cmd "\$@"
EOF
    chmod +x /usr/local/bin/$cmd
done
echo "  ✓ Created: sentinel-daemon, sentinel-request, sentinel-status, sentinel-monitor"

echo "[sentinel] Creating config directory..."
mkdir -p /etc/sentinel
if [[ ! -f /etc/sentinel/config.toml ]]; then
    cat > /etc/sentinel/config.toml << 'EOF'
[inference]
service = "ollama"
restart_delay = 5

[watchdog]
poll_interval = 5
owner_user = "benjamin"

[web]
enabled = true
port = 8765
host = "0.0.0.0"
EOF
    echo "[sentinel] Created default config at /etc/sentinel/config.toml"
else
    echo "[sentinel] Config already exists, skipping."
fi

echo "[sentinel] Configuring Ollama for multi-model concurrent inference..."
OLLAMA_OVERRIDE="/etc/systemd/system/ollama.service.d"
mkdir -p $OLLAMA_OVERRIDE
cat > $OLLAMA_OVERRIDE/sentinel.conf << 'EOF'
[Service]
Environment="OLLAMA_MAX_LOADED_MODELS=4"
Environment="OLLAMA_NUM_PARALLEL=8"
EOF
echo "  ✓ Set OLLAMA_MAX_LOADED_MODELS=4, OLLAMA_NUM_PARALLEL=8"

echo "[sentinel] Installing systemd service..."
cp sentinel.service /etc/systemd/system/sentinel.service
systemctl daemon-reload
systemctl enable sentinel
systemctl restart ollama
systemctl start sentinel

echo ""
echo "[sentinel] Installation complete!"
echo "  Sentinel venv:  $VENV_PATH"
echo "  Config:         /etc/sentinel/config.toml"
echo "  Status:         sentinel-status"
echo "  Monitor:        sentinel-monitor"
echo "  Wrap workloads: sentinel-request python your_script.py"
echo "  HTTP API:       http://$(hostname):8765/capacity"
echo "  Ollama proxy:   http://$(hostname):8765/v1/  (OpenAI-compatible)"
echo "  Logs:           journalctl -u sentinel -f"
echo ""
echo "  OpenClaw config:"
echo "    baseUrl: \"http://$(hostname):8765/v1\""
echo "    api:     \"openai-responses\""
echo ""
