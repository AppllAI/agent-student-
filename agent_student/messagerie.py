"""Point d'accès unique vers la personne (le canal actif : Telegram, WhatsApp, la fenêtre ou la console).

Boutons : `dire(texte, boutons=[[("✅ Envoyer", "ok 12"), ("❌ Non", "ecarte 12")]])`.
Un bouton touché renvoie simplement sa commande, comme si tu l'avais tapée : tout passe par le Concierge.
Les canaux sans boutons affichent les commandes à taper."""
from __future__ import annotations

from pathlib import Path

from . import config, db

_canal = None
Boutons = list[list[tuple[str, str]]]


def brancher(canal):
    global _canal
    _canal = canal


def _prefixe(texte: str) -> str:
    """En mode répétition, chaque message le rappelle : impossible de confondre avec le vrai."""
    try:
        mode = config.regles_envoi()["mode"]
    except Exception:
        mode = ""
    return f"🧪 {texte}" if mode == "repetition" and not texte.startswith("🧪") else texte


def dire(texte: str, boutons: Boutons | None = None):
    texte = _prefixe(texte)
    db.dire("agent", texte)
    if _canal:
        try:
            _canal.envoyer(texte, boutons)
        except TypeError:          # canal ancien sans boutons
            _canal.envoyer(texte)
    else:
        print(f"\n💬 {texte}\n" + texte_boutons(boutons), flush=True)


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
