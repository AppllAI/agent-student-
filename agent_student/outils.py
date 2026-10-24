"""Outils sans IA (gratuits) utilisés par plusieurs agents."""
from __future__ import annotations

import hashlib
import html
import json
import re
import unicodedata
from html.parser import HTMLParser
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130 Safari/537.36"
_PISTEURS = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "ref", "from", "src", "trk"}
RE_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_EMAILS_IGNORES = ("noreply", "no-reply", "privacy", "gdpr", "dpo@", "webmaster", "example.", "sentry", "wixpress",
                   "helpdesk", "support@", "facturation", "billing", "invoice", "comptabilite", "accounting",
                   "press@", "presse@", "marketing@", "unsubscribe", "abuse@")
# Adresses faites pour recevoir des candidatures
RE_EMAIL_RECRUTEMENT = re.compile(r"^(jobs?|emplois?|recrutements?|recruit\w*|careers?|carrieres?|rh|hr|vacatures?|"
                                  r"solliciteren|sollicitatie|personeel|talent|candidatures?|werken|work|staffing)"
                                  r"[.@_-]", re.I)
# Adresses des SITES d'emploi eux-mêmes : y envoyer une candidature serait une bourde (ce n'est pas l'employeur)
SITES_EMPLOI = ("lexgo.", "jobat.", "stepstone.", "indeed.", "legalworld.", "references.be", "actiris.", "leforem.",
                "vdab.", "linkedin.", "glassdoor.", "monster.", "talent.com", "jobted.", "optioncarriere.", "jooble.",
                "kapitalis.", "jobsinbrussels.", "ictjob.", "justifit.", "student.be", "studentjob.", "stujobs.",
                "jobteaser.")   # les agences d'intérim (Randstad, Adecco…) restent permises : ce sont des recruteurs
# Les mêmes, en noms de domaine (pour dire à la recherche web de NE PAS y perdre de recherches)
SITES_EMPLOI_DOMAINES = ("lexgo.be", "jobat.be", "stepstone.be", "indeed.com", "legalworld.be", "references.be",
                         "actiris.brussels", "leforem.be", "vdab.be", "linkedin.com", "glassdoor.com", "glassdoor.be",
                         "monster.be", "talent.com", "jobted.be", "optioncarriere.be", "jooble.org", "ictjob.be",
                         "jobsinbrussels.com", "student.be", "studentjob.be", "stujobs.be", "jobteaser.com")


def sans_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s or "") if unicodedata.category(c) != "Mn").lower()


def normaliser_url(url: str) -> str:
    p = urlparse((url or "").strip())
    qs = [(k, v) for k, v in parse_qsl(p.query) if k.lower() not in _PISTEURS]
    return urlunparse((p.scheme.lower(), p.netloc.lower().removeprefix("www."), p.path.rstrip("/"), "", urlencode(qs), ""))


def cle_offre(url: str | None, entreprise: str | None, titre: str | None) -> str:
    """Empreinte anti-doublon : la même offre publiée sur 3 sites = 1 seule candidature."""
    if entreprise and titre:
        base = re.sub(r"[^a-z0-9]", "", sans_accents(entreprise)) + "|" + re.sub(r"[^a-z0-9]", "", sans_accents(titre))
    else:
        base = normaliser_url(url or "")
    return hashlib.sha1(base.encode()).hexdigest()[:16]


class _Texte(HTMLParser):
    def __init__(self):
        super().__init__()
        self.morceaux, self._ignorer = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript", "svg", "nav", "footer", "header"):
            self._ignorer += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript", "svg", "nav", "footer", "header") and self._ignorer:
            self._ignorer -= 1
        if tag in ("p", "div", "li", "br", "h1", "h2", "h3", "tr"):
            self.morceaux.append("\n")

    def handle_data(self, d):
        if not self._ignorer:
            self.morceaux.append(d)


def texte_html(page: str, limite: int = 7000) -> str:
    t = _Texte()
    t.feed(page)
    brut = re.sub(r"[ \t]+", " ", "".join(t.morceaux))
    return re.sub(r"\n\s*\n+", "\n", brut).strip()[:limite]


