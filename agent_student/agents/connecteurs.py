"""Connecteurs d'offres : le cœur commun du moteur, IDENTIQUE pour tous les agents (seul metier.py change).

Uniquement des sources publiques, gratuites et autorisées (pas de connexion à un compte, pas de contournement) :

  • EURES        portail européen de l'emploi : TOUTES les offres des services publics belges (Forem, VDAB, Actiris,
                 ADG) + agences + employeurs, en FR/NL/EN. API publique du portail.
  • Forem        données ouvertes (ODWB) : ~26 000 offres en temps réel, dont celles reprises de Jobat et d'autres sites.
  • Recruteurs   les pages carrières des entreprises reposent sur quelques logiciels de recrutement (ATS) qui publient
                 leurs offres en API ouverte : Greenhouse, Lever, Ashby, Workable, SmartRecruiters, Recruitee,
                 Teamtailor, Personio. Une entreprise découverte une fois (offre vue, site lu, recherche web) est
                 ensuite surveillée gratuitement, pour toujours. C'est là que sont les offres que « personne ne voit ».
  • Alertes      les alertes emploi que la personne reçoit par email (LinkedIn, Jobat, StepStone, Indeed, Glassdoor…)
                 sont lues comme des offres : on a ainsi LinkedIn & co sans jamais toucher à leurs sites (CGU).
  • LexGO        (métier juridique) API publique officielle.

Toutes les offres passent ensuite par le même circuit : Extracteur → Garde → Évaluateur → Rédacteur → Juge."""
from __future__ import annotations

import html
import json
import re
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone

from .. import db, outils

UA_AGENT = "Mozilla/5.0 (compatible; agent-emploi/1.0; recherche d'emploi personnelle)"
FRAICHEUR_JOURS = 14          # au-delà, une offre a beaucoup moins de chances : on ne la ramène plus


def _get(url, **kw):
    import requests
    r = requests.get(url, headers={"User-Agent": UA_AGENT, "Accept": "application/json, text/xml, */*"},
                     timeout=kw.pop("timeout", 25), **kw)
    r.raise_for_status()
    return r


def _post(url, corps):
    import requests
    r = requests.post(url, json=corps, timeout=30, headers={"User-Agent": UA_AGENT, "Accept": "application/json"})
    r.raise_for_status()
    return r


def _frais(d: str | None) -> bool:
    """Publiée dans les FRAICHEUR_JOURS derniers jours (ou date inconnue : on laisse l'Évaluateur juger)."""
    if not d:
        return True
    try:
        return date.fromisoformat(str(d)[:10]) >= date.today() - timedelta(days=FRAICHEUR_JOURS)
    except ValueError:
        return True


# ---------- Mots-clés et lieux ----------
VILLES_BE = {   # ville (sans accents) → région NUTS1 (be1 Bruxelles, be2 Flandre, be3 Wallonie)
    "bruxelles": "be1", "brussel": "be1", "brussels": "be1", "ixelles": "be1", "etterbeek": "be1", "uccle": "be1",
    "schaerbeek": "be1", "anderlecht": "be1", "woluwe": "be1", "saint-gilles": "be1", "zaventem": "be2",
    "anvers": "be2", "antwerpen": "be2", "gand": "be2", "gent": "be2", "louvain": "be2", "leuven": "be2",
    "bruges": "be2", "brugge": "be2", "malines": "be2", "mechelen": "be2", "hasselt": "be2", "courtrai": "be2",
    "kortrijk": "be2", "alost": "be2", "aalst": "be2", "ostende": "be2", "oostende": "be2", "genk": "be2",
    "liege": "be3", "luik": "be3", "namur": "be3", "namen": "be3", "mons": "be3", "bergen": "be3", "charleroi": "be3",
    "louvain-la-neuve": "be3", "wavre": "be3", "nivelles": "be3", "ottignies": "be3", "tournai": "be3",
    "arlon": "be3", "verviers": "be3", "waterloo": "be3", "braine-l'alleud": "be3", "eupen": "be3",
}
PAYS_BE = ("belgique", "belgium", "belgie", "belgien", "be", "bel")


def regions(dom: dict) -> list[str]:
    r = {VILLES_BE.get(outils.sans_accents(v).strip()) for v in dom.get("villes") or []}
    r.discard(None)
    return sorted(r) or ["be"]


