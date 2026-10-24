"""Agents 15 + 16 — Coach & Profileur : la mise en route, 100 % sur ta messagerie.

  • Profileur : tu envoies ton CV en PDF → il en tire une fiche profil factuelle (la seule source
    de vérité des lettres) et te dit ce qui manque.
  • Coach : il te pose les bonnes questions (à l'écrit ou en vocal) et transforme tes réponses
    en règles strictes dans config/objectif.yaml."""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from .. import config, db, llm, messagerie

LISTE = {"type": "array", "items": {"type": "string"}}
SCHEMA_COACH = {
    "type": "object",
    "properties": {
        "reponse": {"type": "string"},
        "objectif_maj": {"type": "object", "properties": {
            "description": {"type": "string"}, "postes_vises": LISTE, "secteurs_uniquement": LISTE,
            "secteurs_exclus": LISTE, "villes": LISTE, "teletravail_ok": {"type": "boolean"}, "langues": LISTE,
            "contrats": LISTE, "exclure_entreprises": LISTE, "mots_cles_recherche": LISTE,
            "disponibilites": {"type": "string"}, "periode": {"type": "string"},
            "heures_max_par_semaine": {"type": "integer"}, "salaire_min_horaire": {"type": "number"},
            "permis_ou_vehicule": {"type": "string"}}},
        "termine": {"type": "boolean"},
        "accords": {"type": ["string", "null"], "enum": ["masculin", "féminin", None],
                    "description": "Si la personne vient de dire si ses lettres sont au masculin ou au féminin"},
    },
    "required": ["reponse", "objectif_maj", "termine"],
}
SCHEMA_PROFIL = {
    "type": "object",
    "properties": {"fiche_markdown": {"type": "string"}, "nom": {"type": ["string", "null"]},
                   "telephone": {"type": ["string", "null"]}, "manquants": LISTE},
    "required": ["fiche_markdown", "manquants"],
}
SCHEMA_AJOUT = {"type": "object", "properties": {"lignes": LISTE}, "required": ["lignes"]}


def onboarding_fini() -> bool:
    return bool(db.kv_get("onboarding_fini", False))


MENU = ("📋 *Ce que tu peux me dire* (ou en vocal 🎙️, avec tes mots) :\n"
        "• « ok » / « ok sauf 3 » / « montre 3 » → valider, retirer, voir une candidature\n"
        "• « oui » / « plutôt jeudi » / « non » → répondre à un entretien\n"
        "• « essai » + un lien d'annonce → je te montre ce que je ferais, sans envoyer\n"
        "• « teste-toi » → 5 candidatures d'exemple notées, pour juger ma qualité\n"
        "• « pas d'horeca », « ajoute Louvain-la-Neuve », « libre aussi le dimanche » → changer ta recherche\n"
        "• « pause jusqu'au 20 juin » (blocus, vacances) → je reprends tout seul ce jour-là\n"
        "• « j'ai trouvé un job » · « cherche » · « stats » · « reprendre » · « mode »")


def bienvenue():
    messagerie.dire("*Agent Student*\nJe cherche des jobs étudiants en continu, j'écris un message sur mesure pour "
                    "chacun et je postule. Je ne te dérange que pour décider.\n\n"
                    "*Pour démarrer :*\n1️⃣ Envoie ton CV en PDF ici (même court, c'est normal)\n"
                    "2️⃣ Dis-moi ce que tu cherches : type de job, ville, et surtout *quand tu es dispo* "
                    "(jours, heures, période)\n\n"
                    "🧪 Je démarre en *mode test* : je fais tout seul et j'envoie chaque candidature (avec CV et "
                    "lettre) à l'adresse email de ton CV, jamais aux employeurs. Ici, je reste silencieux. "
                    "Quand tout te plaît : « mode validation ».\n\n" + MENU)


def parler(texte: str):
    o = config.objectif()
    actif = o.get("domaine_actif", "job_etudiant")
    dom = o.setdefault("domaines", {}).setdefault(actif, {})
    contexte = {"objectif_actuel": dom, "cv_recu": config.chemin_cv().exists() and bool(config.profil_texte().strip()),
                "profil": config.profil_texte()[:1500], "historique": db.historique(6), "message": texte}
    try:
        r = llm.demander("coach", "rapide", llm.prompt("coach"), json.dumps(contexte, ensure_ascii=False),
                         SCHEMA_COACH, max_tokens=900)
    except llm.BudgetDepasse as e:
        return messagerie.dire(f"{e}. On reprend demain !")
    accords = r.get("accords")
    if accords in ("masculin", "féminin") and "Accords" not in config.profil_texte():
        f = config.PROFIL_DIR / "profil.md"
        if f.exists():
            with open(f, "a", encoding="utf-8") as fh:
                fh.write(f"\n- Accords : {accords}\n")
    permis = SCHEMA_COACH["properties"]["objectif_maj"]["properties"]
    maj = {k: v for k, v in (r.get("objectif_maj") or {}).items() if k in permis and v not in (None, "", [])}
    if maj:
        dom.update(maj)
        config.sauver_objectif(o)
        db.journal("coach", f"objectif mis à jour : {', '.join(maj)}")
        if onboarding_fini():   # tes critères ont changé : les offres récentes écartées sont revues
            from .evaluateur import reevaluer_recentes
            reevaluer_recentes(regles_aussi=True)
    messagerie.dire(r["reponse"])
    if r.get("termine") and config.chemin_cv().exists():
        db.kv_set("onboarding_fini", True)
        from .. import orchestrateur
        orchestrateur.declencher()


