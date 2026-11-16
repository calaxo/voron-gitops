# Hôte Klipper — préparation

Mini-PC (i5-12400, 64 Go, NVMe) sous **Ubuntu Server 24.04 LTS**, noyau standard,
Docker, piloté par **Komodo** (agent Periphery). Aucune valeur propre à la machine dans
ce dossier : tout ce qui identifie l'hôte reste hors du dépôt.

## 1. Installation de l'OS

- Modèle d'autoinstall : [`autoinstall/user-data.example`](autoinstall/user-data.example).
  Le copier **hors du dépôt**, remplacer les `CHANGE_ME` (utilisateur, hash
  `mkpasswd -m sha-512`, clé SSH publique, fuseau), puis le placer sur la clé USB
  d'installation avec un `meta-data` vide.
- IP fixe : réservation DHCP côté routeur plutôt qu'une IP en dur dans netplan.
- Pas de noyau PREEMPT_RT (voir le README racine).

## 2. Bootstrap (fichiers système)

```sh
git clone <URL_DU_DEPOT> && cd voron-gitops
sudo ./host/bootstrap.sh
```

Le script installe (idempotent) :

| Fichier | Destination | Rôle |
|---|---|---|
| `udev/80-can-txqueue.rules` | `/etc/udev/rules.d/` | `tx_queue_len=128` sur toute interface CAN |
| `systemd/network/80-can0.network` | `/etc/systemd/network/` | `can0` UP à `BitRate=1M` |
| `systemd/system/cpu-governor-performance.service` | `/etc/systemd/system/` | gouverneur `performance` |
| `sysctl/90-klipper-host.conf` | `/etc/sysctl.d/` | `vm.swappiness=10` |
| `config/klipper/local/mcu.cfg.example` | `/etc/voron-gitops/klipper-local/mcu.cfg` | UUID CAN (à remplir) |

Vérifier le bus :

```sh
ip -details -statistics link show can0   # state UP, bitrate 1000000, qlen 128
```

Récupérer les UUID (Klipper arrêté) puis les écrire dans
`/etc/voron-gitops/klipper-local/mcu.cfg` :

```sh
docker run --rm --network host --entrypoint /opt/venv/bin/python \
  mkuf/klipper:v0.13.0-820-g0cee841e3 klipper/scripts/canbus_query.py can0
```

Autosuspend USB : l'autoinstall ajoute `usbcore.autosuspend=-1` au noyau. Sur une
installation manuelle, l'ajouter dans `GRUB_CMDLINE_LINUX_DEFAULT` puis `update-grub`.

## 3. Docker

Dépôt officiel Docker (pas le paquet `docker.io` d'Ubuntu, pour avoir le plugin compose
à jour) — <https://docs.docker.com/engine/install/ubuntu/> :

```sh
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
  https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo $VERSION_CODENAME) stable" \
  | sudo tee /etc/apt/sources.list.d/docker.list
sudo apt update
sudo apt install docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
```

Recommandé : rotation des logs par défaut dans `/etc/docker/daemon.json`
(`"log-driver": "json-file", "log-opts": {"max-size": "10m", "max-file": "3"}`).

## 4. Komodo Periphery

Agent en **conteneur** (même modèle que les autres hôtes) : [`komodo/compose.yaml`](komodo/compose.yaml),
image épinglée par digest, racine `/srv/komodo`. `bootstrap.sh` pose les fichiers ; le secret
reste hors dépôt :

```sh
sudo nano /srv/komodo/compose.env     # PERIPHERY_PASSKEYS = KOMODO_PASSKEY du Core, ALLOWED_IPS = IP du Core
cd /srv/komodo && sudo docker compose -p komodo up -d
```

Puis déclarer le serveur dans Komodo Core : adresse `https://<IP de l'hôte>:8120`,
*Insecure TLS* coché (certificat auto-signé), **Auto Prune décoché**.

## 5. Durcissement — [`hardening/harden.sh`](hardening/harden.sh)

Lancé par `bootstrap.sh` (idempotent, relançable seul). Règle : **rien ne doit pouvoir
redémarrer klippy ou la machine pendant une impression**, et **rien ne parle à l'extérieur
sans qu'on le demande**.

| Mesure | Pourquoi |
|---|---|
| purge `unattended-upgrades`, `needrestart` + `apt/99-pas-de-maj-auto` + timers `apt-daily*` masqués | une montée de containerd/Docker/noyau en pleine impression = conteneurs ou machine redémarrés |
| `apt-mark hold` des paquets Docker | pas de montée Docker par un `apt upgrade` distrait |
| Docker `live-restore` | si dockerd redémarre quand même, les conteneurs continuent |
| purge `snapd` (+ pin -10) | rafraîchissements automatiques, store Canonical |
| purge `apport`, `landscape-common`, `fwupd`, `popularity-contest`, `ubuntu-report`, `whoopsie` ; motd-news, annonces Pro (`apt_news`), hook ESM et `ua-timer` coupés | télémétrie / appels sortants Canonical, LVFS |
| purge `modemmanager`, `multipath-tools`, `open-iscsi`, `udisks2`, `packagekit`, `lxd-installer`, `byobu` | inutiles sur ce mini-PC |
| cloud-init désactivé | n'a servi qu'à l'installation |
| veille masquée, `usbcore.autosuspend=-1`, `pcie_aspm=off` (noyau) | coupures USB-CAN / carte réseau |
| watchdog matériel 60 s (`systemd/50-watchdog.conf`), `kernel.panic = 10` | redémarrage seul si l'hôte se fige (personne à côté) |
| journald persistant borné à 500 Mo | diagnostic après plantage sans remplir le disque |
| SSH : clés uniquement, root interdit | accès admin = utilisateur + sudo |

Restent volontairement : `avahi-daemon` (`printer.local`), `smartmontools`, `thermald`,
`systemd-timesyncd` (heure), `sysstat` (local). Pas d'`ufw` : Docker contourne ses règles pour
les ports publiés ; le filtrage est fait par nginx (plages privées) et Periphery (`ALLOWED_IPS`).

## 6. Mises à jour (à la main, imprimante au repos)

```sh
sudo apt update && apt list --upgradable
sudo apt-mark unhold docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo apt full-upgrade
sudo ./host/hardening/harden.sh        # remet les hold et purge ce qu'une dépendance aurait réinstallé
sudo reboot                            # si noyau / libc
```

Images des stacks : changer le tag + digest dans le dépôt, puis *Deploy* dans Komodo.

## 7. Tout recréer

1. Clé d'autoinstall (§ 1) → Ubuntu + Docker.
2. `git clone` du dépôt, `sudo ./host/bootstrap.sh`.
3. Remplir `/srv/komodo/compose.env` (passkey) et `/etc/voron-gitops/klipper-local/mcu.cfg` (UUID CAN).
4. Komodo : déclarer le serveur, *Deploy* du stack klipper.
