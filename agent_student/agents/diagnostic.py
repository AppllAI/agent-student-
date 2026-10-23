"""Agent Diagnostic (sans IA, gratuit) : « est-ce que tout marche ? » en 10 secondes.

Vérifie ce qui fait qu'un agent « ne fait rien » : clé IA refusée ou crédit épuisé, boîte mail qui refuse le mot
de passe (envoi ET lecture), CV absent, adresse de test introuvable, pause oubliée, budget du jour atteint.
Au démarrage, il ne parle que s'il y a un problème ; sur demande (« diagnostic »), il donne le bilan complet."""
from __future__ import annotations

import time

from .. import config, db, llm, messagerie


def _ia() -> tuple[bool, str]:
    raison = db.kv_get("ia_bloquee_raison") if time.time() < float(db.kv_get("ia_bloquee_jusqu", 0) or 0) else None
    if raison:
        return False, f"IA en pause : {raison} (nouvel essai automatique toutes les 30 min)"
    try:
        import requests
        r = requests.get("https://api.anthropic.com/v1/models", timeout=15,
                         headers={"x-api-key": config.env("ANTHROPIC_API_KEY") or "", "anthropic-version": "2023-06-01"})
    except Exception as e:
        return False, f"IA injoignable (internet ?) : {type(e).__name__}"
    if r.status_code in (401, 403):
        return False, "clé API Claude refusée : recrée-la sur console.anthropic.com et colle-la dans ⚙ Réglages"
    return (True, "clé IA valide") if r.ok else (False, f"IA : réponse {r.status_code}")


def _envoi() -> tuple[bool, str]:
    from .envoyeur import connexion_smtp
    try:
        s = connexion_smtp()
        s.quit()
        return True, "envoi d'emails OK"
    except Exception as e:
        auth = "auth" in type(e).__name__.lower() or "535" in str(e)
        return False, ("mot de passe d'application refusé par la boîte mail (recrée-le, 16 lettres, et colle-le "
                       "dans ⚙ Réglages)" if auth else f"envoi d'emails impossible : {type(e).__name__}")


def _lecture() -> tuple[bool, str]:
    import imaplib
    try:
        m = imaplib.IMAP4_SSL(config.env("IMAP_HOTE", "imap.gmail.com"), timeout=30)
        m.login(config.env("EMAIL_ADRESSE"), config.env("EMAIL_MOT_DE_PASSE"))
        m.logout()
        return True, "lecture des réponses OK"
    except Exception as e:
        return False, f"lecture des réponses impossible ({type(e).__name__}) : vérifie que l'IMAP est activé"


def verifier() -> list[tuple[bool, str]]:
    r = []
    if config.simulation():
        r.append((True, "mode SIMULATION (pas de clé API) : rien de réel ne part"))
    else:
        r += [_ia(), _envoi(), _lecture()]
    r.append((config.chemin_cv().exists(), "CV reçu" if config.chemin_cv().exists() else "pas encore de CV : envoie-le en PDF"))
    profil = bool(config.profil_texte().strip())
    r.append((profil, "profil lu" if profil else "profil pas encore lu (CV en cours d'analyse ou illisible)"))
    if config.regles_envoi()["mode"] == "repetition":
        adr = config.email_test()
        du_cv = adr != (config.env("EMAIL_ADRESSE") or "").lower()
        r.append((True, f"mode test : les candidatures arrivent sur {adr}"
                  + ("" if du_cv else " (pas d'email trouvé dans le CV : c'est l'adresse de l'agent)")))
    if db.kv_get("pause", False):
        fin = db.kv_get("pause_jusqu")
        r.append((False, "en PAUSE" + (f" jusqu'au {fin}" if fin else "") + " : écris « reprendre »"))
    budget = float(config.env("BUDGET_JOUR_EUR", "3"))
    depense = llm.depense_du_jour()
    r.append((depense < budget, f"IA aujourd'hui : {depense:.2f} € sur {budget:g} €"
              + ("" if depense < budget else " — budget atteint, « budget 5 » pour l'augmenter")))
    return r


def rapport(envoyer: bool = True) -> str:
    lignes = verifier()
    ko = [t for ok, t in lignes if not ok]
    texte = ("Tout est en ordre." if not ko else f"{len(ko)} problème(s) :") + "\n" + \
        "\n".join(("✅ " if ok else "❌ ") + t for ok, t in lignes)
    if envoyer:
        messagerie.dire(texte)
    return texte


def au_demarrage():
    """Au lancement : silencieux si tout va bien, sinon UN message avec seulement ce qui bloque."""
    time.sleep(20)                  # laisse Telegram et la base démarrer
    ko = [t for ok, t in verifier() if not ok]
    db.journal("diagnostic", "tout est en ordre" if not ko else " ; ".join(ko)[:300])
    if ko:
        messagerie.dire("Au démarrage, quelque chose bloque :\n" + "\n".join(f"❌ {t}" for t in ko))
