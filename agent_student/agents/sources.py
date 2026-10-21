"""Agents Éclaireurs : trouvent les jobs étudiants, en parallèle, aussi souvent que possible.

  Gratuits (sans IA) :
    • Listes surveillées toutes les 30 min : Student.be, StudentJob.be (par ville) — on relève les liens
      d'annonces, l'Extracteur lit ensuite chaque annonce
    • Indeed Belgique via JobSpy (librairie open source MIT, optionnelle)
    • Pages à surveiller (flux RSS ou pages carrières d'enseignes)
  Payant (IA + recherche web) → 2-3 fois par jour :
    • Éclaireur web : annonces de jobs étudiants ailleurs (sites d'enseignes, agences d'intérim étudiant…)
"""
from __future__ import annotations

import json
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor

from .. import config, db, llm, outils

SCHEMA_OFFRES = {
    "type": "object",
    "properties": {"offres": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "url": {"type": "string"}, "titre": {"type": "string"}, "entreprise": {"type": ["string", "null"]},
            "ville": {"type": ["string", "null"]}, "secteur": {"type": ["string", "null"]},
            "contrat": {"type": ["string", "null"]}, "langue": {"type": ["string", "null"]},
            "description": {"type": ["string", "null"]}, "email_contact": {"type": ["string", "null"]},
            "mode_candidature": {"type": "string", "enum": ["email", "formulaire", "inconnu"]},
            "publie_le": {"type": ["string", "null"], "description": "AAAA-MM-JJ si visible"},
        },
        "required": ["url", "titre"],
    }}},
    "required": ["offres"],
}


# ---------- Listes d'annonces (Student.be, StudentJob…) : sans IA ----------
# Un lien d'annonce = une URL dont le chemin ressemble à ces motifs (pas les pages de liste ni de recherche)
MOTIFS_ANNONCE = (
    r"student\.be/(?:fr|nl|en)/(?:jobs-etudiants|studentenjobs|student-jobs)/[a-z0-9]+(?:-[a-z0-9]+){2,}/?$",
    r"studentjob\.be/(?:offre|vacatures)/\d+-[a-z0-9-]+/?$",
    r"/(?:offre|offres|job|jobs|emploi|vacature|vacatures)/[^/?#]*\d{3,}[^/?#]*/?$",
)


def _liens_annonces(page: str, base: str) -> list[tuple[str, str]]:
    """(url, texte du lien) de chaque annonce trouvée sur une page de liste."""
    import re
    from html import unescape
    from urllib.parse import urljoin

    vus, sortie = set(), []
    liens = re.findall(r"""<a\b[^>]*?href\s*=\s*["']([^"'#]+)["'][^>]*>(.*?)</a>""", page, re.S | re.I)
    # Pages construites en JavaScript : les liens sont dans des données JSON, pas dans des <a>
    liens += [(u.replace("\\/", "/"), "") for u in
              re.findall(r"""["']((?:https?:\\?/\\?/[a-z0-9.-]+)?(?:\\?/[a-z0-9_.-]+){2,6}\\?/?)["']""", page, re.I)]
    for href, texte in liens:
        url = urljoin(base, unescape(href)).split("?")[0]
        if url in vus or not any(re.search(m, url) for m in MOTIFS_ANNONCE):
            continue
        titre = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", unescape(texte))).strip()
        if len(titre) < 4:                       # lien image / icône : on prend le titre depuis l'URL
            titre = url.rstrip("/").rsplit("/", 1)[-1].split("-", 1)[-1].replace("-", " ").strip().capitalize()
        vus.add(url)
        sortie.append((url, titre[:160]))
    return sortie


def _listes(dom: dict) -> list[dict]:
    sortie = []
    for url in dom.get("listes_a_surveiller") or []:
        try:
            page = outils.telecharger(url)
        except Exception as e:
            db.journal("eclaireur", f"liste {url} : {type(e).__name__}")
            continue
        liens = _liens_annonces(page, url)
        if not liens:
            db.journal("eclaireur", f"liste {url} : aucune annonce reconnue (page de {len(page)} caractères)")
        sortie += [{"url": u, "titre": t, "source": "liste"} for u, t in liens[:40]]
        time.sleep(1)                            # politesse envers le site
    return sortie


# ---------- Indeed via JobSpy (optionnel) ----------
def _jobspy(dom: dict) -> list[dict]:
    try:
        from jobspy import scrape_jobs
    except ImportError:
        return []
    sortie = []
    for mot in dom.get("mots_cles_recherche", [])[:4]:
        for ville in (dom.get("villes") or ["Belgique"])[:3]:
            df = scrape_jobs(site_name=["indeed"], location=ville,
                             search_term=mot if "tudiant" in mot else f"job étudiant {mot}",
                             country_indeed="belgium", results_wanted=20, hours_old=24 * 7)
            for _, r in df.iterrows():
                sortie.append({"url": r.get("job_url"), "titre": r.get("title"), "entreprise": r.get("company"),
                               "ville": r.get("location"), "description": (r.get("description") or "")[:4000],
                               "email_contact": (outils.emails_dans(str(r.get("emails") or "")) or [None])[0],
                               "publie_le": str(r.get("date_posted") or "")[:10] or None, "source": "indeed"})
    return sortie


