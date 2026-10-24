"""Agents Éclaireurs : trouvent les offres, en parallèle, aussi souvent que possible. Fichier COMMUN à tous les agents
(ce qui change d'un métier à l'autre est dans metier.py).

  Gratuits (sans IA), à chaque veille (toutes les 15 min), chaque source à son rythme :
    • EURES : toutes les offres des services publics belges (Forem, VDAB, Actiris) + agences + employeurs
    • Forem (données ouvertes) : ~26 000 offres en temps réel, dont celles reprises de Jobat
    • Recruteurs : pages carrières des entreprises (Greenhouse, Lever, Ashby, Workable, SmartRecruiters, Recruitee,
      Teamtailor, Personio) — découvertes au fil de l'eau, puis surveillées pour toujours
    • Indeed Belgique + Google Jobs via JobSpy (Google reprend aussi LinkedIn, Jobat, StepStone… à notre place)
    • Listes et pages suivies (pages de listes, flux RSS, pages carrières)
    • Sources du métier (metier.SOURCES) : LexGO pour le droit, Student.be / StudentJob pour les jobs étudiants
    • Les alertes emploi reçues par email (LinkedIn, Jobat, StepStone…) : lues par le Trieur (agents/connecteurs.py)
  Payant (IA + recherche web) → 2 fois par jour :
    • Éclaireur web : ce qui manque encore (barreaux, sites spécialisés, pages carrières sans logiciel connu)
    • Découvreur de recruteurs : 1 fois par jour, de nouvelles pages carrières d'entreprises à surveiller
Une source qui ne répond plus a 3 minutes, ensuite on avance sans elle."""
from __future__ import annotations

import json
import time
import xml.etree.ElementTree as ET
import re
from concurrent.futures import ThreadPoolExecutor, wait

from .. import config, db, llm, metier, outils
from . import connecteurs

LEXGO = "https://www.lexgo.be/api/v1"
# Déjà lus gratuitement : la recherche web (payante) ne doit pas y perdre une seule recherche
DEJA_SURVEILLES = metier.DEJA_LUS + ["indeed.com", "be.indeed.com", "leforem.be", "odwb.be"]

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


# ---------- LexGO (API publique, gratuite) ----------
def _get_json(url, params=None, etag_cle=None):
    import requests

    entetes = {"Accept": "application/json", "User-Agent": connecteurs.UA_AGENT}
    if etag_cle and db.kv_get(etag_cle):
        entetes["If-None-Match"] = db.kv_get(etag_cle)
    r = requests.get(url, params=params, headers=entetes, timeout=20)
    if r.status_code == 304:
        return None
    r.raise_for_status()
    if etag_cle and r.headers.get("ETag"):
        db.kv_set(etag_cle, r.headers["ETag"])
    return r.json()


def _fonctions_lexgo(dom) -> list[int]:
    """Associe tes postes visés (stagiaire, juriste…) aux catégories LexGO."""
    cache = db.kv_get("lexgo_fonctions")
    if cache is None:
        cache = (_get_json(f"{LEXGO}/functions") or {}).get("functions", [])
        db.kv_set("lexgo_fonctions", cache)
    voulus = [outils.sans_accents(p) for p in dom.get("postes_vises") or []]
    ids = []
    for f in cache:
        noms = " ".join(outils.sans_accents(f.get(k) or "") for k in ("fr_name", "nl_name", "en_name"))
        if any(mot in noms for v in voulus for mot in v.split() if len(mot) > 3):
            ids.append(f["id"])
    return ids or [None]


def _lexgo(dom: dict) -> list[dict]:
    if "lexgo" not in metier.SOURCES:
        return []
    connus = {r["url"] for r in db.q("SELECT url FROM offres WHERE source='lexgo'")}
    sortie, details = [], 0
    for fid in _fonctions_lexgo(dom):
        params = {"domain": "be", "limit": 50}
        if fid:
            params["function_id"] = fid
        page = _get_json(f"{LEXGO}/jobs", params, etag_cle=f"lexgo_etag_{fid}")
        for j in (page or {}).get("jobs", []):
            url = j.get("click_url") or j.get("url")
            if not url or url in connus or details >= 40:
                continue
            try:
                time.sleep(1.1)          # LexGO autorise 60 requêtes / minute
                d = _get_json(f"{LEXGO}/jobs/{j['id']}")
                details += 1
            except Exception:
                d = j
            t, desc = d.get("titles") or {}, d.get("descriptions") or d.get("summaries") or {}
            c = d.get("contract") or {}
            sortie.append({
                "url": url, "source": "lexgo",
                "titre": t.get("fr") or t.get("en") or t.get("nl"),
                "entreprise": (d.get("company") or {}).get("name"),
                "ville": (d.get("location") or {}).get("city"),
                "secteur": ", ".join(x.get("fr_name") or x.get("en_name") or "" for x in d.get("disciplines") or []) or None,
                "contrat": ", ".join(k for k in ("permanent", "temporary", "self_employed") if c.get(k)) or None,
                "langue": None,   # LexGO donne la langue de l'ANNONCE, pas la langue exigée
                "description": ((f"[Annonce rédigée en : {', '.join(d.get('languages') or [])}] "
                                 if d.get("languages") else "")
                                + (desc.get("fr") or desc.get("en") or desc.get("nl") or ""))[:5000],
                "publie_le": (d.get("published_at") or "")[:10] or None,
                "mode_candidature": "formulaire",
            })
    return sortie


