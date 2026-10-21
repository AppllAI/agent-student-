"""Agent Prospecteur — candidatures spontanées.

Beaucoup de jobs étudiants ne sont jamais publiés (magasins, restos, événements). Chaque matin, il trouve N employeurs qui
collent à ton objectif, relève leur email de recrutement sur leur site officiel, et les fait entrer
dans le même circuit que les offres (Garde → Évaluateur → Rédacteur → Juge → ton ok)."""
from __future__ import annotations

import json
from datetime import date

from .. import config, db, llm, outils

SCHEMA = {
    "type": "object",
    "properties": {"employeurs": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "nom": {"type": "string"}, "site": {"type": ["string", "null"]}, "ville": {"type": ["string", "null"]},
            "secteurs": {"type": "array", "items": {"type": "string"}},
            "email_recrutement": {"type": ["string", "null"]}, "destinataire": {"type": ["string", "null"]},
            "faits": {"type": "array", "items": {"type": "string"}, "maxItems": 3},
        },
        "required": ["nom", "secteurs", "faits"],
    }}},
    "required": ["employeurs"],
}


def prospecter() -> int:
    regles, dom = config.regles_envoi(), config.domaine_actif()
    n = int(regles.get("candidatures_spontanees_par_jour") or 0)
    if n <= 0 or db.kv_get("prospection_du") == date.today().isoformat():
        return 0
    deja = sorted({r["entreprise"] for r in db.q("SELECT DISTINCT entreprise FROM offres WHERE entreprise IS NOT NULL")})
    contenu = json.dumps({"objectif": {k: dom.get(k) for k in ("description", "postes_vises", "secteurs_uniquement",
                                                              "secteurs_exclus", "villes", "langues")},
                          "deja_contactes": deja[-150:], "nombre": n}, ensure_ascii=False)
    r = llm.demander("prospecteur", "rapide", llm.prompt("prospecteur"), contenu, SCHEMA,
                     web=True, recherches_max=4, max_tokens=3500, priorite="fond")
    db.kv_set("prospection_du", date.today().isoformat())
    poste = (dom.get("postes_vises") or ["job étudiant"])[0]
    ajoutes = 0
    with db.tx() as c:
        for e in r.get("employeurs", [])[:n]:
            email = (outils.emails_dans(e.get("email_recrutement") or "") or [None])[0]
            if not email:            # sans email vérifié, pas de candidature spontanée
                continue
            secteur = ", ".join(e.get("secteurs") or []) or None
            cle = outils.cle_offre(e.get("site"), e["nom"], "spontanee")
            cur = c.execute(
                """INSERT OR IGNORE INTO offres (cle, url, source, titre, entreprise, ville, secteur, description,
                   email_contact, mode_candidature, infos_cabinet, statut, publie_le)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,'enrichie',?)""",
                (cle, e.get("site") or f"spontanee:{e['nom']}", "spontanee", f"Candidature spontanée – {poste}",
                 e["nom"], e.get("ville"), secteur,
                 f"Candidature spontanée (aucune offre publiée). Employeur actif en : {secteur}. " + " ".join(e.get("faits") or []),
                 email, "email", json.dumps({"faits": e.get("faits", []), "destinataire": e.get("destinataire")},
                                            ensure_ascii=False), date.today().isoformat()))
            ajoutes += cur.rowcount
    db.journal("prospecteur", f"{ajoutes} employeur(s) à contacter spontanément")
    return ajoutes
