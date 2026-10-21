"""Dessine le logo de l'application (toque d'étudiant jaune + bulle de message) en PNG.
python scripts/creer_icone.py  →  assets/icone_1024.png, icone_256.png, icone.ico"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

S = 1024
MARINE_HAUT, MARINE_BAS = (22, 150, 140), (10, 78, 96)
JAUNE, JAUNE_CLAIR = (250, 196, 40), (255, 222, 110)
OR = JAUNE
SORTIE = Path(__file__).resolve().parent.parent / "assets"


def degrade():
    img = Image.new("RGB", (S, S))
    d = ImageDraw.Draw(img)
    for y in range(S):
        t = y / S
        d.line([(0, y), (S, y)], fill=tuple(int(a + (b - a) * t) for a, b in zip(MARINE_HAUT, MARINE_BAS)))
    return img


def main():
    fond = degrade()
    masque = Image.new("L", (S, S), 0)
    ImageDraw.Draw(masque).rounded_rectangle([40, 40, S - 40, S - 40], radius=220, fill=255)
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    img.paste(fond, (0, 0), masque)
    d = ImageDraw.Draw(img)

    # Toque d'étudiant (mortier) : plateau en losange + calotte + gland
    cx, cy = 470, 430
    d.polygon([(cx, cy + 120), (cx - 175, cy + 60), (cx - 175, cy + 230), (cx, cy + 300), (cx + 175, cy + 230),
               (cx + 175, cy + 60)], fill=(18, 30, 40))                                 # calotte
    d.polygon([(cx, cy - 150), (cx + 340, cy), (cx, cy + 150), (cx - 340, cy)], fill=JAUNE)  # plateau
    d.polygon([(cx, cy - 110), (cx + 270, cy), (cx, cy + 110), (cx - 270, cy)], fill=JAUNE_CLAIR)
    d.ellipse([cx - 26, cy - 26, cx + 26, cy + 26], fill=JAUNE)                          # bouton
    d.line([(cx, cy), (cx + 250, cy + 70)], fill=JAUNE, width=12)                        # cordon
    d.line([(cx + 250, cy + 70), (cx + 250, cy + 250)], fill=JAUNE, width=12)
    d.polygon([(cx + 225, cy + 245), (cx + 275, cy + 245), (cx + 290, cy + 330), (cx + 210, cy + 330)], fill=JAUNE)

    # Bulle de message (l'agent te parle)
    bx, by, r = 760, 735, 150
    ombre = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(ombre).ellipse([bx - r + 10, by - r + 18, bx + r + 10, by + r + 18], fill=(0, 0, 0, 110))
    img = Image.alpha_composite(img, ombre.filter(ImageFilter.GaussianBlur(18)))
    d = ImageDraw.Draw(img)
    d.ellipse([bx - r, by - r, bx + r, by + r], fill=(255, 255, 255))
    d.polygon([(bx - 110, by + 80), (bx - 165, by + 170), (bx - 40, by + 125)], fill=(255, 255, 255))
    for i, dx in enumerate((-62, 0, 62)):
        d.ellipse([bx + dx - 24, by - 24, bx + dx + 24, by + 24], fill=MARINE_HAUT if i != 1 else OR)

    SORTIE.mkdir(exist_ok=True)
    img.save(SORTIE / "icone_1024.png")
    img.resize((256, 256), Image.LANCZOS).save(SORTIE / "icone_256.png")
    img.save(SORTIE / "icone.ico", sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print("Icônes écrites dans", SORTIE)


if __name__ == "__main__":
    main()
