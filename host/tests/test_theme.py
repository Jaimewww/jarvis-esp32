"""Theme tokens: six complete themes copied from the approved design, readable text."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from jarvis import theme
from jarvis.theme import DEFAULT_DARK, LIGHT, THEMES, contrast

HEX = re.compile(r"^#[0-9a-f]{6}$")
NAMES = {"teal", "plum", "graphite", "paper", "gentleman-cute", "gentleman-sexy"}
TOKENS_JSON = Path(__file__).resolve().parents[2] / "design/tui-theme/.preview/tokens.json"
# design token name -> host token name
JSON_NAMES = {
    "bg": "background",
    "surface": "surface",
    "bubble": "message",
    "border": "border",
    "dim": "dim",
    "text": "text",
    "text2": "text2",
    "muted": "muted",
    "accent": "accent",
    "live": "live",
    "ok": "ok",
    "warn": "over",
    "err": "failed",
    "wtint": "over_tint",
    "etint": "failed_tint",
}
TEXT_TOKENS = ("text", "text2", "muted", "accent", "live", "ok", "over", "failed")
BACKGROUND_TOKENS = ("background", "surface", "message", "over_tint", "failed_tint")


def test_six_themes_with_every_token() -> None:
    assert set(THEMES) == NAMES
    for name, tokens in THEMES.items():
        assert tokens.name == name
        for field in JSON_NAMES.values():
            assert HEX.match(getattr(tokens, field)), (name, field)
        assert len(tokens.gradient) == 12 and all(HEX.match(step) for step in tokens.gradient)
        assert tokens.gradient_start == tokens.gradient[0]
        assert tokens.gradient_end == tokens.gradient[-1]


def test_one_light_theme_and_a_named_default_dark() -> None:
    assert LIGHT == "paper" and not THEMES[LIGHT].dark
    assert DEFAULT_DARK == "gentleman-cute" and THEMES[DEFAULT_DARK].dark
    assert [name for name, tokens in THEMES.items() if not tokens.dark] == ["paper"]


def test_text_tokens_are_readable_on_every_background() -> None:
    for name, tokens in THEMES.items():
        for fg in TEXT_TOKENS:
            for bg in BACKGROUND_TOKENS:
                ratio = contrast(getattr(tokens, fg), getattr(tokens, bg))
                assert ratio >= 4.5, (name, fg, bg, round(ratio, 2))
        for step in tokens.gradient:
            assert contrast(step, tokens.background) >= 4.5, (name, step)


def test_contrast_formula() -> None:
    assert contrast("#000000", "#ffffff") == pytest.approx(21.0)
    assert contrast("#777777", "#777777") == pytest.approx(1.0)


def test_host_never_reads_the_design_folder() -> None:
    source = Path(theme.__file__).read_text(encoding="utf-8")
    assert "design/" not in source and "tokens.json" not in source


@pytest.mark.skipif(not TOKENS_JSON.exists(), reason="design tokens not in this checkout")
def test_values_are_the_approved_design_tokens() -> None:
    design = json.loads(TOKENS_JSON.read_text(encoding="utf-8"))["themes"]
    assert set(design) == NAMES
    for name, spec in design.items():
        tokens = THEMES[name]
        assert tokens.title == spec["name"]
        for key, field in JSON_NAMES.items():
            assert getattr(tokens, field) == spec["color"][key], (name, key)
        assert list(tokens.gradient) == spec["gradient"]["steps"]


def test_style_maps_class_codes_to_tokens() -> None:
    tokens = THEMES["paper"]
    style = theme.style(tokens, "lv b bb")
    assert style.color is not None and style.color.triplet is not None
    assert style.color.triplet.hex == tokens.live
    assert style.bgcolor is not None and style.bgcolor.triplet is not None
    assert style.bgcolor.triplet.hex == tokens.message
    assert style.bold
    assert theme.style(tokens, "h11").color.triplet.hex == tokens.gradient_end  # type: ignore[union-attr]
    assert theme.gradient_class(0, 5) == "h0" and theme.gradient_class(4, 5) == "h11"
