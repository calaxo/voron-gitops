# voron-mcp

Serveur [MCP](https://modelcontextprotocol.io) qui permet à un assistant (Claude, etc.) de piloter une
imprimante 3D **Klipper** via l'API **Moonraker** (HTTP + websocket JSON-RPC). Pensé pour une
Voron 2.4 avec changeur d'outils (StealthChanger), mais tout ce qui est spécifique à une machine
passe par des variables d'environnement.

> ⚠️ Un assistant qui pilote une machine qui chauffe et bouge peut casser du matériel. Le serveur
> démarre **désarmé** ; gardez une main près de l'arrêt d'urgence.

## Modèle de sécurité

| Catégorie | Exemples | Condition |
|---|---|---|
| Lecture | `printer_status`, `get_position`, `dump_tmc`, `temperature_history`… | toujours autorisé, non journalisé |
| Action | `move`, `home`, `set_heater` (>0), `pid_calibrate`, `speed_test`, `stream_gcode`… | serveur **armé** (`arm`, auto-désarmement après TTL), journalisé |
| Sécurité | `emergency_stop`, `heaters_off`, `pause_print`, `cancel_print`, `disarm` | toujours autorisé, journalisé |
| Dangereux (gcode brut) | `SET_KINEMATIC_POSITION`, `FORCE_MOVE`, `SET_STEPPER_ENABLE`, `RESTART`, `FIRMWARE_RESTART`, `SAVE_CONFIG`, `SET_TMC_FIELD` | armé **+ `confirm=true`** |

Garde-fous :

- **Journal d'audit** JSONL de tout appel non-lecture (y compris les refus) — `VORON_MCP_AUDIT_LOG`.
  `session_journal` y ajoute des notes libres.
- **Homing single-flight** : `G28` (et `QUAD_GANTRY_LEVEL`, `Z_TILT_ADJUST`, `BED_MESH_CALIBRATE`,
  configurable) refusé si un autre est en cours dans le serveur ou si Klipper est occupé
  (`idle_timeout = Printing`) ou en impression. Jamais de homings mis en file.
- **Simulation des mouvements** avant envoi (gcode brut compris, G90/G91, G92, offsets gcode) :
  limites `axis_minimum/maximum` lues sur Moonraker, `Z_MAX_SAFE`, vitesse `MAX_MOVE_SPEED`,
  **zone interdite** (boîte, ex. zone des docks à l'avant au-dessus d'une hauteur) testée sur le
  *segment* parcouru, pas seulement le point d'arrivée. Seules les séquences de dock y entrent.
- **Watchdog radiateur / cold-end** : capteur `temperature_sensor` (défaut `radiateur_hotend`),
  seuil (défaut 55 °C) → pause (ou annulation) de l'impression, arrêt du stream, chauffe extrudeur
  coupée. Vérifié aussi pendant `stream_gcode`.
- `FORCE_MOVE` limité (`MAX_FORCE_MOVE_MM`), correction de portique en montée seulement.

## Outils

**Lecture** : `printer_status`, `query_objects`, `printer_info`, `get_position` (pas MCU par moteur),
`query_endstops`, `query_button`, `accelerometer_query`, `dump_tmc` (registres + défauts),
`temperature_history` (min/max/moyenne sur fenêtre), `klippy_log_tail` (filtre), `console_history`
(gcode_store), `console_watch` (websocket), `list_files`, `print_progress`, `wait_idle`,
`wait_for_temperature`, `radiator_check`, `list_contact_points`, `compute_gantry_correction`,
`stream_status`, `safety_status`.

**Mouvement** : `gcode`, `home`, `move`, `jog_z_motor`, `move_all_z_together` (pas entrelacés),
`set_kinematic_position`, `stepper_enable`, `set_velocity_limit`, `z_offset_adjust`,
`load_unload_filament`.

**Thermique / sorties** : `set_heater`, `heaters_off`, `set_fan`, `set_led`, `pid_calibrate`,
`watchdog_start`, `watchdog_stop`.

**Calibration** : `set_pressure_advance`, `set_input_shaper`, `test_resonances`, `shaper_calibrate`,
`speed_test` (à la TEST_SPEED d'Ellis, paliers `vitesse:accel`, arrêt à la première perte,
limites restaurées), `generate_pa_test`, `generate_flow_test`.

**Nivellement manuel** : `record_contact_point`, `clear_contact_points`, `compute_gantry_correction`,
`apply_gantry_correction`.

**Docks** : `tool_dock`, `tool_pickup` (paramétrés, `dry_run`, `step_by_step`), `sequence_next`,
`sequence_abort`.

**Impression** : `upload_gcode`, `start_print`, `pause_print`, `resume_print`, `cancel_print`,
`stream_gcode` / `stream_stop` (envoi par paquets sans virtual_sdcard, progression par
`;LAYER_CHANGE`).

**Système** : `arm`, `disarm`, `emergency_stop`, `restart`, `firmware_restart`, `session_journal`.

Les opérations longues (`pid_calibrate`, `shaper_calibrate`, `test_resonances`, `speed_test`)
bloquent jusqu'à la fin (timeout `VORON_MCP_LONG_TIMEOUT`). Les fichiers CSV de résonance sont
écrits **sur l'hôte Klipper**, le serveur renvoie leurs chemins.

## Configuration (variables d'environnement)

| Variable | Défaut | Rôle |
|---|---|---|
| `MOONRAKER_URL` | `http://printer.local:7125` | URL Moonraker |
| `MOONRAKER_API_KEY` | — | en-tête `X-Api-Key` |
| `MCP_TRANSPORT` | `stdio` | `stdio` ou `http` (streamable HTTP) |
| `MCP_HOST` / `MCP_PORT` | `127.0.0.1` / `8000` | écoute en mode HTTP (image : `0.0.0.0`) |
| `VORON_MCP_ARMED` | `false` | armé au démarrage |
| `VORON_MCP_ARM_TTL_MIN` | `60` | auto-désarmement (0 = jamais) |
| `VORON_MCP_DATA_DIR` | `~/.voron-mcp` (`/data` dans l'image) | audit, points de contact, gcodes générés |
| `VORON_MCP_AUDIT_LOG` | `$DATA_DIR/audit.jsonl` | journal |
| `VORON_MCP_GCODE_DIR` | `$DATA_DIR/gcodes` | chemins relatifs de `stream_gcode` / `upload_gcode` |
| `VORON_MCP_HTTP_TIMEOUT` / `VORON_MCP_LONG_TIMEOUT` | `30` / `3600` | secondes |
| `Z_MAX_SAFE` | — | Z maximum autorisé |
| `KEEPOUT_X_MIN`…`KEEPOUT_Z_MAX` | — | zone interdite (active si `KEEPOUT_Y_MIN` ou `_MAX` défini) |
| `MAX_MOVE_SPEED` | `500` | mm/s |
| `MAX_FORCE_MOVE_MM` | `10` | par appel |
| `MAX_EXTRUDER_TEMP` / `MAX_BED_TEMP` | `300` / `120` | °C |
| `SINGLE_FLIGHT_CMDS` | `G28,QUAD_GANTRY_LEVEL,Z_TILT_ADJUST,BED_MESH_CALIBRATE` | |
| `RADIATOR_SENSOR` / `RADIATOR_MAX_C` / `RADIATOR_ACTION` | `radiateur_hotend` / `55` / `pause` | watchdog |
| `Z_STEPPERS` | `stepper_z,stepper_z1,stepper_z2,stepper_z3` | |
| `Z_CORNER_MAP` | `front_left:stepper_z,rear_left:stepper_z1,rear_right:stepper_z2,front_right:stepper_z3` | |
| `LEVER_FACTOR` / `MAX_GANTRY_CORRECTION_MM` | `1.0` / `3` | correction de portique |

Côté Klipper : `[force_move] enable_force_move: True` pour `jog_z_motor`, `move_all_z_together`,
`apply_gantry_correction`.

## Lancer

```bash
docker build -t local/voron-mcp:dev .

# stdio (client MCP local)
docker run -i --rm --network host \
  -e MOONRAKER_URL=http://printer.local:7125 \
  -v voron-mcp-data:/data \
  local/voron-mcp:dev

# HTTP streamable
docker run -d --network host -e MCP_TRANSPORT=http -e MCP_PORT=8000 \
  -e MOONRAKER_URL=http://printer.local:7125 -v voron-mcp-data:/data local/voron-mcp:dev
```

Exemple de déclaration côté client (Claude Code, `.mcp.json`) :

```json
{
  "mcpServers": {
    "voron": {
      "command": "docker",
      "args": ["run", "-i", "--rm", "--network", "host",
               "-e", "MOONRAKER_URL=http://printer.local:7125",
               "-e", "Z_MAX_SAFE=300",
               "-v", "voron-mcp-data:/data", "local/voron-mcp:dev"]
    }
  }
}
```

Sans Docker : `pip install .` puis `voron-mcp` (ou `python -m voron_mcp`).

## Tests

Les tests tournent contre un faux Moonraker en mémoire (`httpx.MockTransport`), aucune imprimante
nécessaire :

```bash
pip install ".[test]" && pytest -q
# ou
docker build --target test .
```

## Notes de conception

- `client.py` : HTTP Moonraker + session websocket JSON-RPC ; les réponses console des commandes
  (GET_POSITION, DUMP_TMC…) sont récupérées dans le `gcode_store` en comparant aux horodatages du
  store lui-même (pas de dépendance à l'horloge de l'hôte).
- `safety.py` : armement, audit, classification gcode, simulateur de mouvement, single-flight.
- `app.py` : `run_script()`, point d'entrée unique de tout gcode envoyé par le serveur.
- `tools/*.py` : outils par domaine ; `gcode_gen.py` : générateurs de tests d'impression.
- Convention de nivellement : les pas Z croissent vers le haut ; un coin qui touche à un compte de
  pas plus élevé est plus bas et doit être remonté de la différence (× facteur de levier).
