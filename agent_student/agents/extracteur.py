"""Agent 4 — Extracteur : complète chaque offre (description, email, matière…).

Cascade économe (idée reprise d'ApplyPilot) :
  1. données structurées JSON-LD JobPosting de la page  → gratuit
  2. adresses email trouvées dans le texte              → gratuit
  3. IA rapide sur le texte de la page, SEULEMENT si des champs clés manquent encore
"""
from __future__ import annotations

import json
from datetime import date

from .. import config, db, llm, outils

SCHEMA = {
    "type": "object",
    "properties": {k: {"type": ["string", "null"]} for k in
                   ("titre", "entreprise", "ville", "secteur", "contrat", "langue", "description", "email_contact",
                    "horaires", "publie_le", "date_limite")}
    | {"mode_candidature": {"type": "string", "enum": ["email", "formulaire", "inconnu"]},
       "salaire_horaire": {"type": ["number", "null"], "description": "€ brut par heure, seulement s'il est écrit"}},
    "required": ["secteur", "mode_candidature"],
}
CLES = ("titre", "entreprise", "ville", "secteur", "contrat", "langue", "description", "email_contact",
        "horaires", "salaire_horaire", "publie_le", "date_limite")


def enrichir(limite: int = 40):
    from concurrent.futures import ThreadPoolExecutor
    from . import garde
    dom, regles = config.domaine_actif(), config.regles_envoi()
    offres = []
    for o in db.q("SELECT * FROM offres WHERE statut='decouverte' ORDER BY id LIMIT ?", limite):
        raison = garde.verdict(dict(o), dom, regles)        # déjà exclue par le titre ? 0 € et 0 seconde
        if raison:
            db.changer_statut(o["id"], "ecartee", "garde", raison, score=0)
        else:
            offres.append(dict(o))
    with ThreadPoolExecutor(5) as ex:            # 5 pages lues en même temps
        list(ex.map(_enrichir_une, offres))


def _enrichir_une(o: dict):
    """Lit la page de l'offre et complète ce qui manque (JSON-LD gratuit d'abord, IA rapide ensuite)."""
    page = ""
    if o["url"] and o["url"].startswith("http"):
        try:
            page = outils.telecharger(o["url"])
        except Exception as e:
            db.journal("extracteur", f"page illisible ({type(e).__name__}), on garde le résumé", o["id"])
    jp = outils.jobposting_jsonld(page) if page else None
    if jp:
        for k in CLES:
            if not o.get(k) and jp.get(k):
                o[k] = jp[k]
    texte = outils.texte_html(page) if page else ""
    if not o.get("email_contact"):
        o["email_contact"] = (outils.emails_dans(texte) or [None])[0]

    manque = [k for k in ("entreprise", "secteur", "description", "horaires") if not o.get(k)]
    if manque and (texte or o.get("description")):
        try:
            r = llm.demander("extracteur", "rapide", llm.prompt("extracteur"),
                             json.dumps({"url": o["url"], "date_du_jour": date.today().isoformat(),
                                         "deja_connu": {k: o.get(k) for k in CLES},
                                         "page": texte or o.get("description")}, ensure_ascii=False),
                             SCHEMA, max_tokens=900)
            for k in (*CLES, "mode_candidature"):
                if not o.get(k) and r.get(k):
                    o[k] = r[k]
        except llm.BudgetDepasse:
            return
        except Exception as e:
            db.journal("extracteur", f"IA en échec : {e}", o["id"])

    if not o.get("mode_candidature") or o["mode_candidature"] == "inconnu":
        o["mode_candidature"] = "email" if o.get("email_contact") else "formulaire"
    if not o.get("description") and not o.get("titre"):
        db.changer_statut(o["id"], "ecartee", "extracteur", "offre illisible")
        return
    db.changer_statut(o["id"], "enrichie", "extracteur",
                      **{k: o.get(k) for k in (*CLES, "mode_candidature")})