def sans_ville(mot: str) -> str:
    """« juriste Bruxelles » → « juriste » : le lieu est filtré à part (régions), pas dans le texte."""
    garde = [m for m in mot.split() if outils.sans_accents(m) not in VILLES_BE]
    return " ".join(garde) or mot


def mots_cles(dom: dict) -> list[str]:
    vus = []
    for m in (dom.get("mots_cles_recherche") or []) + (dom.get("postes_vises") or []):
        m = sans_ville(m).strip()
        if m and outils.sans_accents(m) not in [outils.sans_accents(v) for v in vus]:
            vus.append(m)
    return vus[:8]


def _racines(dom: dict) -> list[str]:
    """Racines des mots utiles (5 lettres) : « juriste » trouve aussi « jurist », « stagiaire » trouve « stagiair »."""
    r = set()
    for m in mots_cles(dom):
        for t in re.findall(r"[a-z]{4,}", outils.sans_accents(m)):
            if t not in ("dans", "pour", "avec", "temps", "plein", "partiel", "belgique"):
                r.add(t[:5])
    return sorted(r)


def pertinente(titre: str, dom: dict, texte: str = "") -> bool:
    racines = _racines(dom)
    t = outils.sans_accents(f"{titre} {texte[:300]}")
    return not racines or any(r in t for r in racines)


def en_belgique(lieu: str, dom: dict) -> bool:
    l = outils.sans_accents(lieu or "")
    if not l:
        return True
    if any(re.search(rf"(?<![a-z]){p}(?![a-z])", l) for p in PAYS_BE) or any(v in l for v in VILLES_BE):
        return True
    return bool(dom.get("teletravail_ok")) and any(x in l for x in ("remote", "teletravail", "europe", "emea"))


# ---------- EURES ----------
EURES = "https://europa.eu/eures/api/jv-searchengine/public"


def eures(dom: dict) -> list[dict]:
    sortie, vus = [], set()
    for mot in mots_cles(dom)[:6]:
        corps = {"resultsPerPage": 50, "page": 1, "sortSearch": "BEST_MATCH",
                 "keywords": [{"keyword": mot, "specificSearchCode": "EVERYWHERE"}], "publicationPeriod": "LAST_WEEK",
                 "euresFlagCodes": [], "otherBenefitsCodes": [], "requiredLanguages": [], "positionScheduleCodes": [],
                 "sectorCodes": [], "educationAndQualificationLevelCodes": [], "positionOfferingCodes": [],
                 "locationCodes": regions(dom), "occupationUris": [], "requiredExperienceCodes": [],
                 "minNumberPost": None, "sessionId": "agent"}
        for j in _post(f"{EURES}/jv-search/search", corps).json().get("jvs") or []:
            if j.get("id") in vus:
                continue
            vus.add(j.get("id"))
            tr = j.get("translations") or {}
            langue = next((l for l in ("fr", "en", "nl", "de") if l in tr), None)
            titre = re.sub(r"\s+", " ", (tr.get(langue) or {}).get("title") or j.get("title") or "").strip()
            desc = outils.texte_html((tr.get(langue) or {}).get("description") or j.get("description") or "", 4000)
            publie = datetime.fromtimestamp((j.get("creationDate") or 0) / 1000, timezone.utc).date().isoformat()
            if not titre or not _frais(publie) or not pertinente(titre, dom, desc):
                continue
            sortie.append({"url": f"https://europa.eu/eures/portal/jv-se/jv-details/{j['id']}?lang=fr",
                           "titre": titre[:160], "entreprise": (j.get("employer") or {}).get("name"),
                           "description": desc, "publie_le": publie, "source": "eures",
                           "contrat": ", ".join(j.get("positionScheduleCodes") or []) or None})
    return sortie


