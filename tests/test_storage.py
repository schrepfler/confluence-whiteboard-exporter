from __future__ import annotations

import stat
from pathlib import Path

import pytest

from wb2canvas.extract import SessionExpiredError, _raise_if_login_page
from wb2canvas.storage import atomic_write_text


def test_atomic_write_secret_file_is_0600(tmp_path: Path) -> None:
    p = tmp_path / "state.json"
    atomic_write_text(p, "{}", mode=0o600)
    assert stat.S_IMODE(p.stat().st_mode) == 0o600


def test_atomic_write_failure_leaves_previous_content_and_no_temp(tmp_path: Path) -> None:
    p = tmp_path / "dump.json"
    p.write_text("previous")

    class Boom(str):
        def __len__(self) -> int:  # pragma: no cover - not reached
            return 0

    with pytest.raises(TypeError):
        atomic_write_text(p, b"not-text")  # type: ignore[arg-type]
    assert p.read_text() == "previous"
    assert [x.name for x in tmp_path.iterdir()] == ["dump.json"]


@pytest.mark.parametrize("url", [
    "https://id.atlassian.com/login?continue=https%3A%2F%2Fx.atlassian.net",
    "https://auth.atlassian.com/authorize?x=1",
])
def test_login_redirect_is_reported_as_expired_session(url: str) -> None:
    with pytest.raises(SessionExpiredError, match="auth attach"):
        _raise_if_login_page(url)


def test_board_url_is_not_a_login_page() -> None:
    _raise_if_login_page("https://x.atlassian.net/wiki/spaces/S/whiteboard/1")


class _FakeChromium:
    def __init__(self, fail_channels: set[str | None]) -> None:
        self.fail_channels = fail_channels
        self.launched: list[str | None] = []

    async def launch(self, *, headless: bool, channel: str | None = None):
        from playwright.async_api import Error

        if channel in self.fail_channels:
            raise Error(f"cannot launch {channel or 'bundled'}\nmore detail")
        self.launched.append(channel)
        return f"browser:{channel or 'bundled'}"


class _FakePlaywright:
    def __init__(self, fail_channels: set[str | None]) -> None:
        self.chromium = _FakeChromium(fail_channels)


def test_launch_prefers_installed_chrome() -> None:
    import asyncio

    from wb2canvas.extract import launch_browser

    assert asyncio.run(launch_browser(_FakePlaywright(set()), headless=True)) == "browser:chrome"


def test_launch_falls_back_to_bundled_chromium() -> None:
    import asyncio

    from wb2canvas.extract import launch_browser

    assert asyncio.run(launch_browser(_FakePlaywright({"chrome"}), headless=True)) == "browser:bundled"


def test_launch_without_any_browser_explains_how_to_get_one() -> None:
    import asyncio

    from wb2canvas.extract import launch_browser

    with pytest.raises(RuntimeError, match="install Google Chrome"):
        asyncio.run(launch_browser(_FakePlaywright({"chrome", None}), headless=True))
