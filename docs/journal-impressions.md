# Journal des impressions de référence

Impressions qui servent à mesurer l'effet des réglages (durée réelle relevée dans l'historique
Moonraker : `GET /server/history/list`). Même modèle, même profil → les écarts de durée et de qualité
viennent de la machine.

| Date | Pièce | Profils (Orca) | Estimation slicer | Durée d'impression réelle | Durée totale | Filament | Machine | Résultat |
|---|---|---|---|---|---|---|---|---|
| 14/11 | Cube de calibration 20 mm | PrusaSlicer, 300 mm/s, 6000 mm/s², 230 °C (banc) | — | 15 min 54 s | — | — | Banc WSL, shaper X 3hump_ei 97,2 / Y 56,0 | 20,03 × 20,30 mm, propre |
| 17/11 | 3DBenchy (stemfie) | `0.20mm rapide 300 (cube)` + `eSUN PLA+ rapide 230` | 26 min 38 s | **31 min 33 s** | 32 min 03 s | 3,72 m (≈ 11 g) | Mini-PC, même shaper, SCV 5, portique nivelé au journal (≈ 0,16 mm) | première couche très propre ; détails à noter |

Écart estimation/réel du Benchy : +18 % (angles freinés par `square_corner_velocity` 5 mm/s, petits
segments qui n'atteignent pas 300 mm/s). Comparer les futures impressions sur la **durée réelle**.
