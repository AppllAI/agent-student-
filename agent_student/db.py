"""Mémoire partagée de l'équipe (SQLite).

Les agents ne se parlent pas en texte libre : ils se passent le relais via le STATUT
de chaque offre (machine à états). C'est ce qui garde le système fiable et économe :
aucun agent n'a besoin de relire l'historique des autres.

  decouverte ─► enrichie ─► qualifiee ─► redigee ─► a_valider ─► validee ─► envoyee ─► relancee
       │            │           │            │                    │    │          │
       └──────► ecartee ◄───────┘            │      repetee ◄─────┘    │          └──► refus / entretien
                                             │   (mode répétition :    manuelle (formulaire)
                                             │    envoyé à TOI seulement)
"""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime

from . import config

TRANSITIONS = {
    "decouverte": {"enrichie", "ecartee", "erreur"},
    "enrichie": {"qualifiee", "ecartee", "erreur"},
    "qualifiee": {"redigee", "ecartee", "erreur"},
    "redigee": {"a_valider", "validee", "ecartee", "erreur"},
    "a_valider": {"validee", "ecartee", "qualifiee"},
    "validee": {"envoyee", "manuelle", "repetee", "ecartee", "a_valider", "erreur"},
    "repetee": {"validee", "ecartee"},
    "manuelle": {"envoyee", "ecartee"},
    "envoyee": {"relancee", "refus", "entretien"},
    "relancee": {"refus", "entretien", "sans_reponse"},
    "entretien": {"refus", "offre_recue"},
    "erreur": {"decouverte", "enrichie", "qualifiee", "validee", "ecartee"},
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS offres (
    id INTEGER PRIMARY KEY,
    cle TEXT UNIQUE,                 -- empreinte anti-doublon (url normalisée / employeur+titre)
    url TEXT, source TEXT,
    titre TEXT, entreprise TEXT, ville TEXT, secteur TEXT, contrat TEXT, langue TEXT,
    horaires TEXT, salaire_horaire REAL,     -- job étudiant : quand on travaille, combien
    date_limite TEXT,                         -- date limite de candidature, si l'annonce en donne une
    description TEXT,
    email_contact TEXT, mode_candidature TEXT,   -- email | formulaire | inconnu
    score INTEGER, raison TEXT,
    infos_cabinet TEXT,
    lettre TEXT, objet_mail TEXT, corps_mail TEXT,
    statut TEXT NOT NULL DEFAULT 'decouverte',
    tentatives INTEGER DEFAULT 0,
    notifie INTEGER DEFAULT 0,
    cree_le TEXT DEFAULT CURRENT_TIMESTAMP,
    maj_le TEXT DEFAULT CURRENT_TIMESTAMP,
    envoye_le TEXT,
    message_id TEXT,                 -- Message-ID de notre email (pour relier les réponses)
    publie_le TEXT,                  -- date de publication (fraîcheur = chances d'entretien)
    pourquoi TEXT,                   -- les 2-3 arguments « pourquoi toi » trouvés par l'Évaluateur
    note_lettre INTEGER              -- note du Juge /10
);
CREATE INDEX IF NOT EXISTS ix_offres_statut ON offres(statut);

CREATE TABLE IF NOT EXISTS evenements (
    id INTEGER PRIMARY KEY, offre_id INTEGER, agent TEXT, quoi TEXT,
    le TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS emails_recus (
    id INTEGER PRIMARY KEY, uid TEXT UNIQUE, offre_id INTEGER,
    expediteur TEXT, sujet TEXT, corps TEXT,
    categorie TEXT, analyse TEXT, traite INTEGER DEFAULT 0,
    recu_le TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS rdv (
    id INTEGER PRIMARY KEY, offre_id INTEGER, email_id INTEGER,
    entreprise TEXT, debut TEXT, lieu TEXT,
    statut TEXT DEFAULT 'propose',   -- propose | confirme | decline
    cree_le TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS conversation (
    id INTEGER PRIMARY KEY, role TEXT, texte TEXT, le TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS couts (
    id INTEGER PRIMARY KEY, agent TEXT, modele TEXT,
    entree INTEGER, sortie INTEGER, cache INTEGER, eur REAL,
    le TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS kv (cle TEXT PRIMARY KEY, valeur TEXT);
"""

_c: sqlite3.Connection | None = None
_verrou = threading.RLock()   # une seule connexion partagée, accès sérialisé : simple et sans fuite


def connexion() -> sqlite3.Connection:
    global _c
    with _verrou:
        if _c is None:
            config.DONNEES.mkdir(parents=True, exist_ok=True)
            _c = sqlite3.connect(config.DONNEES / "agent.db", timeout=30, check_same_thread=False)
            _c.row_factory = sqlite3.Row
            _c.execute("PRAGMA journal_mode=WAL")
            _c.executescript(SCHEMA)
            _migrer(_c)
        return _c


def _migrer(c):
    """Ajoute les colonnes des nouvelles versions à une base existante (sans rien perdre)."""
    existantes = {r[1] for r in c.execute("PRAGMA table_info(offres)")}
    for col in ("publie_le TEXT", "pourquoi TEXT", "note_lettre INTEGER", "horaires TEXT", "salaire_horaire REAL",
                "date_limite TEXT"):
        if col.split()[0] not in existantes:
            c.execute(f"ALTER TABLE offres ADD COLUMN {col}")
    c.commit()


@contextmanager
def tx():
    with _verrou:
        c = connexion()
        try:
            yield c
            c.commit()
        except Exception:
            c.rollback()
            raise


def q(sql, *p):
    with _verrou:
        return connexion().execute(sql, p).fetchall()


def q1(sql, *p):
    with _verrou:
        return connexion().execute(sql, p).fetchone()


def journal(agent: str, quoi: str, offre_id: int | None = None):
    with tx() as c:
        c.execute("INSERT INTO evenements (offre_id, agent, quoi) VALUES (?,?,?)", (offre_id, agent, quoi))
    print(f"[{datetime.now():%H:%M:%S}] {agent:<12} {quoi}", flush=True)


def changer_statut(offre_id: int, nouveau: str, agent: str, raison: str | None = None, **champs):
    """Seule façon de faire avancer une offre : refuse les transitions illogiques."""
    with tx() as c:
        o = c.execute("SELECT statut FROM offres WHERE id=?", (offre_id,)).fetchone()
        if o is None:
            raise KeyError(offre_id)
        if nouveau != o["statut"] and nouveau not in TRANSITIONS.get(o["statut"], set()):
            raise ValueError(f"Transition interdite {o['statut']} → {nouveau} (offre {offre_id})")
        champs["statut"] = nouveau
        if raison is not None:
            champs["raison"] = raison
        sets = ", ".join(f"{k}=?" for k in champs) + ", maj_le=CURRENT_TIMESTAMP"
        c.execute(f"UPDATE offres SET {sets} WHERE id=?", (*champs.values(), offre_id))
    journal(agent, f"→ {nouveau}" + (f" ({raison})" if raison else ""), offre_id)


def maj(offre_id: int, **champs):
    with tx() as c:
        sets = ", ".join(f"{k}=?" for k in champs) + ", maj_le=CURRENT_TIMESTAMP"
        c.execute(f"UPDATE offres SET {sets} WHERE id=?", (*champs.values(), offre_id))


def kv_get(cle, defaut=None):
    r = q1("SELECT valeur FROM kv WHERE cle=?", cle)
    return json.loads(r["valeur"]) if r else defaut


def kv_set(cle, valeur):
    with tx() as c:
        c.execute("INSERT INTO kv (cle, valeur) VALUES (?,?) ON CONFLICT(cle) DO UPDATE SET valeur=excluded.valeur",
                  (cle, json.dumps(valeur, ensure_ascii=False)))


def dire(role: str, texte: str):
    with tx() as c:
        c.execute("INSERT INTO conversation (role, texte) VALUES (?,?)", (role, texte))


def historique(n=12):
    return [dict(r) for r in q("SELECT role, texte FROM conversation ORDER BY id DESC LIMIT ?", n)][::-1]
