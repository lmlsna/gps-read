#!/bin/bash
set -e

# Check if running as root
if [ "$EUID" -eq 0 ]; then
    echo "Error: Please run this script as a regular user with sudo privileges, not as root."
    exit 1
fi

# Check if user has sudo privileges
if ! sudo -n true 2>/dev/null; then
    echo "This script requires sudo privileges. You may be prompted for your password."
fi

echo "==> Installing system packages..."
sudo apt update
sudo apt install -y python3-venv python3-pip

echo "==> Creating virtual environment..."
project_dir="$(dirname "$0")"
python3 -m venv "${project_dir}/.venv"

echo "==> Activating virtual environment and installing Python packages..."
source "${project_dir}/.venv/bin/activate"
pip install --upgrade pip
pip install -r "${project_dir}/requirements.txt"

echo "==> Adding user to dialout group..."
sudo usermod -aG dialout "$USER"

echo ""
echo "==> Installation complete!"
echo ""
echo "IMPORTANT: You need to log out and log back in (or run 'newgrp dialout')"
echo "for the group membership change to take effect."
echo ""
echo "To use the GPS reader, activate the virtual environment first:"
echo "  source ${project_dir}/.venv/bin/activate"
echo "  ./gps_read.py --help"
echo ""
