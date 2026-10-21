"""Canaux de discussion. Tous ont la même interface, l'équipe ne sait pas lequel est branché :
  envoyer(texte) · envoyer_fichier(chemin, legende) · ecouter(callback)  (callback reçoit un Entrant)"""
from __future__ import annotations

from .. import config


def choisir(nom: str | None = None):
    nom = nom or config.env("CANAL", "telegram")
    if nom == "whatsapp":
        from .whatsapp import WhatsApp
        return WhatsApp()
    if nom == "telegram":
        from .telegram import Telegram
        return Telegram()
    from .console import Console
    return Console()
