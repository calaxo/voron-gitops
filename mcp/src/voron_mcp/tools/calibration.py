"""Pressure advance, input shaper, resonance tests, PID, speed test."""

from typing import Any

from ..app import App
from ..parsers import parse_get_position, parse_shaper, parse_written_files
from ..safety import SafetyError, _segment_hits_box


def _f(v: float) -> str:
    return f"{v:.3f}".rstrip("0").rstrip(".")


def parse_pairs(pairs: list[str] | str) -> list[tuple[float, float]]:
    """'300:5000, 400:8000' or ['300:5000'] -> [(300.0, 5000.0), ...]"""
    if isinstance(pairs, str):
        pairs = [p for p in pairs.replace(";", ",").split(",")]
    out = []
    for p in pairs:
        p = p.strip()
        if not p:
            continue
        v, _, a = p.partition(":")
        if not a:
            raise ValueError(f"bad pair '{p}', expected velocity:accel")
        out.append((float(v), float(a)))
    if not out:
        raise ValueError("no velocity:accel pair")
    return out


def speed_pattern(x0: float, x1: float, y0: float, y1: float, small: float, iterations: int,
                  speed: float, zigzag_rows: int = 4) -> list[str]:
    """Ellis TEST_SPEED style pattern (gcode coordinates)."""
    f = f"F{_f(speed * 60)}"
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    s0x, s1x, s0y, s1y = cx - small / 2, cx + small / 2, cy - small / 2, cy + small / 2
    lines: list[str] = []
    for _ in range(iterations):
        lines += [  # large diagonals
            f"G1 X{_f(x0)} Y{_f(y0)} {f}", f"G1 X{_f(x1)} Y{_f(y1)} {f}",
            f"G1 X{_f(x0)} Y{_f(y1)} {f}", f"G1 X{_f(x1)} Y{_f(y0)} {f}",
            # large box
            f"G1 X{_f(x0)} Y{_f(y0)} {f}", f"G1 X{_f(x0)} Y{_f(y1)} {f}",
            f"G1 X{_f(x1)} Y{_f(y1)} {f}", f"G1 X{_f(x1)} Y{_f(y0)} {f}",
        ]
        # zigzag across the bed
        for r in range(zigzag_rows + 1):
            y = y0 + (y1 - y0) * r / zigzag_rows
            lines.append(f"G1 X{_f(x1 if r % 2 == 0 else x0)} Y{_f(y)} {f}")
        # small pattern in the middle (diagonals + box)
        lines += [
            f"G1 X{_f(s0x)} Y{_f(s0y)} {f}", f"G1 X{_f(s1x)} Y{_f(s1y)} {f}",
            f"G1 X{_f(s0x)} Y{_f(s1y)} {f}", f"G1 X{_f(s1x)} Y{_f(s0y)} {f}",
            f"G1 X{_f(s0x)} Y{_f(s0y)} {f}", f"G1 X{_f(s0x)} Y{_f(s1y)} {f}",
            f"G1 X{_f(s1x)} Y{_f(s1y)} {f}", f"G1 X{_f(s1x)} Y{_f(s0y)} {f}",
        ]
    return lines


