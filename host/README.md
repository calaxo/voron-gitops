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
| `systemd/network/10-can-gs_usb.link` | `/etc/systemd/network/` | `TxQueueLength=128` sur toute interface du pilote `gs_usb` |
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

L'instance Komodo Core existe déjà ; il suffit d'installer l'agent sur l'hôte :

```sh
# Script officiel (vérifier la commande dans la doc de la version de Komodo utilisée)
curl -sSL https://raw.githubusercontent.com/moghtech/komodo/main/scripts/setup-periphery.py | sudo python3
```

- Configuration : modèle [`komodo/periphery.config.toml.example`](komodo/periphery.config.toml.example)
  → `/etc/komodo/periphery.config.toml` ; `passkeys` = secret de Komodo Core
  (**jamais dans git**), `allowed_ips` = IP de Komodo Core.
- `sudo systemctl enable --now periphery`, puis déclarer le serveur dans Komodo Core.
- Les stacks sont ensuite décrits dans [`../komodo/`](../komodo/).
