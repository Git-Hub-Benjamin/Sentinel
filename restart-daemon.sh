#!/bin/bash
# Restart the Sentinel daemon (requires sudo)
echo "Restarting Sentinel daemon..."
sudo systemctl restart sentinel
sleep 2
echo "Daemon restarted. Checking status..."
systemctl status sentinel
