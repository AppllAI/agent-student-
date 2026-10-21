"""Agent 5 — Garde : applique tes règles NON NÉGOCIABLES, sans IA (gratuit, instantané, infaillible).
Tout ce qu'il peut trancher seul n'arrive jamais jusqu'à l'IA → économie de tokens."""
from __future__ import annotations

import re
from datetime import datetime, timedelta

from .. import config, db
from ..outils import sans_accents

# Synonymes FR / NL / EN : « horeca » exclu doit aussi attraper « serveur », « plonge », « bediening »…
SYNONYMES = {
    "horeca": ["horeca", "restaurant", "resto", "bar", "cafe", "serveur", "serveuse", "barman", "plonge", "plongeur",
               "cuisine", "commis", "brasserie", "fast-food", "fast food", "snack", "hotel", "bediening", "keuken",
               "waiter", "kitchen"],
    "vente": ["vente", "vendeur", "vendeuse", "magasin", "boutique", "caisse", "caissier", "caissiere", "retail",
              "commerce", "rayon", "reassort", "winkel", "verkoop", "kassa", "sales assistant", "shop"],
    "logistique": ["logistique", "entrepot", "preparateur de commandes", "picking", "manutention", "magasinier",
                   "inventaire", "magazijn", "orderpicker", "warehouse"],
    "evenementiel": ["evenementiel", "evenement", "festival", "hotesse", "hote d'accueil", "steward", "accueil",
                     "salon", "concert", "evenementen", "onthaal", "event"],
    "animation": ["animation", "animateur", "animatrice", "plaine de jeux", "stage enfants", "moniteur", "monitrice",
                  "jeugdwerk", "monitor", "camp"],
    "garde d'enfants": ["babysitting", "baby-sitting", "babysitter", "garde d'enfants", "nounou", "kinderopvang",
                        "oppas", "childcare", "au pair"],
    "cours particuliers": ["cours particuliers", "soutien scolaire", "remediation", "tutorat", "professeur particulier",
                           "bijles", "tutor", "tutoring"],
    "call center": ["call center", "callcenter", "centre d'appels", "televente", "teleconseiller", "hotline",
                    "telemarketing", "service clientele par telephone"],
    "livraison": ["livraison", "livreur", "coursier", "delivery", "bezorger", "koerier", "deliveroo", "uber eats"],
    "nettoyage": ["nettoyage", "entretien des locaux", "menage", "schoonmaak", "cleaning", "technicien de surface"],
    "administratif": ["administratif", "encodage", "secretariat", "bureau", "reception", "data entry", "administratie",
                      "office"],
    "promotion": ["promotion", "street marketing", "flyers", "sampling", "degustation", "animateur commercial",
                  "promoteur", "brand ambassador"],
    "travail de nuit": ["nuit", "de nuit", "nachtwerk", "night shift"],
}


def _termes(secteur: str) -> list[str]:
    m = sans_accents(secteur).strip()
    for cle, syn in SYNONYMES.items():
        if m == cle or m in syn:
            return syn
    return [m]


def _contient(texte: str, terme: str) -> bool:
    """Mot entier (« bar » ne doit pas attraper « embarquement »)."""
    return re.search(rf"(?<![a-z]){re.escape(terme)}(?![a-z])", texte) is not None


def verdict(o: dict, dom: dict, regles: dict) -> str | None:
    """Renvoie la raison de rejet, ou None si l'offre passe."""
    secteur = sans_accents(o.get("secteur") or "")
    titre = sans_accents(o.get("titre") or "")
    tout = f"{titre} {secteur} {sans_accents((o.get('description') or '')[:600])}"

    for m in dom.get("secteurs_exclus") or []:
        # titre ou secteur = signal fort ; la description seulement pour son début (le cœur du poste)
        if any(_contient(f"{titre} {secteur} {sans_accents((o.get('description') or '')[:200])}", t) for t in _termes(m)):
            return f"secteur exclu : {m}"

    seules = dom.get("secteurs_uniquement") or []
    if seules and secteur and secteur not in ("generaliste", "divers"):
        if not any(_contient(tout, t) for m in seules for t in _termes(m)):
            return f"hors secteurs voulus ({', '.join(seules)})"

    for e in dom.get("exclure_entreprises") or []:
        if sans_accents(e) in sans_accents(o.get("entreprise") or ""):
            return f"employeur exclu : {e}"

    if o.get("entreprise"):
        limite = (datetime.now() - timedelta(days=regles["delai_meme_cabinet_jours"])).isoformat(" ")
        deja = db.q1("SELECT 1 FROM offres WHERE id<>? AND lower(entreprise)=lower(?) AND envoye_le>=?",
                     o["id"], o["entreprise"], limite)
        if deja:
            return "déjà postulé chez cet employeur récemment"

    # Salaire horaire minimum (quand l'offre l'indique)
    mini = float(dom.get("salaire_min_horaire") or 0)
    if mini and o.get("salaire_horaire") and float(o["salaire_horaire"]) < mini:
        return f"salaire trop bas ({o['salaire_horaire']} €/h < {mini} €/h)"

    langue = sans_accents(o.get("langue") or "")
    connues = [sans_accents(l) for l in dom.get("langues") or []]
    if langue and connues:
        exigees = [x for x in ("neerlandais", "anglais", "allemand", "francais") if x in langue]
        manquantes = [x for x in exigees if not any(x in c for c in connues)]
        if manquantes and ("bilingue" in langue or "+" in langue or " et " in langue or len(exigees) == 1):
            return f"langue exigée non maîtrisée : {', '.join(manquantes)}"
    return None


def filtrer():
    dom, regles = config.domaine_actif(), config.regles_envoi()
    n = 0
    for o in db.q("SELECT * FROM offres WHERE statut='enrichie'"):
        raison = verdict(dict(o), dom, regles)
        if raison:
            db.changer_statut(o["id"], "ecartee", "garde", raison, score=0)
            n += 1
    return n
