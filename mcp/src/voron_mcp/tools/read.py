"""Read-only tools."""

import re
import statistics
from typing import Any

from ..app import App
from ..parsers import parse_accelerometer, parse_dump_tmc, parse_get_position

_MULTI_PREFIXES = (
    "temperature_sensor ", "temperature_fan ", "heater_fan ", "fan_generic ", "controller_fan ",
    "heater_generic ", "gcode_button ", "neopixel ", "filament_switch_sensor ", "filament_motion_sensor ",
)


def _r(v: Any, nd: int = 2) -> Any:
    if isinstance(v, float):
        return round(v, nd)
    if isinstance(v, list):
        return [_r(x, nd) for x in v]
    return v


async def fetch_status(app: App) -> dict:
    objects = await app.client.list_objects()
    q: dict[str, Any] = {
        "webhooks": ["state", "state_message"],
        "toolhead": ["homed_axes", "position", "max_velocity", "max_accel", "minimum_cruise_ratio",
                     "square_corner_velocity", "axis_minimum", "axis_maximum", "print_time",
                     "estimated_print_time", "extruder"],
        "gcode_move": ["gcode_position", "homing_origin", "speed_factor", "extrude_factor",
                       "absolute_coordinates"],
        "motion_report": ["live_position", "live_velocity", "live_extruder_velocity"],
        "idle_timeout": ["state", "printing_time"],
        "print_stats": ["state", "filename", "print_duration", "total_duration", "filament_used",
                        "message", "info"],
        "virtual_sdcard": ["progress", "is_active", "file_position"],
        "display_status": ["progress", "message"],
        "stepper_enable": ["steppers"],
        "fan": ["speed", "rpm"],
        "heater_bed": ["temperature", "target", "power"],
        "configfile": ["settings"],
    }
    for o in objects:
        if o == "extruder" or re.fullmatch(r"extruder\d+", o):
            q[o] = ["temperature", "target", "power", "pressure_advance", "smooth_time", "can_extrude"]
        elif o.startswith(_MULTI_PREFIXES) or o.startswith(("quad_gantry_level", "z_tilt", "probe", "tools_calibrate")):
            q[o] = None
    q = {k: v for k, v in q.items() if k in objects or k in ("webhooks",)}
    return await app.client.query(q)


def summarize_status(st: dict) -> dict:
    th = st.get("toolhead", {})
    out: dict[str, Any] = {
        "klippy": st.get("webhooks", {}).get("state"),
        "klippy_message": (st.get("webhooks", {}).get("state_message") or "")[:300],
        "idle_state": st.get("idle_timeout", {}).get("state"),
        "homed_axes": th.get("homed_axes"),
        "position": _r(th.get("position")),
        "gcode_position": _r(st.get("gcode_move", {}).get("gcode_position")),
        "gcode_offset": _r(st.get("gcode_move", {}).get("homing_origin"), 4),
        "live_position": _r(st.get("motion_report", {}).get("live_position")),
        "live_velocity": _r(st.get("motion_report", {}).get("live_velocity")),
        "limits": {
            "max_velocity": th.get("max_velocity"),
            "max_accel": th.get("max_accel"),
            "minimum_cruise_ratio": th.get("minimum_cruise_ratio"),
            "square_corner_velocity": th.get("square_corner_velocity"),
            "axis_minimum": _r(th.get("axis_minimum")),
            "axis_maximum": _r(th.get("axis_maximum")),
        },
        "print": {
            **{k: st.get("print_stats", {}).get(k) for k in ("state", "filename", "print_duration", "message")},
            "progress": _r(st.get("virtual_sdcard", {}).get("progress"), 4),
        },
        "steppers_enabled": st.get("stepper_enable", {}).get("steppers"),
    }
    temps: dict[str, Any] = {}
    fans: dict[str, Any] = {}
    extra: dict[str, Any] = {}
    for name, val in st.items():
        if name == "heater_bed" or name.startswith(("extruder", "heater_generic ")):
            temps[name] = {k: _r(val.get(k)) for k in ("temperature", "target", "power") if k in val}
            if "pressure_advance" in val:
                extra.setdefault("pressure_advance", {})[name] = {
                    "pa": val.get("pressure_advance"), "smooth_time": val.get("smooth_time")}
        elif name.startswith("temperature_sensor "):
            temps[name.split(" ", 1)[1]] = _r(val.get("temperature"))
        elif name.startswith("temperature_fan "):
            temps[name] = {k: _r(val.get(k)) for k in ("temperature", "target", "speed")}
        elif name == "fan" or name.startswith(("heater_fan ", "fan_generic ", "controller_fan ")):
            fans[name] = {k: _r(val.get(k)) for k in ("speed", "rpm") if val.get(k) is not None}
        elif name.startswith("gcode_button "):
            extra.setdefault("buttons", {})[name.split(" ", 1)[1]] = val.get("state")
        elif name.startswith(("filament_switch_sensor ", "filament_motion_sensor ")):
            extra.setdefault("filament_sensors", {})[name.split(" ", 1)[1]] = val.get("filament_detected")
        elif name.startswith(("quad_gantry_level", "z_tilt")):
            extra[name] = {"applied": val.get("applied")}
    settings = st.get("configfile", {}).get("settings", {})
    shaper = settings.get("input_shaper")
    if shaper:
        extra["input_shaper_config"] = {k: v for k, v in shaper.items() if k.startswith(("shaper_", "damping"))}
    idle = settings.get("idle_timeout")
    if idle:
        extra["idle_timeout_s"] = idle.get("timeout")
    out["temperatures"] = temps
    out["fans"] = fans
    out.update(extra)
    return out