def recevoir_cv(chemin: Path):
    config.PROFIL_DIR.mkdir(parents=True, exist_ok=True)
    if Path(chemin).resolve() != config.chemin_cv().resolve():
        shutil.copy(chemin, config.chemin_cv())
    texte = _texte_pdf(config.chemin_cv())
    pdf = None
    if len(texte.strip()) < 300:            # PDF scanné / image : Claude lit le PDF lui-même
        pdf = config.chemin_cv().read_bytes()
        if len(pdf) > 20_000_000:
            return messagerie.dire("Ton CV fait plus de 20 Mo, c'est trop lourd. Exporte-le à nouveau en PDF "
                                   "depuis Word ou Google Docs et renvoie-le.")
        texte = "Le CV est le document PDF joint."
    try:
        r = llm.demander("profileur", "qualite", llm.prompt("profileur"), texte[:12000], SCHEMA_PROFIL,
                         max_tokens=3000, pdf=pdf)
    except llm.BudgetDepasse as e:
        return messagerie.dire(f"{e}.")
    except Exception as e:
        db.journal("profileur", f"lecture du CV en échec : {e}")
        return messagerie.dire("⚠️ Je n'ai pas réussi à analyser ton CV (erreur technique). Je réessaie tout seul "
                               "dans quelques minutes, pas besoin de le renvoyer.")
    if len((r.get("fiche_markdown") or "").strip()) < 50:
        db.journal("profileur", "fiche profil vide renvoyée par l'IA")
        return messagerie.dire("⚠️ Je n'ai rien pu tirer de ce CV. Renvoie-le exporté en PDF depuis Word ou "
                               "Google Docs (pas une photo).")
    (config.PROFIL_DIR / "profil.md").write_text(r["fiche_markdown"], encoding="utf-8")
    identite = {"NOM_CANDIDAT": r.get("nom"), "TELEPHONE": r.get("telephone")}
    db.kv_set("identite", identite)
    appliquer_identite()
    from .evaluateur import reevaluer_recentes
    n = reevaluer_recentes()        # offres notées avant la lecture du CV : on les renote avec ton vrai profil
    if n:
        db.journal("profileur", f"{n} offre(s) remise(s) en évaluation avec ton profil")
    apercu = "\n".join(l for l in r["fiche_markdown"].splitlines() if l.strip())[:700]
    msg = f"📄 CV lu ✅ Voici ce que j'ai retenu (c'est la seule base de tes lettres) :\n\n{apercu}\n\n" \
          "Si quelque chose est faux, dis-le-moi (« je parle aussi néerlandais B2 »…)."
    if r["manquants"]:
        msg += "\nIl me manque : " + ", ".join(r["manquants"]) + ". Tu peux me le dire quand tu veux."
    messagerie.dire(msg)


def completer_profil(texte: str):
    """« J'ai un niveau B2 en néerlandais » → ajouté à la fiche, mot pour mot ou presque."""
    r = llm.demander("profileur", "rapide",
                     "Transforme ce que la personne dit sur elle en lignes factuelles de fiche profil "
                     "(« - Langues : néerlandais B2 »). Uniquement ce qu'elle affirme.",
                     texte, SCHEMA_AJOUT, max_tokens=300, reflexion=False)
    f = config.PROFIL_DIR / "profil.md"
    f.parent.mkdir(parents=True, exist_ok=True)
    with open(f, "a", encoding="utf-8") as fh:
        fh.write("\n" + "\n".join(r["lignes"]) + "\n")
    messagerie.dire("Ajouté à ton profil.")


def appliquer_identite():
    for k, v in (db.kv_get("identite") or {}).items():
        if v:
            os.environ[k] = v


def _texte_pdf(chemin: Path) -> str:
    try:
        from pypdf import PdfReader
    except ImportError:
        return ""
    try:
        return "\n".join(p.extract_text() or "" for p in PdfReader(str(chemin)).pages)
    except Exception:
        return ""
