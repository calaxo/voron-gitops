import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from fake_moonraker import FakeMoonraker  # noqa: E402
from voron_mcp.app import App  # noqa: E402
from voron_mcp.client import MoonrakerClient  # noqa: E402
from voron_mcp.config import Config, KeepOut  # noqa: E402
from voron_mcp.tools import register_all  # noqa: E402


@pytest.fixture
def fake() -> FakeMoonraker:
    return FakeMoonraker()


@pytest.fixture
def cfg(tmp_path: Path) -> Config:
    return Config(
        moonraker_url="http://printer.test:7125",
        data_dir=tmp_path,
        store_settle_s=0,
        z_max_safe=300.0,
        keepout=KeepOut(y_min=0.0, y_max=30.0, z_min=250.0),
    )


@pytest.fixture
async def app(cfg: Config, fake: FakeMoonraker):
    client = MoonrakerClient(cfg.moonraker_url, transport=httpx.MockTransport(fake.handler),
                             store_settle_s=0)
    a = App(cfg, client)
    register_all(a, None)
    yield a
    for bg in a.background.values():
        if isinstance(bg, dict) and "task" in bg and not bg["task"].done():
            bg["task"].cancel()
    await client.aclose()


@pytest.fixture
def T(app):
    """Shortcut: T.home(axes='xy') -> await app.tools['home'](...)"""
    class _T:
        def __getattr__(self, name):
            return app.tools[name]
    return _T()
