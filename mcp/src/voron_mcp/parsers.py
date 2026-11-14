"""Parsers for Klipper console output."""

from __future__ import annotations

import re
from typing import Any

_NUM = re.compile(r"^-?\d+(\.\d+)?(e-?\d+)?$", re.I)


def _strip(line: str) -> str:
    line = line.strip()
    if line.startswith("//"):
        line = line[2:].strip()
    return line


def _num(v: str) -> Any:
    if _NUM.match(v):
        return float(v) if any(c in v for c in ".eE") else int(v)
    return v


def parse_kv_pairs(text: str) -> dict[str, Any]:
    """'stepper_x:12 stepper_y:-3' or 'X:1.0 Y:2.0' -> dict."""
    out: dict[str, Any] = {}
    for tok in text.split():
        if ":" in tok:
            k, _, v = tok.rpartition(":")
            out[k] = _num(v)
    return out


def parse_get_position(lines: list[str]) -> dict[str, dict[str, Any]]:
    """Parse GET_POSITION output into sections (mcu, stepper, kinematic,
    toolhead, gcode, gcode base, gcode homing)."""
    out: dict[str, dict[str, Any]] = {}
    for raw in lines:
        line = _strip(raw)
        m = re.match(r"^([a-z ]+):\s+(.*)$", line)
        if not m:
            continue
        section, rest = m.group(1).strip(), m.group(2)
        kv = parse_kv_pairs(rest)
        if kv:
            out[section] = kv
    return out


def parse_accelerometer(lines: list[str]) -> list[dict[str, Any]]:
    """'accelerometer values (x, y, z): 470.7, 941.4, 9728.1' (optionally
    prefixed by a chip name)."""
    res = []
    for raw in lines:
        line = _strip(raw)
        m = re.search(r"(?:(\S+)\s+)?(?:accelerometer )?values \(x, y, z\):\s*([-\d.e]+),\s*([-\d.e]+),\s*([-\d.e]+)", line, re.I)
        if m:
            x, y, z = (float(m.group(i)) for i in (2, 3, 4))
            chip = m.group(1) if m.group(1) and m.group(1).lower() != "accelerometer" else None
            res.append({"chip": chip, "x": x, "y": y, "z": z, "norm": round((x * x + y * y + z * z) ** 0.5, 1)})
    return res


TMC_ERROR_FIELDS = {"ot", "s2ga", "s2gb", "s2vsa", "s2vsb", "s2g", "s2vs", "drv_err", "uv_cp", "otpw_ot"}
TMC_WARN_FIELDS = {"otpw", "ola", "olb", "reset", "t143", "t150", "t157"}


def parse_dump_tmc(lines: list[str]) -> dict[str, Any]:
    regs: dict[str, dict[str, Any]] = {}
    section = None
    other: list[str] = []
    for raw in lines:
        line = _strip(raw)
        if not line:
            continue
        if line.startswith("=") and "register" in line.lower():
            section = "write_only" if "write" in line.lower() else "queried"
            continue
        m = re.match(r"^([A-Za-z_0-9]+):\s+([0-9a-fA-F]{8}|-?\d+)\b\s*(.*)$", line)
        if not m:
            other.append(line)
            continue
        name, value, rest = m.groups()
        fields: dict[str, Any] = {}
        for tok in rest.split():
            if "=" in tok:
                k, _, v = tok.partition("=")
                vm = re.match(r"^-?\d+", v)
                fields[k] = int(vm.group(0)) if vm else v
                if vm and v != vm.group(0):
                    fields[k + "_text"] = v
        regs[name] = {"value": value, "fields": fields, "section": section}
    errors, warnings = [], []
    for name, reg in regs.items():
        for k, v in reg["fields"].items():
            if isinstance(v, int) and v:
                if k in TMC_ERROR_FIELDS:
                    errors.append(f"{name}.{k}")
                elif k in TMC_WARN_FIELDS:
                    warnings.append(f"{name}.{k}")
    for comm_reg in ("IOIN", "IFCNT", "DRV_STATUS"):
        reg = regs.get(comm_reg)
        if reg and reg["value"].lower() in {"ffffffff"}:
            errors.append(f"{comm_reg}=ffffffff (communication fault?)")
    return {"registers": regs, "errors": errors, "warnings": warnings, "other": other}


def parse_pid(lines: list[str]) -> dict[str, float] | None:
    text = "\n".join(lines)
    m = re.search(r"pid_Kp=([-\d.]+)\s+pid_Ki=([-\d.]+)\s+pid_Kd=([-\d.]+)", text)
    if not m:
        return None
    return {"Kp": float(m.group(1)), "Ki": float(m.group(2)), "Kd": float(m.group(3))}


def parse_shaper(lines: list[str]) -> dict[str, Any]:
    """Parse SHAPER_CALIBRATE output (per-axis fitted shapers + recommendation)."""
    axes: dict[str, dict[str, Any]] = {}
    current = None
    files = []
    for raw in lines:
        line = _strip(raw)
        m = re.search(r"parameters for (\S+) axis", line, re.I)
        if m:
            current = m.group(1).lower()
            axes.setdefault(current, {"fitted": {}})
        m = re.search(
            r"Fitted shaper '(\w+)' frequency = ([\d.]+) Hz \(vibrations = ([\d.]+)%, smoothing ~= ([\d.]+)\)",
            line,
        )
        if m:
            ax = axes.setdefault(current or "?", {"fitted": {}})
            ax["fitted"][m.group(1)] = {
                "freq": float(m.group(2)),
                "vibrations_pct": float(m.group(3)),
                "smoothing": float(m.group(4)),
            }
        m = re.search(r"with '(\w+)', suggested max_accel <= ([\d.]+)", line)
        if m:
            ax = axes.setdefault(current or "?", {"fitted": {}})
            ax["fitted"].setdefault(m.group(1), {})["max_accel"] = float(m.group(2))
        m = re.search(r"Recommended shaper_type_([xyz]) = (\w+), shaper_freq_\1 = ([\d.]+) Hz", line)
        if m:
            ax = axes.setdefault(m.group(1), {"fitted": {}})
            ax["recommended"] = {"type": m.group(2), "freq": float(m.group(3))}
            fitted = ax["fitted"].get(m.group(2), {})
            if "max_accel" in fitted:
                ax["recommended"]["max_accel"] = fitted["max_accel"]
        m = re.search(r"written to (\S+)", line)
        if m:
            files.append(m.group(1))
    return {"axes": axes, "files_on_host": files}


def parse_written_files(lines: list[str]) -> list[str]:
    out = []
    for raw in lines:
        m = re.search(r"written to (\S+)", raw)
        if m:
            out.append(m.group(1))
    return out


def parse_endstops_text(lines: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in lines:
        for k, v in parse_kv_pairs(_strip(raw)).items():
            out[k] = str(v)
    return out
