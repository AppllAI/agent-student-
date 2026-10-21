"""Agent Enquêteur : 2-3 faits vérifiés sur l'employeur + email de recrutement manquant.
Il travaille pour les meilleures offres (score ≥ 8, la personnalisation paie) et pour toute offre
retenue sans email : sur Student.be / StudentJob, trouver l'email de l'employeur = postuler directement par email
au lieu de te faire coller la lettre à la main."""
from __future__ import annotations

import json

from .. import db, llm, outils

SCHEMA = {
    "type": "object",
    "properties": {
        "faits": {"type": "array", "items": {"type": "string"}, "maxItems": 3},
        "email_recrutement": {"type": ["string", "null"]},
        "destinataire": {"type": ["string", "null"], "description": "Nom de la personne à qui adresser la lettre"},
    },
    "required": ["faits"],
}


def _plateforme(o) -> bool:
    """Offre publiée sur Student.be / StudentJob : on postule par leur bouton, l'employeur la reçoit à coup sûr."""
    return any(p in (o["url"] or "") for p in ("student.be/", "studentjob.be", "stujobs."))


def enqueter(score_min: int = 8, max_par_jour: int = 25):
    """Faits sur l'employeur pour les meilleures offres ; email de recrutement pour les offres sans email.
    Une seule enquête par employeur (EXKI a 10 restaurants : une fois suffit), plafond par jour."""
    from concurrent.futures import ThreadPoolExecutor
    from datetime import date

    cle_jour = f"enquetes_{date.today().isoformat()}"
    faites = db.kv_get(cle_jour, 0) or 0

    def une(o):
        nonlocal faites
        infos = {"faits": [], "destinataire": None}
        nom = (o["entreprise"] or "").strip().lower()
        cache = db.kv_get(f"enquete:{nom}") if nom else None
        # Student.be / StudentJob : on postule par leur bouton → l'enquête ne sert qu'aux meilleures (faits)
        besoin = o["entreprise"] and ((o["score"] or 0) >= score_min
                                      or (not o["email_contact"] and not _plateforme(o)))
        if cache:
            infos = {"faits": cache.get("faits", []), "destinataire": cache.get("destinataire")}
            _email(o, cache.get("email"))
        elif besoin and faites < max_par_jour:
            faites += 1
            db.kv_set(cle_jour, faites)
            try:
                r = llm.demander("enqueteur", "rapide", llm.prompt("enqueteur"),
                                 json.dumps({"employeur": o["entreprise"], "ville": o["ville"],
                                             "poste": o["titre"], "url_offre": o["url"],
                                             "email_connu": o["email_contact"]}, ensure_ascii=False),
                                 SCHEMA, web=True, recherches_max=1, max_tokens=1000, priorite="fond")
                infos = {"faits": r.get("faits", [])[:2], "destinataire": r.get("destinataire")}
                email = (outils.emails_dans(r.get("email_recrutement") or "") or [None])[0]
                db.kv_set(f"enquete:{nom}", {**infos, "email": email})
                _email(o, email)
            except llm.BudgetDepasse:
                pass          # budget « fond » épuisé : on écrit quand même la lettre, sans enquête
            except Exception as e:
                db.journal("enqueteur", f"échec : {e}", o["id"])
        db.maj(o["id"], infos_cabinet=json.dumps(infos, ensure_ascii=False))

    offres = db.q("SELECT * FROM offres WHERE statut='qualifiee' AND infos_cabinet IS NULL ORDER BY score DESC")
    # 1er passage : un seul représentant par employeur (en parallèle) ; 2e : les autres profitent du cache
    vus, premiers, suite = set(), [], []
    for o in offres:
        nom = (o["entreprise"] or "").strip().lower()
        (suite if nom and nom in vus else premiers).append(o)
        vus.add(nom)
    with ThreadPoolExecutor(3) as ex:      # 3 enquêtes en parallèle
        list(ex.map(une, premiers))
    for o in suite:
        une(o)


def _email(o, email):
    """Sur Student.be / StudentJob, on ne quitte le bouton « Postuler » que pour une VRAIE adresse de recrutement
    (jobs@, rh@, recrutement@…) : un helpdesk@ ou info@ d'une chaîne ne lit pas les candidatures."""
    if not email or o["email_contact"]:
        return
    if _plateforme(o) and not outils.RE_EMAIL_RECRUTEMENT.match(email):
        return
    db.maj(o["id"], email_contact=email, mode_candidature="email")
