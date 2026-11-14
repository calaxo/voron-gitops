# voron-gitops

Configuration GitOps d'un hôte d'impression 3D **Klipper** : Voron 2.4 350 +
StealthChanger, piloté par **Komodo** qui déploie des stacks Docker depuis ce dépôt.

> Dépôt **public** : aucune donnée personnelle ni propre à la machine (IP, noms d'hôte,
> domaines, UUID CAN, numéros de série, clés). Voir [Confidentialité](#confidentialité).

## Matériel

| Élément | Détail |
|---|---|
| Imprimante | Voron 2.4 350, StealthChanger (1 outil pour l'instant) |
| Tête | StealthBurner + Clockwork 2 + Phaetus Rapido Plus 2 HF (PT1000) |
| Carte tête | BTT EBB SB2209 CAN (STM32G0B1), ADXL345 intégré |
| Carte principale | BTT Octopus Pro H723 (TMC5160T Pro SPI), **pont USB-CAN** |
| Bus CAN | 1 Mbit/s, `txqueuelen 128` |
| Firmware MCU | Klipper commit `0cee841` |
| Hôte | Mini-PC i5-12400 / 64 Go / NVMe, Ubuntu Server 24.04 LTS, Docker |
| Interface | Mainsail + Moonraker |

## Architecture

```
            git push                         ┌──────────── Hôte Ubuntu 24.04 ────────────┐
 dépôt ───────────────► Komodo Core ──────►  │ Komodo Periphery                          │
 (ce repo)            (déploiement manuel)   │   └─ docker compose  stacks/klipper       │
                                             │       ├─ klipper    (network_mode: host) ─┼─ can0 ─► Octopus (pont) ─► EBB
                                             │       │     └─ klipper.sock (volume run)   │
                                             │       ├─ moonraker  (réseau du stack)      │
                                             │       ├─ mainsail   (statique)             │
                                             │       └─ proxy nginx :80 ◄── LAN           │
                                             │   systemd-networkd : can0 1M, qlen 128     │
                                             └────────────────────────────────────────────┘
```

- **Komodo → stacks Docker** : Komodo Core (instance existante) commande l'agent
  Periphery de l'hôte, qui clone ce dépôt et lance `stacks/klipper/compose.yaml`.
  Modèle de Resource Sync : [`komodo/resources.toml.example`](komodo/resources.toml.example).
  Pas de webhook ni d'auto-update : un redéploiement redémarre klippy.
- **Images [prind](https://github.com/mkuf/prind)** (`mkuf/klipper`, `mkuf/moonraker`)
  + image officielle Mainsail. Versions figées par **tag + digest**.
- **Klipper épinglé sur le firmware** : prind publie une image par `git describe` amont ;
  `mkuf/klipper:v0.13.0-820-g0cee841e3` = commit `0cee841`, identique aux MCU.
  Reflasher les MCU sur un autre commit ⇒ changer ce tag dans le même commit.
- **CAN et réseau hôte** : l'interface SocketCAN `can0` n'existe que dans le namespace
  réseau de l'hôte → le conteneur Klipper est en `network_mode: host` (ni `privileged`
  ni `/dev` nécessaires). Moonraker parle à Klipper via le socket Unix
  `klipper.sock` d'un volume partagé (`run`) et reste sur le réseau du stack.
- **Proxy nginx** : sert Mainsail et relaie l'API/websocket Moonraker sur un seul port
  (comme Traefik dans prind), filtré aux plages privées.
- **Config en lecture seule** : `config/` est monté `:ro`. Git est la seule source de
  vérité ; `SAVE_CONFIG` ne peut pas écrire → recopier les résultats (PID, shaper…)
  dans le `.cfg`, commit, redéploiement.
- **Pourquoi pas de noyau PREEMPT_RT** : Klipper planifie les pas *à l'avance* et ce
  sont les MCU qui les exécutent à l'heure ; l'hôte n'a qu'à rester quelques centaines
  de ms en avance. Un i5-12400 peu chargé avec le gouverneur `performance` le fait très
  largement. RT = noyau hors support standard Ubuntu, pilotes/Docker moins testés, gain nul.

## Arborescence

```
config/klipper/        printer.cfg + includes (mcu, kinematics, tmc, homing, extruder,
                       heaters, fans, leds, sensors, display, adxl, input_shaper, probe,
                       client, macros, toolchanger) ; local/mcu.cfg.example
config/moonraker/      moonraker.conf
stacks/klipper/        compose.yaml + nginx.conf (actif)
stacks/camera/         ustreamer — squelette désactivé (TODO)
stacks/obico/          moonraker-obico — squelette désactivé (TODO)
host/                  bootstrap de l'hôte : CAN, gouverneur CPU, sysctl, autoinstall, Komodo
komodo/                modèle de Resource Sync
docs/                  historique de calibration, séquences de dock, sécurité
```

## Démarrage rapide

1. **Hôte** : installer Ubuntu (modèle `host/autoinstall/user-data.example`), puis
   `sudo ./host/bootstrap.sh` et Docker + Komodo Periphery — voir [host/README.md](host/README.md).
2. **UUID CAN** : `canbus_query.py` (commande dans host/README.md), puis remplir
   `/etc/voron-gitops/klipper-local/mcu.cfg` (modèle `config/klipper/local/mcu.cfg.example`).
3. **Komodo** : créer le stack depuis `komodo/resources.toml.example` (nom du serveur,
   URL du dépôt), puis *Deploy*.
4. Ouvrir `http://printer.local` (ou l'IP de l'hôte) → Mainsail.
5. Premier démarrage : `G28` (XY seulement), Z au papier puis `SET_Z_MANUAL Z=0.1`.

Test local sans Komodo :

```sh
cp .env.example stacks/klipper/.env
docker compose -f stacks/klipper/compose.yaml config   # validation
docker compose -f stacks/klipper/compose.yaml up -d    # sur l'hôte seulement
```

Après une modification de `.cfg` : *Pull* dans Komodo puis `RESTART` (ou
`FIRMWARE_RESTART`) dans Mainsail — `docker compose up` ne recrée pas un conteneur dont
la définition n'a pas changé.

## État / TODO

- [ ] **Homing Z** : sonde OptoTap (`probe.cfg`), puis QGL ; retirer `SET_Z_MANUAL`.
- [ ] Aimants N52 sur l'outil → nouveau `SHAPER_CALIBRATE`, relever `max_accel`.
- [ ] Vérifier en réel les séquences de dock (`docs/dock-sequences.md`).
- [ ] Caméra (`stacks/camera`) puis Obico auto-hébergé (`stacks/obico`).
- [ ] Multi-outils : passer à klipper-toolchanger.

## Confidentialité

- Aucune IP réelle, sous-réseau, nom d'hôte, domaine, e-mail, nom, UUID CAN ni numéro
  de série dans le dépôt. Exemples : `printer.local`, `192.0.2.x` (RFC 5737), `CHANGE_ME`.
- Valeurs propres à la machine : fichiers hors dépôt (`/etc/voron-gitops/…`) ou
  variables d'environnement du stack Komodo ; modèles `*.example` commités.
- Secrets (passkey Komodo, token Obico…) : jamais dans git.
- Avant chaque push : `git grep -nE '([0-9]{1,3}\.){3}[0-9]{1,3}'` et relire les
  diffs de `config/`.

## Licence

[MIT](LICENSE).
