# Séquences de dock StealthChanger (T0)

> **À VÉRIFIER.** Séquences mesurées à la main, en déplaçant la tête pas à pas.
> Z = hauteur **réelle** buse/plateau, posée par `SET_Z_MANUAL` (pas encore de homing Z).
> Toute erreur de Z se répercute sur le dock : revérifier après l'arrivée de l'OptoTap.

Implémentation : [`config/klipper/toolchanger.cfg`](../config/klipper/toolchanger.cfg)
(`TC_DOCK`, `TC_PICKUP`, `T0`, `TC_WIPE`, `TC_SET_STATE`).

## Zone interdite (keep-out)

- **Y -0,5 … 67,5 au-dessus de Z ≈ 130** : docks à l'avant, pas encore d'ombilicaux.
- On n'y entre que par les séquences, toujours depuis **Y 70**.
- Les macros refusent de démarrer si la tête est déjà dans la zone (position incertaine).

## Dépose (DOCK) — outil monté → navette vide

| # | Mouvement | Vitesse |
|---|---|---|
| 0 | Approche hors zone : Y 70, puis Z 300,9, puis X 38,8 | travel |
| 1 | Y 63,5 (X 38,8, Z 300,9) | 50 mm/s |
| 2 | Y 10 | 10 mm/s |
| 3 | Y -0,24 | 2 mm/s |
| 4 | Z 289,0 (l'outil reste sur le dock) | 2 mm/s |
| 5 | Y 10 | 2 mm/s |
| 6 | Y 70 | 20 mm/s |

## Prise (PICKUP) — navette vide → outil monté

| # | Mouvement | Vitesse |
|---|---|---|
| 0 | Approche hors zone : Y 70, puis Z 289,0, puis X 38,8 | travel |
| 1 | Y 10 | 20 mm/s |
| 2 | Y -0,24 | 2 mm/s |
| 3 | Z 302,9 (la navette soulève l'outil) | 2 mm/s |
| 4 | Y 10 | 2 mm/s |
| 5 | Y 70 | 20 mm/s |

## Brosse

- Contact du haut : **Z 300,4** en **X 39,17 Y 10,81**.
- Essuyage à **0,25 mm au-dessus** → Z 300,65.
- `TC_WIPE` est **désactivé par défaut** (`variable_wipe_enabled: False`) : course et
  nombre de passes non mesurés.

## Contraintes de config

| Contrainte | Valeur requise | Valeur actuelle |
|---|---|---|
| `stepper_z.position_max` | ≥ 302,9 | 325 (Z réel max ≈ 333) |
| `stepper_y.position_min` | ≤ -0,24 | -0,5 |

## Procédure de premier essai

1. `G28` (XY), poser Z : buse au papier puis `SET_Z_MANUAL Z=0.1`.
2. Vérifier visuellement l'état, puis `TC_SET_STATE TOOL=0`.
3. `M220 S50` (vitesse réduite), main sur l'arrêt d'urgence.
4. `TC_DOCK`, observer ; puis `TC_PICKUP`.
5. Corriger les variables de `_TC_CFG` si besoin, commit, redéploiement.
