"""The installable bits, checked without a browser."""
import json
import re
import struct
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app

STATIC = Path("static")
MANIFEST = json.loads((STATIC / "manifest.webmanifest").read_text())
CSS = (STATIC / "style.css").read_text()


def png_size(path):
    return struct.unpack(">II", path.read_bytes()[16:24])


def shell_paths():
    worker = (STATIC / "sw.js").read_text()
    return re.findall(r'"(/[^"]*)"', worker[worker.index("const SHELL = ["):worker.index("];")])


def test_manifest_fields_meet_the_install_criteria():
    assert MANIFEST["name"] == MANIFEST["short_name"] == "OpenStride"
    assert MANIFEST["display"] == "standalone"
    assert MANIFEST["start_url"] == "/" and MANIFEST["scope"] == "/"


def test_manifest_colours_come_from_the_tokens():
    assert MANIFEST["theme_color"] in CSS and MANIFEST["background_color"] in CSS
    assert f"--bg: {MANIFEST['background_color']}" in CSS


def test_manifest_icons_exist_at_their_stated_sizes():
    purposes = set()
    for icon in MANIFEST["icons"]:
        width, height = png_size(STATIC / icon["src"].lstrip("/"))
        assert f"{width}x{height}" == icon["sizes"]
        purposes.add((icon["sizes"], icon["purpose"]))
    assert {("192x192", "any"), ("512x512", "any"), ("512x512", "maskable")} <= purposes
    assert (STATIC / "icons" / "icon.svg").exists()


def test_every_shell_file_exists():
    paths = shell_paths()
    assert "/" in paths
    for path in paths:
        assert (STATIC / ("index.html" if path == "/" else path.lstrip("/"))).exists(), path


def test_shell_lists_every_script_font_and_stylesheet():
    wanted = {f"/{p.relative_to(STATIC).as_posix()}" for pattern in ("*.js", "render/*.js", "fonts/*.woff2", "*.css")
              for p in STATIC.glob(pattern)} - {"/sw.js"}
    assert wanted <= set(shell_paths())


def test_pwa_files_are_served_from_the_root():
    client = TestClient(app)
    assert client.get("/manifest.webmanifest").status_code == 200
    assert "javascript" in client.get("/sw.js").headers["content-type"]
    assert client.get("/icons/icon-192.png").headers["content-type"] == "image/png"
