"""Application context shared by every tool."""

from __future__ import annotations

from typing import Any, Callable

from .client import MoonrakerClient
from .config import Config
from .safety import MOTION_OBJECTS, MotionSim, SafetyError, Safety, ScriptInfo


class App:
    def __init__(self, cfg: Config, client: MoonrakerClient | None = None):
        self.cfg = cfg
        self.client = client or MoonrakerClient(
            cfg.moonraker_url,
            api_key=cfg.api_key,
            timeout=cfg.http_timeout,
            long_timeout=cfg.long_timeout,
            store_settle_s=cfg.store_settle_s,
        )
        self.safety = Safety(cfg)
        self.tools: dict[str, Callable[..., Any]] = {}
        self.background: dict[str, Any] = {}  # watchdog / stream tasks
        self.sequences: dict[str, Any] = {}  # step-by-step dock sequences

    async def motion_sim(self, allow_keepout: bool = False) -> MotionSim:
        status = await self.client.query(MOTION_OBJECTS)
        return MotionSim.from_status(self.cfg, status, allow_keepout=allow_keepout)

    async def run_script(
        self,
        script: str,
        *,
        confirm: bool = False,
        allow_keepout: bool = False,
        capture: bool = False,
        timeout: float | None = None,
        check_motion: bool = True,
        single_flight_busy_ok: bool = False,
    ) -> dict:
        """Single entry point for every gcode sent by the server: classify,
        check arm/confirm, simulate motion against limits/keep-out,
        serialize homings, then run."""
        info: ScriptInfo = self.safety.check_script(script, confirm)
        if info.emergency:
            await self.client.emergency_stop()
            return {"emergency_stop": True}
        sim = None
        if check_motion and not info.readonly:
            sim = await self.motion_sim(allow_keepout=allow_keepout)
            sim.feed_all(info.lines)
        body = "\n".join(info.lines)
        if info.single_flight:
            async with self.safety.single_flight(self.client, ",".join(info.single_flight),
                                                 busy_ok=single_flight_busy_ok):
                out = await self._exec(body, capture, timeout)
        else:
            out = await self._exec(body, capture, timeout)
        res: dict[str, Any] = {"sent_lines": len(info.lines)}
        if capture:
            res["response"] = out
        if sim and sim.unverified:
            res["unverified_commands"] = sorted(set(sim.unverified))
        return res

    async def _exec(self, script: str, capture: bool, timeout: float | None) -> Any:
        if capture:
            return await self.client.gcode_capture(script, timeout=timeout)
        await self.client.gcode(script, timeout=timeout)
        return None

    async def capture(self, script: str, timeout: float | None = None) -> list[str]:
        """Read-only console command (GET_POSITION, DUMP_TMC...)."""
        info = self.safety.check_script(script, confirm=False)
        if not info.readonly:
            raise SafetyError("capture() is for read-only commands")
        return await self.client.gcode_capture(script, timeout=timeout)


__all__ = ["App", "SafetyError"]
