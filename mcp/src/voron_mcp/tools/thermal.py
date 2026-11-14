"""Heaters, fans, LEDs, PID, radiator (cold-end) watchdog."""

import asyncio
import time
from typing import Any

from ..app import App
from ..parsers import parse_pid
from ..safety import SafetyError


async def resolve_sensor(app: App, name: str) -> str:
    objects = await app.client.list_objects()
    if name in objects:
        return name
    for prefix in ("temperature_sensor ", "temperature_fan ", "heater_generic "):
        if prefix + name in objects:
            return prefix + name
    raise KeyError(f"no temperature object named '{name}'")


async def read_radiator(app: App) -> float | None:
    obj = f"temperature_sensor {app.cfg.radiator_sensor}"
    st = await app.client.query({obj: ["temperature"]})
    return st.get(obj, {}).get("temperature")


async def radiator_trip(app: App, temp: float, action: str, heater_off: bool, origin: str) -> dict:
    """Protective action when the cold end is too hot. Bypasses arm on purpose."""
    done: list[str] = []
    st = await app.client.query({"print_stats": ["state"]})
    if st.get("print_stats", {}).get("state") == "printing":
        await app.client.print_action("cancel" if action == "cancel" else "pause")
        done.append(f"print {action}")
    stream = app.background.get("stream")
    if stream and not stream["task"].done():
        stream["stop"] = True
        done.append("stream stop requested")
    if heater_off:
        await app.client.gcode("SET_HEATER_TEMPERATURE HEATER=extruder TARGET=0", timeout=30)
        done.append("extruder heater off")
    res = {"trip": True, "temperature": temp, "threshold": app.cfg.radiator_max_c, "actions": done, "origin": origin}
    app.safety.audit("radiator_watchdog_trip", {}, res)
    return res


