"""Safety model: arm/disarm, audit log, gcode classification, motion guards,
single-flight homing."""

from __future__ import annotations

import asyncio
import contextlib
import json
import math
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, AsyncIterator

from .config import Config, KeepOut


class SafetyError(Exception):
    """Raised when a guard refuses an action."""


# Commands that never change the machine state: allowed while disarmed.
READONLY_CMDS = {
    "GET_POSITION", "M114", "M105", "M115", "M119", "M400", "QUERY_ENDSTOPS", "QUERY_BUTTON",
    "ACCELEROMETER_QUERY", "DUMP_TMC", "STATUS", "HELP", "QUERY_PROBE", "QUERY_FILAMENT_SENSOR",
    "QUERY_ADC", "GET_RETRACTION", "M117", "M118", "RESPOND", "PROBE_ACCURACY_QUERY",
    "ANGLE_DEBUG_READ", "TMC_STATUS", "QUERY_TMC",
}
# Commands bypassing kinematics/limits or touching firmware: need armed + confirm=true.
DANGEROUS_CMDS = {
    "SET_KINEMATIC_POSITION", "FORCE_MOVE", "SET_STEPPER_ENABLE", "FIRMWARE_RESTART", "RESTART",
    "SAVE_CONFIG", "SET_TMC_FIELD", "ACCELEROMETER_DEBUG_WRITE", "MANUAL_STEPPER", "M997",
    "SDCARD_RESET_FILE", "SET_KINEMATIC_POSITION_RAW",
}
EMERGENCY_CMDS = {"M112"}
MOVE_CMDS = {"G0", "G1", "G2", "G3"}

_CMD_RE = re.compile(r"^([GMT]\d+(?:\.\d+)?)(?!_)", re.I)
_AXIS_RE = re.compile(r"([XYZEF])\s*(-?\d*\.?\d+(?:e-?\d+)?)", re.I)
_EXT_PARAM_RE = re.compile(r"(\w+)=(\S+)")


def split_lines(script: str) -> list[str]:
    out = []
    for raw in script.replace("\r", "\n").split("\n"):
        line = raw.split(";", 1)[0].strip()
        if line:
            out.append(line)
    return out


def command_of(line: str) -> str:
    m = _CMD_RE.match(line)
    if m:
        cmd = m.group(1).upper()
        # G01 -> G1
        return cmd[0] + str(int(float(cmd[1:]))) if "." not in cmd else cmd
    return line.split()[0].upper()


def axis_params(line: str) -> dict[str, float]:
    m = _CMD_RE.match(line)
    rest = line[m.end():] if m else line
    return {k.upper(): float(v) for k, v in _AXIS_RE.findall(rest)}


def ext_params(line: str) -> dict[str, str]:
    return {k.upper(): v for k, v in _EXT_PARAM_RE.findall(line)}


@dataclass
class ScriptInfo:
    lines: list[str]
    commands: list[str]
    readonly: bool
    dangerous: list[str]
    emergency: bool
    single_flight: list[str]

    def as_dict(self) -> dict:
        return {
            "lines": len(self.lines),
            "readonly": self.readonly,
            "dangerous": self.dangerous,
            "single_flight": self.single_flight,
        }


def classify(script: str, single_flight_cmds: list[str]) -> ScriptInfo:
    lines = split_lines(script)
    cmds = [command_of(line) for line in lines]
    sf = set(c.upper() for c in single_flight_cmds)
    return ScriptInfo(
        lines=lines,
        commands=cmds,
        readonly=all(c in READONLY_CMDS for c in cmds),
        dangerous=sorted({c for c in cmds if c in DANGEROUS_CMDS}),
        emergency=any(c in EMERGENCY_CMDS for c in cmds),
        single_flight=sorted({c for c in cmds if c in sf}),
    )


# --------------------------------------------------------------------- motion
def _segment_hits_box(p0: list[float | None], p1: list[float], box: KeepOut) -> bool:
    lo = [box.x_min, box.y_min, box.z_min]
    hi = [box.x_max, box.y_max, box.z_max]
    lo = [-math.inf if v is None else v for v in lo]
    hi = [math.inf if v is None else v for v in hi]
    if any(c is None for c in p0):
        return all(lo[i] <= p1[i] <= hi[i] for i in range(3))
    t0, t1 = 0.0, 1.0
    for i in range(3):
        a, b = p0[i], p1[i]
        d = b - a
        if abs(d) < 1e-12:
            if a < lo[i] or a > hi[i]:
                return False
            continue
        ta, tb = (lo[i] - a) / d, (hi[i] - a) / d
        if ta > tb:
            ta, tb = tb, ta
        t0, t1 = max(t0, ta), min(t1, tb)
        if t0 > t1:
            return False
    return True


