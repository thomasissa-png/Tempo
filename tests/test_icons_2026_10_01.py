"""Icônes du site (2026-10-01) : régénérées depuis static/icon-192.svg.

Bug corrigé : favicon.ico et favicon-180.png avaient le bas en rectangle bleu
(« pierre tombale ») au lieu d'un demi-cercle ; apple-touch transparent (noir sur iOS) ;
manifeste sans PNG 512 ni maskable propre ; theme_color différent de la meta des pages.
"""

import re
from pathlib import Path

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"
TEMPLATES = ROOT / "templates"
BLUE = (0x21, 0x96, 0xF3)
RED = (0xF4, 0x43, 0x36)


def _close(px, ref, tol=12):
    return all(abs(a - b) <= tol for a, b in zip(px[:3], ref))


def _ico_frame(size):
    im = Image.open(STATIC / "favicon.ico")
    im.size = (size, size)
    return im.copy().convert("RGBA")


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    import app as app_module
    app_module._db_ready.set()
    return TestClient(app_module.app)


@pytest.mark.parametrize("name,size", [
    ("favicon-32.png", 32), ("favicon-48.png", 48), ("favicon-96.png", 96),
    ("favicon-192.png", 192), ("icon-512.png", 512),
    ("favicon-180.png", 180), ("icon-maskable-512.png", 512),
])
def test_png_icons_size_and_format(name, size):
    im = Image.open(STATIC / name)
    assert im.format == "PNG"
    assert im.size == (size, size)


def test_favicon_ico_has_16_32_48():
    im = Image.open(STATIC / "favicon.ico")
    assert im.format == "ICO"
    assert {(16, 16), (32, 32), (48, 48)} <= set(im.info["sizes"])


@pytest.mark.parametrize("size", [16, 32, 48])
def test_favicon_ico_corners_transparent_top_and_bottom(size):
    f = _ico_frame(size)
    last = size - 1
    for xy in [(0, 0), (last, 0), (0, last), (last, last)]:
        assert f.getpixel(xy)[3] == 0, (size, xy)


def test_favicon_ico_48_bottom_is_round_not_rectangle():
    """Preuve que le bas est un demi-cercle : hors du disque (r = 22,5) => transparent.

    L'ancien fichier avait un rectangle bleu opaque à ces positions.
    """
    f = _ico_frame(48)
    for xy in [(4, 43), (43, 43), (6, 45), (41, 45), (3, 40), (44, 40)]:
        assert f.getpixel(xy)[3] == 0, xy
    # Symétrie haut/bas de l'alpha (le rond est rond des deux côtés)
    for x in range(48):
        for y in range(24):
            assert abs(f.getpixel((x, y))[3] - f.getpixel((x, 47 - y))[3]) <= 8, (x, y)
    # Couleurs : rouge en haut, bleu en bas, blanc au centre
    assert _close(f.getpixel((24, 6)), RED)
    assert _close(f.getpixel((24, 41)), BLUE)
    assert _close(f.getpixel((24, 24)), (255, 255, 255))