def detail_eures(url: str) -> dict:
    """Détail d'une offre EURES (ville, langues exigées, permis, adresse email, lien pour postuler)."""
    m = re.search(r"jv-details/([^/?#]+)", url or "")
    if not m:
        return {}
    d = _get(f"{EURES}/jv/id/{m.group(1)}", params={"requestLang": "fr"}).json()
    profils = d.get("jvProfiles") or {}
    p = profils.get("fr") or profils.get("en") or (next(iter(profils.values())) if profils else {})
    lieux = p.get("locations") or [{}]
    langues = []
    for l in p.get("positionLanguages") or []:
        niveau = l.get("requiredSkillLevel") or l.get("desiredSkillLevel")
        exige = " exigé" if l.get("requiredSkillLevel") else " (atout)"
        langues.append(f"{l.get('languageCode', '?')}{' ' + niveau.upper() if niveau else ''}{exige}")
    emp = p.get("employer") or {}
    infos = json.dumps(p, ensure_ascii=False)
    lien = re.search(r'href=\\?"(https?://[^"\\]+)', infos)
    return {"ville": (lieux[0].get("cityName") or "").title() or None, "langue": ", ".join(langues) or None,
            "entreprise": emp.get("name"), "email_contact": (outils.emails_dans(infos) or [None])[0],
            "description": outils.texte_html(p.get("description") or "", 4000) or None,
            "site_employeur": emp.get("website"), "lien_candidature": html.unescape(lien.group(1)) if lien else None,
            "permis": ", ".join(p.get("requiredDrivingLicenses") or []) or None}


# ---------- Forem (données ouvertes ODWB) ----------
FOREM = "https://www.odwb.be/api/explore/v2.1/catalog/datasets/offres-d-emploi-forem/records"


def forem(dom: dict) -> list[dict]:
    sortie, vus = [], set()
    depuis = (date.today() - timedelta(days=FRAICHEUR_JOURS)).isoformat()
    lieux = [r.upper() for r in regions(dom) if r != "be"]
    filtre_lieu = (" and (" + " or ".join(f'lieuxtravailregionnuts = "{r}"' for r in lieux) + ")") if lieux else ""
    for mot in mots_cles(dom)[:6]:
        mot_sur = re.sub(r'["\\]', " ", mot)
        params = {"where": f'search(titreoffre, "{mot_sur}") and datedebutdiffusion >= date\'{depuis}\'{filtre_lieu}',
                  "order_by": "datedebutdiffusion desc", "limit": 50}
        for r in _get(FOREM, params=params, timeout=40).json().get("results") or []:
            if r.get("numerooffreforem") in vus or not r.get("titreoffre"):
                continue
            vus.add(r.get("numerooffreforem"))
            morceaux = [f"Métier : {r['metier']}" if r.get("metier") else "",
                        f"Secteur : {', '.join(r.get('secteurs') or [])}" if r.get("secteurs") else "",
                        f"Études : {', '.join(r.get('niveauxetudes') or [])}" if r.get("niveauxetudes") else "",
                        f"Expérience : {r['experiencerequise']}" if r.get("experiencerequise") else "",
                        f"Permis : {', '.join(r.get('permisdeconduire') or [])}" if r.get("permisdeconduire") else "",
                        f"Publiée via : {r['source']}" if r.get("source") else ""]
            sortie.append({"url": r.get("url"), "titre": r["titreoffre"][:160], "entreprise": r.get("nomemployeur"),
                           "ville": r.get("lieuxtravaillocalite") or (r.get("lieuxtravailregion") or [None])[0],
                           "contrat": " · ".join(x for x in (r.get("typecontrat"), r.get("regimetravail")) if x) or None,
                           "langue": ", ".join(r.get("langues") or []) or None,
                           "description": ". ".join(m for m in morceaux if m), "publie_le": r.get("datedebutdiffusion"),
                           "date_limite": r.get("datefindiffusion"), "source": "forem"})
    return sortie


# ---------- Logiciels de recrutement des entreprises (ATS) ----------
_ATS = (   # (fournisseur, motif de l'adresse publique qui donne l'identifiant de l'entreprise)
    ("greenhouse", r"(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/(?:embed/job_board\?for=)?([a-z0-9_-]+)"),
    ("greenhouse", r"boards-api\.greenhouse\.io/v1/boards/([a-z0-9_-]+)"),
    ("lever", r"jobs\.lever\.co/([a-z0-9_.-]+)"),
    ("lever_eu", r"jobs\.eu\.lever\.co/([a-z0-9_.-]+)"),
    ("ashby", r"jobs\.ashbyhq\.com/([a-z0-9_.%-]+)"),
    ("workable", r"apply\.workable\.com/([a-z0-9_-]+)"),
    ("smartrecruiters", r"(?:jobs|careers)\.smartrecruiters\.com/([a-z0-9_-]+)"),
    ("recruitee", r"([a-z0-9-]+)\.recruitee\.com"),
    ("teamtailor", r"([a-z0-9-]+)\.teamtailor\.com"),
    ("personio", r"([a-z0-9-]+)\.jobs\.personio\.(?:de|com)"),
)
_PAS_UN_SLUG = {"api", "www", "embed", "jobs", "careers", "app", "v1", "j", "o", "en", "fr", "nl", "de"}


