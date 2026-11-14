"""Configuration, read exclusively from environment variables.

Every host-specific value has a neutral default so the code base never
contains anything tied to a particular installation.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _env(name: str, default: str | None = None) -> str | None:
    val = os.environ.get(name)
    if val is None or val.strip() == "":
        return default
    return val.strip()


def _float(name: str, default: float | None) -> float | None:
    val = _env(name)
    if val is None:
        return default
    return float(val)


def _bool(name: str, default: bool) -> bool:
    val = _env(name)
    if val is None:
        return default
    return val.lower() in {"1", "true", "yes", "on", "armed"}


def _list(name: str, default: str) -> list[str]:
    raw = _env(name, default) or ""
    return [p.strip() for p in raw.split(",") if p.strip()]


@dataclass
class KeepOut:
    """Axis-aligned box (toolhead coordinates) that motion must never enter.

    Typical use: the tool docks at the front of the machine. Any bound left
    as ``None`` is unbounded on that side. Disabled when y_min/y_max unset.
    """

    x_min: float | None = None
    x_max: float | None = None
    y_min: float | None = None
    y_max: float | None = None
    z_min: float | None = None  # box starts at this height and goes up
    z_max: float | None = None

    @property
    def enabled(self) -> bool:
        return self.y_min is not None or self.y_max is not None

    def as_dict(self) -> dict:
        return {k: getattr(self, k) for k in ("x_min", "x_max", "y_min", "y_max", "z_min", "z_max")}


@dataclass
class Config:
    moonraker_url: str = "http://printer.local:7125"
    api_key: str | None = None
    http_timeout: float = 30.0
    long_timeout: float = 3600.0
    store_settle_s: float = 0.15

    armed_at_start: bool = False
    arm_ttl_min: float = 60.0
    data_dir: Path = field(default_factory=lambda: Path.home() / ".voron-mcp")
    audit_log: Path | None = None
    gcode_dir: Path | None = None

    z_max_safe: float | None = None
    keepout: KeepOut = field(default_factory=KeepOut)
    max_move_speed: float = 500.0  # mm/s
    max_force_move_mm: float = 10.0
    max_extruder_temp: float = 300.0
    max_bed_temp: float = 120.0
    single_flight_cmds: list[str] = field(
        default_factory=lambda: ["G28", "QUAD_GANTRY_LEVEL", "Z_TILT_ADJUST", "BED_MESH_CALIBRATE"]
    )

    radiator_sensor: str = "radiateur_hotend"
    radiator_max_c: float = 55.0
    radiator_action: str = "pause"  # pause | cancel

    z_steppers: list[str] = field(
        default_factory=lambda: ["stepper_z", "stepper_z1", "stepper_z2", "stepper_z3"]
    )
    # Voron 2.4 convention: Z0 front-left, Z1 rear-left, Z2 rear-right, Z3 front-right
    z_corner_map: dict[str, str] = field(
        default_factory=lambda: {
            "front_left": "stepper_z",
            "rear_left": "stepper_z1",
            "rear_right": "stepper_z2",
            "front_right": "stepper_z3",
        }
    )
    lever_factor: float = 1.0
    max_gantry_correction_mm: float = 3.0

    transport: str = "stdio"
    host: str = "127.0.0.1"
    port: int = 8000

    def __post_init__(self) -> None:
        self.data_dir = Path(self.data_dir)
        if self.audit_log is None:
            self.audit_log = self.data_dir / "audit.jsonl"
        if self.gcode_dir is None:
            self.gcode_dir = self.data_dir / "gcodes"

    @classmethod
    def from_env(cls) -> "Config":
        data_dir = Path(_env("VORON_MCP_DATA_DIR", str(Path.home() / ".voron-mcp")))
        audit = _env("VORON_MCP_AUDIT_LOG")
        gdir = _env("VORON_MCP_GCODE_DIR")
        corner_raw = _env("Z_CORNER_MAP")
        corner_map = None
        if corner_raw:
            corner_map = {}
            for item in corner_raw.split(","):
                corner, _, stepper = item.partition(":")
                corner_map[corner.strip()] = stepper.strip()
        cfg = cls(
            moonraker_url=(_env("MOONRAKER_URL", "http://printer.local:7125") or "").rstrip("/"),
            api_key=_env("MOONRAKER_API_KEY"),
            http_timeout=_float("VORON_MCP_HTTP_TIMEOUT", 30.0) or 30.0,
            long_timeout=_float("VORON_MCP_LONG_TIMEOUT", 3600.0) or 3600.0,
            armed_at_start=_bool("VORON_MCP_ARMED", False),
            arm_ttl_min=_float("VORON_MCP_ARM_TTL_MIN", 60.0) or 0.0,
            data_dir=data_dir,
            audit_log=Path(audit) if audit else None,
            gcode_dir=Path(gdir) if gdir else None,
            z_max_safe=_float("Z_MAX_SAFE", None),
            keepout=KeepOut(
                x_min=_float("KEEPOUT_X_MIN", None),
                x_max=_float("KEEPOUT_X_MAX", None),
                y_min=_float("KEEPOUT_Y_MIN", None),
                y_max=_float("KEEPOUT_Y_MAX", None),
                z_min=_float("KEEPOUT_Z_MIN", None),
                z_max=_float("KEEPOUT_Z_MAX", None),
            ),
            max_move_speed=_float("MAX_MOVE_SPEED", 500.0) or 500.0,
            max_force_move_mm=_float("MAX_FORCE_MOVE_MM", 10.0) or 10.0,
            max_extruder_temp=_float("MAX_EXTRUDER_TEMP", 300.0) or 300.0,
            max_bed_temp=_float("MAX_BED_TEMP", 120.0) or 120.0,
            single_flight_cmds=[c.upper() for c in _list(
                "SINGLE_FLIGHT_CMDS", "G28,QUAD_GANTRY_LEVEL,Z_TILT_ADJUST,BED_MESH_CALIBRATE")],
            radiator_sensor=_env("RADIATOR_SENSOR", "radiateur_hotend") or "radiateur_hotend",
            radiator_max_c=_float("RADIATOR_MAX_C", 55.0) or 55.0,
            radiator_action=(_env("RADIATOR_ACTION", "pause") or "pause").lower(),
            z_steppers=_list("Z_STEPPERS", "stepper_z,stepper_z1,stepper_z2,stepper_z3"),
            lever_factor=_float("LEVER_FACTOR", 1.0) or 1.0,
            max_gantry_correction_mm=_float("MAX_GANTRY_CORRECTION_MM", 3.0) or 3.0,
            transport=(_env("MCP_TRANSPORT", "stdio") or "stdio").lower(),
            host=_env("MCP_HOST", "127.0.0.1") or "127.0.0.1",
            port=int(_env("MCP_PORT", "8000") or 8000),
        )
        if corner_map:
            cfg.z_corner_map = corner_map
        return cfg

    def public(self) -> dict:
        """Non-secret view of the configuration (API key redacted)."""
        return {
            "moonraker_url": self.moonraker_url,
            "api_key_set": bool(self.api_key),
            "data_dir": str(self.data_dir),
            "audit_log": str(self.audit_log),
            "z_max_safe": self.z_max_safe,
            "keepout": self.keepout.as_dict() if self.keepout.enabled else None,
            "max_move_speed": self.max_move_speed,
            "max_force_move_mm": self.max_force_move_mm,
            "radiator": {
                "sensor": self.radiator_sensor,
                "max_c": self.radiator_max_c,
                "action": self.radiator_action,
            },
            "z_steppers": self.z_steppers,
            "z_corner_map": self.z_corner_map,
            "lever_factor": self.lever_factor,
            "single_flight_cmds": self.single_flight_cmds,
        }
