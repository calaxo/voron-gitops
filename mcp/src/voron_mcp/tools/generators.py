"""Calibration print generators exposed as tools."""

import time
from typing import Any, Literal

from ..app import App
from ..gcode_gen import PrintParams, flow_squares_test, pa_line_test

Output = Literal["return", "save", "upload", "stream"]


def register(app: App, tool: Any) -> None:
    async def center() -> tuple[float, float]:
        st = await app.client.query({"toolhead": ["axis_minimum", "axis_maximum"]})
        th = st.get("toolhead", {})
        mn, mx = th.get("axis_minimum", [0, 0]), th.get("axis_maximum", [200, 200])
        return (mn[0] + mx[0]) / 2, (mn[1] + mx[1]) / 2

    async def deliver(name: str, gcode: str, output: str) -> dict:
        lines = gcode.count("\n")
        res: dict[str, Any] = {"filename": name, "lines": lines}
        if output == "return":
            res["gcode"] = gcode
            return res
        path = app.cfg.gcode_dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(gcode, encoding="utf-8")
        res["saved"] = str(path)
        if output == "upload":
            res["upload"] = await app.tools["upload_gcode"](filename=name, local_path=str(path))
        elif output == "stream":
            res["stream"] = await app.tools["stream_gcode"](local_path=str(path))
        res["preview"] = gcode.splitlines()[:12]
        return res

    def params(nozzle_temp: float, bed_temp: float, start_gcode: str | None, end_gcode: str | None,
               line_width: float, layer_height: float) -> PrintParams:
        return PrintParams(nozzle_temp=nozzle_temp, bed_temp=bed_temp, start_gcode=start_gcode,
                           end_gcode=end_gcode, line_width=line_width, layer_height=layer_height)

    @tool("audit")
    async def generate_pa_test(output: Output = "save", center_x: float | None = None,
                               center_y: float | None = None, pa_start: float = 0.0, pa_end: float = 0.07,
                               pa_step: float = 0.005, slow_speed: float = 20.0, fast_speed: float = 120.0,
                               nozzle_temp: float = 215.0, bed_temp: float = 60.0,
                               start_gcode: str | None = None, end_gcode: str | None = None,
                               line_width: float = 0.48, layer_height: float = 0.2) -> dict:
        """Pressure-advance line test (default 15 lines, PA 0→0.07 step 0.005;
        slow/fast/slow segments; marker tab every 0.01). output: return | save
        (data dir) | upload (gcodes root) | stream (needs armed). Default start
        gcode heats + G28; pass start_gcode='PRINT_START ...' to use your macro."""
        cx, cy = (center_x, center_y) if center_x is not None and center_y is not None else await center()
        g = pa_line_test(cx, cy, pa_start, pa_end, pa_step, slow_speed, fast_speed,
                         params=params(nozzle_temp, bed_temp, start_gcode, end_gcode, line_width, layer_height))
        return await deliver(f"pa_test_{int(time.time())}.gcode", g, output)

    @tool("audit")
    async def generate_flow_test(output: Output = "save", center_x: float | None = None,
                                 center_y: float | None = None, flows: list[float] | None = None,
                                 size: float = 20.0, layers: int = 3, speed: float = 60.0,
                                 nozzle_temp: float = 215.0, bed_temp: float = 60.0,
                                 start_gcode: str | None = None, end_gcode: str | None = None,
                                 line_width: float = 0.48, layer_height: float = 0.2) -> dict:
        """Flow squares test: one square per extrusion multiplier (default
        0.90..1.10), `layers` solid layers each. Same output modes as
        generate_pa_test."""
        cx, cy = (center_x, center_y) if center_x is not None and center_y is not None else await center()
        g = flow_squares_test(cx, cy, flows, size, 5.0, layers, speed,
                              params=params(nozzle_temp, bed_temp, start_gcode, end_gcode, line_width, layer_height))
        return await deliver(f"flow_test_{int(time.time())}.gcode", g, output)
