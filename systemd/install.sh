#!/bin/bash
# Install systemd units for sof-profiles.
#   ./install.sh              — export-fire + userinfo-rcon (fresh host)
#   ./install.sh --rcon-only  — userinfo-rcon only (export-fire already running)
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
ENV=/etc/sof-profiles/env
RCON_ONLY=0

usage() {
  echo "Usage: $0 [--rcon-only]"
  echo "  (default)     install export-fire.service + userinfo-rcon.service"
  echo "  --rcon-only   install userinfo-rcon only; connect to existing export-fire"
}

for arg in "$@"; do
  case $arg in
    --rcon-only|--skip-export-fire) RCON_ONLY=1 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $arg"; usage; exit 1 ;;
  esac
done

sudo mkdir -p /etc/sof-profiles
if [[ ! -f $ENV ]]; then
  sudo cp "$DIR/sof-profiles.env.example" "$ENV"
  echo "Created $ENV — edit SOF_PROFILES, RCON_PASSWORD"
  if [[ $RCON_ONLY -eq 0 ]]; then
    echo "  (and SOF_USER_ROOT, SOF_EXPORT_FIRE for export-fire)"
  else
    echo "  (and EXPORT_FIRE_HOST/PORT if not 127.0.0.1:8765)"
  fi
  echo "Then re-run: $0${RCON_ONLY:+ --rcon-only}"
  exit 0
fi

if [[ $RCON_ONLY -eq 0 ]]; then
  sudo cp "$DIR/export-fire.service" "$DIR/userinfo-rcon.service" /etc/systemd/system/
  sudo systemctl daemon-reload
  echo "Installed export-fire + userinfo-rcon. Enable with:"
  echo "  sudo systemctl enable --now export-fire userinfo-rcon"
else
  sudo cp "$DIR/userinfo-rcon.service" /etc/systemd/system/
  sudo systemctl daemon-reload
  echo "Installed userinfo-rcon only (using existing export-fire). Enable with:"
  echo "  sudo systemctl enable --now userinfo-rcon"
fi
