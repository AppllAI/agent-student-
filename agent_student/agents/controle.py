"""Agent Contrôleur — dernier filet de sécurité AVANT chaque envoi, sans IA (gratuit, infaillible).

Il attrape les erreurs qui ruinent une candidature et qu'une IA peut laisser passer :
mauvais nom d'employeur, nom d'un AUTRE employeur copié, texte à trous, pièce jointe manquante…"""
from __future__ import annotations

import re

from .. import config, db, outils
from .redacteur import chemin_lettre

TROUS = re.compile(r"\[[^\]]{0,40}\]|\bX{3,}\b|<[^>@\s]{2,30}>|\{\w+\}|\bTODO\b|nom du cabinet|nom de l'employeur", re.I)
MOTS_VIDES = {"cabinet", "avocats", "avocat", "associes", "associés", "law", "firm", "scrl", "srl", "sprl", "bv",
              "shop", "store", "magasin", "boutique", "brasserie", "restaurant", "group", "groupe", "belgium", "belgique", "brussels",
              "bruxelles", "services", "service", "company", "nv", "asbl", "vzw", "interim", "jobs", "students",
              "the", "les", "des", "and", "partners", "legal", "advocaten"}


def _jetons(nom: str) -> list[str]:
    return [m for m in re.findall(r"[a-z0-9]+", outils.sans_accents(nom)) if len(m) >= 4 and m not in MOTS_VIDES]


NOMBRES = {"un": 1, "une": 1, "deux": 2, "trois": 3, "quatre": 4, "cinq": 5, "six": 6, "sept": 7, "huit": 8,
           "neuf": 9, "dix": 10, "douze": 12, "quinze": 15, "vingt": 20}
_DUREE = re.compile(r"\b(\d+|un|une|deux|trois|quatre|cinq|six|sept|huit|neuf|dix|douze|quinze|vingt)\s+"
                    r"(ans?|annees?|mois|semaines?|jours?)\b")
_CHIFFRE = re.compile(r"\b\d+\b")
_DATE = re.compile(r"\b\d{1,2}(er)?\s+(janvier|fevrier|février|mars|avril|mai|juin|juillet|aout|août|septembre|octobre|"
                   r"novembre|decembre|décembre)\s+\d{4}\b", re.I)
_TEL = re.compile(r"(?:\+32|0)\s*\d[\d\s./-]{7,}\d")


def _norm(t: str) -> str:
    t = outils.sans_accents(t)
    return re.sub(r"\b(" + "|".join(NOMBRES) + r")\b", lambda m: str(NOMBRES[m.group(1)]), t)


def _mots(t: str) -> list[str]:
    return [m for m in re.findall(r"[a-z0-9]+", _norm(t)) if len(m) >= 3]


def preuve_valide(preuve: str | None, profil: str) -> bool:
    """La « preuve » donnée par le Juge existe-t-elle vraiment dans le profil ? (≥ 85 % de ses mots, dans le profil)
    C'est le code qui tranche : une IA ne peut pas valider une invention en inventant aussi la preuve."""
    if not preuve or not preuve.strip():
        return False
    mots = _mots(preuve)
    if not mots:
        return False
    dispo = set(_mots(profil))
    return sum(m in dispo for m in mots) / len(mots) >= 0.85


def chiffres_inventes(lettre: str, sources: str) -> list[str]:
    """Durées et chiffres de la lettre qu'on ne retrouve NI dans le profil NI dans l'offre (ex. « pendant un an »
    alors que le CV dit « 3 mois »). Gratuit, sans IA : attrape ce que le Juge peut laisser passer."""
    lettre = _TEL.sub(" ", _DATE.sub(" ", lettre))          # la date du jour et le téléphone ne sont pas des « faits »
    src = _norm(sources)
    suspects = []
    for m in _DUREE.finditer(_norm(lettre)):
        n = m.group(1)
        unite = m.group(2)[:3]
        if not re.search(rf"\b{n}\s*\(?\s*{unite}", src):
            suspects.append(m.group(0))
    niveaux_src = set(re.findall(r"\b[ABC][12]\b", sources))
    for n in re.findall(r"\b[ABC][12]\b", lettre):          # niveaux de langue (B1 ≠ B2)
        if n not in niveaux_src:
            suspects.append(f"niveau {n}")
    for c in _CHIFFRE.findall(lettre):
        if len(c) <= 4 and c not in sources and not (len(c) == 4 and c.startswith("20")):  # années : déjà vérifiées par le Juge
            suspects.append(c)
    return sorted(set(suspects))


def problemes(o) -> list[str]:
    o = dict(o)
    p = []
    lettre, corps, objet = o.get("lettre") or "", o.get("corps_mail") or "", o.get("objet_mail") or ""
    if not (lettre and corps and objet):
        return ["lettre ou email vide"]
    sources = f"{config.profil_texte()}\n{o.get('titre') or ''}\n{o.get('entreprise') or ''}\n{o.get('description') or ''}"
    inventes = chiffres_inventes(f"{lettre}\n{corps}", sources) if config.profil_texte().strip() else []
    if inventes:
        return [f"chiffre(s) absent(s) de ton profil : {', '.join(inventes[:3])}"]
    tout = outils.sans_accents(f"{objet}\n{corps}\n{lettre}")
    if o.get("entreprise"):
        j = _jetons(o["entreprise"])
        if j and not any(x in tout for x in j):
            p.append(f"le nom « {o['entreprise']} » n'apparaît pas dans la lettre")
    propres = set(_jetons(o.get("entreprise") or ""))
    for r in db.q("SELECT DISTINCT entreprise FROM offres WHERE entreprise IS NOT NULL AND id<>?", o["id"]):
        autres = [x for x in _jetons(r["entreprise"]) if x not in propres and len(x) >= 5]
        if autres and all(re.search(rf"\b{re.escape(x)}\b", tout) for x in autres):
            p.append(f"mentionne un autre employeur : « {r['entreprise']} »")
            break
    if TROUS.search(lettre) or TROUS.search(corps):
        p.append(f"texte à trous : « {(TROUS.search(lettre) or TROUS.search(corps)).group(0)} »")
    mots = len(lettre.split())
    if not 110 <= mots <= 320:
        p.append(f"longueur anormale ({mots} mots)")
    if o.get("email_contact") and not outils.RE_EMAIL.fullmatch(o["email_contact"].strip()):
        p.append("adresse du destinataire invalide")
    if not config.chemin_cv().exists():
        p.append("CV manquant")
    if not chemin_lettre(o["id"]).exists():
        p.append("PDF de la lettre manquant")
    nom = (config.env("NOM_CANDIDAT") or "").strip()
    if nom and nom.split()[-1].lower() not in (lettre + corps).lower():
        p.append("ta signature (nom) n'apparaît pas")
    return p