def register(app: App, tool: Any) -> None:
    cfg = app.cfg

    @tool("act")
    async def set_pressure_advance(advance: float, smooth_time: float | None = None,
                                   extruder: str | None = None) -> dict:
        """SET_PRESSURE_ADVANCE (runtime). Range 0..1."""
        if not 0 <= advance <= 1:
            raise ValueError("advance must be within 0..1")
        cmd = f"SET_PRESSURE_ADVANCE ADVANCE={advance:g}"
        if smooth_time is not None:
            cmd += f" SMOOTH_TIME={smooth_time:g}"
        if extruder:
            cmd += f" EXTRUDER={extruder}"
        await app.run_script(cmd)
        return {"sent": cmd}

    @tool("act")
    async def set_input_shaper(freq_x: float | None = None, freq_y: float | None = None,
                               type_x: str | None = None, type_y: str | None = None,
                               damping_x: float | None = None, damping_y: float | None = None) -> dict:
        """SET_INPUT_SHAPER (runtime; persist via printer.cfg / SAVE_CONFIG)."""
        parts = [f"{k}={v}" for k, v in (("SHAPER_FREQ_X", freq_x), ("SHAPER_FREQ_Y", freq_y),
                 ("SHAPER_TYPE_X", type_x), ("SHAPER_TYPE_Y", type_y),
                 ("DAMPING_RATIO_X", damping_x), ("DAMPING_RATIO_Y", damping_y)) if v is not None]
        cmd = "SET_INPUT_SHAPER " + " ".join(parts)
        res = await app.run_script(cmd.strip(), capture=True)
        return {"sent": cmd, "response": res.get("response")}

    async def _check_point(point: str | None) -> None:
        if not point:
            return
        xyz = [float(v) for v in point.split(",")]
        if len(xyz) != 3:
            raise ValueError("point must be 'x,y,z'")
        sim = await app.motion_sim()
        sim.check_point(xyz, "resonance point")
        if cfg.keepout.enabled and _segment_hits_box(xyz, xyz, cfg.keepout):
            raise SafetyError("resonance point inside keep-out zone")

    @tool("act")
    async def test_resonances(axis: str = "x", output: str = "resonances", name: str | None = None,
                              freq_start: float | None = None, freq_end: float | None = None,
                              hz_per_sec: float | None = None, accel_per_hz: float | None = None,
                              chips: str | None = None, point: str | None = None,
                              timeout_s: float = 1800) -> dict:
        """TEST_RESONANCES (long-running). Output files are written on the printer
        host (paths returned), not by this server. Printer must be homed."""
        await _check_point(point)
        parts = [f"AXIS={axis}", f"OUTPUT={output}"]
        for k, v in (("NAME", name), ("FREQ_START", freq_start), ("FREQ_END", freq_end),
                     ("HZ_PER_SEC", hz_per_sec), ("ACCEL_PER_HZ", accel_per_hz), ("CHIPS", chips),
                     ("POINT", point)):
            if v is not None:
                parts.append(f"{k}={v}")
        res = await app.run_script("TEST_RESONANCES " + " ".join(parts), capture=True, timeout=timeout_s)
        lines = res.get("response") or []
        return {"files_on_host": parse_written_files(lines), "raw": lines[-10:]}

    @tool("act")
    async def shaper_calibrate(axis: str | None = None, max_smoothing: float | None = None,
                               freq_start: float | None = None, freq_end: float | None = None,
                               hz_per_sec: float | None = None, chips: str | None = None,
                               timeout_s: float = 2400) -> dict:
        """SHAPER_CALIBRATE (long-running). Returns per-axis fitted shapers and
        Klipper's recommendation (type, freq, max_accel). Not persisted until
        SAVE_CONFIG."""
        parts = []
        for k, v in (("AXIS", axis), ("MAX_SMOOTHING", max_smoothing), ("FREQ_START", freq_start),
                     ("FREQ_END", freq_end), ("HZ_PER_SEC", hz_per_sec), ("CHIPS", chips)):
            if v is not None:
                parts.append(f"{k}={v}")
        res = await app.run_script(("SHAPER_CALIBRATE " + " ".join(parts)).strip(), capture=True, timeout=timeout_s)
        lines = res.get("response") or []
        parsed = parse_shaper(lines)
        parsed["raw_tail"] = lines[-8:]
        return parsed

    async def _mcu_xy() -> dict:
        lines = await app.capture("GET_POSITION")
        mcu = parse_get_position(lines).get("mcu", {})
        if "stepper_x" not in mcu or "stepper_y" not in mcu:
            raise SafetyError(f"GET_POSITION output not understood: {lines[-5:]}")
        return {"stepper_x": mcu["stepper_x"], "stepper_y": mcu["stepper_y"]}

    @tool("act")
    async def speed_test(pairs: list[str] | str, iterations: int = 5, margin: float = 20.0,
                         small_pattern: float = 20.0, ref_x: float | None = None, ref_y: float | None = None,
                         x_min: float | None = None, x_max: float | None = None,
                         y_min: float | None = None, y_max: float | None = None,
                         z: float | None = None, travel_speed: float = 100.0,
                         tolerance_steps: int = 8, stop_on_loss: bool = True) -> dict:
        """Ellis TEST_SPEED style skipped-steps test, stepwise over 'velocity:accel'
        pairs (e.g. ['300:5000','400:10000']). For each pair: home XY, go to the
        reference point, record MCU A/B steps, run diagonals/boxes/zigzag/small
        pattern, return to ref and record, re-home, return and record; the
        difference = lost microsteps. Stops at the first loss; original velocity
        limits are always restored. Bounds default to axis limits minus `margin`
        (toolhead coords) and are checked against the keep-out zone."""
        plist = parse_pairs(pairs)
        for v, a in plist:
            if v > cfg.max_move_speed:
                raise SafetyError(f"velocity {v} > MAX_MOVE_SPEED={cfg.max_move_speed}")
            if a <= 0 or v <= 0:
                raise ValueError("velocity and accel must be > 0")
        st = await app.client.query({"toolhead": ["max_velocity", "max_accel", "minimum_cruise_ratio",
                                                  "square_corner_velocity", "axis_minimum", "axis_maximum",
                                                  "homed_axes"]})
        th = st["toolhead"]
        orig = {k: th.get(k) for k in ("max_velocity", "max_accel", "minimum_cruise_ratio", "square_corner_velocity")}
        amin, amax = th["axis_minimum"], th["axis_maximum"]
        bx0 = x_min if x_min is not None else amin[0] + margin
        bx1 = x_max if x_max is not None else amax[0] - margin
        by0 = y_min if y_min is not None else amin[1] + margin
        by1 = y_max if y_max is not None else amax[1] - margin
        rx = ref_x if ref_x is not None else amax[0] - 5
        ry = ref_y if ref_y is not None else amax[1] - 5
        restore = "SET_VELOCITY_LIMIT " + " ".join(
            f"{k}={v}" for k, v in (("VELOCITY", orig["max_velocity"]), ("ACCEL", orig["max_accel"]),
                                    ("MINIMUM_CRUISE_RATIO", orig["minimum_cruise_ratio"]),
                                    ("SQUARE_CORNER_VELOCITY", orig["square_corner_velocity"])) if v is not None)
        results: list[dict] = []
        first_loss = None
        try:
            if z is not None:
                if "z" not in str(th.get("homed_axes", "")):
                    raise SafetyError("z requested but Z is not homed")
                await app.run_script(f"G90\nG1 Z{_f(z)} F600\nM400")
            for v, a in plist:
                await app.run_script("G28 X Y", timeout=300)
                sim = await app.motion_sim()
                bx, by = sim.base[0], sim.base[1]  # toolhead = gcode + base
                go_ref = f"G90\nG1 X{_f(rx - bx)} Y{_f(ry - by)} F{_f(travel_speed * 60)}\nM400"
                await app.run_script(go_ref)
                p0 = await _mcu_xy()
                pattern = speed_pattern(bx0 - bx, bx1 - bx, by0 - by, by1 - by, small_pattern, iterations, v)
                await app.run_script(f"SET_VELOCITY_LIMIT VELOCITY={_f(v)} ACCEL={_f(a)}")
                await app.run_script("\n".join(["G90", *pattern, f"G1 X{_f(rx - bx)} Y{_f(ry - by)} F{_f(travel_speed * 60)}", "M400"]),
                                     timeout=1800)
                p1 = await _mcu_xy()
                await app.run_script(restore)
                await app.run_script("G28 X Y", timeout=300)
                await app.run_script(go_ref)
                p2 = await _mcu_xy()
                diff = {s: int(p1[s] - p2[s]) for s in p1}
                lost = any(abs(d) > tolerance_steps for d in diff.values())
                results.append({"velocity": v, "accel": a, "steps_before_test": p0, "steps_after_test": p1,
                                "steps_after_rehome": p2, "difference": diff, "lost_steps": lost})
                if lost and first_loss is None:
                    first_loss = {"velocity": v, "accel": a}
                    if stop_on_loss:
                        break
        finally:
            try:
                await app.client.gcode(restore, timeout=30)
            except Exception:
                pass
        last_ok = next((r for r in reversed(results) if not r["lost_steps"]), None)
        return {"results": results, "first_loss": first_loss,
                "last_good": {"velocity": last_ok["velocity"], "accel": last_ok["accel"]} if last_ok else None,
                "tolerance_steps": tolerance_steps, "limits_restored": orig}
