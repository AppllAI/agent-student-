"""Réponses factices pour faire tourner TOUT le circuit sans clé API (SIMULATION=1).
Elles respectent les mêmes schémas que les vraies : si ça marche ici, la plomberie est bonne."""
from __future__ import annotations

import json
import re

OFFRES = [
    {"url": "https://www.student.be/fr/bruxelles/jobs-etudiants/vendeur-week-end-123", "titre": "Vendeur·se le week-end",
     "entreprise": "Boutique Lumen", "ville": "Bruxelles", "secteur": "vente", "contrat": "contrat étudiant",
     "langue": "français", "horaires": "samedi 10h-18h", "salaire_horaire": 13.5,
     "description": "Boutique de déco à Ixelles cherche un·e étudiant·e pour la caisse, le conseil client et le "
     "réassort, le samedi.", "email_contact": "jobs@lumen-shop.be", "mode_candidature": "email"},
    {"url": "https://fr.studentjob.be/offre/77-serveur-brasserie", "titre": "Serveur·se en brasserie",
     "entreprise": "Brasserie Themis", "ville": "Bruxelles", "secteur": "horeca", "contrat": "contrat étudiant",
     "langue": "français", "horaires": "soirs et week-ends", "salaire_horaire": 12.5,
     "description": "Service en salle, prise de commandes, débarrassage.",
     "email_contact": "jobs@themis-brasserie.be", "mode_candidature": "email"},
    {"url": "https://fr.studentjob.be/offre/88-orderpicker", "titre": "Préparateur de commandes",
     "entreprise": "Nordlog", "ville": "Bruxelles", "secteur": "logistique", "contrat": "contrat étudiant",
     "langue": "néerlandais", "horaires": "en semaine 6h-14h", "salaire_horaire": 14.0,
     "description": "Picking en entrepôt, néerlandais exigé.", "email_contact": None, "mode_candidature": "formulaire"},
    {"url": "https://www.delta-store.be/jobs/vendeur-etudiant", "titre": "Vendeur·se étudiant·e",
     "entreprise": "Delta Store", "ville": "Bruxelles", "secteur": "vente", "contrat": "contrat étudiant",
     "langue": "français", "horaires": "samedis", "salaire_horaire": None,
     "description": "Accueil des clients, mise en rayon, caisse.", "email_contact": None,
     "mode_candidature": "formulaire"},
]