def jobposting_jsonld(page: str) -> dict | None:
    """Beaucoup de sites (Jobat, StepStone, Indeed, pages carrières) publient l'offre en données
    structurées schema.org/JobPosting : on la lit gratuitement, sans IA."""
    for bloc in re.findall(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', page, re.S | re.I):
        try:
            data = json.loads(html.unescape(bloc.strip()))
        except Exception:
            continue
        pile = data if isinstance(data, list) else [data]
        while pile:
            d = pile.pop()
            if isinstance(d, dict):
                if d.get("@type") in ("JobPosting", ["JobPosting"]):
                    return _jp(d)
                pile.extend(d.get("@graph", []) if isinstance(d.get("@graph"), list) else [])
    return None


def _jp(d: dict) -> dict:
    org = d.get("hiringOrganization") or {}
    lieu = d.get("jobLocation") or {}
    if isinstance(lieu, list):
        lieu = lieu[0] if lieu else {}
    adr = (lieu.get("address") or {}) if isinstance(lieu, dict) else {}
    contrat = d.get("employmentType")
    return {
        "titre": d.get("title"),
        "entreprise": org.get("name") if isinstance(org, dict) else org,
        "ville": adr.get("addressLocality") if isinstance(adr, dict) else None,
        "contrat": ", ".join(contrat) if isinstance(contrat, list) else contrat,
        "description": texte_html(d.get("description") or "", 4000),
        "date": d.get("datePosted"),
        "publie_le": str(d.get("datePosted") or "")[:10] or None,
        "date_limite": str(d.get("validThrough") or "")[:10] or None,
        "teletravail": d.get("jobLocationType") == "TELECOMMUTE",
    }


def emails_dans(texte: str) -> list[str]:
    vus = []
    for e in RE_EMAIL.findall(texte or ""):
        e = e.strip(".").lower()
        dom = e.split("@")[-1]
        if (not any(x in e for x in _EMAILS_IGNORES) and not any(s in dom for s in SITES_EMPLOI)
                and not e.endswith((".png", ".jpg", ".gif", ".webp")) and e not in vus):
            vus.append(e)
    return vus


def telecharger(url: str, timeout: int = 20) -> str:
    import requests

    r = requests.get(url, headers={"User-Agent": UA, "Accept-Language": "fr-BE,fr;q=0.9"}, timeout=timeout)
    r.raise_for_status()
    return r.text


def domaine_email(adresse: str | None) -> str:
    m = RE_EMAIL.search(adresse or "")
    return m.group(0).split("@")[1].lower() if m else ""


# ---------- Site officiel de l'employeur : lu sans IA, et preuve des adresses email ----------
WEBMAILS = ("gmail.", "googlemail.", "outlook.", "hotmail.", "live.", "msn.", "yahoo.", "icloud.", "me.com",
            "skynet.be", "proximus.be", "telenet.be", "scarlet.be", "voo.be", "gmx.", "orange.", "protonmail.")
_PAS_UN_EMPLOYEUR = SITES_EMPLOI + ("google.", "facebook.", "instagram.", "twitter.", "x.com", "youtube.",
                                    "wikipedia.", "glassdoor.", "jooble.", "careerjet.", "jobrapido.", "trovit.")
PAGES_UTILES = ("", "/contact", "/fr/contact", "/carrieres", "/fr/carrieres", "/careers", "/jobs", "/recrutement",
                "/vacatures", "/nl/contact", "/a-propos", "/about")


def domaine(url: str | None) -> str:
    return urlparse(url if "://" in (url or "") else f"https://{url or ''}").netloc.lower().removeprefix("www.")


def domaine_officiel(url: str | None = None, email: str | None = None) -> str:
    """Le site de l'employeur quand on le connaît déjà : domaine de son email pro, ou lien vers SON site
    (pas un site d'emploi, pas un webmail)."""
    for d in (domaine_email(email), domaine(url) if (url or "").startswith("http") else ""):
        if d and "." in d and not any(x in d for x in WEBMAILS + _PAS_UN_EMPLOYEUR):
            return d
    return ""


def lire_site(dom: str, limite: int = 9000, delai: int = 8) -> tuple[str, list[str]]:
    """Accueil + pages contact / carrières du site officiel, en parallèle, sans IA (gratuit, ~5 s).
    Renvoie (texte utile, adresses email lues dans les pages)."""
    if not dom:
        return "", []
    from concurrent.futures import ThreadPoolExecutor
    from urllib.parse import unquote

    def une(chemin):
        try:
            return telecharger(f"https://{dom}{chemin}", timeout=delai)
        except Exception:
            return ""

    with ThreadPoolExecutor(6) as ex:
        pages = list(ex.map(une, PAGES_UTILES))
    textes, debuts, emails = [], set(), []
    for chemin, page in zip(PAGES_UTILES, pages):
        if not page:
            continue
        _signaler_ats(page, dom)
        for e in emails_dans(unquote(html.unescape(page))):          # aussi dans l'en-tête / le pied de page
            if e not in emails:
                emails.append(e)
        t = texte_html(page, 2500)
        if len(t) > 80 and t[:200] not in debuts:                     # pages identiques (site en JavaScript)
            debuts.add(t[:200])
            textes.append(f"[{dom}{chemin or '/'}]\n{t}")
    return "\n\n".join(textes)[:limite], emails


def _signaler_ats(page: str, dom: str):
    """Une page carrières hébergée par un logiciel de recrutement, repérée sur le site d'un employeur, est ensuite
    surveillée gratuitement à chaque veille (voir agents/connecteurs.py)."""
    try:
        from .agents import connecteurs
        trouves = connecteurs.detecter_ats(page)
        if trouves:
            connecteurs.noter_ats(trouves, f"site de {dom}")
    except Exception:
        pass


def _meme_domaine(a: str, b: str) -> bool:
    return bool(a and b) and (a == b or a.endswith("." + b) or b.endswith("." + a))


def verifier_email(email: str | None, employeur: str | None, dom: str = "", lus: list[str] | tuple = ()) -> str | None:
    """Une IA peut inventer « jobs@cabinet.be » : le CODE vérifie chaque adresse avant de s'en servir.
      • lue telle quelle sur le site (`lus`, ou en allant lire le site de son domaine, gratuitement) → gardée ;
      • sinon, l'adresse que ce site publie vraiment (jobs@, rh@… de préférence) la remplace ;
      • du domaine officiel de l'employeur, ou d'un site qui porte son nom, sans autre adresse publiée → gardée ;
      • sinon → None (mieux vaut postuler via le site qu'écrire dans le vide)."""
    e = (emails_dans(email or "") or [None])[0]
    if not e:
        return None
    lus = [x.lower() for x in lus]
    if e in lus:
        return e
    d = e.split("@")[-1]
    if any(x in d for x in WEBMAILS + _PAS_UN_EMPLOYEUR):
        return None
    officiel = _meme_domaine(d, dom)
    if officiel and not lus:
        texte, trouves = lire_site(d, limite=4000)
    elif officiel:
        texte, trouves = "", lus                  # le site officiel a déjà été lu
    else:
        texte, trouves = lire_site(d, limite=4000)
    if e in trouves:
        return e
    siens = [x for x in trouves if _meme_domaine(x.split("@")[-1], d)]
    recrutement = [x for x in siens if RE_EMAIL_RECRUTEMENT.match(x)]
    if recrutement or siens:
        return (recrutement or siens)[0]
    jetons = [m for m in re.findall(r"[a-z0-9]{4,}", sans_accents(employeur or ""))
              if m not in ("cabinet", "avocats", "avocat", "associes", "group", "groupe", "belgium", "advocaten",
                           "magasin", "boutique", "restaurant", "brasserie", "store", "shop")]
    if officiel or (texte and jetons and any(j in sans_accents(texte) for j in jetons)):
        return e
    return None
