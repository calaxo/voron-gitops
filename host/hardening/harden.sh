#!/usr/bin/env bash
# =============================================================================
# Durcissement de l'hôte Klipper (Ubuntu Server 24.04) — idempotent, en root.
# Appelé par host/bootstrap.sh ; peut aussi se relancer seul :
#   sudo ./host/hardening/harden.sh
# Objectifs :
#   1. rien ne doit pouvoir redémarrer klippy ou la machine en pleine impression
#      (mises à jour auto, needrestart, veille, snap refresh) ;
#   2. aucune télémétrie / remontée vers des services externes (apport, motd-news,
#      annonces Ubuntu Pro, LVFS, snap store, landscape) ;
#   3. rien d'inutile sur un hôte d'impression dédié.
# Ne contient AUCUNE valeur propre à la machine.
# =============================================================================
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
[ "$(id -u)" -eq 0 ] || { echo "À lancer en root (sudo)." >&2; exit 1; }
export DEBIAN_FRONTEND=noninteractive

echo "==> Paquets inutiles ou bavards (purge)"
# - unattended-upgrades / needrestart : mises à jour et redémarrages de services automatiques
# - snapd : rafraîchissements automatiques + store Canonical (aucun snap utilisé)
# - apport : rapports de plantage (Launchpad) ; landscape-common, ubuntu-pro (annonces) : Canonical
# - fwupd : interroge LVFS ; firmware mis à jour à la main
# - modemmanager, multipath-tools, open-iscsi, udisks2, packagekit, lxd-installer, byobu :
#   sans objet sur ce mini-PC (pas de modem, de SAN, de bureau, de LXD)
PURGE=(unattended-upgrades needrestart snapd apport apport-symptoms landscape-common
       fwupd modemmanager multipath-tools open-iscsi udisks2 packagekit lxd-installer byobu
       update-notifier-common popularity-contest ubuntu-report whoopsie)
PRESENT=()
for p in "${PURGE[@]}"; do dpkg -s "$p" >/dev/null 2>&1 && PRESENT+=("$p"); done
if [ ${#PRESENT[@]} -gt 0 ]; then
  # La purge emporte les métapaquets ubuntu-server(-minimal) : on marque d'abord
  # leurs dépendances comme installées à la main, pour que l'autoremove ne retire
  # que les dépendances des paquets purgés (pas openssh-server, curl, etc.).
  for m in ubuntu-server ubuntu-server-minimal ubuntu-minimal ubuntu-standard; do
    dpkg -s "$m" >/dev/null 2>&1 || continue
    apt-cache depends --no-suggests "$m" 2>/dev/null \
      | awk '/Depends:|Recommends:/{print $2}' | grep -v '^<' \
      | xargs -r dpkg-query -W -f='${Package} ${db:Status-Status}\n' 2>/dev/null \
      | awk '$2=="installed"{print $1}' \
      | grep -vxF -f <(printf '%s\n' "${PURGE[@]}") \
      | xargs -r apt-mark manual >/dev/null || true
  done
  apt-get purge -y -q "${PRESENT[@]}"
  apt-get autoremove --purge -y -q
fi
rm -rf /snap /var/snap /var/lib/snapd /root/snap /home/*/snap
# Empêche une réinstallation de snapd par dépendance.
cat > /etc/apt/preferences.d/pas-de-snapd <<'PREF'
Package: snapd
Pin: release a=*
Pin-Priority: -10
PREF

echo "==> Mises à jour automatiques : désactivées"
install -m 0644 "$HERE/apt/99-pas-de-maj-auto" /etc/apt/apt.conf.d/
rm -f /etc/apt/apt.conf.d/20auto-upgrades
for t in apt-daily.timer apt-daily-upgrade.timer motd-news.timer ua-timer.timer \
         update-notifier-download.timer update-notifier-motd.timer apport-autoreport.timer \
         fwupd-refresh.timer; do
  systemctl disable --now "$t" 2>/dev/null || true
  systemctl mask "$t" 2>/dev/null || true
done

echo "==> Télémétrie / messages Canonical"
[ -f /etc/default/motd-news ] && sed -i 's/^ENABLED=.*/ENABLED=0/' /etc/default/motd-news
command -v pro >/dev/null && pro config set apt_news=false >/dev/null 2>&1 || true
# Messages ESM/Pro injectés dans apt (hook qui contacte esm.ubuntu.com).
rm -f /etc/apt/apt.conf.d/20apt-esm-hook.conf
# cloud-init n'a servi qu'à l'installation.
touch /etc/cloud/cloud-init.disabled 2>/dev/null || true

echo "==> Veille : jamais"
systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target >/dev/null

echo "==> Docker : live-restore (les conteneurs survivent à un redémarrage de dockerd)"
install -d /etc/docker
if ! cmp -s "$HERE/docker/daemon.json" /etc/docker/daemon.json; then
  install -m 0644 "$HERE/docker/daemon.json" /etc/docker/daemon.json
  systemctl reload docker 2>/dev/null || systemctl restart docker
fi
# Pas de montée de version Docker/containerd par un « apt upgrade » distrait.
apt-mark hold docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin >/dev/null 2>&1 || true

echo "==> journald persistant borné"
install -d /etc/systemd/journald.conf.d
install -m 0644 "$HERE/journald/50-voron.conf" /etc/systemd/journald.conf.d/
systemctl restart systemd-journald

echo "==> SSH : clés uniquement, pas de root"
install -m 0644 "$HERE/ssh/50-durcissement.conf" /etc/ssh/sshd_config.d/
sshd -t && systemctl reload ssh

echo "==> Chien de garde matériel"
install -d /etc/systemd/system.conf.d
install -m 0644 "$HERE/systemd/50-watchdog.conf" /etc/systemd/system.conf.d/
systemctl daemon-reexec

echo "==> État"
ls /dev/watchdog0 >/dev/null 2>&1 && echo "   watchdog: $(cat /sys/class/watchdog/watchdog0/identity 2>/dev/null)" || echo "   watchdog: absent"
apt-mark showhold | tr '\n' ' '; echo
echo "OK."
