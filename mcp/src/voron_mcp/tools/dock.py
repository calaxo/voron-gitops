"""Tool-changer dock / pickup as explicit, parameterised step sequences.

All coordinates are toolhead (machine) coordinates; gcode offsets (tool
offsets) are compensated at execution time. Steps inside the dock are
allowed to enter the keep-out zone; the approach travel is not.
"""

from typing import Any

from ..app import App
from ..safety import SafetyError


def _f(v: float) -> str:
    return f"{v:.3f}".rstrip("0").rstrip(".")


def dock_steps(x: float, approach_y: float, dock_y: float, dock_z: float, release_z: float,
               travel_speed: float, slow_speed: float, z_speed: float) -> list[dict]:
    return [
        {"desc": f"travel to approach X{_f(x)} Y{_f(approach_y)} at Z{_f(dock_z)}",
         "moves": [({"Z": dock_z}, z_speed), ({"X": x, "Y": approach_y}, travel_speed)], "keepout": False},
        {"desc": f"slide into dock Y{_f(dock_y)}", "moves": [({"Y": dock_y}, slow_speed)], "keepout": True},
        {"desc": f"release: Z to {_f(release_z)}", "moves": [({"Z": release_z}, z_speed)], "keepout": True},
        {"desc": f"leave dock to Y{_f(approach_y)}", "moves": [({"Y": approach_y}, slow_speed)], "keepout": True},
    ]


def pickup_steps(x: float, approach_y: float, dock_y: float, release_z: float, pickup_z: float,
                 travel_speed: float, slow_speed: float, z_speed: float) -> list[dict]:
    return [
        {"desc": f"travel to approach X{_f(x)} Y{_f(approach_y)} at Z{_f(release_z)}",
         "moves": [({"Z": release_z}, z_speed), ({"X": x, "Y": approach_y}, travel_speed)], "keepout": False},
        {"desc": f"slide empty shuttle into dock Y{_f(dock_y)}", "moves": [({"Y": dock_y}, slow_speed)], "keepout": True},
        {"desc": f"engage: Z to {_f(pickup_z)}", "moves": [({"Z": pickup_z}, z_speed)], "keepout": True},
        {"desc": f"pull tool out to Y{_f(approach_y)}", "moves": [({"Y": approach_y}, slow_speed)], "keepout": True},
    ]


def register(app: App, tool: Any) -> None:
    async def exec_step(step: dict) -> dict:
        sim = await app.motion_sim(allow_keepout=step["keepout"])
        if not all(a in sim.homed for a in "xyz"):
            raise SafetyError("dock sequence requires X, Y and Z homed")
        idx = {"X": 0, "Y": 1, "Z": 2}

        def g1(target: dict, speed: float) -> str:
            parts = " ".join(f"{a}{_f(v - sim.base[idx[a]])}" for a, v in target.items())
            return f"G1 {parts} F{_f(speed * 60)}"

        lines = ["SAVE_GCODE_STATE NAME=_mcp_dock", "G90", *(g1(t, sp) for t, sp in step["moves"])]
        lines += ["M400", "RESTORE_GCODE_STATE NAME=_mcp_dock"]
        await app.run_script("\n".join(lines), allow_keepout=step["keepout"], timeout=300)
        return {"executed": step["desc"]}

    async def run_sequence(name: str, steps: list[dict], step_by_step: bool, dry_run: bool) -> dict:
        if dry_run:
            return {"sequence": name, "plan": [s["desc"] for s in steps]}
        if step_by_step:
            app.sequences["tool"] = {"name": name, "steps": steps, "index": 0}
            return await _next()
        done = []
        for s in steps:
            done.append((await exec_step(s))["executed"])
        return {"sequence": name, "executed": done, "complete": True}

    async def _next() -> dict:
        seq = app.sequences.get("tool")
        if not seq:
            raise ValueError("no step-by-step sequence in progress")
        i = seq["index"]
        step = seq["steps"][i]
        res = await exec_step(step)
        seq["index"] = i + 1
        out = {"sequence": seq["name"], "step": i + 1, "of": len(seq["steps"]), **res}
        if seq["index"] >= len(seq["steps"]):
            app.sequences.pop("tool", None)
            out["complete"] = True
        else:
            out["next"] = seq["steps"][seq["index"]]["desc"]
            out["hint"] = "check visually, then call sequence_next (or sequence_abort)"
        return out

    @tool("act")
    async def tool_dock(x: float, approach_y: float, dock_y: float, dock_z: float, release_z: float,
                        travel_speed: float = 150.0, slow_speed: float = 20.0, z_speed: float = 5.0,
                        step_by_step: bool = False, dry_run: bool = False) -> dict:
        """Drop the current tool into its dock: Z to dock_z, travel to
        (x, approach_y), slide to dock_y, move Z to release_z, back out to
        approach_y. step_by_step=true executes one step per call (continue with
        sequence_next). dry_run=true only returns the plan."""
        return await run_sequence("dock", dock_steps(x, approach_y, dock_y, dock_z, release_z,
                                                     travel_speed, slow_speed, z_speed), step_by_step, dry_run)

    @tool("act")
    async def tool_pickup(x: float, approach_y: float, dock_y: float, release_z: float, pickup_z: float,
                          travel_speed: float = 150.0, slow_speed: float = 20.0, z_speed: float = 5.0,
                          step_by_step: bool = False, dry_run: bool = False) -> dict:
        """Pick a tool from its dock: Z to release_z, travel to (x, approach_y),
        slide to dock_y, Z to pickup_z (engage), pull out to approach_y.
        step_by_step / dry_run as for tool_dock."""
        return await run_sequence("pickup", pickup_steps(x, approach_y, dock_y, release_z, pickup_z,
                                                         travel_speed, slow_speed, z_speed), step_by_step, dry_run)

    @tool("act")
    async def sequence_next() -> dict:
        """Execute the next step of the step-by-step dock/pickup sequence."""
        return await _next()

    @tool("audit")
    async def sequence_abort() -> dict:
        """Forget the step-by-step sequence in progress (no motion)."""
        seq = app.sequences.pop("tool", None)
        return {"aborted": bool(seq), "at_step": seq["index"] if seq else None}
