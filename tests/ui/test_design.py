"""Checks on the design system that need no browser."""
import re
from pathlib import Path

STATIC = Path("static")
CSS = (STATIC / "style.css").read_text()


def blocks_with_dark_tokens():
    light = CSS[CSS.index(":root {"):CSS.index("@media (prefers-color-scheme: dark)")]
    system = CSS[CSS.index("@media (prefers-color-scheme: dark)"):CSS.index(':root[data-theme="dark"]')]
    explicit = CSS[CSS.index(':root[data-theme="dark"]'):CSS.index("* { box-sizing")]
    return light, system, explicit


def test_pace_and_hr_tokens_have_the_spec_colours_in_both_themes():
    light, system, explicit = blocks_with_dark_tokens()
    assert "--pace: #0d7a87" in light and "--hr: #b8325c" in light
    for dark in (system, explicit):
        assert "--pace: #4cc2cf" in dark and "--hr: #f0789c" in dark


def test_dark_tokens_match_between_system_and_explicit_theme():
    _, system, explicit = blocks_with_dark_tokens()
    tokens = lambda text: sorted(re.findall(r"--[\w-]+: [^;]+;", text))
    assert tokens(system) == tokens(explicit)


def test_only_the_pace_and_hr_tokens_colour_pace_and_hr():
    charts = (STATIC / "charts.js").read_text()
    assert not re.search(r"#[0-9a-fA-F]{6}", charts), "chart colours come from tokens"


def test_every_font_face_file_exists():
    files = re.findall(r'url\("(fonts/[^"]+)"\)', CSS)
    assert files
    assert all((STATIC / f).exists() for f in files)
    assert (STATIC / "fonts" / "OFL-Barlow.txt").exists()
    assert (STATIC / "fonts" / "OFL-JetBrainsMono.txt").exists()
