"""Mémoire longue de l'agent : il se souvient de tout, longtemps, pour presque rien.

Trois étages, du plus précis au plus large :
  1. Fil récent    : les derniers messages, mot pour mot (ce qu'on vient de se dire).
  2. Carnet de bord : chaque journée écoulée est résumée UNE seule fois par l'IA rapide (≈ 0,001 €) :
                      ce qui a été dit, demandé, décidé, avec les numéros d'offres et les employeurs.
                      Les 14 derniers jours sont toujours sous les yeux du Manager (texte stable → mis en cache).
  3. Recherche     : « la boîte dont je t'ai parlé il y a 5 jours » → l'outil `se_souvenir` fouille TOUT
                      l'historique (messages, offres, emails reçus, rendez-vous, carnet), sans IA, gratuitement,
                      et rend les passages datés qui correspondent.
Et des souvenirs durables (« retenir ») : préférences, décisions (« plus jamais chez X »), faits sur la personne,
remarques de style — rappelés partout où ils comptent (Manager, Coach, Rédacteur)."""
from __future__ import annotations

import json
import re
from datetime import date, timedelta

from . import db, llm
from .outils import sans_accents

SCHEMA_CARNET = {
    "type": "object",
    "properties": {
        "resume": {"type": "string", "description": "La journée en 2 à 6 puces courtes, avec n° d'offres et employeurs"},
        "a_retenir": {"type": "array", "items": {"type": "object", "properties": {
            "genre": {"type": "string", "enum": ["preference", "decision", "fait", "style"]},
            "texte": {"type": "string"}}, "required": ["genre", "texte"]},
            "description": "Ce qui reste vrai au-delà de cette journée (souvent rien)"},
    },
    "required": ["resume", "a_retenir"],
}
CONSIGNE_CARNET = """Tu tiens le carnet de bord d'un agent de recherche d'emploi. On te donne UNE journée :
les messages échangés avec la personne et ce que l'agent a fait.

`resume` : 2 à 6 puces (« - … »), factuelles, au passé, qui permettront dans 3 semaines de retrouver de quoi on a
parlé : demandes de la personne, décisions, offres citées (n° + employeur), rendez-vous, réglages changés,
problèmes. Pas de bavardage, pas de formule de politesse. Journée sans échange notable → « - Rien de notable. »

`a_retenir` : seulement ce qui reste vrai APRÈS cette journée et que la personne a dit ou décidé elle-même :
une préférence (« préfère les petits cabinets »), une décision (« ne plus postuler chez X »), un fait sur elle
(« a eu son permis »), une remarque de style pour les lettres (« lettres plus courtes »). Souvent : liste vide.
N'invente rien."""
JOURS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
_VIDES = {"les", "des", "une", "pour", "avec", "dans", "sur", "que", "qui", "quoi", "est", "pas", "mon", "mes", "ton",
          "tes", "son", "ses", "elle", "lui", "moi", "toi", "nous", "vous", "dont", "parle", "parlé", "dit", "jour",
          "jours", "il", "y", "a", "ce", "cette", "cet", "the", "and", "tu", "je", "j", "m", "t", "l", "d", "qu"}


# ---------- Souvenirs durables ----------
def retenir(texte: str, genre: str = "fait") -> bool:
    """Ajoute un souvenir durable (sans doublon). Renvoie False s'il était déjà connu."""
    texte = re.sub(r"\s+", " ", texte or "").strip()
    if len(texte) < 3:
        return False
    cle = sans_accents(texte)
    if any(sans_accents(r["texte"]) == cle for r in db.q("SELECT texte FROM souvenirs")):
        return False
    with db.tx() as c:
        c.execute("INSERT INTO souvenirs (genre, texte) VALUES (?,?)", (genre, texte[:400]))
    return True