def detecter_ats(texte: str) -> set[tuple[str, str]]:
    """Toutes les pages carrières ATS citées dans un texte, une page HTML ou une liste d'adresses."""
    trouves = set()
    for fournisseur, motif in _ATS:
        for slug in re.findall(motif, (texte or "").lower()):
            slug = slug.strip(".-")
            if slug and slug not in _PAS_UN_SLUG and len(slug) <= 60:
                trouves.add((fournisseur, slug))
    return trouves


def noter_ats(trouves, origine: str = "") -> int:
    """Ajoute des entreprises à surveiller (gratuitement, à chaque veille)."""
    tableaux = db.kv_get("tableaux_ats") or {}
    neufs = 0
    for fournisseur, slug in trouves:
        cle = f"{fournisseur}:{slug}"
        if cle not in tableaux:
            tableaux[cle] = {"depuis": date.today().isoformat(), "origine": origine[:60], "echecs": 0}
            neufs += 1
    if neufs:
        db.kv_set("tableaux_ats", tableaux)
        db.journal("eclaireur", f"{neufs} nouvelle(s) page(s) carrières d'entreprise à surveiller ({origine})")
    return neufs


def _lire_ats(fournisseur: str, slug: str) -> list[dict]:
    """Offres d'UNE entreprise, au format commun (url, titre, entreprise, lieu, description, publie_le)."""
    nom = slug.replace("-", " ").replace("_", " ").title()
    if fournisseur == "greenhouse":
        d = _get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs", params={"content": "true"}).json()
        return [{"url": j.get("absolute_url"), "titre": j.get("title"), "entreprise": j.get("company_name") or nom,
                 "lieu": (j.get("location") or {}).get("name"), "publie_le": (j.get("first_published") or j.get("updated_at") or "")[:10],
                 "description": outils.texte_html(html.unescape(j.get("content") or ""), 4000)} for j in d.get("jobs") or []]
    if fournisseur in ("lever", "lever_eu"):
        hote = "api.eu.lever.co" if fournisseur == "lever_eu" else "api.lever.co"
        d = _get(f"https://{hote}/v0/postings/{slug}", params={"mode": "json"}).json()
        return [{"url": j.get("hostedUrl"), "titre": j.get("text"), "entreprise": nom,
                 "lieu": " ".join(filter(None, [(j.get("categories") or {}).get("location"), j.get("country")])),
                 "publie_le": datetime.fromtimestamp((j.get("createdAt") or 0) / 1000, timezone.utc).date().isoformat(),
                 "description": (j.get("descriptionPlain") or "")[:4000]} for j in d or []]
    if fournisseur == "ashby":
        d = _get(f"https://api.ashbyhq.com/posting-api/job-board/{slug}").json()
        return [{"url": j.get("jobUrl"), "titre": j.get("title"), "entreprise": nom,
                 "lieu": " ".join(filter(None, [j.get("location"), str(((j.get("address") or {}).get("postalAddress") or {}).get("addressCountry") or "")])),
                 "publie_le": (j.get("publishedAt") or "")[:10],
                 "description": (j.get("descriptionPlain") or outils.texte_html(j.get("descriptionHtml") or "", 4000))[:4000]}
                for j in d.get("jobs") or [] if j.get("isListed", True)]
    if fournisseur == "workable":
        d = _get(f"https://apply.workable.com/api/v1/widget/accounts/{slug}").json()
        return [{"url": j.get("url") or j.get("shortlink"), "titre": j.get("title"), "entreprise": d.get("name") or nom,
                 "lieu": " ".join(filter(None, [j.get("city"), j.get("country")])), "publie_le": (j.get("published_on") or "")[:10],
                 "description": outils.texte_html(j.get("description") or "", 4000)} for j in d.get("jobs") or []]
    if fournisseur == "smartrecruiters":
        d = _get(f"https://api.smartrecruiters.com/v1/companies/{slug}/postings", params={"limit": 100}).json()
        return [{"url": f"https://jobs.smartrecruiters.com/{slug}/{j.get('id')}", "titre": j.get("name"),
                 "entreprise": (j.get("company") or {}).get("name") or nom,
                 "lieu": " ".join(filter(None, [(j.get("location") or {}).get("city"), (j.get("location") or {}).get("country")])),
                 "publie_le": (j.get("releasedDate") or "")[:10], "description": ""} for j in d.get("content") or []]
    if fournisseur == "recruitee":
        d = _get(f"https://{slug}.recruitee.com/api/offers/").json()
        return [{"url": j.get("careers_url"), "titre": j.get("title"), "entreprise": j.get("company_name") or nom,
                 "lieu": " ".join(filter(None, [j.get("city"), j.get("country_code"), j.get("country")])),
                 "publie_le": (j.get("published_at") or "")[:10],
                 "description": outils.texte_html(j.get("description") or "", 4000)} for j in d.get("offers") or []]
    if fournisseur in ("teamtailor", "personio"):
        url = (f"https://{slug}.teamtailor.com/jobs.rss" if fournisseur == "teamtailor"
               else f"https://{slug}.jobs.personio.de/xml")
        racine = ET.fromstring(_get(url).content)
        sortie = []
        for it in racine.iter():
            balise = it.tag.split("}")[-1]
            if balise == "item":            # Teamtailor (RSS)
                lieu = " ".join(x.text or "" for x in it.iter() if x.tag.split("}")[-1] in ("city", "country", "name"))
                sortie.append({"url": it.findtext("link"), "titre": it.findtext("title"), "entreprise": nom, "lieu": lieu,
                               "publie_le": _date_rss(it.findtext("pubDate")),
                               "description": outils.texte_html(it.findtext("description") or "", 4000)})
            elif balise == "position":      # Personio (XML)
                desc = " ".join(x.findtext("value") or "" for x in it.iter() if x.tag.endswith("jobDescription"))
                sortie.append({"url": f"https://{slug}.jobs.personio.de/job/{it.findtext('id')}", "titre": it.findtext("name"),
                               "entreprise": it.findtext("subcompany") or nom, "lieu": it.findtext("office"),
                               "publie_le": (it.findtext("createdAt") or "")[:10], "description": outils.texte_html(desc, 4000)})
        return sortie
    return []


