"""Agent 6 — Évaluateur : note les offres /10 face à ton objectif.
Économie : 8 offres par appel (le profil et l'objectif ne sont envoyés qu'une fois) + modèle rapide."""
from __future__ import annotations

import json

from .. import config, db, llm

SCHEMA = {
    "type": "object",
    "properties": {"notes": {"type": "array", "items": {
        "type": "object",
        "properties": {"id": {"type": "integer"},
                       "regle": {"type": ["string", "null"], "enum": [
                           "secteur_exclu", "hors_secteurs_voulus", "ville", "langue_exigee", "contrat_non_voulu",
                           "niveau_experience", "horaires_impossibles", "trop_d_heures", "permis_vehicule",
                           "pas_un_job_etudiant", "employeur_exclu", None],
                           "description": "la règle STRICTE clairement violée, sinon null"},
                       "regle_violee": {"type": "boolean"},
                       "score": {"type": "integer", "minimum": 1, "maximum": 10}, "raison": {"type": "string"},
                       "pourquoi": {"type": "array", "items": {"type": "string"}, "maxItems": 3,
                                    "description": "2-3 liens concrets entre CE profil et CETTE offre"}},
        "required": ["id", "regle_violee", "score", "raison", "pourquoi"],
    }}},
    "required": ["notes"],
}
CHAMPS = ("id", "source", "titre", "entreprise", "ville", "secteur", "contrat", "langue", "publie_le", "horaires",
          "salaire_horaire", "description")


def evaluer(taille_lot: int = 8):
    if not config.profil_texte().strip():
        return 0          # sans ton profil, toute note serait fausse : on attend la lecture du CV
    dom, regles = config.domaine_actif(), config.regles_envoi()
    offres = [dict(o) for o in db.q("SELECT * FROM offres WHERE statut='enrichie' ORDER BY id")]
    utiles = ("description", "postes_vises", "secteurs_uniquement", "secteurs_exclus", "villes", "teletravail_ok",
              "langues", "contrats", "exclure_entreprises", "disponibilites", "periode",
              "heures_max_par_semaine", "salaire_min_horaire", "permis_ou_vehicule")
    systeme = llm.prompt("evaluateur") + "\n\n## Objectif\n" + \
        json.dumps({k: dom.get(k) for k in utiles if dom.get(k) not in (None, "", [], 0)}, ensure_ascii=False) + \
        "\n\n## Profil\n" + config.profil_texte()
    lots = [offres[i:i + taille_lot] for i in range(0, len(offres), taille_lot)]

    def noter(lot):
        contenu = json.dumps([{k: (o[k][:1500] if k == "description" and o[k] else o[k]) for k in CHAMPS}
                              for o in lot], ensure_ascii=False)
        try:
            return llm.demander("evaluateur", "rapide", systeme, contenu, SCHEMA, max_tokens=2500)["notes"]
        except llm.BudgetDepasse:
            return None
        except Exception as e:
            db.journal("evaluateur", f"lot en échec, réessai au prochain cycle : {type(e).__name__}: {e}")
            return []

    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(3) as ex:            # 3 lots à la fois : 30 offres notées en ~10 s au lieu de 30
        resultats = list(ex.map(noter, lots))
    retenues = 0
    for lot, notes in zip(lots, resultats):
        if notes is None:                        # budget atteint : ce lot attendra demain
            continue
        notees = set()
        for n in notes:
            try:
                oid, score = int(n.get("id")), int(n.get("score") or 1)
            except (TypeError, ValueError):
                continue
            if oid not in {o["id"] for o in lot} or oid in notees:
                continue
            notees.add(oid)
            if n.get("regle_violee") and not n.get("regle"):
                # « violation » sans règle précise = hésitation de l'IA (vu en vrai : « le motif ne tient pas »)
                n["regle_violee"] = False
                score = max(score, regles["score_minimum"])
            ok = not n.get("regle_violee") and score >= regles["score_minimum"]
            db.changer_statut(oid, "qualifiee" if ok else "ecartee", "evaluateur",
                              f"{score}/10 · {n.get('raison') or ''}", score=score,
                              pourquoi=json.dumps(n.get("pourquoi") or [], ensure_ascii=False))
            retenues += ok
        # Offres oubliées par l'IA : 3 essais max, puis on les met de côté (sinon on paie en boucle)
        for o in lot:
            if o["id"] not in notees:
                essais = (o["tentatives"] or 0) + 1
                db.maj(o["id"], tentatives=essais)
                if essais >= 3:
                    db.changer_statut(o["id"], "erreur", "evaluateur", "impossible à noter")
    return retenues


def reevaluer_recentes(jours: int = 21, regles_aussi: bool = False) -> int:
    """Quand ton profil ou ton objectif change : les offres récentes écartées par la NOTE (pas par tes règles)
    sont notées à nouveau."""
    with db.tx() as c:
        cur = c.execute(
            "UPDATE offres SET statut='enrichie', tentatives=0, maj_le=CURRENT_TIMESTAMP "
            "WHERE statut='ecartee' AND date(cree_le) >= date('now', ?) AND id IN "
            "(SELECT offre_id FROM evenements WHERE agent IN (?, ?) AND quoi LIKE '→ ecartee%')",
            (f"-{jours} days", "evaluateur", "garde" if regles_aussi else "evaluateur"))
        return cur.rowcount