def register(app: App, tool: Any) -> None:
    cfg = app.cfg

    @tool("audit")
    async def set_heater(heater: str, target: float, wait: bool = False, tolerance: float = 2.0,
                         timeout_s: float = 900) -> dict:
        """SET_HEATER_TEMPERATURE. Heating (target>0) needs armed; turning off is
        always allowed. wait=true polls until within `tolerance` °C."""
        if target > 0:
            app.safety.require_armed("set_heater")
        limit = cfg.max_bed_temp if heater == "heater_bed" else cfg.max_extruder_temp
        if target < 0 or target > limit:
            raise SafetyError(f"target {target} outside [0, {limit}]")
        hname = heater.split(" ", 1)[1] if heater.startswith("heater_generic ") else heater
        await app.client.gcode(f"SET_HEATER_TEMPERATURE HEATER={hname} TARGET={target:g}", timeout=30)
        if not wait or target <= 0:
            return {"heater": heater, "target": target}
        t0 = time.monotonic()
        while time.monotonic() - t0 < timeout_s:
            st = await app.client.query({heater: ["temperature", "target"]})
            temp = st.get(heater, {}).get("temperature")
            if temp is not None and abs(temp - target) <= tolerance:
                return {"heater": heater, "target": target, "temperature": round(temp, 2),
                        "waited_s": round(time.monotonic() - t0, 1)}
            await asyncio.sleep(1.0)
        return {"ok": False, "error": "timeout waiting for temperature", "heater": heater}

    @tool("audit")
    async def heaters_off() -> dict:
        """TURN_OFF_HEATERS (always allowed)."""
        await app.client.gcode("TURN_OFF_HEATERS", timeout=30)
        return {"heaters": "off"}

    @tool("read")
    async def wait_for_temperature(sensor: str, above: float | None = None, below: float | None = None,
                                   timeout_s: float = 900, poll_s: float = 2.0) -> dict:
        """Wait until a sensor (extruder, heater_bed, any temperature_sensor/fan
        name) is above and/or below the given values."""
        if above is None and below is None:
            raise ValueError("give above and/or below")
        obj = await resolve_sensor(app, sensor)
        t0 = time.monotonic()
        temp = None
        while time.monotonic() - t0 < timeout_s:
            temp = (await app.client.query({obj: ["temperature"]})).get(obj, {}).get("temperature")
            if temp is not None and (above is None or temp >= above) and (below is None or temp <= below):
                return {"sensor": obj, "temperature": round(temp, 2), "waited_s": round(time.monotonic() - t0, 1)}
            await asyncio.sleep(poll_s)
        return {"ok": False, "error": "timeout", "sensor": obj, "temperature": temp}

    @tool("act")
    async def set_fan(fan: str = "part", speed: float = 0.0) -> dict:
        """Fan speed 0..1. fan='part' -> M106/M107; otherwise a [fan_generic] name
        -> SET_FAN_SPEED."""
        if not 0 <= speed <= 1:
            raise ValueError("speed must be within 0..1")
        if fan in ("part", "fan"):
            cmd = "M107" if speed == 0 else f"M106 S{round(speed * 255)}"
        else:
            cmd = f"SET_FAN_SPEED FAN={fan} SPEED={speed:g}"
        await app.client.gcode(cmd, timeout=30)
        return {"sent": cmd}

    @tool("audit")
    async def set_led(led: str, red: float = 0, green: float = 0, blue: float = 0, white: float = 0,
                      index: int | None = None, transmit: bool = True) -> dict:
        """SET_LED on a neopixel/led (values 0..1). Use transmit=false to chain
        several indexes and send the last one with transmit=true."""
        for v in (red, green, blue, white):
            if not 0 <= v <= 1:
                raise ValueError("colour values must be within 0..1")
        cmd = f"SET_LED LED={led} RED={red:g} GREEN={green:g} BLUE={blue:g} WHITE={white:g}"
        if index is not None:
            cmd += f" INDEX={index}"
        cmd += f" TRANSMIT={1 if transmit else 0}"
        await app.client.gcode(cmd, timeout=30)
        return {"sent": cmd}

    @tool("act")
    async def pid_calibrate(heater: str, target: float, timeout_s: float = 1200) -> dict:
        """PID_CALIBRATE (long-running, blocks until done). Returns Kp/Ki/Kd.
        Persist with gcode('SAVE_CONFIG', confirm=true) — this restarts Klipper."""
        limit = cfg.max_bed_temp if heater == "heater_bed" else cfg.max_extruder_temp
        if not 0 < target <= limit:
            raise SafetyError(f"target must be in (0, {limit}]")
        res = await app.run_script(f"PID_CALIBRATE HEATER={heater} TARGET={target:g}",
                                   capture=True, timeout=timeout_s)
        lines = res.get("response") or []
        pid = parse_pid(lines)
        return {"heater": heater, "target": target, "pid": pid, "raw": lines[-6:],
                **({} if pid else {"ok": False, "error": "PID result not found in console output"})}

    @tool("read")
    async def radiator_check() -> dict:
        """Current cold-end/radiator temperature vs the configured threshold."""
        temp = await read_radiator(app)
        return {"sensor": cfg.radiator_sensor, "temperature": temp, "threshold": cfg.radiator_max_c,
                "over": temp is not None and temp > cfg.radiator_max_c}

    @tool("audit")
    async def watchdog_start(interval_s: float = 5.0, action: str | None = None, heater_off: bool = True,
                             max_c: float | None = None) -> dict:
        """Background radiator watchdog: every `interval_s`, if the cold-end sensor
        exceeds the threshold, pause (or cancel) the print, stop any stream and
        (default) switch the extruder heater off. Runs until watchdog_stop."""
        old = app.background.get("watchdog")
        if old and not old["task"].done():
            return {"already_running": True, "status": old["status"]}
        act = (action or cfg.radiator_action).lower()
        if act not in ("pause", "cancel"):
            raise ValueError("action must be pause or cancel")
        threshold = max_c if max_c is not None else cfg.radiator_max_c
        state: dict[str, Any] = {"stop": False, "status": {"running": True, "checks": 0, "last": None,
                                                          "threshold": threshold, "action": act}}

        async def loop() -> None:
            while not state["stop"]:
                try:
                    temp = await read_radiator(app)
                    state["status"]["checks"] += 1
                    state["status"]["last"] = temp
                    if temp is not None and temp > threshold:
                        state["status"]["tripped"] = await radiator_trip(app, temp, act, heater_off, "watchdog")
                        break
                except Exception as exc:  # keep watching through transient errors
                    state["status"]["last_error"] = repr(exc)
                await asyncio.sleep(interval_s)
            state["status"]["running"] = False

        state["task"] = asyncio.create_task(loop())
        app.background["watchdog"] = state
        return {"started": True, **state["status"]}

    @tool("audit")
    async def watchdog_stop() -> dict:
        """Stop the radiator watchdog."""
        wd = app.background.get("watchdog")
        if not wd:
            return {"running": False}
        wd["stop"] = True
        wd["task"].cancel()
        wd["status"]["running"] = False
        return {"stopped": True, "status": wd["status"]}
