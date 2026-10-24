"""Agent Préparateur d'entretien : la veille de chaque rendez-vous confirmé, il t'envoie une fiche en PDF
(l'employeur, pourquoi toi, questions probables et réponses, questions à poser, infos pratiques)."""
from __future__ import annotations

import json
from datetime import datetime, timedelta

from .. import config, db, llm, messagerie, pdf
from .secretaire import joli

SCHEMA = {"type": "object", "properties": {"fiche": {"type": "string"}}, "required": ["fiche"]}


def preparer():
    maintenant = datetime.now()
    faits = set(db.kv_get("fiches_envoyees", []))
    for r in db.q("SELECT * FROM rdv WHERE statut='confirme' AND debut IS NOT NULL"):
        try:
            debut = datetime.fromisoformat(r["debut"])
        except ValueError:
            continue
        if r["id"] in faits or not (maintenant < debut <= maintenant + timedelta(hours=30)):
            continue
        o = db.q1("SELECT titre, entreprise, ville, secteur, description, lettre FROM offres WHERE id=?", r["offre_id"])
        contenu = json.dumps({"rdv": {"employeur": r["entreprise"], "quand": joli(r["debut"]), "lieu": r["lieu"]},
                              "offre": dict(o) if o else None}, ensure_ascii=False)
        try:
            fiche = llm.demander("preparateur", "qualite",
                                 llm.prompt("preparateur") + "\n\n## Profil\n" + config.profil_texte(),
                                 contenu, SCHEMA, web=True, recherches_max=2, max_tokens=2500)["fiche"]
        except llm.BudgetDepasse:
            return
        except Exception as e:
            db.journal("preparateur", f"échec : {e}")
            continue
        chemin = pdf.document(f"Entretien – {r['entreprise']}", fiche,
                              config.DONNEES / "fiches" / f"fiche_entretien_{r['id']}.pdf",
                              f"{joli(r['debut']).capitalize()} · {r['lieu'] or 'lieu à préciser'}")
        messagerie.dire(f"🎯 Ton entretien chez *{r['entreprise']}* c'est {joli(r['debut'])}. "
                        "Ta fiche de préparation est dans le PDF (3 minutes de lecture).")
        messagerie.fichier(chemin, f"Fiche d'entretien – {r['entreprise']}")
        faits.add(r["id"])
        db.kv_set("fiches_envoyees", sorted(faits))
