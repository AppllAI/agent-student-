"""Agent 13 — Relanceur (sans IA) : une relance polie, une seule fois, après N jours sans réponse.
Modèle fixe = 0 token. Après 3×N jours sans rien, l'offre est classée « sans réponse »."""
from __future__ import annotations

from datetime import datetime, timedelta
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

from .. import config, db
from .envoyeur import _joindre_pdf, _nom, expedier, heure_ouvrable
from .redacteur import chemin_lettre

MODELE = """Madame, Monsieur,

Je me permets de revenir vers vous concernant ma candidature au poste de {poste}, envoyée le {date}.
Ce poste m'intéresse toujours vivement et je reste à votre disposition pour un entretien à votre convenance.
Pour votre facilité, je joins à nouveau mon CV et ma lettre de motivation.

Je vous prie d'agréer, Madame, Monsieur, l'expression de mes salutations distinguées.

{signature}"""


def relancer():
    regles = config.regles_envoi()
    if not config.simulation() and not heure_ouvrable(regles):
        return 0
    n = regles["relance_apres_jours"]
    maintenant = datetime.now()
    faites = 0
    for o in db.q("SELECT * FROM offres WHERE statut IN ('envoyee','relancee') AND envoye_le IS NOT NULL"):
        age = maintenant - datetime.fromisoformat(o["envoye_le"])
        if o["statut"] == "relancee":
            if age > timedelta(days=3 * n):
                db.changer_statut(o["id"], "sans_reponse", "relance")
            continue
        if age < timedelta(days=n) or not o["email_contact"]:
            continue
        m = EmailMessage()
        moi = config.env("EMAIL_ADRESSE", "moi@exemple.be")
        m["From"] = f"{config.env('NOM_CANDIDAT')} <{moi}>" if config.env("NOM_CANDIDAT") else moi
        m["To"] = o["email_contact"]
        m["Subject"] = f"Re: {o['objet_mail']}"
        m["Date"] = formatdate(localtime=True)
        m["Message-ID"] = make_msgid()
        if o["message_id"]:
            m["In-Reply-To"] = o["message_id"]
            m["References"] = o["message_id"]
        signature = config.env("NOM_CANDIDAT", "") + (f"\n{config.env('TELEPHONE')}" if config.env("TELEPHONE") else "")
        m.set_content(MODELE.format(poste=o["titre"], date=f"{datetime.fromisoformat(o['envoye_le']):%d/%m/%Y}", signature=signature.strip()))
        _joindre_pdf(m, config.chemin_cv(), _nom("CV"))                 # le recruteur a tout sous la main
        _joindre_pdf(m, chemin_lettre(o["id"]), _nom("Lettre_de_motivation"))
        try:
            expedier(m)
            db.changer_statut(o["id"], "relancee", "relance")
            faites += 1
        except Exception as e:
            db.journal("relance", f"échec : {e}", o["id"])
    return faites