# ---------- Listes d'annonces : sans IA ----------
# Un lien d'annonce = une URL dont le chemin ressemble à ces motifs (pas les pages de liste ni de recherche)
MOTIFS_ANNONCE = (
    r"student\.be/(?:fr|nl|en)/(?:jobs-etudiants|studentenjobs|student-jobs)/[a-z0-9]+(?:-[a-z0-9]+){3,}/?$",
    r"studentjob\.be/(?:offre|vacatures?)/\d+-[a-z0-9-]+/?$",
    r"lexgo\.be/(?:fr|nl|en)/(?:emploi|job|vacature)s?/\d+[a-z0-9-]*/?$",
    r"/(?:offre|offres|job|jobs|emploi|vacature|vacatures)/[^/?#]*\d{3,}[^/?#]*/?$",
)


def _liens_annonces(page: str, base: str) -> list[tuple[str, str]]:
    """(url, texte du lien) de chaque annonce trouvée sur une page de liste."""
    import re
    from html import unescape
    from urllib.parse import urljoin

    vus, sortie = set(), []
    liens = re.findall(r"""<a\b[^>]*?href\s*=\s*["']([^"'#]+)["'][^>]*>(.*?)</a>""", page, re.S | re.I)
    liens += [(u, t) for t, u in re.findall(r"\[([^\]]{0,400})\]\((https?://[^)\s]+)\)", page)]   # texte Markdown
    # Pages construites en JavaScript : les liens sont dans des données JSON, pas dans des <a>
    liens += [(u.replace("\\/", "/"), "") for u in
              re.findall(r"""["']((?:https?:\\?/\\?/[a-z0-9.-]+)?(?:\\?/[a-z0-9_.-]+){2,6}\\?/?)["']""", page, re.I)]
    for href, texte in liens:
        url = urljoin(base, unescape(href)).split("?")[0]
        if url in vus or not any(re.search(m, url) for m in MOTIFS_ANNONCE):
            continue
        titre = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", unescape(texte))).strip()
        # Le texte du lien est souvent toute la « carte » (« … Nouveau ! Soyez le premier à postuler lire la suite ») :
        # on coupe ce bruit ; s'il reste trop long ou vide, le titre vient de l'adresse de l'annonce.
        titre = re.split(r"\s(?:Nouveau\s*!|Soyez le premier|lire la suite|Postuler|Voir l.offre|Nieuw\s*!|Lees meer)",
                         titre, maxsplit=1, flags=re.I)[0].strip()
        if len(titre) < 4 or len(titre) > 90:
            slug = url.rstrip("/").rsplit("/", 1)[-1]
            slug = re.sub(r"(?:-[0-9a-f]{4,12}){3,}$", "", slug)        # identifiant technique en fin d'adresse
            titre = re.sub(r"^\d+-", "", slug).replace("-", " ").strip().capitalize()
        vus.add(url)
        sortie.append((url, titre[:160]))
    return sortie


# Noms des villes tels que les sites étudiants les écrivent dans leurs adresses
_VILLES = {"bruxelles": ("bruxelles", "brussel"), "brussel": ("bruxelles", "brussel"), "malines": ("mechelen",) * 2,
           "mechelen": ("mechelen",) * 2, "anvers": ("antwerpen",) * 2, "antwerpen": ("antwerpen",) * 2,
           "louvain": ("leuven",) * 2, "leuven": ("leuven",) * 2, "gand": ("gent",) * 2, "gent": ("gent",) * 2,
           "liege": ("liege", "luik"), "namur": ("namur", "namen"), "mons": ("mons", "bergen"),
           "louvain-la-neuve": ("louvain-la-neuve",) * 2, "charleroi": ("charleroi",) * 2, "bruges": ("brugge",) * 2,
           "brugge": ("brugge",) * 2, "wavre": ("wavre", "waver"), "nivelles": ("nivelles", "nijvel"),
           "ottignies": ("ottignies",) * 2, "hasselt": ("hasselt",) * 2, "courtrai": ("kortrijk",) * 2,
           "kortrijk": ("kortrijk",) * 2, "alost": ("aalst",) * 2, "aalst": ("aalst",) * 2, "arlon": ("arlon",) * 2,
           "tournai": ("tournai", "doornik"), "verviers": ("verviers",) * 2}


