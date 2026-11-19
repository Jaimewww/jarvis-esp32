"""Dark and light mode: f3 restyles both views; --theme starts in any of the six."""

from __future__ import annotations

import asyncio
import html
import re
from pathlib import Path
from typing import Any

import pytest
from conftest import fake_server, frame, wait_for

from jarvis import devview, tui
from jarvis.theme import DEFAULT_DARK, LIGHT, THEMES
from jarvis.tui import JarvisApp, main, parse_args

SNAPSHOT = frame(
    {
        "type": "esp32_snapshot",
        "devices": [{"device_id": "esp32-s3", "state": "idle", "rssi": -52, "ip": "192.168.1.40", "online": True}],
    }
)


def _svg(app: JarvisApp) -> str:
    return app.export_screenshot().lower()


def _screen_text(app: JarvisApp) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", app.export_screenshot())).replace("\xa0", " ")


def _app(tmp_path: Path, port: int, **kwargs: Any) -> JarvisApp:
    return JarvisApp(
        url="ws://127.0.0.1:9/ws",
        token_path=tmp_path / "missing-token",
        backend_url=f"ws://127.0.0.1:{port}/api/v1/ws",
        backend_token="t",
        animate=False,
        **kwargs,
    )


def test_f3_toggles_dark_and_light_on_both_views(tmp_path: Path, caplog: Any) -> None:
    dark, light = THEMES[DEFAULT_DARK], THEMES[LIGHT]

    async def handler(socket: Any) -> None:
        await socket.send(SNAPSHOT)
        await socket.wait_closed()

    async def scenario() -> list[tuple[str, str, str]]:
        shots: list[tuple[str, str, str]] = []
        async with fake_server(handler) as port:
            app = _app(tmp_path, port)
            async with app.run_test(size=(140, 40)) as pilot:
                await wait_for(lambda: "esp32-s3" in _screen_text(app), timeout=5.0)
                for key in (None, "f3", "f2", "f3"):
                    if key:
                        await pilot.press(key)
                    await pilot.pause()
                    shots.append((app.theme, _svg(app), _screen_text(app)))
        return shots

    (t0, svg0, text0), (t1, svg1, text1), (t2, svg2, text2), (t3, svg3, text3) = asyncio.run(scenario())

    # Starts dark; the footer offers the light mode.
    assert t0 == f"jarvis-{DEFAULT_DARK}"
    assert dark.background in svg0 and light.background not in svg0
    assert "f3 light mode" in text0
    # f3: light, in the user view.
    assert t1 == f"jarvis-{LIGHT}"
    assert light.background in svg1 and dark.background not in svg1
    assert "f3 dark mode" in text1
    # f2: the developer view is light too.
    assert t2 == f"jarvis-{LIGHT}" and "Voice turns" in text2
    assert light.background in svg2 and dark.background not in svg2
    # f3 again: dark developer view.
    assert t3 == f"jarvis-{DEFAULT_DARK}" and "Voice turns" in text3
    assert dark.background in svg3 and light.background not in svg3
    assert [r.getMessage() for r in caplog.records if r.levelname == "ERROR"] == []


@pytest.mark.parametrize("name", sorted(THEMES))
def test_theme_option_starts_in_any_theme(tmp_path: Path, name: str) -> None:
    assert parse_args(["--theme", name]).theme == name
    tokens = THEMES[name]

    async def handler(socket: Any) -> None:
        await socket.wait_closed()

    async def scenario() -> tuple[str, str, str]:
        async with fake_server(handler) as port:
            app = _app(tmp_path, port, theme_name=name)
            async with app.run_test(size=(120, 30)) as pilot:
                await pilot.pause()
                first, svg = app.theme, _svg(app)
                await pilot.press("f3")
                await pilot.pause()
                return first, svg, app.theme

    first, svg, toggled = asyncio.run(scenario())
    assert first == f"jarvis-{name}"
    assert tokens.background in svg
    # f3 goes to the other mode: a dark start pairs with the light theme and back.
    assert toggled == (f"jarvis-{DEFAULT_DARK}" if not tokens.dark else f"jarvis-{LIGHT}")


def test_theme_option_defaults_and_rejects_unknown_names(capsys: Any) -> None:
    assert parse_args([]).theme == DEFAULT_DARK
    with pytest.raises(SystemExit):
        parse_args(["--theme", "neon"])
    with pytest.raises(SystemExit) as stop:
        main(["--help"])
    assert stop.value.code == 0
    output = capsys.readouterr().out
    assert "--theme" in output and "f3" in output
    for name in THEMES:
        assert name in output


def test_views_hold_no_colour_of_their_own() -> None:
    for module in (devview, tui):
        source = Path(module.__file__).read_text(encoding="utf-8")
        assert not re.search(r"#[0-9a-fA-F]{6}\b", source), module.__name__
