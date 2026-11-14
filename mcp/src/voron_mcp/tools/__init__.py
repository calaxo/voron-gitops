"""Tool registration helpers.

Tool kinds:
  read   - no side effect, always allowed, not audited
  act    - requires the server to be armed, audited
  audit  - always allowed but audited (emergency stop, arm/disarm, pause,
           cancel, journal) or the tool performs its own finer-grained check
"""

from __future__ import annotations

import functools
import inspect
from typing import Any, Callable

from ..app import App
from ..client import MoonrakerError
from ..safety import SafetyError


def make_registrar(app: App, mcp: Any) -> Callable[..., Callable]:
    def tool(kind: str = "read") -> Callable:
        assert kind in {"read", "act", "audit"}

        def deco(fn: Callable) -> Callable:
            name = fn.__name__
            sig = inspect.signature(fn)

            @functools.wraps(fn)
            async def wrapper(*args: Any, **kwargs: Any) -> dict:
                try:
                    bound = sig.bind(*args, **kwargs)
                    bound.apply_defaults()
                    call_args = dict(bound.arguments)
                except TypeError:
                    call_args = kwargs
                try:
                    if kind == "act":
                        app.safety.require_armed(name)
                    res = await fn(*args, **kwargs)
                    if not isinstance(res, dict):
                        res = {"result": res}
                    res.setdefault("ok", True)
                except (SafetyError, MoonrakerError, ValueError, FileNotFoundError, KeyError) as exc:
                    res = {"ok": False, "error": str(exc), "error_type": type(exc).__name__}
                if kind != "read":
                    app.safety.audit(name, call_args, res)
                return res

            app.tools[name] = wrapper
            if mcp is not None:
                mcp.tool(name=name, description=inspect.cleandoc(fn.__doc__ or name))(wrapper)
            return wrapper

        return deco

    return tool


def register_all(app: App, mcp: Any) -> None:
    from . import (
        calibration, dock, generators, leveling, motion, printing, read, system, thermal,
    )

    tool = make_registrar(app, mcp)
    for mod in (system, read, motion, thermal, calibration, leveling, dock, printing, generators):
        mod.register(app, tool)
