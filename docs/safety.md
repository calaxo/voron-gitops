# Sécurité

## Électrique / thermique

- **Plateau 750 W secteur via SSR** + **fusible thermique 128 °C en série** (coupure
  matérielle indépendante de Klipper). `max_temp: 100`, `max_power: 0.6`.
- **Sonde radiateur** (NTC sur le radiateur du Rapido) avec `max_temp: 65` : si le
  ventilo hotend lâche, Klipper s'arrête et coupe toutes les chauffes.
- `verify_heater` actif sur buse et plateau (détection sonde décrochée / chauffe inefficace).
- `heater_fan` hotend à partir de 50 °C ; ventilos de baie automatiques (`controller_fan`).
- `idle_timeout` 30 min : chauffes coupées, moteurs désactivés.
- Ne jamais laisser chauffer sans surveillance tant que l'enceinte n'est pas finie.

## Mouvement

- **Pas de homing Z** (OptoTap à venir) : `G28 Z` est refusé, Z se pose à la main
  (`SET_Z_MANUAL`). Une erreur de Z = crash buse/plateau ou dock. Vérifier à chaque fois.
- `enable_force_move: True` uniquement pour `SET_KINEMATIC_POSITION` ; à désactiver
  dès que Z est référencé par la sonde.
- Homing XY à **courant réduit** (0,6 A) : un capteur raté fait sauter le moteur au
  lieu de forcer contre le cadre.
- **Zone de dock** (Y < 67,5 au-dessus de Z ≈ 130) : seules les macros de dock y
  entrent ; PAUSE et PRINT_END parquent à l'arrière (Y 345).
- Premiers essais de dock : `M220 S50`, main sur l'arrêt d'urgence.

## Logiciel / GitOps

- **Jamais de déploiement automatique** (webhook, poll, auto-update Komodo) : un
  redéploiement redémarre klippy et tue l'impression en cours. Déployer imprimante
  au repos.
- Version Klipper hôte **= version firmware MCU** (commit `0cee841`). Changer l'une
  sans l'autre → « MCU protocol error » au démarrage.
- Config montée en lecture seule : pas de modification silencieuse depuis l'UI.
- Bus CAN : 1 Mbit/s, `txqueuelen 128`, pas d'autosuspend USB sur le pont.

## Réseau

- Mainsail/Moonraker **LAN uniquement** : filtrage nginx (plages privées) +
  `trusted_clients` Moonraker sur plages privées. Ne jamais publier sur Internet ;
  accès distant = VPN.
- Pas d'authentification Moonraker : toute machine du LAN peut piloter l'imprimante.
  Activer `[authorization] force_logins` si le LAN n'est pas de confiance.
