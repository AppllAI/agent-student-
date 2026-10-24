"""Documents PDF : tout ce qui part en pièce jointe (lettre, rapport de test, fiche d'entretien) est un vrai PDF.

  • lettre(...)   : lettre de motivation mise en page à la belge (expéditeur, destinataire à droite, lieu et date,
                    objet en gras, corps justifié), tenue sur UNE page (la police rétrécit un peu si besoin).
  • document(...) : fiche ou rapport lisible (titres, listes, paragraphes).

Avec fpdf2 (installé avec l'application) : police du système (Arial, DejaVu…) pour tous les accents.
Sans fpdf2 : un petit générateur intégré écrit quand même un PDF (jamais un .txt dans le corps d'un email)."""
from __future__ import annotations

import re
import unicodedata
from pathlib import Path

POLICES = (   # (normale, grasse) : Windows, Mac, Linux
    ("C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/arialbd.ttf"),
    ("/System/Library/Fonts/Supplemental/Arial.ttf", "/System/Library/Fonts/Supplemental/Arial Bold.ttf"),
    ("/Library/Fonts/Arial.ttf", "/Library/Fonts/Arial Bold.ttf"),
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    ("/usr/share/fonts/TTF/DejaVuSans.ttf", "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf"),
)
# Polices de base des PDF (sans fichier de police) : seulement l'alphabet latin-1 → équivalents sûrs
_EQUIVALENTS = {"’": "'", "‘": "'", "‚": ",", "“": '"', "”": '"', "„": '"', "–": "-", "—": "-", "‑": "-",
                "…": "...", "œ": "oe", "Œ": "OE", "€": "EUR", "•": "-", "≥": ">=", "≤": "<=", "→": "->",
                "←": "<-", "↔": "<->", "✓": "v", "×": "x", "\u00a0": " ", "\u202f": " ", "\u2009": " "}
MM = 72 / 25.4


def propre(texte: str) -> str:
    """Retire ce qu'une lettre ne doit jamais contenir (emojis, caractères invisibles, gras Markdown)."""
    t = re.sub(r"\*\*(.+?)\*\*|__(.+?)__", lambda m: m.group(1) or m.group(2), texte or "")
    t = "".join(c for c in t if not (ord(c) > 0xFFFF or 0x2600 <= ord(c) <= 0x27BF or 0x1F000 <= ord(c)
                                     or unicodedata.category(c) in ("Cf", "Co", "Cs")))
    return t.replace("\r", "").replace("\t", " ")


def _latin1(texte: str) -> str:
    t = "".join(_EQUIVALENTS.get(c, c) for c in propre(texte))
    return t.encode("latin-1", "ignore").decode("latin-1")


# ---------- Moteurs de rendu (fpdf2, sinon intégré) ----------
class _RenduFpdf:
    def __init__(self, marges=(22, 20, 22)):
        from fpdf import FPDF
        self.pdf = FPDF(format="A4")
        self.pdf.set_margins(*marges)
        self.pdf.set_auto_page_break(True, margin=18)
        self.pdf.add_page()
        self.police, self.unicode = "Helvetica", False
        for normale, grasse in POLICES:
            try:
                self.pdf.add_font("Texte", "", normale)
                self.pdf.add_font("Texte", "B", grasse if Path(grasse).exists() else normale)
                self.police, self.unicode = "Texte", True
                break
            except Exception:
                continue

    def _t(self, texte):
        return propre(texte) if self.unicode else _latin1(texte)

    def _regler(self, taille, gras):
        self.pdf.set_font(self.police, "B" if gras else "", taille)
        return taille * 0.47          # hauteur de ligne (mm) confortable pour cette taille

    def ligne(self, texte, taille=11, gras=False, x=None, align="L"):
        h = self._regler(taille, gras)
        if x is not None:
            self.pdf.set_x(x)
        self.pdf.multi_cell(0, h, self._t(texte), align=align, new_x="LMARGIN", new_y="NEXT")

    def para(self, texte, taille=11, gras=False, retrait=0, justifier=True):
        h = self._regler(taille, gras)
        if retrait:
            self.pdf.set_x(self.pdf.l_margin + retrait)
        self.pdf.multi_cell(0, h, self._t(texte), align="J" if justifier else "L", new_x="LMARGIN", new_y="NEXT")

    def espace(self, mm):
        self.pdf.ln(mm)

    def filet(self):
        y = self.pdf.get_y() + 1
        self.pdf.set_draw_color(180, 180, 180)
        self.pdf.line(self.pdf.l_margin, y, self.pdf.w - self.pdf.r_margin, y)
        self.pdf.ln(3)

    def pages(self):
        return self.pdf.page_no()

    def sauver(self, chemin):
        self.pdf.output(str(chemin))


