"""Canal console : discuter avec l'agent dans le terminal (tests, ou si tu n'as pas de téléphone sous la main).
Astuces : « /cv chemin.pdf » simule l'envoi d'un CV, « /vocal chemin.ogg » un vocal, « /cycle » force un passage."""
from __future__ import annotations

from pathlib import Path


class Console:
    nom = "console"

    def envoyer(self, texte, boutons=None):
        from ..messagerie import texte_boutons
        print(f"\n🤖 {texte}{texte_boutons(boutons)}\n", flush=True)

    def envoyer_fichier(self, chemin: Path, legende=""):
        print(f"🤖 📎 {legende} → {chemin}", flush=True)

    def ecouter(self, callback):
        from ..agents.concierge import Entrant
        from .. import orchestrateur

        while True:
            try:
                ligne = input("toi > ").strip()
            except (EOFError, KeyboardInterrupt):
                return
            if ligne.startswith("/cv "):
                p = Path(ligne[4:].strip().strip('"'))
                callback(Entrant(fichier=p, nom_fichier=p.name))
            elif ligne.startswith("/vocal "):
                callback(Entrant(audio=Path(ligne[7:].strip().strip('"'))))
            elif ligne == "/cycle":
                orchestrateur.declencher()
            elif ligne:
                callback(Entrant(texte=ligne))
