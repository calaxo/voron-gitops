"""Raw gcode, homing, moves, stepper-level helpers."""

import math
from typing import Any, Literal

from ..app import App
from ..safety import SafetyError


def _fmt(v: float) -> str:
    return f"{v:.4f}".rstrip("0").rstrip(".") if v != int(v) else str(int(v))


def interleaved_force_moves(
    steppers: list[str], distances: dict[str, float], step_mm: float, velocity: float, accel: float
) -> list[str]:
    """Round-robin FORCE_MOVE commands: every stepper advances by at most
    `step_mm` per round so no single motor gets ahead (avoids gantry racking)."""
    if step_mm <= 0:
        raise ValueError("step_mm must be > 0")
    remaining = {s: distances.get(s, 0.0) for s in steppers}
    lines: list[str] = []
    while any(abs(v) > 1e-9 for v in remaining.values()):
        for s in steppers:
            r = remaining[s]
            if abs(r) <= 1e-9:
                continue
            d = math.copysign(min(step_mm, abs(r)), r)
            lines.append(f"FORCE_MOVE STEPPER={s} DISTANCE={_fmt(round(d, 6))} VELOCITY={_fmt(velocity)} ACCEL={_fmt(accel)}")
            remaining[s] = round(r - d, 9)
    return lines