@dataclass
class MotionSim:
    """Tracks the toolhead position through a script to check each move
    against soft limits, Z_MAX_SAFE and the keep-out box *before* sending."""

    cfg: Config
    axis_min: list[float]
    axis_max: list[float]
    homed: str
    absolute: bool
    pos: list[float | None]  # toolhead coordinates
    base: list[float]  # toolhead = gcode + base
    allow_keepout: bool = False
    warnings: list[str] = field(default_factory=list)
    unverified: list[str] = field(default_factory=list)
    _saved: dict = field(default_factory=dict)

    @classmethod
    def from_status(cls, cfg: Config, status: dict, allow_keepout: bool = False) -> "MotionSim":
        th = status.get("toolhead", {})
        gm = status.get("gcode_move", {})
        pos = list(th.get("position", [0, 0, 0, 0]))[:3]
        gpos = list(gm.get("gcode_position", pos))[:3]
        mpos = list(gm.get("position", pos))[:3]
        return cls(
            cfg=cfg,
            axis_min=list(th.get("axis_minimum", [-math.inf] * 3))[:3],
            axis_max=list(th.get("axis_maximum", [math.inf] * 3))[:3],
            homed=str(th.get("homed_axes", "")).lower(),
            absolute=bool(gm.get("absolute_coordinates", True)),
            pos=[float(p) for p in pos],
            base=[float(mpos[i]) - float(gpos[i]) for i in range(3)],
            allow_keepout=allow_keepout,
        )

    def check_point(self, target: list[float], label: str = "move") -> None:
        for i, ax in enumerate("xyz"):
            if ax not in self.homed:
                continue
            if target[i] < self.axis_min[i] - 1e-6 or target[i] > self.axis_max[i] + 1e-6:
                raise SafetyError(
                    f"{label}: {ax.upper()}={target[i]:.3f} outside soft limits "
                    f"[{self.axis_min[i]}, {self.axis_max[i]}]"
                )
        if self.cfg.z_max_safe is not None and "z" in self.homed and target[2] > self.cfg.z_max_safe + 1e-6:
            raise SafetyError(f"{label}: Z={target[2]:.3f} above Z_MAX_SAFE={self.cfg.z_max_safe}")

    def _move(self, line: str, params: dict[str, float]) -> None:
        target: list[float | None] = list(self.pos)
        for i, ax in enumerate("XYZ"):
            if ax not in params:
                continue
            v = params[ax]
            if self.absolute:
                target[i] = v + self.base[i]
            else:
                if self.pos[i] is None:
                    raise SafetyError(
                        f"cannot verify relative move '{line}' after a homing in the same script; "
                        "split the script (home first, then move)"
                    )
                target[i] = self.pos[i] + v
        if "F" in params and params["F"] / 60.0 > self.cfg.max_move_speed:
            raise SafetyError(f"'{line}': speed {params['F'] / 60:.0f} mm/s > MAX_MOVE_SPEED={self.cfg.max_move_speed}")
        known = [t if t is not None else 0.0 for t in target]
        if all(t is not None for t in target):
            self.check_point(known, f"'{line}'")
        else:
            for i in range(3):
                if target[i] is not None and "xyz"[i] in self.homed:
                    tmp = [self.axis_min[0], self.axis_min[1], self.axis_min[2]]
                    tmp[i] = target[i]
                    self.check_point(tmp, f"'{line}'")
        box = self.cfg.keepout
        if box.enabled and not self.allow_keepout and all(t is not None for t in target):
            if _segment_hits_box(self.pos, known, box):
                raise SafetyError(f"'{line}': path enters the keep-out zone {box.as_dict()}")
        self.pos = target

    def feed(self, line: str) -> None:
        cmd = command_of(line)
        if cmd in MOVE_CMDS:
            self._move(line, axis_params(line))
        elif cmd == "G90":
            self.absolute = True
        elif cmd == "G91":
            self.absolute = False
        elif cmd == "G28":
            axes = [a.lower() for a in "XYZ" if a in line[3:].upper()] or ["x", "y", "z"]
            for a in axes:
                self.pos["xyz".index(a)] = None
                if a not in self.homed:
                    self.homed += a
        elif cmd == "G92":
            for i, ax in enumerate("XYZ"):
                p = axis_params(line)
                if ax in p and self.pos[i] is not None:
                    self.base[i] = self.pos[i] - p[ax]
        elif cmd == "SET_KINEMATIC_POSITION":
            p = ext_params(line)
            for i, ax in enumerate("XYZ"):
                if ax in p:
                    self.pos[i] = float(p[ax])
                    if ax.lower() not in self.homed:
                        self.homed += ax.lower()
        elif cmd == "SAVE_GCODE_STATE":
            self._saved[ext_params(line).get("NAME", "default")] = self.absolute
        elif cmd == "RESTORE_GCODE_STATE":
            self.absolute = self._saved.get(ext_params(line).get("NAME", "default"), self.absolute)
        elif cmd in READONLY_CMDS or cmd in {"M82", "M83", "M106", "M107", "M104", "M140", "M109",
                                             "M190", "SET_HEATER_TEMPERATURE", "SET_FAN_SPEED",
                                             "SET_LED", "SET_PRESSURE_ADVANCE", "SET_VELOCITY_LIMIT",
                                             "SET_INPUT_SHAPER", "M204", "M220", "M221", "TURN_OFF_HEATERS",
                                             "SET_GCODE_OFFSET", "FORCE_MOVE", "SET_STEPPER_ENABLE",
                                             "M84", "M18", "TEMPERATURE_WAIT", "G4", "M112",
                                             "PID_CALIBRATE", "BED_MESH_PROFILE", "EXCLUDE_OBJECT",
                                             "EXCLUDE_OBJECT_DEFINE", "SET_PRINT_STATS_INFO"}:
            pass
        else:
            self.unverified.append(cmd)

    def feed_all(self, lines: list[str]) -> None:
        for line in lines:
            self.feed(line)


