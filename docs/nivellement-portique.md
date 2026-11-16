# Nivellement manuel du portique (sans sonde)

Tant qu'il n'y a pas d'OptoTap, le portique de la 2.4 est mis à plat **à la feuille** et
**moteur par moteur**. Ce fichier garde les mesures et les coefficients pour ne pas les
redécouvrir à chaque fois.

## Carte des moteurs et unités

| Coin | Moteur | Point de mesure (X, Y) |
|---|---|---|
| avant gauche | `stepper_z` | 40, 30 |
| arrière gauche | `stepper_z1` | 40, 310 |
| arrière droit | `stepper_z2` | 310, 310 |
| avant droit | `stepper_z3` | 310, 30 |
| centre (référence du zéro) | — | 175, 175 |

- `rotation_distance 40`, `gear_ratio 80:16`, 16 micropas → **400 pas par mm**, positif = le portique **monte**.
- Les moteurs (coins du portique, ≈ X −60/410, Y −10/420) sont plus écartés que les points mesurés :
  une montée de moteur fait bouger le point mesuré **moins** que la montée elle-même.

## Méthode qui marche

1. X et Y référencés **séparément** (`G28 X`, puis `G28 Y`), sous surveillance.
2. Centre : `MANUAL_PROBE`, feuille de journal (≈ 0,07-0,1 mm), `ACCEPT`, puis zéro posé avec
   `SET_KINEMATIC_POSITION Z=0.1 SET_HOMED=Z`.
3. Les 4 coins : remonter (+10 mm), aller au point, `MANUAL_PROBE` en partant **haut**, descendre
   un appui à la fois. Ne jamais supposer la hauteur d'un coin à partir des autres.
4. Écart d'un coin = Z au contact − 0,1. Correction **en montée uniquement** : le coin au contact le
   plus bas sert de référence ; les autres montent de `(écart − écart_min) × facteur`.
5. Appliquer par `FORCE_MOVE STEPPER=… DISTANCE=… VELOCITY=2-3`, pas de 0,5 à 1 mm **en alternant**
   les coins, buse au centre et en hauteur.
6. Reprendre le zéro au centre (toute correction déplace aussi le centre).

**Facteur de levier** : ×1,15-1,2 au banc (13/11) ; **×1,4-1,6 mesuré le 16/11 en avant/arrière**
(passe 2 → 3 : l'écart avant/arrière n'a bougé que de 0,63× la montée des moteurs). Utiliser **×1,4**.
3 passes maximum : au journal, 0,3-0,5 mm d'écart résiduel est proche de la précision de la mesure ;
le reste se rattrape au Z fin pendant la première couche.

## Ne tient pas après coupure

Moteurs Z coupés (arrêt, `M112`, coupure secteur, `M84`), un coin peut glisser sous le poids du
portique. **Tout est à refaire.** Pour couper seulement X/Y : `SET_STEPPER_ENABLE STEPPER=stepper_x ENABLE=0`
(idem `stepper_y`). Jamais d'arrêt d'urgence automatique : il coupe aussi les Z.

## Mesures du 16-17/11/2026 (mini-PC, plateau 60 °C, buse 150 °C)

Écarts au centre en mm (positif = le coin touche plus tôt = portique plus bas à ce coin).

| Passe | Avant G (`z`) | Arrière G (`z1`) | Arrière D (`z2`) | Avant D (`z3`) | Correction appliquée ensuite (montée, mm) |
|---|---|---|---|---|---|
| 1 | −9,55 | +8,10 | +8,65 | −12,34 | z +2,79 · z1 +20,44 · z2 +20,99 · z3 0 (×1, bruts) |
| 2 | +1,58 | −1,74 | −1,36 | +2,54 | z +2,79 · z1 0 · z2 +0,32 · z3 +3,60 (×0,84, erreur de sens) |
| 3 | +1,20 | −0,40 | −0,57 | +1,25 | z +2,48 · z1 +0,24 · z2 0 · z3 +2,55 (×1,4) |
| 4 | −0,07 | +0,12 | +0,26 | +0,44 | z 0 · z1 +0,27 · z2 +0,46 · z3 +0,71 (×1,4) |

La passe 1 a été mesurée après un contact violent à l'avant droit (appuis envoyés en rafale par
l'appli) : ses valeurs sont moins fiables. Résultat après la passe 4 : centre décalé de +0,34 mm,
conforme à la prévision (+0,36).

Le 17/11 vers 0 h 20, un arrêt d'urgence (chien de garde de l'assistant, délai trop court pour un
`G28 X Y`) a coupé les Z : réglage perdu, à refaire.