def register(app: App, tool: Any) -> None:
    cfg = app.cfg

    @tool("audit")
    async def gcode(script: str, confirm: bool = False, timeout_s: float | None = None,
                    capture: bool = True) -> dict:
        """Run raw gcode with guards. Read-only commands (GET_POSITION, M114,
        QUERY_ENDSTOPS, DUMP_TMC...) work disarmed; M112 always works; anything
        else needs armed. SET_KINEMATIC_POSITION / FORCE_MOVE / SET_STEPPER_ENABLE /
        (FIRMWARE_)RESTART / SAVE_CONFIG need confirm=true. Moves are simulated
        against soft limits, Z_MAX_SAFE and the keep-out zone; G28/QGL are
        single-flight. Returns console responses when capture=true."""
        return await app.run_script(script, confirm=confirm, capture=capture, timeout=timeout_s)

    @tool("act")
    async def home(axes: str = "xyz") -> dict:
        """G28 on the given axes ('xyz', 'xy', 'z'...). Single-flight: refused if a
        homing is already running or the printer is busy. Never queue homings."""
        ax = "".join(sorted({a for a in axes.lower() if a in "xyz"}))
        if not ax:
            raise ValueError("axes must contain x, y and/or z")
        cmd = "G28" if ax == "xyz" else "G28 " + " ".join(a.upper() for a in ax)
        await app.run_script(cmd, timeout=300)
        st = await app.client.query({"toolhead": ["homed_axes", "position"]})
        return {"sent": cmd, "homed_axes": st.get("toolhead", {}).get("homed_axes"),
                "position": st.get("toolhead", {}).get("position")}

    @tool("act")
    async def move(x: float | None = None, y: float | None = None, z: float | None = None,
                   relative: bool = False, speed_mm_s: float = 100.0, wait: bool = True) -> dict:
        """G1 move (absolute gcode coords, or relative). Checked against soft
        limits, Z_MAX_SAFE and the keep-out zone before sending. The previous
        G90/G91 mode is restored."""
        parts = [f"{a}{_fmt(v)}" for a, v in (("X", x), ("Y", y), ("Z", z)) if v is not None]
        if not parts:
            raise ValueError("give at least one of x, y, z")
        if speed_mm_s <= 0 or speed_mm_s > cfg.max_move_speed:
            raise SafetyError(f"speed must be in (0, {cfg.max_move_speed}] mm/s")
        script = ["SAVE_GCODE_STATE NAME=_mcp_move", "G91" if relative else "G90",
                  f"G1 {' '.join(parts)} F{_fmt(speed_mm_s * 60)}", "RESTORE_GCODE_STATE NAME=_mcp_move"]
        if wait:
            script.append("M400")
        await app.run_script("\n".join(script))
        st = await app.client.query({"toolhead": ["position"], "gcode_move": ["gcode_position"]})
        return {"sent": script[2], "position": st.get("toolhead", {}).get("position"),
                "gcode_position": st.get("gcode_move", {}).get("gcode_position")}

    @tool("act")
    async def jog_z_motor(stepper: str, distance: float, velocity: float = 5.0, accel: float = 0.0,
                          step_mm: float | None = None) -> dict:
        """FORCE_MOVE a single Z stepper by `distance` mm (+ = up), optionally in
        `step_mm` increments. Needs [force_move] enable_force_move: True. Z
        position is not trustworthy afterwards (re-home / level)."""
        if stepper not in cfg.z_steppers:
            raise ValueError(f"unknown Z stepper '{stepper}', expected one of {cfg.z_steppers}")
        if abs(distance) > cfg.max_force_move_mm:
            raise SafetyError(f"|distance| > MAX_FORCE_MOVE_MM={cfg.max_force_move_mm}")
        lines = interleaved_force_moves([stepper], {stepper: distance}, step_mm or abs(distance), velocity, accel)
        await app.run_script("\n".join(lines + ["M400"]), confirm=True)
        return {"stepper": stepper, "distance": distance, "commands": len(lines),
                "note": "kinematic Z not updated by FORCE_MOVE: re-home/level before printing"}

    @tool("act")
    async def move_all_z_together(distance: float, step_mm: float = 1.0, velocity: float = 5.0,
                                  accel: float = 0.0) -> dict:
        """Move the 4 Z steppers together with FORCE_MOVE in interleaved small
        steps (default 1 mm) so the gantry never racks. For when Z is not homed."""
        if abs(distance) > cfg.max_force_move_mm:
            raise SafetyError(f"|distance| > MAX_FORCE_MOVE_MM={cfg.max_force_move_mm}")
        steppers = cfg.z_steppers
        lines = interleaved_force_moves(steppers, {s: distance for s in steppers}, step_mm, velocity, accel)
        await app.run_script("\n".join(lines + ["M400"]), confirm=True)
        return {"steppers": steppers, "distance": distance, "commands": len(lines)}

    @tool("act")
    async def set_kinematic_position(x: float | None = None, y: float | None = None, z: float | None = None,
                                     confirm: bool = False) -> dict:
        """SET_KINEMATIC_POSITION (tells Klipper where the toolhead is without
        moving; marks axes homed). Dangerous: needs confirm=true."""
        parts = [f"{a}={_fmt(v)}" for a, v in (("X", x), ("Y", y), ("Z", z)) if v is not None]
        if not parts:
            raise ValueError("give at least one of x, y, z")
        cmd = "SET_KINEMATIC_POSITION " + " ".join(parts)
        await app.run_script(cmd, confirm=confirm)
        return {"sent": cmd}

    @tool("act")
    async def stepper_enable(stepper: str | None = None, enable: bool = True, confirm: bool = False) -> dict:
        """Enable/disable a stepper (SET_STEPPER_ENABLE) or, with no stepper and
        enable=false, all motors (M84). Disabling needs confirm=true: on a
        belted Voron the gantry can drop when Z motors are released."""
        if not enable and not confirm:
            raise SafetyError("disabling motors needs confirm=true (gantry may drop)")
        if stepper is None:
            if enable:
                raise ValueError("enable requires a stepper name")
            await app.run_script("M84")
            return {"sent": "M84"}
        cmd = f"SET_STEPPER_ENABLE STEPPER={stepper} ENABLE={1 if enable else 0}"
        await app.run_script(cmd, confirm=True)
        return {"sent": cmd}

    @tool("act")
    async def set_velocity_limit(velocity: float | None = None, accel: float | None = None,
                                 square_corner_velocity: float | None = None,
                                 minimum_cruise_ratio: float | None = None) -> dict:
        """SET_VELOCITY_LIMIT (runtime only, not saved)."""
        parts = [f"{k}={_fmt(v)}" for k, v in (("VELOCITY", velocity), ("ACCEL", accel),
                 ("SQUARE_CORNER_VELOCITY", square_corner_velocity),
                 ("MINIMUM_CRUISE_RATIO", minimum_cruise_ratio)) if v is not None]
        if velocity is not None and velocity > cfg.max_move_speed:
            raise SafetyError(f"velocity > MAX_MOVE_SPEED={cfg.max_move_speed}")
        cmd = "SET_VELOCITY_LIMIT " + " ".join(parts)
        res = await app.run_script(cmd.strip(), capture=True)
        return {"sent": cmd, "response": res.get("response")}

    @tool("act")
    async def z_offset_adjust(delta: float, move: bool = True) -> dict:
        """SET_GCODE_OFFSET Z_ADJUST=delta (+ = nozzle further from bed). Max 1 mm
        per call."""
        if abs(delta) > 1.0:
            raise SafetyError("|delta| > 1 mm per call")
        cmd = f"SET_GCODE_OFFSET Z_ADJUST={_fmt(delta)} MOVE={1 if move else 0}"
        await app.run_script(cmd)
        st = await app.client.query({"gcode_move": ["homing_origin"]})
        return {"sent": cmd, "gcode_offset": st.get("gcode_move", {}).get("homing_origin")}

    @tool("act")
    async def load_unload_filament(action: Literal["load", "unload"], length: float = 50.0,
                                   speed_mm_s: float = 5.0, prime_mm: float = 5.0,
                                   retract_speed_mm_s: float = 40.0) -> dict:
        """Load: extrude `length` at `speed`. Unload: prime `prime_mm` (soften the
        tip) then retract `length` fast. Requires a hot extruder."""
        st = await app.client.query({"extruder": ["can_extrude", "temperature"]})
        ex = st.get("extruder", {})
        if not ex.get("can_extrude"):
            raise SafetyError(f"extruder too cold ({ex.get('temperature')} °C): heat it first")
        if length > 150:
            raise SafetyError("length > 150 mm")
        if action == "load":
            moves = [f"G1 E{_fmt(length)} F{_fmt(speed_mm_s * 60)}"]
        else:
            moves = [f"G1 E{_fmt(prime_mm)} F{_fmt(speed_mm_s * 60)}",
                     f"G1 E-{_fmt(length)} F{_fmt(retract_speed_mm_s * 60)}"]
        script = ["SAVE_GCODE_STATE NAME=_mcp_fil", "M83", *moves, "M400", "RESTORE_GCODE_STATE NAME=_mcp_fil"]
        await app.run_script("\n".join(script), timeout=300)
        return {"action": action, "moves": moves}