class _RenduSimple:
    """Générateur PDF minimal (polices de base, WinAnsi) : utilisé seulement si fpdf2 manque."""
    L, H = 595.28, 841.89

    def __init__(self, marges=(22, 20, 22)):
        self.g, self.haut, self.d = marges[0] * MM, marges[1] * MM, marges[2] * MM
        self.flux, self.y = [[]], self.H - self.haut

    @staticmethod
    def _largeur(texte, taille, gras):
        return len(texte) * taille * (0.54 if gras else 0.5)

    def _ecrire(self, texte, taille, gras, x):
        if self.y < 18 * MM:
            self.flux.append([])
            self.y = self.H - self.haut
        t = _latin1(texte).replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        self.flux[-1].append(f"BT /{'F2' if gras else 'F1'} {taille} Tf {x:.1f} {self.y:.1f} Td ({t}) Tj ET")
        self.y -= taille * 1.34

    def ligne(self, texte, taille=11, gras=False, x=None, align="L"):
        if align == "R":
            pos = self.L - self.d - self._largeur(texte, taille, gras)
        else:
            pos = x * MM if x is not None else self.g
        self._ecrire(texte, taille, gras, pos)

    def para(self, texte, taille=11, gras=False, retrait=0, justifier=True):
        largeur = self.L - self.g - self.d - retrait * MM
        ligne = ""
        for mot in texte.split():
            essai = f"{ligne} {mot}".strip()
            if ligne and self._largeur(essai, taille, gras) > largeur:
                self._ecrire(ligne, taille, gras, self.g + retrait * MM)
                ligne = mot
            else:
                ligne = essai
        if ligne:
            self._ecrire(ligne, taille, gras, self.g + retrait * MM)

    def espace(self, mm):
        self.y -= mm * MM

    def filet(self):
        self.espace(3)

    def pages(self):
        return len(self.flux)

    def sauver(self, chemin):
        objets = ["<< /Type /Catalog /Pages 2 0 R >>", None,
                  "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
                  "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>"]
        pages = []
        for f in self.flux:
            contenu = "\n".join(f).encode("cp1252", "replace")
            objets.append(f"<< /Length {len(contenu)} >>\nstream\n".encode() + contenu + b"\nendstream")
            objets.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {self.L} {self.H}] /Contents {len(objets)} 0 R "
                          "/Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> >>")
            pages.append(len(objets))
        objets[1] = f"<< /Type /Pages /Kids [{' '.join(f'{p} 0 R' for p in pages)}] /Count {len(pages)} >>"
        sortie, positions = bytearray(b"%PDF-1.4\n"), []
        for i, o in enumerate(objets, 1):
            positions.append(len(sortie))
            sortie += f"{i} 0 obj\n".encode() + (o if isinstance(o, bytes) else o.encode("latin-1")) + b"\nendobj\n"
        xref = len(sortie)
        sortie += f"xref\n0 {len(objets) + 1}\n0000000000 65535 f \n".encode()
        sortie += b"".join(f"{p:010d} 00000 n \n".encode() for p in positions)
        sortie += f"trailer\n<< /Size {len(objets) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
        Path(chemin).write_bytes(bytes(sortie))


def _rendu(marges=(22, 20, 22)):
    try:
        return _RenduFpdf(marges)
    except ImportError:
        return _RenduSimple(marges)


# ---------- Lettre de motivation ----------
def texte_lettre(l: dict) -> str:
    """La lettre en texte continu (pour le Juge, le Contrôleur et l'aperçu)."""
    blocs = ["\n".join(l.get("expediteur") or []), "\n".join(l.get("destinataire") or []), l.get("lieu_date") or "",
             f"Objet : {l['objet_lettre']}" if l.get("objet_lettre") else "", l.get("appel") or "",
             *(l.get("paragraphes") or []), l.get("politesse") or "", l.get("signature") or ""]
    return "\n\n".join(b.strip() for b in blocs if b and b.strip())


