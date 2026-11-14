"""FastMCP server wiring."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from .app import App
from .config import Config
from .tools import register_all

INSTRUCTIONS = """\
Control a Klipper printer (Voron 2.4 + tool changer) through Moonraker.
Safety: the server starts DISARMED. Read tools always work; motion, heating,
firmware and print-start tools need `arm` (auto-disarm after a TTL).
emergency_stop, heaters_off, pause_print, cancel_print always work.
Never send repeated homings: `home` is single-flight and refuses while busy.
Dangerous raw commands (SET_KINEMATIC_POSITION, FORCE_MOVE, SET_STEPPER_ENABLE,
RESTART, FIRMWARE_RESTART, SAVE_CONFIG) need confirm=true. Every non-read call
is written to the audit log; use session_journal to note observations.
"""


def build(cfg: Config | None = None, app: App | None = None) -> tuple[FastMCP, App]:
    cfg = cfg or Config.from_env()
    app = app or App(cfg)
    mcp = FastMCP("voron-mcp", instructions=INSTRUCTIONS)
    mcp.settings.host = cfg.host
    mcp.settings.port = cfg.port
    register_all(app, mcp)
    return mcp, app


def main() -> None:
    cfg = Config.from_env()
    mcp, _ = build(cfg)
    if cfg.transport in ("http", "streamable-http", "streamable_http"):
        mcp.run(transport="streamable-http")
    elif cfg.transport == "sse":
        mcp.run(transport="sse")
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
