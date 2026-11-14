"""Manual gantry leveling helpers based on per-Z-motor MCU step counts.

Workflow: at each corner, bring the nozzle (or a feeler) into contact with
the reference and call record_contact_point(label=<corner>). Then
compute_gantry_correction() and apply_gantry_correction() (raise-only).

Sign convention: Z steps increase upward. When lowering to a contact, a
corner that touches at a *higher* step count sits *lower* than the others,
so it must be raised by the difference.
"""

import csv
import json
import time
from pathlib import Path
from typing import Any

from ..app import App
from ..parsers import parse_get_position
from ..safety import SafetyError
from .motion import interleaved_force_moves


def _points_path(app: App) -> Path:
    return app.cfg.data_dir / "contact_points.json"


def load_points(app: App) -> dict[str, dict]:
    p = _points_path(app)
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def save_points(app: App, pts: dict[str, dict]) -> None:
    p = _points_path(app)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(pts, indent=1), encoding="utf-8")


async def z_mm_per_step(app: App, stepper: str) -> float | None:
    st = await app.client.query({"configfile": ["settings"]})
    s = st.get("configfile", {}).get("settings", {}).get(stepper)
    if not s:
        return None
    rd = float(s.get("rotation_distance", 0) or 0)
    micro = int(s.get("microsteps", 16) or 16)
    full = int(s.get("full_steps_per_rotation", 200) or 200)
    ratio = 1.0
    for pair in s.get("gear_ratio") or []:
        a, b = pair
        ratio *= float(a) / float(b)
    if rd <= 0:
        return None
    return rd / (full * micro * ratio)


def compute_correction(points: dict[str, dict], corner_map: dict[str, str], lever: float,
                       mm_per_step: float, reference: dict | None = None,
                       labels: dict[str, str] | None = None) -> dict:
    labels = labels or {c: c for c in corner_map}
    deltas: dict[str, int] = {}
    for corner, stepper in corner_map.items():
        lab = labels.get(corner, corner)
        if lab not in points:
            raise KeyError(f"missing contact point '{lab}' for corner {corner}")
        steps = points[lab]["mcu"].get(stepper)
        if steps is None:
            raise KeyError(f"point '{lab}' has no mcu count for {stepper}")
        if reference is not None:
            steps -= reference["mcu"][stepper]
        deltas[stepper] = int(steps)
    low = min(deltas.values())
    corr_steps = {s: round((d - low) * lever) for s, d in deltas.items()}
    corr_mm = {s: round(v * mm_per_step, 4) for s, v in corr_steps.items()}
    return {
        "relative_contact_steps": {s: d - low for s, d in deltas.items()},
        "spread_mm": round((max(deltas.values()) - low) * mm_per_step, 4),
        "lever_factor": lever,
        "mm_per_step": mm_per_step,
        "raise_steps": corr_steps,
        "raise_mm": corr_mm,
    }


def register(app: App, tool: Any) -> None:
    cfg = app.cfg

    @tool("audit")
    async def record_contact_point(label: str, note: str = "") -> dict:
        """GET_POSITION and store X/Y + per-Z-motor MCU steps (and stepper mm)
        under `label` (e.g. front_left) in the data dir (JSON + CSV history)."""
        lines = await app.capture("GET_POSITION")
        pos = parse_get_position(lines)
        mcu, stp, th = pos.get("mcu", {}), pos.get("stepper", {}), pos.get("toolhead", {})
        missing = [s for s in cfg.z_steppers if s not in mcu]
        if missing:
            raise SafetyError(f"GET_POSITION lacks {missing}: {lines[-4:]}")
        rec = {"label": label, "ts": time.time(), "x": th.get("X"), "y": th.get("Y"), "z": th.get("Z"),
               "mcu": {s: mcu[s] for s in cfg.z_steppers},
               "stepper_mm": {s: stp.get(s) for s in cfg.z_steppers}, "note": note}
        pts = load_points(app)
        pts[label] = rec
        save_points(app, pts)
        csv_path = cfg.data_dir / "contact_points.csv"
        new = not csv_path.exists()
        with open(csv_path, "a", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            if new:
                w.writerow(["ts", "label", "x", "y", "z", *cfg.z_steppers, "note"])
            w.writerow([rec["ts"], label, rec["x"], rec["y"], rec["z"], *rec["mcu"].values(), note])
        return rec

    @tool("read")
    async def list_contact_points() -> dict:
        """Stored contact points."""
        return {"points": load_points(app)}

    @tool("audit")
    async def clear_contact_points(labels: list[str] | None = None) -> dict:
        """Delete stored contact points (all, or the given labels)."""
        pts = load_points(app)
        removed = [k for k in list(pts) if labels is None or k in labels]
        for k in removed:
            pts.pop(k)
        save_points(app, pts)
        return {"removed": removed}

    @tool("read")
    async def compute_gantry_correction(labels: dict[str, str] | None = None,
                                        reference_label: str | None = None,
                                        lever_factor: float | None = None,
                                        mm_per_step: float | None = None) -> dict:
        """From the 4 corner contact points, compute raise-only corrections per Z
        motor (steps and mm), scaled by the lever factor (motor vs contact point
        distance). `labels` maps corner->label (default: corner names
        front_left/rear_left/rear_right/front_right). `reference_label`: a point
        recorded with all motors in a common state, subtracted per motor."""
        pts = load_points(app)
        ref = pts.get(reference_label) if reference_label else None
        if reference_label and ref is None:
            raise KeyError(f"no reference point '{reference_label}'")
        mps = mm_per_step or await z_mm_per_step(app, cfg.z_steppers[0])
        if not mps:
            raise ValueError("cannot determine mm_per_step from config; pass mm_per_step")
        res = compute_correction(pts, cfg.z_corner_map, lever_factor or cfg.lever_factor, mps, ref, labels)
        if max(res["raise_mm"].values()) > cfg.max_gantry_correction_mm:
            res["warning"] = f"correction above MAX_GANTRY_CORRECTION_MM={cfg.max_gantry_correction_mm}: apply will refuse"
        app.background["last_gantry_correction"] = res
        return res

    @tool("act")
    async def apply_gantry_correction(raise_mm: dict[str, float] | None = None, step_mm: float = 0.1,
                                      velocity: float = 2.0) -> dict:
        """Apply a raise-only correction with interleaved FORCE_MOVEs (each motor
        advances at most `step_mm` per round). Default: the last computed
        correction. Refuses negative values or totals above
        MAX_GANTRY_CORRECTION_MM. Re-home / re-level afterwards."""
        if raise_mm is None:
            last = app.background.get("last_gantry_correction")
            if not last:
                raise ValueError("no correction given or computed")
            raise_mm = last["raise_mm"]
        for s, v in raise_mm.items():
            if s not in cfg.z_steppers:
                raise ValueError(f"unknown Z stepper {s}")
            if v < 0:
                raise SafetyError("raise-only: negative correction refused")
            if v > cfg.max_gantry_correction_mm:
                raise SafetyError(f"{s}: {v} mm > MAX_GANTRY_CORRECTION_MM={cfg.max_gantry_correction_mm}")
        steppers = [s for s in cfg.z_steppers if raise_mm.get(s, 0) > 0]
        if not steppers:
            return {"applied": {}, "note": "nothing to do"}
        lines = interleaved_force_moves(steppers, raise_mm, step_mm, velocity, 0)
        await app.run_script("\n".join(lines + ["M400"]), confirm=True, timeout=600)
        return {"applied": {s: raise_mm[s] for s in steppers}, "commands": len(lines),
                "note": "re-home Z and re-level before printing"}
