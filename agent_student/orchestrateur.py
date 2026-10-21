"""Le chef d'orchestre : du code simple, pas une IA (gratuit, et il ne se trompe jamais de chemin).

  Toutes les 30 min  : Veille gratuite (Student.be, StudentJob, Indeed, pages suivies) → s'il y a du neuf, traitement immédiat
  Toutes les heures  : Éclaireur web (7h-22h), 2 requêtes en parallèle → traitement
  Chaque matin       : Prospecteur (candidatures spontanées) → traitement
  Traitement         : Extracteur → Garde → Évaluateur → Enquêteur → Rédacteur ⇄ Juge → cartes sur Telegram
  Toutes les 10 min  : Envoyeur (Contrôleur avant chaque email ; heures de bureau)
  Toutes les 5 min   : Trieur (boîte mail) → Secrétaire si entretien
  Toutes les heures  : Relanceur, Préparateur d'entretien (la veille), bilan du soir
  En permanence      : Concierge, qui écoute tes messages ; le travail long part en arrière-plan
"""
from __future__ import annotations

import queue
import threading
import time
from datetime import datetime

from . import config, db, messagerie
from .agents import (coach, concierge, enqueteur, envoyeur, evaluateur, extracteur, garde, preparateur,
                     prospecteur, rapporteur, redacteur, relance, sources, trieur)

_traitement = threading.Lock()
_envoi = threading.Lock()
_boite = threading.Lock()
_recherche = threading.Lock()


def _proteger(nom, f, *a):
    try:
        return f(*a)
    except Exception as e:  # un agent qui plante ne fait jamais tomber les autres
        db.journal("orchestre", f"{nom} a échoué : {type(e).__name__}: {e}")


def en_pause() -> bool:
    return bool(db.kv_get("pause", False))


def pret() -> bool:
    return coach.onboarding_fini() or (config.OBJECTIF.exists() and bool(config.profil_texte().strip()))


def _reessayer_cv():
    """CV reçu mais pas encore analysé (erreur passagère) : nouvel essai, au plus 3 fois, espacés de 30 min."""
    if not config.chemin_cv().exists() or config.profil_texte().strip():
        return
    essais = db.kv_get("profil_essais", {"n": 0, "dernier": 0})
    if essais["n"] >= 3 or time.time() - essais["dernier"] < 1800:
        return
    db.kv_set("profil_essais", {"n": essais["n"] + 1, "dernier": time.time()})
    _proteger("profileur", coach.recevoir_cv, config.chemin_cv())


def en_fond(f, *a):
    """Travail long hors du fil des messages : tu peux continuer à écrire pendant ce temps.
    En cas d'échec, tu reçois un message au lieu d'un silence."""
    def tache():
        try:
            f(*a)
        except Exception as e:
            db.journal("orchestre", f"{getattr(f, '__name__', 'tâche')} a échoué : {type(e).__name__}: {e}")
            messagerie.dire(f"⚠️ Oups, ça a planté de mon côté ({type(e).__name__}). "
                            "Envoie une capture de « Ce que font les agents » au développeur.")
    threading.Thread(target=tache, daemon=True).start()


# ---------- étapes ----------
def traitement():
    """Fait avancer toutes les offres en attente jusqu'à la carte « prête à envoyer »."""
    _reessayer_cv()
    if en_pause() or not pret() or not _traitement.acquire(blocking=False):
        return
    try:
        for nom, etape in (("extracteur", extracteur.enrichir), ("garde", garde.filtrer),
                           ("évaluateur", evaluateur.evaluer), ("enquêteur", enqueteur.enqueter),
                           ("rédacteur", redacteur.rediger), ("proposition", redacteur.proposer_lot)):
            _proteger(nom, etape)
    finally:
        _traitement.release()
    _envoyer()


def veille():
    if en_pause() or not pret():
        return
    db.journal("eclaireur", "recherche en cours (Student.be, StudentJob, Indeed, pages suivies)…")
    nouvelles = _proteger("veille", sources.veille_gratuite) or 0
    en_attente = db.q1("SELECT COUNT(*) AS n FROM offres WHERE statut IN ('decouverte','enrichie','qualifiee')")["n"]
    db.journal("eclaireur", f"recherche finie : {nouvelles} nouvelle(s) offre(s) · {en_attente} en traitement"
                            f" · prochaine dans {config.env_int('INTERVALLE_VEILLE_MIN', 30)} min")
    if nouvelles or en_attente:
        traitement()


