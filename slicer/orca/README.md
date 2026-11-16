# Profils OrcaSlicer (2.5.x)

Profils utilisateur de la Voron 2.4 350, à copier dans `%APPDATA%\OrcaSlicer\user\default\{machine,filament,process}\`
**Orca fermé** (il réécrit ses fichiers à la fermeture).

- `machine/Voron 2.4 350.json` : hérite de « Voron 2.4 350 0.4 nozzle » ; G-code de début = `PRINT_START BED=… EXTRUDER=… CHAMBER=…`
  (la macro pose la purge ; **Z à poser avant** avec `SET_Z_MANUAL Z=0.1` tant qu'il n'y a pas de sonde), fin = `PRINT_END`,
  miniatures 32×32 + 300×300 pour Mainsail. Après copie, remettre l'adresse de l'imprimante dans *Imprimante physique*
  (type Octo/Klipper, `http://<IP de l'hôte>`) : le dépôt est public, l'adresse réelle n'y figure pas.
- `filament/` : eSUN PLA+ (validé au Benchy du 13/11, PA 0,040) ; ASA OVERTURE (repris du profil Bambu P1S des pièces, à recalibrer).
- `process/` : « prudent (banc) » = vitesses du premier Benchy ; « pièces Voron (ASA) » = 4 murs, 5/5, 40 % gyroïde.