# ---------- Pages surveillées ----------
def _pages(dom: dict) -> list[dict]:
    sortie = []
    for url in dom.get("pages_a_surveiller") or []:
        try:
            page = outils.telecharger(url)
        except Exception as e:
            db.journal("eclaireur", f"page {url} : {e}")
            continue
        if "<rss" in page[:500] or "<feed" in page[:500]:
            atom = "{http://www.w3.org/2005/Atom}"
            for it in ET.fromstring(page.encode()).iter():
                if it.tag.split("}")[-1] not in ("item", "entry"):
                    continue
                lien = it.findtext("link") or (it.find(f"{atom}link").get("href") if it.find(f"{atom}link") is not None else None)
                titre = it.findtext("title") or it.findtext(f"{atom}title")
                if lien and titre:
                    sortie.append({"url": lien, "titre": titre, "source": "flux"})
        else:
            jp = outils.jobposting_jsonld(page)
            if jp:
                sortie.append({**jp, "url": url, "source": "page", "publie_le": (jp.get("date") or "")[:10] or None})
    return sortie


# ---------- Éclaireur web (IA) ----------
def _une_requete(requete: str, dom: dict, connues: list) -> list[dict]:
    contenu = json.dumps({"requete": requete, "villes": dom.get("villes"), "postes": dom.get("postes_vises"),
                          "secteurs_uniquement": dom.get("secteurs_uniquement"),
                          "secteurs_exclus": dom.get("secteurs_exclus"), "langues": dom.get("langues"),
                          "deja_connues": connues, "nombre_max": 10}, ensure_ascii=False)
    r = llm.demander("eclaireur", "rapide", llm.prompt("eclaireur_web"), contenu, SCHEMA_OFFRES,
                     web=True, recherches_max=3, max_tokens=4000, priorite="fond")
    offres = [{**o, "source": "web"} for o in r.get("offres", [])]
    bonnes = [o for o in offres if annonce_precise(o)]
    if len(bonnes) < len(offres):
        db.journal("eclaireur", f"web : {len(offres) - len(bonnes)} résultat(s) ignoré(s) (pas une annonce précise)")
    return bonnes


_FIN_DE_LISTE = {"jobs", "job", "emploi", "emplois", "vacatures", "offres", "offres-d-emploi", "carrieres",
                 "careers", "recrutement", "jobs-etudiants", "studentenjobs", "student-jobs",
                 "offres-d-emploi-etudiant", ""}
_MORCEAUX_DE_LISTE = ("/search", "/recherche", "/functions/", "?q=", "&q=", "?keywords=", "?query=")


def annonce_precise(o: dict) -> bool:
    """Une vraie annonce : un employeur nommé et un lien vers L'annonce, pas vers une page de résultats."""
    from urllib.parse import urlparse
    url = (o.get("url") or "").strip().lower()
    if not o.get("entreprise") or not url.startswith("http") or any(m in url for m in _MORCEAUX_DE_LISTE):
        return False
    dernier = urlparse(url).path.rstrip("/").rsplit("/", 1)[-1]
    return dernier not in _FIN_DE_LISTE


def recherche_web(n_requetes: int = 1) -> int:
    dom = config.domaine_actif()
    requetes = dom.get("mots_cles_recherche") or []
    if not requetes:
        return 0
    i = db.kv_get("rotation_requete", 0)
    choix = [requetes[(i + k) % len(requetes)] for k in range(min(n_requetes, len(requetes)))]
    db.kv_set("rotation_requete", i + len(choix))
    connues = [r["url"] for r in db.q("SELECT url FROM offres ORDER BY id DESC LIMIT 120")]
    with ThreadPoolExecutor(len(choix)) as ex:
        lots = list(ex.map(lambda q: _sur(lambda d: _une_requete(q, d, connues), dom, f"web « {q} »"), choix))
    return enregistrer([o for lot in lots for o in lot])


def veille_gratuite() -> int:
    dom = config.domaine_actif()
    with ThreadPoolExecutor(3) as ex:
        lots = list(ex.map(lambda f: _sur(f, dom, f.__name__.strip("_")), (_listes, _jobspy, _pages)))
    return enregistrer([o for lot in lots for o in lot])


def chercher() -> int:
    """Passage complet (utilisé par les tests et « cherche » sur Telegram)."""
    return veille_gratuite() + recherche_web()


def enregistrer(offres: list[dict]) -> int:
    nouvelles = 0
    with db.tx() as c:
        for o in offres:
            if not o.get("url") or not o.get("titre"):
                continue
            o["email_contact"] = (outils.emails_dans(o.get("email_contact") or "") or [None])[0]
            cle = outils.cle_offre(o["url"], o.get("entreprise"), o.get("titre"))
            cur = c.execute(
                """INSERT OR IGNORE INTO offres (cle, url, source, titre, entreprise, ville, secteur, contrat, langue,
                   horaires, salaire_horaire, description, email_contact, mode_candidature, publie_le)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (cle, o["url"], o.get("source"), o["titre"], o.get("entreprise"), o.get("ville"), o.get("secteur"),
                 o.get("contrat"), o.get("langue"), o.get("horaires"), o.get("salaire_horaire"), o.get("description"), o.get("email_contact"),
                 o.get("mode_candidature"), o.get("publie_le")))
            nouvelles += cur.rowcount
    if nouvelles:
        db.journal("eclaireur", f"{nouvelles} nouvelle(s) offre(s)")
    return nouvelles


def _sur(f, dom, nom):
    try:
        return f(dom)
    except Exception as e:
        db.journal("eclaireur", f"{nom} indisponible : {type(e).__name__}: {e}")
        return []