def oublier(mots: str) -> int:
    """Retire les souvenirs durables qui contiennent ces mots (« oublie que je veux Liège »)."""
    cles = _mots(mots)
    ids = [r["id"] for r in db.q("SELECT id, texte FROM souvenirs")
           if cles and all(k in sans_accents(r["texte"]) for k in cles)]
    with db.tx() as c:
        c.executemany("DELETE FROM souvenirs WHERE id=?", [(i,) for i in ids])
    return len(ids)


def notes_style() -> str:
    """Tout ce qu'elle a dit sur ses lettres (« plus court », « parle de mon Erasmus »…) : le Rédacteur l'applique."""
    from . import config
    f = config.PROFIL_DIR / "style.md"          # remarques notées par les versions précédentes
    anciennes = f.read_text(encoding="utf-8").strip() if f.exists() else ""
    nouvelles = "\n".join(f"- {x}" for x in souvenirs(("style", "preference")))
    return "\n".join(x for x in (anciennes, nouvelles) if x)


def souvenirs(genres: tuple[str, ...] | None = None) -> list[str]:
    rows = db.q("SELECT genre, texte, le FROM souvenirs ORDER BY id")
    return [f"{r['texte']} (noté le {r['le'][:10]})" for r in rows if not genres or r["genre"] in genres]


# ---------- Carnet de bord ----------
def _joli(jour: str) -> str:
    d = date.fromisoformat(jour)
    return f"{JOURS[d.weekday()]} {d:%d/%m/%Y}"


def _journee(jour: str) -> str:
    """Tout ce qui s'est passé ce jour-là, en texte compact (pour le résumé)."""
    lignes = [f"[{r['le'][11:16]}] {'Elle' if r['role'] == 'moi' else 'Agent'} : {r['texte'][:500]}"
              for r in db.q("SELECT role, texte, le FROM conversation WHERE date(le)=? ORDER BY id LIMIT 200", jour)]
    faits = db.q("SELECT e.quoi, e.offre_id, o.titre, o.entreprise FROM evenements e LEFT JOIN offres o ON o.id=e.offre_id "
                 "WHERE date(e.le)=? AND e.agent IN ('concierge','envoyeur','manager','coach','trieur','secretaire') "
                 "ORDER BY e.id LIMIT 80", jour)
    lignes += [f"(action) n°{r['offre_id']} {r['entreprise'] or ''} – {r['titre'] or ''} : {r['quoi'][:120]}"
               for r in faits if r["offre_id"]]
    return "\n".join(lignes)


def mettre_a_jour_carnet(max_jours: int = 3) -> int:
    """Résume les journées passées pas encore résumées (au plus `max_jours` par appel, les plus anciennes d'abord).
    Appelé toutes les heures : chaque journée ne coûte qu'une fois."""
    faits = 0
    deja = {r["jour"] for r in db.q("SELECT jour FROM carnet")}
    jours = [r["j"] for r in db.q("SELECT DISTINCT date(le) AS j FROM conversation WHERE date(le) < date('now') "
                                  "AND date(le) >= date('now', '-60 days') ORDER BY j")]
    for jour in [j for j in jours if j not in deja][:max_jours]:
        texte = _journee(jour)
        try:
            r = llm.demander("carnet", "rapide", CONSIGNE_CARNET, f"Journée du {_joli(jour)} :\n{texte[:12000]}",
                             SCHEMA_CARNET, max_tokens=800, priorite="fond", reflexion=False, effort="low")
        except llm.BudgetDepasse:
            break
        with db.tx() as c:
            c.execute("INSERT OR REPLACE INTO carnet (jour, resume) VALUES (?,?)", (jour, (r.get("resume") or "").strip()))
        for s in r.get("a_retenir") or []:
            if isinstance(s, dict) and s.get("texte"):
                retenir(s["texte"], s.get("genre") or "fait")
        faits += 1
    return faits


def carnet(jours: int = 14) -> list[str]:
    rows = db.q("SELECT jour, resume FROM carnet WHERE jour >= date('now', ?) ORDER BY jour", f"-{int(jours)} days")
    return [f"### {_joli(r['jour'])}\n{r['resume']}" for r in rows]


