"""Régénère toutes les icônes raster du site depuis la géométrie de static/icon-192.svg.

Logo : rond, moitié haute rouge #F44336, moitié basse bleue #2196F3, disque blanc
au centre. Géométrie du SVG (viewBox 192) : rayon extérieur 90, disque blanc 38.

Dessin à 4096 px puis réduction LANCZOS (Pillow prémultiplie l'alpha en RGBA :
pas de liseré sombre). Usage : python3 tools/generate_icons.py
"""
from pathlib import Path

from PIL import Image, ImageDraw

STATIC = Path(__file__).resolve().parent.parent / "static"

RED = (0xF4, 0x43, 0x36, 255)
BLUE = (0x21, 0x96, 0xF3, 255)
WHITE = (255, 255, 255, 255)

# Proportions de icon-192.svg (référence des balises rel=icon)
OUTER_RATIO = 90 / 192  # rayon extérieur / côté
INNER_OVER_OUTER = 38 / 90  # rayon du disque blanc / rayon extérieur

HIRES = 4096


def _logo(hires: int, outer_radius: float, background=None) -> Image.Image:
    """Dessine le logo centré, rayon extérieur en pixels haute résolution."""
    img = Image.new("RGBA", (hires, hires), background or (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    c = hires / 2
    r = outer_radius
    box = (c - r, c - r, c + r, c + r)
    draw.ellipse(box, fill=BLUE)
    # Demi-disque haut : angles Pillow dans le sens horaire depuis 3 h
    draw.pieslice(box, start=180, end=360, fill=RED)
    ri = r * INNER_OVER_OUTER
    draw.ellipse((c - ri, c - ri, c + ri, c + ri), fill=WHITE)
    return img


def full_frame(size: int) -> Image.Image:
    """Rond plein cadre sur fond transparent (comme le SVG)."""
    return _logo(HIRES, HIRES * OUTER_RATIO).resize((size, size), Image.Resampling.LANCZOS)


def apple_touch(size: int = 180) -> Image.Image:
    """Fond blanc opaque, marge de 10 % de chaque côté (iOS noircit le transparent)."""
    img = _logo(HIRES, HIRES * 0.40, background=WHITE)
    return img.resize((size, size), Image.Resampling.LANCZOS).convert("RGB")


def maskable(size: int = 512) -> Image.Image:
    """Fond blanc plein, rond dans la zone sûre Android (rayon 40 % du côté)."""
    img = _logo(HIRES, HIRES * 0.40, background=WHITE)
    return img.resize((size, size), Image.Resampling.LANCZOS).convert("RGB")


def main() -> None:
    frames = {s: full_frame(s) for s in (16, 32, 48, 96, 192, 512)}
    for s in (32, 48, 96, 192):
        frames[s].save(STATIC / f"favicon-{s}.png", optimize=True)
    frames[512].save(STATIC / "icon-512.png", optimize=True)
    # ICO : chaque taille est notre propre réduction (pas le thumbnail interne de Pillow)
    frames[48].save(
        STATIC / "favicon.ico",
        format="ICO",
        sizes=[(16, 16), (32, 32), (48, 48)],
        append_images=[frames[16], frames[32]],
    )
    apple_touch().save(STATIC / "favicon-180.png", optimize=True)
    maskable().save(STATIC / "icon-maskable-512.png", optimize=True)
    print("Icônes régénérées dans", STATIC)


if __name__ == "__main__":
    main()
