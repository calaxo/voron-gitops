"""Files, print control, and gcode streaming for setups without virtual_sdcard."""

import asyncio
import re
import time
from pathlib import Path
from typing import Any

from ..app import App
from ..safety import SafetyError, classify, split_lines
from .thermal import radiator_trip, read_radiator

_LAYER_RE = re.compile(r"^;\s*(LAYER_CHANGE|LAYER:\s*-?\d+)", re.I)


def resolve_local(app: App, local_path: str) -> Path:
    p = Path(local_path)
    if not p.is_absolute():
        p = Path(app.cfg.gcode_dir) / p
    if not p.exists():
        raise FileNotFoundError(f"{p} not found")
    return p


def prepare_stream(text: str) -> tuple[list[tuple[str, int]], int]:
    """-> [(gcode_line, layer_index)], total_layers. Comments stripped; layer
    markers (;LAYER_CHANGE, ;LAYER:n) counted."""
    out: list[tuple[str, int]] = []
    layer = 0
    for raw in text.splitlines():
        s = raw.strip()
        if _LAYER_RE.match(s):
            layer += 1
            continue
        code = s.split(";", 1)[0].strip()
        if code:
            out.append((code, layer))
    return out, layer


def register(app: App, tool: Any) -> None:
    cfg = app.cfg

    @tool("audit")
    async def upload_gcode(filename: str, local_path: str | None = None, content: str | None = None,
                           path: str = "", start: bool = False) -> dict:
        """Upload a gcode file (from a local path or inline content) to the
        printer's gcodes root. start=true also starts the print (needs armed)."""
        if start:
            app.safety.require_armed("upload_gcode(start)")
        if (local_path is None) == (content is None):
            raise ValueError("give exactly one of local_path or content")
        data = resolve_local(app, local_path).read_bytes() if local_path else content.encode()
        res = await app.client.upload(filename, data, path=path, start=start)
        return {"uploaded": filename, "bytes": len(data), "started": start, "moonraker": res}

    @tool("act")
    async def start_print(filename: str) -> dict:
        """Start printing a file already in the gcodes root."""
        await app.client.print_action("start", filename)
        return {"started": filename}

    @tool("audit")
    async def pause_print() -> dict:
        """Pause the current print (always allowed)."""
        await app.client.print_action("pause")
        return {"paused": True}

    @tool("act")
    async def resume_print() -> dict:
        """Resume a paused print."""
        await app.client.print_action("resume")
        return {"resumed": True}

    @tool("audit")
    async def cancel_print() -> dict:
        """Cancel the current print (always allowed)."""
        await app.client.print_action("cancel")
        return {"cancelled": True}

    @tool("read")
    async def print_progress() -> dict:
        """Print state/progress (virtual_sdcard) plus the gcode stream, if any."""
        st = await app.client.query({
            "print_stats": ["state", "filename", "print_duration", "total_duration", "filament_used", "info", "message"],
            "virtual_sdcard": ["progress", "is_active"], "display_status": ["progress", "message"]})
        stream = app.background.get("stream")
        return {"print_stats": st.get("print_stats"), "progress": st.get("virtual_sdcard", {}).get("progress"),
                "display": st.get("display_status"), "stream": stream["status"] if stream else None}

    @tool("act")
    async def stream_gcode(local_path: str, chunk_lines: int = 20, background: bool = True,
                           start_line: int = 0, radiator_check_s: float = 10.0, confirm: bool = False) -> dict:
        """Send a local gcode file in chunks through /printer/gcode/script (for
        setups without virtual_sdcard). Every chunk passes the same guards as the
        gcode tool. Layer progress from ;LAYER_CHANGE / ;LAYER:n. The radiator
        temperature is checked every `radiator_check_s`; on overheat the stream
        stops and the extruder heater is switched off. Stop with stream_stop."""
        cur = app.background.get("stream")
        if cur and not cur["task"].done():
            raise SafetyError("a stream is already running")
        text = resolve_local(app, local_path).read_text(encoding="utf-8", errors="replace")
        lines, total_layers = prepare_stream(text)
        info = classify("\n".join(l for l, _ in lines), cfg.single_flight_cmds)
        if info.dangerous and not confirm:
            raise SafetyError(f"file contains dangerous commands {info.dangerous}; confirm=true required")
        lines = lines[start_line:]
        state: dict[str, Any] = {"stop": False, "status": {
            "file": Path(local_path).name, "running": True, "lines_total": len(lines) + start_line,
            "lines_sent": start_line, "layer": 0, "layers_total": total_layers, "started": time.time(),
            "error": None}}

        async def run() -> None:
            st = state["status"]
            last_rad = 0.0
            try:
                for i in range(0, len(lines), chunk_lines):
                    if state["stop"]:
                        st["stopped"] = True
                        break
                    if not app.safety.armed:
                        st["error"] = "server disarmed during stream: stopped"
                        break
                    if radiator_check_s and time.monotonic() - last_rad >= radiator_check_s:
                        last_rad = time.monotonic()
                        temp = await read_radiator(app)
                        st["radiator"] = temp
                        if temp is not None and temp > cfg.radiator_max_c:
                            st["tripped"] = await radiator_trip(app, temp, "pause", True, "stream")
                            break
                    chunk = lines[i:i + chunk_lines]
                    await app.run_script("\n".join(l for l, _ in chunk), confirm=confirm,
                                         single_flight_busy_ok=True)
                    st["lines_sent"] += len(chunk)
                    st["layer"] = chunk[-1][1]
                else:
                    await app.client.gcode("M400")
                    st["complete"] = True
            except Exception as exc:
                st["error"] = f"{type(exc).__name__}: {exc}"
                app.safety.audit("stream_gcode_error", {"file": st["file"]}, st)
            finally:
                st["running"] = False
                st["elapsed_s"] = round(time.time() - st["started"], 1)

        state["task"] = asyncio.create_task(run())
        app.background["stream"] = state
        if background:
            await asyncio.sleep(0)
            return {"started": True, **state["status"]}
        await state["task"]
        return dict(state["status"])

    @tool("read")
    async def stream_status() -> dict:
        """Status of the current/last gcode stream."""
        s = app.background.get("stream")
        return s["status"] if s else {"running": False}

    @tool("audit")
    async def stream_stop() -> dict:
        """Stop sending the stream after the current chunk (already queued
        moves still finish; use emergency_stop for an immediate halt)."""
        s = app.background.get("stream")
        if not s or s["task"].done():
            return {"running": False}
        s["stop"] = True
        return {"stop_requested": True}