# Listes générées pour chacune de tes villes, selon le métier ({fr} / {nl} = nom de la ville dans l'adresse)
_LISTES_PAR_VILLE = {"job_etudiant": ["https://www.student.be/fr/{fr}/jobs-etudiants/",
                                      "https://www.student.be/nl/{nl}/studentenjobs/",
                                      "https://fr.studentjob.be/offres-d-emploi/{fr}",
                                      "https://www.studentjob.be/vacatures/{nl}"]}


def _pages_de_listes(dom: dict) -> list[str]:
    """Les listes que tu as indiquées + celles du métier pour CHACUNE de tes villes."""
    urls = list(dom.get("listes_a_surveiller") or [])
    modeles = dom.get("listes_par_ville") or _LISTES_PAR_VILLE.get(config.objectif().get("domaine_actif"), [])
    for v in (dom.get("villes") or []) if modeles else []:
        cle = re.sub(r"[^a-z-]", "", outils.sans_accents(v).replace(" ", "-"))
        fr, nl = _VILLES.get(cle, (cle, cle))
        urls += [m.format(fr=fr, nl=nl) for m in modeles]
    vus = []
    for u in urls:
        if u not in vus:
            vus.append(u)
    return vus[:16]


def _listes(dom: dict) -> list[dict]:
    sortie = []
    for url in _pages_de_listes(dom):
        try:
            page = outils.telecharger(url)
        except Exception as e:
            # adresse de ville inconnue du site (404) : on le note une fois par jour, sans insister
            cle = f"liste_ko_{url}"
            if db.kv_get(cle) != str(__import__("datetime").date.today()):
                db.kv_set(cle, str(__import__("datetime").date.today()))
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
    """Indeed + Google Jobs (Google regroupe les offres des agences d'intérim — Randstad, Start People, Tempo-Team,
    Adecco… — et des sites des enseignes). Seulement les offres des 4 derniers jours."""
    try:
        from jobspy import scrape_jobs
    except ImportError:
        return []
    mots = (dom.get("mots_cles_recherche") or [])[:4]
    if not mots:
        return []
    villes = (dom.get("villes") or ["Belgique"])[:3]

    def une(site, mot, ville):
        # un site à la fois : si Google change sa page, Indeed continue de marcher (et inversement)
        extra = {"google_search_term": f"{mot} {ville} depuis 3 jours"} if site == "google" else {}
        try:
            df = scrape_jobs(site_name=[site], location=ville, search_term=mot, country_indeed="belgium",
                             results_wanted=15, hours_old=24 * 4, **extra)
        except Exception as e:
            db.journal("eclaireur", f"{site} « {mot} » : {type(e).__name__}")
            return []
        return [{"url": r.get("job_url"), "titre": r.get("title"), "entreprise": r.get("company"),
                 "ville": r.get("location"), "description": (r.get("description") or "")[:4000],
                 "email_contact": (outils.emails_dans(str(r.get("emails") or "")) or [None])[0],
                 "publie_le": str(r.get("date_posted") or "")[:10] or None, "source": r.get("site") or "indeed"}
                for _, r in df.iterrows()]

    with ThreadPoolExecutor(4) as ex:
        lots = list(ex.map(lambda smv: une(*smv), [(s, m, v) for s in ("indeed", "google") for m in mots
                                                    for v in villes]))
    return [o for lot in lots for o in lot if o["url"] and o["titre"]]


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
    from datetime import date
    contenu = json.dumps({"requete": requete, "villes": dom.get("villes"), "postes": dom.get("postes_vises"),
                          "secteurs_uniquement": dom.get("secteurs_uniquement"),
                          "secteurs_exclus": dom.get("secteurs_exclus"), "langues": dom.get("langues"),
                          "date_du_jour": date.today().isoformat(),
                          "deja_connues": connues, "nombre_max": 10}, ensure_ascii=False)
    r = llm.demander("eclaireur", "rapide", llm.prompt("eclaireur_web"), contenu, SCHEMA_OFFRES,
                     web=True, recherches_max=3, max_tokens=4000, priorite="fond", sites_bloques=DEJA_SURVEILLES)
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