def depuis_texte(texte: str) -> dict:
    """Ancienne lettre (texte brut) : chaque paragraphe tel quel, mise en page simple."""
    return {"paragraphes": [re.sub(r"\s*\n\s*", " ", b).strip() if len(b) > 90 else b.strip()
                            for b in (texte or "").replace("\r", "").split("\n\n") if b.strip()]}


def lettre(l: dict | str, chemin) -> Path:
    if isinstance(l, str):
        l = depuis_texte(l)
    chemin = Path(chemin).with_suffix(".pdf")
    chemin.parent.mkdir(parents=True, exist_ok=True)
    for taille in (11, 10.5, 10, 9.5):        # une lettre tient sur une page
        r = _rendu((24, 20, 22))
        _composer_lettre(r, l, taille)
        if r.pages() <= 1:
            break
    r.sauver(chemin)
    return chemin


def _composer_lettre(r, l: dict, t: float):
    for i, ligne in enumerate(l.get("expediteur") or []):
        r.ligne(ligne, t, gras=(i == 0))
    dest = [x for x in (l.get("destinataire") or []) if x.strip()]
    if dest:
        r.espace(7)
        for i, ligne in enumerate(dest):
            r.ligne(ligne, t, gras=(i == 0), x=112)
    if l.get("lieu_date"):
        r.espace(7)
        r.ligne(l["lieu_date"], t, align="R")
    if l.get("objet_lettre"):
        r.espace(7)
        objet = re.sub(r"^\s*objet\s*:\s*", "", l["objet_lettre"], flags=re.I)
        r.para(f"Objet : {objet}", t, gras=True, justifier=False)
    if l.get("appel"):
        r.espace(5)
        r.ligne(l["appel"], t)
    r.espace(2.5)
    for p in l.get("paragraphes") or []:
        lignes = p.strip().split("\n")
        if len(lignes) > 1 and all(len(x) < 70 for x in lignes):     # petit bloc (coordonnées, liste)
            for x in lignes:
                r.ligne(x.strip(), t)
        else:
            r.para(" ".join(x.strip() for x in lignes), t)
        r.espace(2.6)
    if l.get("politesse"):
        r.para(l["politesse"], t)
    if l.get("signature"):
        r.espace(9)
        for i, x in enumerate(l["signature"].strip().split("\n")):
            r.ligne(x.strip(), t, gras=(i == 0))


# ---------- Fiches et rapports ----------
def document(titre: str, texte: str, chemin, sous_titre: str = "") -> Path:
    """Texte façon Markdown léger (# titre, ## section, - puce, 1. liste, --- filet) → PDF lisible."""
    chemin = Path(chemin).with_suffix(".pdf")
    chemin.parent.mkdir(parents=True, exist_ok=True)
    r = _rendu((20, 18, 20))
    r.para(titre, 16, gras=True, justifier=False)
    if sous_titre:
        r.para(sous_titre, 9.5, justifier=False)
    r.espace(3)
    for brut in (texte or "").replace("\r", "").split("\n"):
        ligne = brut.strip()
        if not ligne:
            r.espace(2)
        elif re.fullmatch(r"[-─—_=*]{3,}", ligne):
            r.filet()
        elif m := re.match(r"^(#{1,4})\s+(.*)", ligne):
            r.espace(2)
            r.para(m.group(2), 13 if len(m.group(1)) <= 2 else 11.5, gras=True, justifier=False)
        elif m := re.match(r"^[-•–*]\s+(.*)", ligne):
            r.para("•  " + m.group(1), 10.5, retrait=4, justifier=False)
        elif re.match(r"^\d+[.)]\s+", ligne):
            r.para(ligne, 10.5, retrait=2, justifier=False)
        elif re.fullmatch(r"\*\*[^*]+\*\*:?", ligne):
            r.para(ligne, 11, gras=True, justifier=False)
        else:
            r.para(ligne, 10.5)
    r.sauver(chemin)
    return chemin
