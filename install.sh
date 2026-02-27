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
service = "tgi"
restart_delay = 5

[watchdog]
poll_interval = 5
ignored_processes = ["Xorg", "gnome-shell", "plasmashell"]

[web]
enabled = false
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
echo "[sentinel] Installation complete!"
echo "  Sentinel venv: $VENV_PATH"
echo "  Config: /etc/sentinel/config.toml"
echo "  Status: sentinel-status"
echo "  Wrap workloads: sentinel-request python your_script.py"
echo "  Logs: journalctl -u sentinel -f"
echo ""