def recherche_web(n_requetes: int = 2) -> int:
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
    _sur(lambda d: decouvrir_recruteurs(), dom, "découverte de recruteurs")
    return enregistrer([o for lot in lots for o in lot])


def _eures(dom):
    return connecteurs.eures(dom)


def _forem(dom):
    return connecteurs.forem(dom)


def _recruteurs(dom):
    return connecteurs.ats(dom)


# Chaque source à son rythme (minutes) : assez souvent pour être parmi les premiers, sans abuser des sites
RYTHMES = {"_lexgo": 15, "_listes": 30, "_jobspy": 60, "_pages": 60, "_eures": 60, "_forem": 60, "_recruteurs": 180}


def _a_lancer(f, force: bool) -> bool:
    cle = f"derniere_veille{f.__name__}"
    if not force and time.time() - float(db.kv_get(cle) or 0) < RYTHMES.get(f.__name__, 15) * 60 - 30:
        return False
    db.kv_set(cle, time.time())
    return True


def veille_gratuite(force: bool = False) -> int:
    dom = config.domaine_actif()
    # Une source qui ne répond plus (Indeed bloque parfois sans jamais rendre la main) ne doit JAMAIS
    # bloquer l'agent : chacune a 3 minutes, ensuite on avance avec ce qu'on a.
    ex = ThreadPoolExecutor(7)
    futurs = {ex.submit(_sur, f, dom, f.__name__.strip("_")): f.__name__.strip("_")
              for f in (_lexgo, _listes, _jobspy, _pages, _eures, _forem, _recruteurs) if _a_lancer(f, force)}
    fini, pas_fini = wait(futurs, timeout=180)
    ex.shutdown(wait=False, cancel_futures=True)
    for f in pas_fini:
        db.journal("eclaireur", f"{futurs[f]} trop lent (> 3 min) : ignoré pour cette fois")
    return enregistrer([o for f in fini for o in (f.result() or [])])


def chercher() -> int:
    """Passage complet (utilisé par les tests et « cherche » sur Telegram)."""
    return veille_gratuite(force=True) + recherche_web()


SCHEMA_PAGES = {"type": "object", "properties": {"pages": {"type": "array", "items": {"type": "string"},
                "description": "adresses des pages carrières trouvées"}}, "required": ["pages"]}


def decouvrir_recruteurs() -> int:
    """Une fois par jour : la recherche web trouve des pages carrières d'entreprises (logiciels de recrutement) qui
    publient des offres pour ce profil en Belgique. Ensuite, elles sont surveillées gratuitement à chaque veille."""
    from datetime import date
    if db.kv_get("decouverte_ats_du") == date.today().isoformat():
        return 0
    dom = config.domaine_actif()
    mots = connecteurs.mots_cles(dom)
    if not mots:
        return 0
    db.kv_set("decouverte_ats_du", date.today().isoformat())
    contenu = json.dumps({"mots_cles": mots[:4], "villes": dom.get("villes"),
                          "secteurs": dom.get("secteurs_uniquement") or dom.get("description")}, ensure_ascii=False)
    r = llm.demander("decouvreur", "rapide", llm.prompt("decouvreur_ats"), contenu, SCHEMA_PAGES,
                     web=True, recherches_max=4, max_tokens=2500, priorite="fond")
    return connecteurs.noter_ats(connecteurs.detecter_ats(" ".join(r.get("pages") or [])), "recherche web")


def enregistrer(offres: list[dict]) -> int:
    # Chaque offre publiée via un logiciel de recrutement révèle une page carrières à surveiller ensuite
    connecteurs.noter_ats(connecteurs.detecter_ats(" ".join(o.get("url") or "" for o in offres)), "offres vues")
    nouvelles = 0
    with db.tx() as c:
        for o in offres:
            if not o.get("url") or not o.get("titre"):
                continue
            o["email_contact"] = (outils.emails_dans(o.get("email_contact") or "") or [None])[0]
            cle = outils.cle_offre(o["url"], o.get("entreprise"), o.get("titre"))
            cur = c.execute(
                """INSERT OR IGNORE INTO offres (cle, url, source, titre, entreprise, ville, secteur, contrat, langue,
                   horaires, salaire_horaire, description, email_contact, mode_candidature, publie_le, date_limite)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (cle, o["url"], o.get("source"), o["titre"], o.get("entreprise"), o.get("ville"), o.get("secteur"),
                 o.get("contrat"), o.get("langue"), o.get("horaires"), o.get("salaire_horaire"), o.get("description"), o.get("email_contact"),
                 o.get("mode_candidature"), o.get("publie_le"), o.get("date_limite")))
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
