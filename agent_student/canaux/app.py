"""Canal « app » : l'agent tourne sur le serveur de l'application, un processus isolé par utilisateur.

Le serveur et l'agent se parlent par une petite base SQLite dans le dossier de l'utilisateur (`canal.db`) :
  • entree : ce que la personne écrit dans l'app (texte, ou CV déposé)   serveur → agent
  • sortie : ce que l'agent répond (texte, boutons, fichiers PDF)          agent → serveur
Pas de port réseau ouvert, pas de secret échangé : seul le serveur, sur la même machine, lit et écrit ce fichier."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

from .. import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS entree (
    id INTEGER PRIMARY KEY, texte TEXT, fichier TEXT, nom_fichier TEXT,
    traite INTEGER DEFAULT 0, le TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS sortie (
    id INTEGER PRIMARY KEY, genre TEXT DEFAULT 'texte', texte TEXT, boutons TEXT, fichier TEXT, legende TEXT,
    lu INTEGER DEFAULT 0, notifie INTEGER DEFAULT 0, le TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS etat (cle TEXT PRIMARY KEY, valeur TEXT);
"""


def ouvrir(racine: Path) -> sqlite3.Connection:
    c = sqlite3.connect(Path(racine) / "canal.db", timeout=30, check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.executescript(SCHEMA)
    return c


class App:
    nom = "app"

    def __init__(self):
        config.RACINE.mkdir(parents=True, exist_ok=True)
        self.c = ouvrir(config.RACINE)
        self.verrou = threading.Lock()

    def _ecrire(self, **champs):
        with self.verrou:
            self.c.execute(f"INSERT INTO sortie ({', '.join(champs)}) VALUES ({', '.join('?' * len(champs))})",
                           tuple(champs.values()))
            self.c.commit()

    def envoyer(self, texte, boutons=None, genre="texte"):
        self._ecrire(genre=genre, texte=texte,
                     boutons=json.dumps([[{"libelle": l, "commande": c} for l, c in ligne] for ligne in boutons],
                                        ensure_ascii=False) if boutons else None)

    def demander_note(self, texte, boutons=None):
        """Message spécial : l'app affiche le formulaire « note sur 20 + ce que tu as aimé / pas aimé »."""
        self.envoyer(texte, boutons, genre="notation")

    def envoyer_fichier(self, chemin: Path, legende=""):
        self._ecrire(genre="fichier", fichier=str(Path(chemin).resolve()), legende=legende)

    def occupe(self):
        with self.verrou:
            self.c.execute("INSERT OR REPLACE INTO etat (cle, valeur) VALUES ('occupe', ?)", (str(time.time()),))
            self.c.commit()

    def ecouter(self, callback):
        from ..agents.concierge import Entrant
        while True:
            with self.verrou:
                rows = self.c.execute("SELECT * FROM entree WHERE traite=0 ORDER BY id LIMIT 20").fetchall()
                if rows:
                    self.c.executemany("UPDATE entree SET traite=1 WHERE id=?", [(r["id"],) for r in rows])
                    self.c.commit()
            for r in rows:
                callback(Entrant(texte=r["texte"] or "", fichier=Path(r["fichier"]) if r["fichier"] else None,
                                 nom_fichier=r["nom_fichier"] or ""))
            time.sleep(0.5 if rows else 1)
