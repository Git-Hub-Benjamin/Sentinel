#!/usr/bin/env bash
set -e

if [[ $EUID -ne 0 ]]; then
    echo "Run as root: sudo ./install.sh"
    exit 1
fi

echo "[sentinel] Installing dependencies..."
pip install -e . --quiet

echo "[sentinel] Creating config directory..."
mkdir -p /etc/sentinel
if [[ ! -f /etc/sentinel/config.toml ]]; then
    cat > /etc/sentinel/config.toml << 'EOF'
[inference]
service = "ollama"
restart_delay = 3

[watchdog]
poll_interval = 5
ignored_processes = ["Xorg", "gnome-shell", "plasmashell"]

[web]
enabled = true
port = 8765
EOF
    echo "[sentinel] Created default config at /etc/sentinel/config.toml"
else
    echo "[sentinel] Config already exists, skipping."
fi

echo "[sentinel] Installing systemd service..."
cp sentinel.service /etc/systemd/system/sentinel.service
systemctl daemon-reload
systemctl enable sentinel
systemctl start sentinel

echo ""
echo "[sentinel] Installation complete."
echo "  Status:  sentinel-status"
echo "  Wrap workloads with: sentinel-request python your_script.py"
echo "  Logs:    journalctl -u sentinel -f"
