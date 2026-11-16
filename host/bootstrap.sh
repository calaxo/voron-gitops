#!/usr/bin/env bash
# =============================================================================
# Bootstrap de l'hôte Klipper (Ubuntu Server 24.04) — idempotent, à lancer en root
# depuis un clone du dépôt :  sudo ./host/bootstrap.sh
# Ne contient AUCUNE valeur propre à la machine.
# =============================================================================
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
LOCAL_CONFIG_DIR="${LOCAL_CONFIG_DIR:-/etc/voron-gitops/klipper-local}"

[ "$(id -u)" -eq 0 ] || { echo "À lancer en root (sudo)." >&2; exit 1; }

echo "==> CAN (systemd-networkd)"
rm -f /etc/systemd/network/10-can-gs_usb.link
install -m 0644 "$HERE/udev/80-can-txqueue.rules" /etc/udev/rules.d/
udevadm control --reload
install -m 0644 "$HERE/systemd/network/80-can0.network"   /etc/systemd/network/
systemctl enable --now systemd-networkd
# Applique le .link à une interface déjà présente (sinon : débrancher/rebrancher l'USB).
udevadm trigger --action=add --subsystem-match=net || true
networkctl reload || true

echo "==> Gouverneur CPU performance"
install -m 0644 "$HERE/systemd/system/cpu-governor-performance.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now cpu-governor-performance.service

echo "==> sysctl"
install -m 0644 "$HERE/sysctl/90-klipper-host.conf" /etc/sysctl.d/
sysctl --system >/dev/null

echo "==> Dossier des valeurs locales (UUID CAN)"
install -d -m 0755 "$LOCAL_CONFIG_DIR"
if [ ! -f "$LOCAL_CONFIG_DIR/mcu.cfg" ]; then
  install -m 0644 "$HERE/../config/klipper/local/mcu.cfg.example" "$LOCAL_CONFIG_DIR/mcu.cfg"
  echo "   -> $LOCAL_CONFIG_DIR/mcu.cfg créé depuis le modèle : RENSEIGNER LES UUID."
fi

echo "==> Durcissement (mises à jour auto, télémétrie, paquets inutiles, watchdog…)"
bash "$HERE/hardening/harden.sh"

echo "==> Komodo Periphery (conteneur, /srv/komodo)"
install -d -m 0755 /srv/komodo
install -m 0644 "$HERE/komodo/compose.yaml" /srv/komodo/compose.yaml
if [ ! -f /srv/komodo/compose.env ]; then
  install -m 0600 "$HERE/komodo/compose.env.example" /srv/komodo/compose.env
  echo "   -> /srv/komodo/compose.env créé : RENSEIGNER PERIPHERY_PASSKEYS et PERIPHERY_ALLOWED_IPS,"
  echo "      puis : cd /srv/komodo && docker compose -p komodo up -d"
elif command -v docker >/dev/null && ! grep -q '^PERIPHERY_PASSKEYS=CHANGE_ME' /srv/komodo/compose.env; then
  (cd /srv/komodo && docker compose -p komodo up -d)
fi

echo "==> Vérifications"
ip -details link show can0 2>/dev/null | grep -E "qlen|bitrate" || echo "   can0 absent (Octopus branchée ?)"
cat /sys/devices/system/cpu/cpufreq/policy0/scaling_governor 2>/dev/null || true
command -v docker >/dev/null || echo "   Docker absent : voir host/README.md"
echo "OK."
