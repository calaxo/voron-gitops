# Historique de calibration

Valeurs validées sur la machine (banc puis machine complète, novembre 2026).
Les valeurs **actives** sont dans `config/klipper/` ; ce fichier garde le *pourquoi*.
SAVE_CONFIG est inopérant (config en lecture seule) : chaque résultat est recopié à la
main dans le `.cfg` concerné puis commité.

## Mouvement

| Élément | Valeur | Méthode / remarque |
|---|---|---|
| `rotation_distance` X/Y | **32** | Poulies A/B 16 dents : 100 mm demandés = 80 mm réels avec 40 |
| `run_current` X/Y | **1,2 A** (homing à 0,6 A) | 0,8 A → décalage de couche sur un Benchy ; moteurs 17HS19 2 A |
| `run_current` Z | 0,8 A | |
| Sens Z | Z1 et Z3 inversés | Constaté portique entier : positif = le portique monte |
| Fins de course | X `^PG6` actif haut, Y `^!PG9` actif bas | Aimants collés de côté, volontairement |
| `position_min` X / Y | 3 / -0,5 | Butée physique avant Y ≈ -0,85 |
| Z max réel | ≈ 333 mm | `position_max` = 325 (marge) |

## Vitesse / accélération (TEST_SPEED)

| Essai | Résultat |
|---|---|
| ≤ 500 mm/s, ≤ 15000 mm/s² | aucun pas perdu |
| 600 mm/s, 20000 mm/s² | décrochage |
| **Limite de travail** | **400 mm/s / 10000 mm/s²** (moteurs) |
| Cube de calibration | OK à 300 mm/s / 6000 mm/s² |

## Input shaper (ADXL345 de l'EBB, `axes_map: z,y,x`)

| Axe | Shaper | Fréquence | Remarque |
|---|---|---|---|
| X | `3hump_ei` | 97,2 Hz | pic propre ~50 Hz |
| Y | `3hump_ei` | 56,0 Hz | pic ~27 Hz + composante Z 30-45 Hz |

Sur Y, la tête bascule : l'outil n'est tenu que par les pions (aimants N52 pas encore
posés). Accélération recommandée par le shaper sur Y ≈ **2000 mm/s²** → `max_accel: 2000`.
**À refaire après pose des aimants N52.**

## Chauffes (PID_CALIBRATE)

| Chauffe | Kp | Ki | Kd | Conditions |
|---|---|---|---|---|
| Plateau (750 W, `max_power` 0,6) | 29,375 | 1,106 | 194,978 | 60 °C |
| Buse (Rapido Plus 2 HF, PT1000/MAX31865) | 19,299 | 5,146 | 18,094 | |

## Filament : eSUN PLA+ (1,75 mm)

| Paramètre | Valeur |
|---|---|
| Buse / plateau (normal) | 215 °C / 60 °C |
| Buse (rapide) | 230 °C |
| Pressure advance | 0,040 (test en lignes 0 → 0,07, 9ᵉ ligne, à 215 °C) |
| Ventilo pièce | 100 % à partir de la couche 2-3 |
| Rétraction | 0,8 mm à 35 mm/s |
| Vitesse / accel validées | 300 mm/s / 6000 mm/s² (cube) |
| Accel conseillée (shaper Y) | ≈ 2000 mm/s² tant que pas d'aimants N52 |

Démarrage slicer :
`PRINT_START BED=[first_layer_bed_temperature] EXTRUDER=[first_layer_temperature]`

## À faire

- [ ] Aimants N52 → nouveau `SHAPER_CALIBRATE`, relever `max_accel`.
- [ ] Sonde OptoTap → homing Z, QGL, `z_offset`.
- [ ] Calibration `rotation_distance` extrudeur (mesure 100 mm).
- [ ] PID buse refait à la température de travail (230 °C) si oscillations.
