"""In-memory fake of the Moonraker HTTP API (httpx.MockTransport handler).

It models just enough of Klipper to exercise the server: object status,
gcode scripts with a tiny motion model, a gcode_store journal, temperature
store, files, print actions and klippy.log.
"""

from __future__ import annotations

import copy
import json
import re
from typing import Callable

import httpx

STEPS_PER_MM = 80


class FakeMoonraker:
    def __init__(self) -> None:
        self.status: dict[str, dict] = {
            "webhooks": {"state": "ready", "state_message": "Printer is ready"},
            "toolhead": {
                "homed_axes": "", "position": [175.0, 175.0, 10.0, 0.0],
                "axis_minimum": [0.0, 0.0, -5.0, 0.0], "axis_maximum": [350.0, 350.0, 340.0, 0.0],
                "max_velocity": 300.0, "max_accel": 5000.0, "minimum_cruise_ratio": 0.5,
                "square_corner_velocity": 5.0, "extruder": "extruder",
            },
            "gcode_move": {"position": [175.0, 175.0, 10.0, 0.0], "gcode_position": [175.0, 175.0, 10.0, 0.0],
                           "homing_origin": [0.0, 0.0, 0.0, 0.0], "absolute_coordinates": True},
            "motion_report": {"live_position": [175.0, 175.0, 10.0, 0.0], "live_velocity": 0.0},
            "idle_timeout": {"state": "Idle"},
            "print_stats": {"state": "standby", "filename": ""},
            "virtual_sdcard": {"progress": 0.0, "is_active": False},
            "extruder": {"temperature": 25.0, "target": 0.0, "power": 0.0, "pressure_advance": 0.04,
                         "smooth_time": 0.04, "can_extrude": False},
            "heater_bed": {"temperature": 24.0, "target": 0.0, "power": 0.0},
            "temperature_sensor radiateur_hotend": {"temperature": 30.0},
            "temperature_sensor chamber": {"temperature": 28.0},
            "fan": {"speed": 0.0},
            "heater_fan hotend_fan": {"speed": 1.0},
            "gcode_button probe_switch": {"state": "RELEASED"},
            "stepper_enable": {"steppers": {"stepper_x": False}},
            "configfile": {"settings": {
                "input_shaper": {"shaper_type_x": "mzv", "shaper_freq_x": 50.0},
                "stepper_z": {"rotation_distance": 40.0, "microsteps": 16, "full_steps_per_rotation": 200,
                              "gear_ratio": [[80.0, 16.0]]},
                "idle_timeout": {"timeout": 1800},
            }},
        }
        self.scripts: list[str] = []
        self.store: list[dict] = []
        self.t = 1000.0
        self.errors: dict[str, str] = {}
        self.responders: dict[str, Callable[[str], list[str]]] = {
            "GET_POSITION": self._get_position,
        }
        self.mcu_shift = {"stepper_x": 0, "stepper_y": 0}
        self.z_mcu = {"stepper_z": 0, "stepper_z1": 0, "stepper_z2": 0, "stepper_z3": 0}
        self.lost_pending = 0
        self.loss_above_velocity: float | None = None
        self.velocity = 300.0
        self.estop = 0
        self.restarts: list[str] = []
        self.actions: list[tuple[str, str | None]] = []
        self.uploads: list[bytes] = []
        self.temp_store: dict = {}
        self.log_text = ""
        self.files: list[dict] = []
        self.on_script: Callable[[str], None] | None = None

    # ------------------------------------------------------------- helpers
    def add_store(self, msg: str, typ: str) -> None:
        self.t += 1.0
        self.store.append({"message": msg, "time": self.t, "type": typ})

    def _get_position(self, _line: str) -> list[str]:
        x, y, z = self.status["toolhead"]["position"][:3]
        mx = round(x * STEPS_PER_MM) + self.mcu_shift["stepper_x"]
        my = round(y * STEPS_PER_MM) + self.mcu_shift["stepper_y"]
        zs = " ".join(f"{k}:{v + round(z * STEPS_PER_MM)}" for k, v in self.z_mcu.items())
        zmm = " ".join(f"{k}:{z:.6f}" for k in self.z_mcu)
        return ["// mcu: stepper_x:%d stepper_y:%d %s\n// stepper: stepper_x:%.6f stepper_y:%.6f %s\n"
                "// kinematic: X:%.6f Y:%.6f Z:%.6f\n// toolhead: X:%.6f Y:%.6f Z:%.6f E:0.000000\n"
                "// gcode: X:%.6f Y:%.6f Z:%.6f E:0.000000" % (mx, my, zs, x, y, zmm, x, y, z, x, y, z, x, y, z)]

    def _apply_motion(self, line: str) -> None:
        th = self.status["toolhead"]
        gm = self.status["gcode_move"]
        parts = line.split()
        cmd = parts[0].upper()
        if cmd == "G90":
            gm["absolute_coordinates"] = True
        elif cmd == "G91":
            gm["absolute_coordinates"] = False
        elif cmd == "G28":
            axes = [a.lower() for a in "XYZ" if a in line[3:].upper()] or ["x", "y", "z"]
            th["homed_axes"] = "".join(a for a in "xyz" if a in th["homed_axes"] or a in axes)
            if "x" in axes:
                th["position"][0] = 350.0
            if "y" in axes:
                th["position"][1] = 350.0
            if "z" in axes:
                th["position"][2] = 0.0
            # physical shift appears when re-homing after lost steps
            self.mcu_shift["stepper_x"] += self.lost_pending
            self.mcu_shift["stepper_y"] -= self.lost_pending
            self.lost_pending = 0
        elif cmd in ("G0", "G1"):
            fast = False
            for tok in parts[1:]:
                ax, val = tok[0].upper(), tok[1:]
                if ax in "XYZ":
                    i = "XYZ".index(ax)
                    v = float(val)
                    th["position"][i] = v if gm["absolute_coordinates"] else th["position"][i] + v
                if ax == "F" and self.loss_above_velocity and float(val) / 60 > self.loss_above_velocity:
                    fast = True
            if fast:
                self.lost_pending = 32
        elif cmd == "SET_VELOCITY_LIMIT":
            m = re.search(r"VELOCITY=([\d.]+)", line)
            if m:
                th["max_velocity"] = float(m.group(1))
            m = re.search(r"ACCEL=([\d.]+)", line)
            if m:
                th["max_accel"] = float(m.group(1))
        elif cmd == "FORCE_MOVE":
            st = re.search(r"STEPPER=(\S+)", line).group(1)
            d = float(re.search(r"DISTANCE=(-?[\d.]+)", line).group(1))
            self.z_mcu[st] += round(d * STEPS_PER_MM)
        gm["position"] = list(th["position"])
        gm["gcode_position"] = list(th["position"])

    def run_script(self, script: str) -> str | None:
        self.scripts.append(script)
        if self.on_script:
            self.on_script(script)
        for line in script.splitlines():
            self.add_store(line, "command")
            cmd = line.split()[0].upper()
            if cmd in self.errors:
                self.add_store("!! " + self.errors[cmd], "response")
                return self.errors[cmd]
            if cmd in self.responders:
                for r in self.responders[cmd](line):
                    self.add_store(r, "response")
            self._apply_motion(line)
        return None

    # ------------------------------------------------------------- handler
    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        ok = lambda res: httpx.Response(200, json={"result": res})  # noqa: E731
        if path == "/printer/objects/list":
            return ok({"objects": list(self.status)})
        if path == "/printer/objects/query":
            objs = json.loads(request.content)["objects"]
            st = {}
            for k, fields in objs.items():
                if k in self.status:
                    full = copy.deepcopy(self.status[k])
                    st[k] = full if not fields else {f: full[f] for f in fields if f in full}
            return ok({"eventtime": self.t, "status": st})
        if path == "/printer/gcode/script":
            err = self.run_script(json.loads(request.content)["script"])
            if err:
                return httpx.Response(400, json={"error": {"code": 400, "message": err}})
            return ok("ok")
        if path == "/server/gcode_store":
            count = int(request.url.params.get("count", 100))
            return ok({"gcode_store": self.store[-count:]})
        if path == "/printer/emergency_stop":
            self.estop += 1
            return ok("ok")
        if path in ("/printer/restart", "/printer/firmware_restart"):
            self.restarts.append(path)
            return ok("ok")
        if path == "/printer/query_endstops/status":
            return ok({"x": "open", "y": "open", "z": "TRIGGERED"})
        if path.startswith("/printer/print/"):
            action = path.rsplit("/", 1)[1]
            self.actions.append((action, request.url.params.get("filename")))
            if action == "pause":
                self.status["print_stats"]["state"] = "paused"
            return ok("ok")
        if path == "/printer/info":
            return ok({"state": "ready", "software_version": "fake"})
        if path == "/server/temperature_store":
            return ok(self.temp_store)
        if path == "/server/files/list":
            return ok(self.files)
        if path == "/server/files/upload":
            self.uploads.append(request.content)
            return httpx.Response(201, json={"result": {"item": {"path": "x.gcode", "root": "gcodes"}}})
        if path == "/server/files/klippy.log":
            return httpx.Response(200, text=self.log_text)
        return httpx.Response(404, json={"error": {"code": 404, "message": f"not found {path}"}})