@pytest.mark.parametrize("name", ["favicon-48.png", "favicon-96.png", "favicon-192.png", "icon-512.png"])
def test_full_frame_pngs_transparent_round(name):
    im = Image.open(STATIC / name).convert("RGBA")
    s = im.width
    for xy in [(0, 0), (s - 1, 0), (0, s - 1), (s - 1, s - 1)]:
        assert im.getpixel(xy)[3] == 0
    assert _close(im.getpixel((s // 2, s // 8)), RED)
    assert _close(im.getpixel((s // 2, s - s // 8)), BLUE)


def test_apple_touch_icon_opaque_white_margin():
    im = Image.open(STATIC / "favicon-180.png")
    assert "A" not in im.getbands() or im.getextrema()[3][0] == 255
    rgb = im.convert("RGB")
    # Marge blanche (~10 %) sur les 4 bords, y compris en bas
    for xy in [(0, 0), (179, 0), (0, 179), (179, 179), (90, 5), (90, 174), (5, 90), (174, 90)]:
        assert rgb.getpixel(xy) == (255, 255, 255), xy
    assert _close(rgb.getpixel((90, 30)), RED)
    assert _close(rgb.getpixel((90, 150)), BLUE)


def test_maskable_icon_opaque_with_logo_in_safe_zone():
    im = Image.open(STATIC / "icon-maskable-512.png")
    assert "A" not in im.getbands() or im.getextrema()[3][0] == 255
    rgb = im.convert("RGB")
    # Zone sûre Android = cercle de rayon 40 % : rien hors de 41 % du centre
    for xy in [(256, 46), (256, 466), (46, 256), (466, 256)]:
        assert rgb.getpixel(xy) == (255, 255, 255), xy
    assert _close(rgb.getpixel((256, 70)), RED)
    assert _close(rgb.getpixel((256, 442)), BLUE)


def _meta_theme_color():
    m = re.search(r'<meta name="theme-color" content="(#[0-9A-Fa-f]{6})">',
                  (TEMPLATES / "_base.html").read_text(encoding="utf-8"))
    return m.group(1)


def _check_manifest(data):
    assert data["theme_color"].lower() == _meta_theme_color().lower() == "#2563eb"
    icons = data["icons"]
    png_any = {i["sizes"] for i in icons if i["type"] == "image/png" and i["purpose"] == "any"}
    assert {"192x192", "512x512"} <= png_any
    mask = [i for i in icons if i["purpose"] == "maskable"]
    assert len(mask) == 1 and mask[0]["type"] == "image/png" and mask[0]["sizes"] == "512x512"
    # Le SVG bord à bord n'est plus déclaré maskable
    assert all("maskable" not in i["purpose"] for i in icons if i["type"] == "image/svg+xml")
    assert any(i["type"] == "image/svg+xml" and i["purpose"] == "any" for i in icons)
    for i in icons:
        assert (ROOT / i["src"].lstrip("/")).is_file(), i["src"]
        if i["type"] == "image/png":
            w, h = Image.open(ROOT / i["src"].lstrip("/")).size
            assert f"{w}x{h}" == i["sizes"]


def test_manifest_route_valid(client):
    r = client.get("/manifest.json")
    assert r.status_code == 200
    _check_manifest(r.json())


def test_static_manifest_file_matches_route(client):
    import json
    static = json.loads((STATIC / "manifest.json").read_text(encoding="utf-8"))
    _check_manifest(static)
    assert static["icons"] == client.get("/manifest.json").json()["icons"]


def test_all_pages_theme_color_matches_manifest():
    for p in TEMPLATES.glob("*.html"):
        for c in re.findall(r'<meta name="theme-color" content="([^"]+)">', p.read_text(encoding="utf-8")):
            assert c.lower() == "#2563eb", p.name


def test_favicon_links_single_partial_no_duplicates():
    partial = (TEMPLATES / "_favicons.html").read_text(encoding="utf-8")
    for needle in ['sizes="48x48" href="/static/favicon-48.png', 'sizes="96x96" href="/static/favicon-96.png',
                   'sizes="192x192" href="/static/favicon-192.png', 'type="image/svg+xml"',
                   'href="/static/favicon.ico', 'rel="apple-touch-icon" sizes="180x180" href="/static/favicon-180.png']:
        assert partial.count(needle) == 1, needle
    for p in TEMPLATES.glob("*.html"):
        if p.name == "_favicons.html":
            continue
        t = p.read_text(encoding="utf-8")
        assert 'rel="icon"' not in t and "apple-touch-icon" not in t, p.name
        assert t.count('{% include "_favicons.html" %}') <= 1, p.name


@pytest.mark.parametrize("path", ["/", "/mentions-legales", "/calendrier", "/methodologie", "/blog/"])
def test_rendered_pages_have_icon_links_once_with_version(client, path):
    import site_facts
    html = client.get(path).text
    v = site_facts.ICON_VERSION
    assert v and html.count('rel="apple-touch-icon"') == 1
    assert html.count(f'href="/static/favicon.ico?v={v}"') == 1
    assert html.count(f'href="/static/favicon-48.png?v={v}"') == 1
    assert html.count(f'href="/static/favicon-180.png?v={v}"') == 1
    assert html.count('href="/static/icon-192.svg"') == 1


@pytest.mark.parametrize("path,ctype", [
    ("/favicon.ico", "image/x-icon"), ("/apple-touch-icon.png", "image/png"),
    ("/apple-touch-icon-precomposed.png", "image/png"),
])
def test_root_icon_routes(client, path, ctype):
    r = client.get(path)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith(ctype)
    src = "favicon.ico" if path.endswith(".ico") else "favicon-180.png"
    assert r.content == (STATIC / src).read_bytes()


def test_cold_start_proxy_serves_favicon():
    import main
    assert main._static_files["/favicon.ico"] == (STATIC / "favicon.ico").read_bytes()
    assert main._static_files["/static/favicon.ico"] == (STATIC / "favicon.ico").read_bytes()