def contexte(jours: int = 14) -> str:
    """Bloc « mémoire » pour le prompt système : souvenirs durables + carnet des derniers jours.
    Il ne change qu'une fois par jour (ou quand on retient quelque chose) → le cache de prompt le rend presque gratuit."""
    parties = []
    s = souvenirs()
    if s:
        parties.append("## Ce que je sais d'elle durablement\n" + "\n".join(f"- {x}" for x in s[-40:]))
    c = carnet(jours)
    if c:
        parties.append(f"## Carnet de bord ({jours} derniers jours)\n" + "\n".join(c))
    return "\n\n".join(parties)


# ---------- Recherche dans tout l'historique (sans IA) ----------
def _mots(texte: str) -> list[str]:
    return [m for m in re.findall(r"[a-z0-9@.'-]{3,}", sans_accents(texte or "")) if m not in _VIDES]


def chercher(mots: str = "", depuis: str | None = None, jusqu_au: str | None = None, limite: int = 15) -> list[str]:
    """Fouille messages, offres, emails reçus, rendez-vous et carnet. Avec des mots : les passages qui en contiennent
    le plus ; sans mots : tout ce qui s'est passé dans la période. Chaque résultat est daté."""
    debut = depuis or (date.today() - timedelta(days=365)).isoformat()
    fin = jusqu_au or date.today().isoformat()
    sources = [
        ("message", "SELECT role, texte, le FROM conversation WHERE date(le) BETWEEN ? AND ?",
         lambda r: f"{'Elle' if r['role'] == 'moi' else 'Agent'} : {r['texte'][:400]}"),
        ("offre", "SELECT id, titre, entreprise, ville, statut, url, cree_le AS le FROM offres "
                  "WHERE date(cree_le) BETWEEN ? AND ? OR date(maj_le) BETWEEN ? AND ?",
         lambda r: f"offre n°{r['id']} {r['titre']} – {r['entreprise'] or '?'} ({r['ville'] or '?'}) · {r['statut']} · {r['url']}"),
        ("email", "SELECT id, expediteur, sujet, categorie, analyse, recu_le AS le FROM emails_recus "
                  "WHERE date(recu_le) BETWEEN ? AND ? AND categorie <> 'autre'",
         lambda r: f"email #{r['id']} de {r['expediteur']} « {r['sujet']} » ({r['categorie']}) : "
                   f"{json.loads(r['analyse'] or '{}').get('resume', '')}"),
        ("rdv", "SELECT id, entreprise, debut, lieu, statut, cree_le AS le FROM rdv WHERE date(cree_le) BETWEEN ? AND ?",
         lambda r: f"rendez-vous #{r['id']} {r['entreprise']} le {r['debut']} à {r['lieu'] or '?'} ({r['statut']})"),
        ("carnet", "SELECT jour AS le, resume FROM carnet WHERE jour BETWEEN ? AND ?",
         lambda r: f"carnet : {r['resume'][:500]}"),
    ]
    cles = _mots(mots)
    trouves = []
    for genre, sql, texte in sources:
        params = (debut, fin, debut, fin) if sql.count("?") == 4 else (debut, fin)
        for r in db.q(sql + " LIMIT 3000", *params):
            t = texte(r)
            norm = sans_accents(t)
            score = sum(1 for k in cles if k in norm) if cles else 1
            if score:
                trouves.append((score, r["le"] or "", f"[{(r['le'] or '')[:16]}] {t}"))
    trouves.sort(key=lambda x: x[1], reverse=True)          # le plus récent d'abord…
    if cles:
        trouves.sort(key=lambda x: -x[0])                     # … puis le plus pertinent (tri stable)
        return [t for _, _, t in trouves[:limite]]
    return [t for _, _, t in reversed(trouves[:limite])]      # période sans mots-clés : dans l'ordre du temps