def repondre(agent: str, contenu: str, schema: dict) -> dict:
    try:
        d = json.loads(contenu)
    except Exception:
        d = {"texte": contenu}

    if agent == "eclaireur":
        return {"offres": [o for o in OFFRES if o["url"] not in (d.get("deja_connues") or [])]}
    if agent == "extracteur":
        return {"secteur": "généraliste", "mode_candidature": "inconnu"}
    if agent == "evaluateur":
        notes = []
        for o in d:
            ok = "vente" in (o.get("secteur") or "")
            notes.append({"id": o["id"], "regle_violee": False, "score": 9 if ok else 5,
                          "raison": "Secteur, ville et horaires alignés" if ok else "Secteur secondaire",
                          "pourquoi": ["Job de caissier chez Delhaize ↔ tenue de caisse"] if ok else []})
        return {"notes": notes}
    if agent == "enqueteur":
        return {"faits": [f"{d.get('employeur')} est une boutique de déco à Ixelles."],
                "email_recrutement": None, "destinataire": None}
    if agent == "redacteur":
        o = d["offre"]
        corps = ("Mon job de caissier m'a appris à rester souriant pendant les rushs, à tenir une caisse avec "
                 "rigueur et à conseiller les clients, ce qui correspond à ce que vous décrivez. ") * 5
        return {"lettre": f"Bruxelles, le {d['date_du_jour']}\n\nMadame, Monsieur,\n\nVotre offre de {o['poste']} "
                          f"chez {o['employeur']} m'intéresse beaucoup.\n\n{corps}\n\nJe vous prie d'agréer, "
                          "Madame, Monsieur, l'expression de mes salutations distinguées.\n\nAlex Test",
                "objet": f"Candidature – {o['poste']}",
                "email": "Madame, Monsieur,\n\nJe suis disponible le samedi. Mon CV et ma lettre sont en pièces jointes."
                         "\n\nBien à vous"}
    if agent == "juge":
        return {"note": 9, "invention": False, "corrections": []}
    if agent == "prospecteur":
        return {"employeurs": [
            {"nom": "Librairie Horizon", "site": "https://librairie-horizon.example", "ville": "Bruxelles",
             "secteurs": ["vente"], "email_recrutement": "jobs@librairie-horizon.example",
             "destinataire": None, "faits": ["Librairie indépendante à Saint-Gilles."]},
            {"nom": "Sans Email & Co", "site": "https://sansemail.example", "ville": "Bruxelles",
             "secteurs": ["vente"], "email_recrutement": None, "faits": []}]}
    if agent == "preparateur":
        return {"fiche": "1. L'employeur…\n2. Pourquoi toi…"}
    if agent == "trieur":
        t = f"{d.get('sujet', '')} {d.get('corps', '')}".lower()
        if "entretien" in t or "rencontrer" in t:
            return {"categorie": "entretien", "resume": "Invitation à un entretien.",
                    "employeur": (d.get("offre_liee") or {}).get("entreprise"),
                    "creneaux": ["2026-12-24T16:30"], "lieu": "Avenue Louise 54, Bruxelles"}
        if "pas retenu" in t or "regret" in t:
            return {"categorie": "refus", "resume": "Candidature non retenue.", "creneaux": []}
        return {"categorie": "autre", "resume": "Sans lien.", "creneaux": []}
    if agent == "secretaire":
        return {"email": "Madame, Monsieur,\n\nJe vous remercie et vous confirme ma présence.\n\nBien à vous"}
    if agent == "concierge":
        m = d.get("message", "").lower()
        rdv = (d.get("contexte") or {}).get("rdv_proposes") or []
        if rdv and re.search(r"\b(oui|ok|parfait|confirme)", m):
            return {"action": "confirmer_rdv", "rdv_id": rdv[0]["id"], "reponse": "Je confirme !"}
        if "blocus" in m:
            return {"action": "pause", "jusqu_au": "2099-06-20", "reponse": "Bon courage pour le blocus 💪"}
        if "essayer" in m or "voir si ça marche" in m:
            return {"action": "banc", "reponse": "Je me teste"}
        if "longue" in m or "formel" in m:
            return {"action": "style", "texte": "Lettres plus courtes (180 mots max).", "reponse": "Noté"}
        return {"action": "discuter", "reponse": "Bien noté 👍"}
    if agent == "coach":
        m = d.get("message", "").lower()
        maj = {}
        if "vente" in m:
            maj["secteurs_uniquement"] = ["vente"]
        if "horeca" in m:
            maj["secteurs_exclus"] = ["horeca"]
        if "samedi" in m:
            maj["disponibilites"] = "samedis toute la journée"
        if "lance" in m or "go" in m:
            return {"reponse": "C'est parti 🚀", "objectif_maj": maj, "termine": True}
        return {"reponse": "Super. Tu es dispo quels jours, et à quelles heures ?", "objectif_maj": maj,
                "termine": False}
    if agent == "profileur":
        if "lignes" in schema.get("properties", {}):
            return {"lignes": [f"- {d.get('texte', contenu)}"]}
        return {"fiche_markdown": "# Profil\n- Nom : Alex Test\n- Bachelier en sciences économiques (simulation)\n",
                "nom": "Alex Test", "telephone": "0470 00 00 00", "manquants": ["permis de conduire"]}
    raise KeyError(f"Pas de réponse simulée pour {agent}")