MOTION_OBJECTS = {
    "toolhead": ["position", "axis_minimum", "axis_maximum", "homed_axes"],
    "gcode_move": ["position", "gcode_position", "absolute_coordinates", "homing_origin"],
}


# --------------------------------------------------------------------- state
class Safety:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._armed_until: float | None = None
        self.arm_reason = ""
        if cfg.armed_at_start:
            self.arm(None, "VORON_MCP_ARMED at start")
        self.homing_lock = asyncio.Lock()
        self.single_flight_label = ""

    # -- arm / disarm
    def arm(self, minutes: float | None, reason: str = "") -> dict:
        ttl = minutes if minutes is not None else self.cfg.arm_ttl_min
        self._armed_until = time.time() + ttl * 60 if ttl and ttl > 0 else math.inf
        self.arm_reason = reason
        return self.status()

    def disarm(self) -> dict:
        self._armed_until = None
        self.arm_reason = ""
        return self.status()

    @property
    def armed(self) -> bool:
        if self._armed_until is None:
            return False
        if time.time() > self._armed_until:
            self._armed_until = None
            return False
        return True

    def require_armed(self, what: str) -> None:
        if not self.armed:
            raise SafetyError(f"{what}: refused, server is DISARMED (call arm first)")

    def status(self) -> dict:
        armed = self.armed
        remaining = None
        if armed and self._armed_until not in (None, math.inf):
            remaining = round((self._armed_until - time.time()) / 60, 1)
        return {
            "armed": armed,
            "armed_minutes_left": remaining,
            "reason": self.arm_reason if armed else "",
            "single_flight_busy": self.homing_lock.locked(),
            "single_flight_label": self.single_flight_label if self.homing_lock.locked() else "",
        }

    # -- audit
    def audit(self, tool: str, args: dict, result: Any) -> None:
        path = self.cfg.audit_log
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            res = json.dumps(result, default=str)
            if len(res) > 2000:
                res = res[:2000] + "...(truncated)"
            entry = {
                "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "tool": tool,
                "armed": self.armed,
                "args": args,
                "ok": not (isinstance(result, dict) and result.get("ok") is False),
                "result": res,
            }
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, default=str) + "\n")
        except OSError:
            pass  # never break a safety action because the log is unwritable

    # -- script checks
    def check_script(self, script: str, confirm: bool) -> ScriptInfo:
        info = classify(script, self.cfg.single_flight_cmds)
        if not info.lines:
            raise SafetyError("empty script")
        if info.emergency:
            return info
        if not info.readonly:
            self.require_armed("gcode")
        if info.dangerous and not confirm:
            raise SafetyError(
                f"dangerous command(s) {info.dangerous}: re-send with confirm=true if you really mean it"
            )
        return info

    @contextlib.asynccontextmanager
    async def single_flight(self, client: Any, label: str, busy_ok: bool = False) -> AsyncIterator[None]:
        """Refuse a homing/leveling if one is running (in this server or, as
        far as we can tell, on the printer). Never queue a second one."""
        if self.homing_lock.locked():
            raise SafetyError(
                f"{label}: refused, '{self.single_flight_label}' is already running (single-flight)"
            )
        async with self.homing_lock:
            self.single_flight_label = label
            st = await client.query({"idle_timeout": ["state"], "print_stats": ["state"]})
            if not busy_ok and st.get("print_stats", {}).get("state") in ("printing", "paused"):
                raise SafetyError(f"{label}: refused while a print is {st['print_stats']['state']}")
            if not busy_ok and st.get("idle_timeout", {}).get("state") == "Printing":
                raise SafetyError(
                    f"{label}: refused, printer is busy executing gcode (a homing may already be "
                    "running); wait_idle first instead of queueing another"
                )
            yield
