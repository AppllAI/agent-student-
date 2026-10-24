"""Point d'accès unique vers la personne (le canal actif : Telegram, WhatsApp, la fenêtre ou la console).

Boutons : `dire(texte, boutons=[[("✅ Envoyer", "ok 12"), ("❌ Non", "ecarte 12")]])`.
Un bouton touché renvoie simplement sa commande, comme si tu l'avais tapée : tout passe par le Concierge.
Les canaux sans boutons affichent les commandes à taper."""
from __future__ import annotations

from pathlib import Path

from . import config, db

import threading

_canal = None
Boutons = list[list[tuple[str, str]]]
_local = threading.local()       # capture des messages par le Manager (fil courant uniquement)


class capturer:
    """Pendant un outil du Manager : les messages des autres agents sont RÉCUPÉRÉS au lieu d'être envoyés,
    pour que le Manager réponde une seule fois, court et clair. Les fichiers, eux, partent normalement."""
    def __enter__(self):
        self.textes = []
        _local.capture = self.textes
        return self

    def __exit__(self, *a):
        _local.capture = None

    @property
    def texte(self):
        return "\n".join(self.textes)


def brancher(canal):
    global _canal
    _canal = canal


def _prefixe(texte: str) -> str:
    """(Ancien préfixe 🧪 du mode test : retiré, le mode est rappelé seulement quand c'est utile.)"""
    try:
        mode = config.regles_envoi()["mode"]
    except Exception:
        mode = ""
    return texte                      # le mode test est rappelé quand c'est utile, pas sur chaque message


def en_test() -> bool:
    try:
        return config.regles_envoi()["mode"] == "repetition"
    except Exception:
        return False


def signaler(texte: str, boutons: Boutons | None = None):
    """Notification du travail de fond. En mode TEST : rien sur ta messagerie (tout arrive par email),
    seulement une ligne dans le journal de l'application."""
    if en_test():
        db.journal("agent", texte.replace("\n", " ")[:160])
        return
    dire(texte, boutons)


def signaler_fichier(chemin, legende=""):
    if not en_test():
        fichier(chemin, legende)


def dire(texte: str, boutons: Boutons | None = None):
    capture = getattr(_local, "capture", None)
    if capture is not None:
        capture.append(texte + (" " + texte_boutons(boutons) if boutons else ""))
        return
    texte = _prefixe(texte)
    db.dire("agent", texte)
    if _canal:
        try:
            _canal.envoyer(texte, boutons)
        except TypeError:          # canal ancien sans boutons
            _canal.envoyer(texte)
    else:
        print(f"\n💬 {texte}\n" + texte_boutons(boutons), flush=True)


def demander_note(texte: str, boutons: Boutons | None = None):
    """Demande de note de la phase de test : formulaire dédié dans l'app, message normal ailleurs.
    Toujours affichée, même en mode test (c'est justement le moment où on a besoin de son avis)."""
    if getattr(_local, "capture", None) is None and _canal and hasattr(_canal, "demander_note"):
        db.dire("agent", texte)
        return _canal.demander_note(texte, boutons)
    dire(texte, boutons)


def fichier(chemin: Path, legende: str = ""):
    if _canal:
        _canal.envoyer_fichier(Path(chemin), legende)
    else:
        print(f"📎 {chemin} {legende}", flush=True)


def occupe():
    """« … est en train d'écrire » : la personne sait tout de suite que son message est pris en compte."""
    if _canal and hasattr(_canal, "occupe"):
        try:
            _canal.occupe()
        except Exception:
            pass


def texte_boutons(boutons: Boutons | None) -> str:
    if not boutons:
        return ""
    return "\n" + "   ".join(f"[{lib} → « {cmd} »]" for ligne in boutons for lib, cmd in ligne)