def _date_rss(v: str | None) -> str | None:
    from email.utils import parsedate_to_datetime
    try:
        return parsedate_to_datetime(v).date().isoformat() if v else None
    except (TypeError, ValueError):
        return None


def ats(dom: dict, max_par_passage: int = 60) -> list[dict]:
    """Surveille les pages carrières découvertes (en rotation : 60 entreprises par passage, en parallèle)."""
    noter_ats(detecter_ats(" ".join(dom.get("pages_a_surveiller") or [])), "pages à surveiller")
    tableaux = db.kv_get("tableaux_ats") or {}
    cles = sorted(k for k, v in tableaux.items() if v.get("echecs", 0) < 5)
    if not cles:
        return []
    debut = (db.kv_get("rotation_ats") or 0) % len(cles)
    lot = (cles[debut:] + cles[:debut])[:max_par_passage]
    db.kv_set("rotation_ats", debut + len(lot))

    def une(cle):
        fournisseur, slug = cle.split(":", 1)
        try:
            return cle, _lire_ats(fournisseur, slug)
        except Exception:
            return cle, None

    with ThreadPoolExecutor(8) as ex:
        resultats = list(ex.map(une, lot))
    sortie = []
    for cle, offres in resultats:
        tableaux[cle]["echecs"] = 0 if offres is not None else tableaux[cle].get("echecs", 0) + 1
        for j in offres or []:
            if (j.get("url") and j.get("titre") and _frais(j.get("publie_le")) and en_belgique(j.get("lieu"), dom)
                    and pertinente(j["titre"], dom, j.get("description") or "")):
                sortie.append({"url": j["url"], "titre": j["titre"][:160], "entreprise": j.get("entreprise"),
                               "ville": (j.get("lieu") or "")[:80] or None, "description": j.get("description") or None,
                               "publie_le": j.get("publie_le") or None, "source": "recruteur"})
    db.kv_set("tableaux_ats", tableaux)
    return sortie


