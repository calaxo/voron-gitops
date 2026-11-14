"""Safety state, emergency stop, restarts, session journal."""

from typing import Any

from ..app import App
from ..safety import SafetyError


def register(app: App, tool: Any) -> None:
    @tool("audit")
    async def arm(minutes: float | None = None, reason: str = "") -> dict:
        """Arm the server: allows motion, heating, firmware commands. Auto-disarms
        after `minutes` (default VORON_MCP_ARM_TTL_MIN). Give a short reason."""
        return app.safety.arm(minutes, reason)

    @tool("audit")
    async def disarm() -> dict:
        """Disarm the server: only read-only tools and emergency_stop remain allowed."""
        return app.safety.disarm()

    @tool("read")
    async def safety_status() -> dict:
        """Armed state, running background tasks (watchdog, stream), active dock
        sequence and the effective (non-secret) safety configuration."""
        bg = {k: v.get("status") for k, v in app.background.items() if isinstance(v, dict) and "task" in v}
        return {
            **app.safety.status(),
            "background": bg,
            "sequences": {k: {"next_step": s["index"], "steps": len(s["steps"])} for k, s in app.sequences.items()},
            "config": app.cfg.public(),
        }

    @tool("audit")
    async def emergency_stop() -> dict:
        """M112 emergency stop through Moonraker. ALWAYS allowed, even disarmed.
        Klipper then needs firmware_restart."""
        await app.client.emergency_stop()
        for name, bg in list(app.background.items()):
            if isinstance(bg, dict) and bg.get("task") and not bg["task"].done():
                bg["stop"] = True
        return {"emergency_stop": True}

    @tool("act")
    async def restart() -> dict:
        """Klipper host restart (reloads printer.cfg)."""
        await app.client.restart()
        return {"restart": "requested"}

    @tool("act")
    async def firmware_restart() -> dict:
        """Klipper FIRMWARE_RESTART (resets MCUs; needed after emergency stop)."""
        await app.client.firmware_restart()
        return {"firmware_restart": "requested"}

    @tool("audit")
    async def session_journal(note: str, tags: list[str] | None = None, data: dict | None = None) -> dict:
        """Append a free-form note (observation, measurement, decision) to the
        audit log so the calibration session can be reconstructed later."""
        if not note.strip():
            raise SafetyError("empty note")
        return {"logged": True, "note": note, "tags": tags or []}