def recherche_web(force: bool = False):
    h = datetime.now().hour
    if en_pause() or not pret() or (not force and not 7 <= h < 22) or not _recherche.acquire(blocking=False):
        return
    try:
        _proteger("éclaireur web", sources.recherche_web)
    finally:
        _recherche.release()
    traitement()


def prospection():
    if en_pause() or not pret() or datetime.now().hour < 7:
        return
    if _proteger("prospecteur", prospecteur.prospecter):
        traitement()


def _envoyer():
    if en_pause():
        return
    with _envoi:
        _proteger("envoyeur", envoyeur.envoyer)


def boite():
    if not _boite.acquire(blocking=False):
        return
    try:
        _proteger("trieur", trieur.relever)
    finally:
        _boite.release()


def horaire():
    _proteger("préparateur", preparateur.preparer)
    if en_pause():
        return
    _proteger("relance", relance.relancer)
    if datetime.now().hour >= 19:
        _proteger("bilan", rapporteur.bilan_du_jour)


def pipeline():
    """Tout, tout de suite (commande « cherche », tests)."""
    if en_pause() or not pret():
        return
    _proteger("veille", sources.veille_gratuite)
    _proteger("éclaireur web", sources.recherche_web)
    _proteger("prospecteur", prospecteur.prospecter)
    traitement()


def declencher():
    en_fond(pipeline)


def envoyer_bientot():
    en_fond(_envoyer)


def un_cycle():
    """Pour tester : un passage complet, sans attendre."""
    pipeline()
    boite()
    horaire()


def _boucle(f, minutes, decalage=0):
    time.sleep(decalage)
    while True:
        _proteger(f.__name__, f)
        time.sleep(minutes * 60)


def demarrer(canal):
    db.connexion()
    coach.appliquer_identite()
    messagerie.brancher(canal)
    if not db.kv_get("regles_v2_appliquees"):
        # Nouvelles règles (langues « atout » ≠ « exigée », note minimum 5) : on redonne leur chance
        # aux offres récentes écartées par les anciennes règles.
        db.kv_set("regles_v2_appliquees", True)
        n = _proteger("evaluateur", evaluateur.reevaluer_recentes, 21, True) or 0
        db.journal("evaluateur", f"nouvelles règles : {n} offre(s) écartée(s) récemment sont réévaluées")
    file: queue.Queue = queue.Queue()

    def ouvrier():
        while True:
            m = file.get()
            _proteger("concierge", concierge.recevoir, m)
            file.task_done()

    threading.Thread(target=ouvrier, daemon=True).start()
    rythme = config.env_int
    for f, minutes, decalage in ((veille, rythme("INTERVALLE_VEILLE_MIN", 30), 5),
                                 (recherche_web, rythme("INTERVALLE_WEB_MIN", 360), 60),
                                 (prospection, 60, 120),
                                 (_envoyer, 10, 30),
                                 (boite, rythme("INTERVALLE_BOITE_MIN", 5), 10),
                                 (horaire, 60, 90)):
        threading.Thread(target=_boucle, args=(f, minutes, decalage), daemon=True).start()

    if not db.kv_get("bienvenue_envoyee"):
        db.kv_set("bienvenue_envoyee", True)
        coach.bienvenue()
    db.journal("orchestre", f"démarré · canal {canal.nom} · mode {config.regles_envoi()['mode']}"
                            f"{' · SIMULATION' if config.simulation() else ''}")
    if en_pause():
        db.journal("orchestre", "⏸️ EN PAUSE : aucune recherche ni envoi tant que tu n'écris pas « reprendre »")
        messagerie.dire("⏸️ Je suis en pause : je ne cherche et n'envoie rien. Écris « reprendre » pour que je m'y remette.",
                        [[("▶️ Reprendre", "reprendre")]])
    canal.ecouter(file.put)   # bloquant
    file.join()               # fin de session : on finit de traiter les messages reçus