def register(app: App, tool: Any) -> None:
    @tool("read")
    async def printer_status() -> dict:
        """Compact snapshot: klippy state, homed axes, position (commanded + live),
        velocity, temperatures (heaters, temperature_sensors/fans), fans, pressure
        advance, input shaper (config), limits, idle_timeout, print progress,
        stepper enable state, buttons and filament sensors."""
        return summarize_status(await fetch_status(app))

    @tool("read")
    async def query_objects(objects: dict[str, list[str] | None] | None = None) -> dict:
        """Raw Moonraker objects query, e.g. {"toolhead": null, "extruder": ["temperature"]}.
        Without argument: list the available printer objects."""
        if not objects:
            return {"objects": await app.client.list_objects()}
        return {"status": await app.client.query(objects)}

    @tool("read")
    async def printer_info() -> dict:
        """Klippy host info (state, versions, config file path)."""
        return await app.client.info()

    @tool("read")
    async def get_position() -> dict:
        """GET_POSITION parsed: mcu step counts per stepper, stepper/kinematic/
        toolhead/gcode positions."""
        lines = await app.capture("GET_POSITION")
        parsed = parse_get_position(lines)
        return {"position": parsed} if parsed else {"position": {}, "raw": lines[-20:]}

    @tool("read")
    async def query_endstops() -> dict:
        """State of every endstop (open / TRIGGERED)."""
        return {"endstops": await app.client.endstops()}

    @tool("read")
    async def query_button(name: str) -> dict:
        """State (PRESSED/RELEASED) of a [gcode_button <name>] (e.g. a probe or dock switch)."""
        st = await app.client.query({f"gcode_button {name}": ["state"]})
        state = st.get(f"gcode_button {name}", {}).get("state")
        if state is None:
            lines = await app.capture(f"QUERY_BUTTON BUTTON={name}")
            return {"button": name, "raw": lines}
        return {"button": name, "state": state}

    @tool("read")
    async def accelerometer_query(chip: str | None = None) -> dict:
        """ACCELEROMETER_QUERY: one sample (mm/s²) per chip; norm ≈ 9810 when the
        chip is healthy and still."""
        cmd = "ACCELEROMETER_QUERY" + (f" CHIP={chip}" if chip else "")
        lines = await app.capture(cmd)
        return {"samples": parse_accelerometer(lines), "raw": lines[-5:]}

    @tool("read")
    async def dump_tmc(stepper: str, register: str | None = None) -> dict:
        """DUMP_TMC parsed into registers/fields; flags faults (ot, s2g*, uv_cp,
        drv_err...) and warnings (otpw, ola/olb, reset)."""
        cmd = f"DUMP_TMC STEPPER={stepper}" + (f" REGISTER={register}" if register else "")
        lines = await app.capture(cmd)
        parsed = parse_dump_tmc(lines)
        parsed["stepper"] = stepper
        return parsed

    @tool("read")
    async def temperature_history(
        sensors: list[str] | None = None, window_s: int = 300, include_series: bool = False
    ) -> dict:
        """Stats over the last `window_s` seconds (1 sample/s, max ~1200) from
        Moonraker's temperature_store: min/max/mean/last per sensor, plus target
        and power for heaters. `sensors` match by substring."""
        store = await app.client.temperature_store()
        out: dict[str, Any] = {}
        for name, series in store.items():
            if sensors and not any(s in name for s in sensors):
                continue
            temps = [t for t in (series.get("temperatures") or [])[-window_s:] if t is not None]
            if not temps:
                continue
            entry: dict[str, Any] = {
                "min": round(min(temps), 2), "max": round(max(temps), 2),
                "mean": round(statistics.fmean(temps), 2), "last": round(temps[-1], 2),
                "stdev": round(statistics.pstdev(temps), 3), "samples": len(temps),
            }
            if series.get("targets"):
                entry["target_last"] = series["targets"][-1]
            if series.get("powers"):
                pw = series["powers"][-window_s:]
                entry["power_mean"] = round(statistics.fmean(pw), 3) if pw else None
            if include_series:
                entry["series"] = [round(t, 2) for t in temps]
            out[name] = entry
        return {"window_s": window_s, "sensors": out}

    @tool("read")
    async def klippy_log_tail(lines: int = 100, contains: str | None = None, regex: str | None = None,
                              scan_lines: int = 5000) -> dict:
        """Last lines of klippy.log (logs root). Optional filter: substring
        `contains` or `regex`, applied over the last `scan_lines`."""
        tail = await app.client.tail_text("/server/files/klippy.log", max(lines, scan_lines))
        if contains:
            tail = [l for l in tail if contains.lower() in l.lower()]
        if regex:
            rx = re.compile(regex)
            tail = [l for l in tail if rx.search(l)]
        return {"lines": tail[-lines:]}

    @tool("read")
    async def console_history(count: int = 50, contains: str | None = None, responses_only: bool = False) -> dict:
        """Recent console (gcode_store): commands and responses, incl. output of
        GET_POSITION / DUMP_TMC / ACCELEROMETER_QUERY sent from any client."""
        store = await app.client.gcode_store(max(count, 1) * (4 if contains else 1))
        rows = []
        for e in store:
            if responses_only and e.get("type") != "response":
                continue
            msg = str(e.get("message", ""))
            if contains and contains.lower() not in msg.lower():
                continue
            rows.append({"t": e.get("time"), "type": e.get("type"), "msg": msg})
        return {"entries": rows[-count:]}

    @tool("read")
    async def list_files(root: str = "gcodes", contains: str | None = None, limit: int = 50) -> dict:
        """List files of a Moonraker root (gcodes, config, logs), newest first."""
        files = await app.client.list_files(root)
        if contains:
            files = [f for f in files if contains.lower() in f.get("path", "").lower()]
        files.sort(key=lambda f: f.get("modified", 0), reverse=True)
        return {"root": root, "count": len(files),
                "files": [{"path": f.get("path"), "size": f.get("size"), "modified": f.get("modified")}
                          for f in files[:limit]]}

    @tool("read")
    async def wait_idle(timeout_s: float = 300, poll_s: float = 1.0) -> dict:
        """Wait until Klipper is no longer executing gcode (idle_timeout != Printing)."""
        import asyncio
        import time
        t0 = time.monotonic()
        while True:
            st = await app.client.query({"idle_timeout": ["state"]})
            state = st.get("idle_timeout", {}).get("state")
            if state != "Printing":
                return {"idle_state": state, "waited_s": round(time.monotonic() - t0, 1)}
            if time.monotonic() - t0 > timeout_s:
                return {"ok": False, "error": "timeout", "idle_state": state}
            await asyncio.sleep(poll_s)

    @tool("read")
    async def console_watch(seconds: float = 10.0, max_lines: int = 200) -> dict:
        """Listen on the Moonraker websocket for `seconds` and return console
        output (notify_gcode_response) plus the last status deltas of toolhead/
        print_stats (notify_status_update)."""
        import asyncio
        import time
        from ..client import MoonrakerWS
        lines: list[str] = []
        status: dict[str, Any] = {}
        async with MoonrakerWS(app.client) as ws:
            await ws.subscribe({"print_stats": ["state", "info"], "toolhead": ["homed_axes"]})
            end = time.monotonic() + seconds
            while time.monotonic() < end and len(lines) < max_lines:
                try:
                    msg = await asyncio.wait_for(ws.notifications.get(), timeout=max(0.05, end - time.monotonic()))
                except asyncio.TimeoutError:
                    break
                if msg.get("method") == "notify_gcode_response":
                    lines.extend(str(p) for p in msg.get("params", []))
                elif msg.get("method") == "notify_status_update":
                    for k, v in (msg.get("params") or [{}])[0].items():
                        status.setdefault(k, {}).update(v)
        return {"lines": lines, "status_updates": status}