# ---------- Alertes emploi reçues par email (LinkedIn, Jobat, StepStone, Indeed…) ----------
EXPEDITEURS_ALERTES = ("linkedin.com", "jobat.be", "stepstone.be", "stepstone.de", "indeed.com", "indeed.be",
                       "glassdoor", "monster.", "ictjob.be", "jobteaser", "welcometothejungle", "talent.com",
                       "jooble", "studentjob", "student.be", "actiris", "leforem", "vdab.be", "references.be",
                       "lexgo.be", "legalworld", "jobsinbrussels", "jobted", "careerjet", "google.com")
# Sites qu'on ne lit JAMAIS automatiquement (conditions d'utilisation) : l'offre vient de l'alerte, c'est tout
NE_PAS_LIRE = ("linkedin.com", "jobat.be", "stepstone.be", "stepstone.de")
_LIEN_OFFRE = (
    (r"linkedin\.com/(?:comm/)?jobs/view/(\d+)", lambda m: f"https://www.linkedin.com/jobs/view/{m.group(1)}"),
    (r"indeed\.[a-z.]+/(?:rc/clk|viewjob|pagead/clk)[^\s\"'<>]*?[?&]jk=([0-9a-f]+)", lambda m: f"https://be.indeed.com/viewjob?jk={m.group(1)}"),
    (r"(https?://(?:www\.)?jobat\.be/[a-z]{2}/(?:emplois|vacatures|jobs)/[^\s\"'<>?]+)", lambda m: m.group(1)),
    (r"(https?://(?:www\.)?stepstone\.be/[^\s\"'<>?]*--[^\s\"'<>?]+)", lambda m: m.group(1)),
    (r"(https?://(?:www\.)?glassdoor\.[a-z.]+/job-listing/[^\s\"'<>?]+)", lambda m: m.group(1)),
)


def est_alerte(expediteur: str) -> bool:
    e = (expediteur or "").lower()
    return any(x in e for x in EXPEDITEURS_ALERTES) and not any(x in e for x in ("noreply@google.com",))


def _texte_avec_liens(page_html: str) -> str:
    """HTML d'email → texte ligne par ligne où chaque lien est écrit juste après son texte."""
    t = re.sub(r"""<a\b[^>]*?href\s*=\s*["']([^"']+)["'][^>]*>(.*?)</a>""",
               lambda m: f"\n{re.sub(r'<[^>]+>', ' ', m.group(2))}\n{html.unescape(m.group(1))}\n", page_html, flags=re.S | re.I)
    t = re.sub(r"<(?:br|/p|/div|/tr|/td|/li|/h\d)[^>]*>", "\n", t, flags=re.I)
    return html.unescape(re.sub(r"<[^>]+>", " ", t))


def offres_d_une_alerte(expediteur: str, sujet: str, texte: str, page_html: str = "") -> list[dict]:
    """Chaque offre d'un email d'alerte : titre / employeur / lieu = les lignes juste avant le lien de l'offre."""
    brut = texte or ""
    if page_html:
        brut += "\n" + _texte_avec_liens(page_html)
    lignes = [re.sub(r"\s+", " ", l).strip() for l in brut.splitlines()]
    site = next((x for x in EXPEDITEURS_ALERTES if x in (expediteur or "").lower()), "alerte")
    sortie, vus, avant = [], set(), []
    for l in lignes:
        url = None
        for motif, canon in _LIEN_OFFRE:
            m = re.search(motif, l, re.I)
            if m:
                url = canon(m)
                break
        if not url:
            if l and not l.startswith("http") and len(l) < 140:
                avant.append(l)
            continue
        infos = [x for x in avant[-4:] if not re.search(r"^(voir|view|bekijk|postuler|apply|solliciteer)\b", x, re.I)][-3:]
        avant = []
        if url in vus or not infos:
            continue
        vus.add(url)
        titre, entreprise, lieu = (infos + [None, None, None])[:3]
        sortie.append({"url": url, "titre": titre[:160], "entreprise": entreprise, "ville": lieu, "source": "alerte",
                       "mode_candidature": "formulaire", "publie_le": date.today().isoformat(),
                       "description": f"Offre reçue dans une alerte {site} : {titre}"
                                      + (f" chez {entreprise}" if entreprise else "") + (f" ({lieu})" if lieu else "")
                                      + f". Sujet de l'alerte : {sujet[:120]}"})
    return sortie


def lecture_interdite(url: str) -> bool:
    return any(x in (url or "").lower() for x in NE_PAS_LIRE)
