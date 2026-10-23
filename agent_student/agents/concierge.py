"""Agent Concierge : le SEUL qui te parle. Il reçoit tes messages (texte, vocal, PDF, boutons touchés),
comprend ce que tu veux et passe la main au bon agent (motif « routing » d'Anthropic).

Réactivité :
  • commandes et boutons (« ok 12 », « montre 12 », « stats »…) → reconnus SANS IA, réponse instantanée ;
  • phrase libre → IA rapide (Haiku), réponse en quelques secondes, « … écrit » affiché pendant ce temps ;
  • travail long (envoi, recherche, essai, lecture du CV) → « ⏳ je m'en occupe » tout de suite,
    puis il tourne en arrière-plan et tu reçois une notification quand c'est prêt."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from .. import config, db, llm, messagerie
from . import coach, rapporteur, secretaire, transcripteur
from .redacteur import carte, chemin_lettre

SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": [
            "valider", "ecarter", "details", "confirmer_rdv", "autre_date", "decliner_rdv", "repondre_recruteur",
            "objectif", "profil", "style", "fait", "chercher", "essai", "banc", "stats", "pause", "reprendre", "vrai_tout",
            "aide", "discuter"]},
        "ids": {"type": "array", "items": {"type": "integer"}},
        "rdv_id": {"type": ["integer", "null"]}, "creneau": {"type": ["string", "null"]},
        "email_id": {"type": ["integer", "null"]}, "texte": {"type": ["string", "null"]},
        "jusqu_au": {"type": ["string", "null"], "description": "pause : date de reprise AAAA-MM-JJ si elle en donne une"},
        "reponse": {"type": "string"},
    },
    "required": ["action", "reponse"],
}
MODES = {"repetition": "🧪 Test : je fais tout seul et j'envoie chaque candidature à l'adresse de ton CV (jamais aux "
                       "employeurs). Je ne t'écris plus ici, regarde ta boîte mail.",
         "validation": "✋ Validation : je t'envoie chaque candidature, rien ne part sans ton ✅.",
         "auto": "🚀 Auto : j'envoie seul les candidatures que le Juge note ≥ 7/10. Je te préviens pour les entretiens."}


@dataclass
class Entrant:
    texte: str = ""
    audio: Path | None = None
    fichier: Path | None = None
    nom_fichier: str = ""


def _fond(f, *a):
    from .. import orchestrateur

    def tache(*x):
        # un travail de fond parle directement à la personne, jamais capturé par le Manager
        ancien = getattr(messagerie._local, "capture", None)
        messagerie._local.capture = None
        try:
            f(*x)
        finally:
            messagerie._local.capture = ancien
    tache.__name__ = getattr(f, "__name__", "tache")
    orchestrateur.en_fond(tache, *a)


def recevoir(m: Entrant):
    if m.fichier:
        if (m.nom_fichier or str(m.fichier)).lower().endswith(".pdf"):
            messagerie.dire("Je lis ton CV…")

            def lire():
                coach.recevoir_cv(Path(m.fichier))
                if not coach.onboarding_fini():
                    coach.parler(m.texte or "Voici mon CV.")
            return _fond(lire)
        return messagerie.dire("Je ne sais lire que les PDF pour l'instant 📄")
    texte = (m.texte or "").strip()
    if m.audio:
        messagerie.occupe()
        try:
            texte = (transcripteur.transcrire(Path(m.audio)) or "").strip()
        except Exception as e:
            db.journal("transcripteur", f"échec : {e}")
            texte = ""
        if not texte:
            return messagerie.dire("Je n'arrive pas encore à écouter les vocaux (voir réglages : transcription). "
                                   "Tu peux me l'écrire ?")
        messagerie.dire(f"« {texte[:300]} »")
    if not texte:
        return
    db.dire("moi", texte)

    if _raccourci(texte):
        return
    attente = db.kv_get("attente_dispo")
    if attente:                                   # elle répond aux « quelles sont tes dispos ? »
        db.kv_set("attente_dispo", None)
        return _fond(secretaire.autre_date, attente, texte)
    if not coach.onboarding_fini():
        messagerie.occupe()
        return coach.parler(texte)
    messagerie.occupe()
    from . import manager
    try:
        manager.repondre(texte)
    except Exception as e:                     # filet : l'ancien routeur simple prend le relais
        db.journal("manager", f"échec, routeur simple : {type(e).__name__}: {e}")
        _ia(texte)


# ---------- Commandes et boutons : sans IA, instantané ----------
def _ids(s):
    return [int(x) for x in re.findall(r"\d+", s)]


def _raccourci(t: str) -> bool:
    b = t.lower().strip(" !.?").replace("é", "e").replace("è", "e")
    b = b.lstrip("/")                              # /stats, /aide… (menu Telegram)
    attente = [r["id"] for r in db.q("SELECT id FROM offres WHERE statut='a_valider' ORDER BY score DESC")]
    rdv_ouverts = db.q("SELECT id FROM rdv WHERE statut='propose'")
    if b in ("stats", "bilan", "statistiques"):
        messagerie.dire(rapporteur.stats())
    elif b in ("aide", "help", "start", "commandes", "menu"):
        messagerie.dire(coach.MENU)
    elif b in ("teste-toi", "testes-toi", "teste toi", "banc", "banc d'essai", "banc-essai"):
        _fond(_banc)
    elif b in ("pause", "stop"):
        pause()
    elif b in ("reprendre", "reprends", "go"):
        db.kv_set("pause", False)
        db.kv_set("pause_jusqu", None)
        from .. import orchestrateur
        messagerie.dire("C'est reparti, je lance une recherche maintenant.")
        orchestrateur.declencher()
    elif b in ("cherche", "recherche", "cherche maintenant", "scan"):
        from .. import orchestrateur
        messagerie.dire("Je lance une recherche complète maintenant. Je te montre ce que je trouve.")
        orchestrateur.declencher()
    elif m := re.fullmatch(r"mode\s+(repetition|validation|auto)", b):
        _changer_mode(m.group(1))
    elif b in ("mode",):
        messagerie.dire(f"Mode actuel : {MODES[config.regles_envoi()['mode']]}",
                        [[("🧪 Répétition", "mode repetition"), ("✋ Validation", "mode validation"),
                          ("🚀 Auto", "mode auto")]])
    elif m := re.fullmatch(r"(essai|teste|test)\s+(https?://\S+)", t.strip(), re.I):
        _fond(_essai, m.group(2))
    elif re.fullmatch(r"(montre|details?|voir|lettre)\s+\d+", b):
        details(_ids(b)[0])
    elif re.fullmatch(r"fait\s+[\d ,et]+", b):
        _fait(_ids(b))
    elif m := re.fullmatch(r"budget\s+(\d+(?:[.,]\d+)?)", b):
        regler_budget(float(m.group(1).replace(",", ".")))
    elif b in ("diagnostic", "verifie", "verifie tout", "check"):
        from . import diagnostic
        _fond(diagnostic.rapport, True)
    elif b in ("vrai tout", "envoie tout pour de vrai", "tout pour de vrai"):
        _vrai_tout()
    elif re.fullmatch(r"vrai\s+\d+", b):
        _vrai(_ids(b)[0])
    elif re.fullmatch(r"(ecarte|non|retire)\s+[\d ,et]+", b):
        ecarter(_ids(b))
        messagerie.dire("Écartée(s).")
    elif m := re.fullmatch(r"rdv\s+(\d+)\s+(oui|non|autre)", b):
        rid, choix = int(m.group(1)), m.group(2)
        if choix == "oui":
            _fond(secretaire.confirmer, rid)
        elif choix == "non":
            _fond(secretaire.decliner, rid)
        else:
            db.kv_set("attente_dispo", rid)
            messagerie.dire("Quelles sont tes disponibilités ? (ex. « mardi ou jeudi après 14h »)")
    elif re.fullmatch(r"(ok|valide|envoie|go)\s+[\d ,et]+", b) and not b.startswith(("ok sauf", "ok tout")):
        valider(_ids(b))
    elif attente and not rdv_ouverts and re.fullmatch(r"(ok|oui|valide|envoie)( tout| tous| toutes)?", b):
        valider(attente)
    elif attente and re.fullmatch(r"(ok|valide|envoie)( tout)? sauf [\d ,et]+", b):
        sauf = set(_ids(b))
        ecarter(list(sauf))
        valider([i for i in attente if i not in sauf])
    else:
        return False
    return True


# ---------- Phrase libre : IA rapide ----------
def _contexte():
    attente = [dict(r) for r in db.q("SELECT id, titre, entreprise, ville, score FROM offres WHERE statut='a_valider'")]
    rdv = [dict(r) for r in db.q("SELECT id, email_id, entreprise, debut, lieu FROM rdv WHERE statut='propose'")]
    questions = [{"email_id": r["id"], "de": r["expediteur"], "resume": json.loads(r["analyse"] or "{}").get("resume")}
                 for r in db.q("SELECT * FROM emails_recus WHERE categorie='question' AND traite=0")]
    manuelles = [dict(r) for r in db.q("SELECT id, entreprise, url FROM offres WHERE statut='manuelle'")]
    s = {r["statut"]: r["n"] for r in db.q("SELECT statut, COUNT(*) AS n FROM offres GROUP BY statut")}
    return {"ETAT_ACTUEL": {
                "en_pause": bool(db.kv_get("pause", False)), "pause_jusqu": db.kv_get("pause_jusqu"),
                "mode_envoi": config.regles_envoi()["mode"],
                "offres_en_traitement": sum(s.get(k, 0) for k in ("decouverte", "enrichie", "qualifiee", "redigee")),
                "ecartees_total": s.get("ecartee", 0), "repetees": s.get("repetee", 0),
                "envoyees": sum(s.get(k, 0) for k in ("envoyee", "relancee", "refus", "entretien", "sans_reponse"))},
            "en_attente_de_validation": attente, "rdv_proposes": rdv, "questions_recruteurs": questions,
            "a_postuler_via_site": manuelles}


def _ia(texte: str):
    try:
        # Sonnet : c'est lui qui te comprend, la qualité compte plus que la petite différence de prix
        d = llm.demander("concierge", "qualite", llm.prompt("concierge"),
                         json.dumps({"contexte": _contexte(), "aujourd_hui": __import__("datetime").date.today().isoformat(),
                                    "historique": db.historique(8), "message": texte},
                                    ensure_ascii=False), SCHEMA, max_tokens=700, reflexion=False)
    except llm.BudgetDepasse as e:
        return messagerie.dire(f"{e}. Les commandes et boutons marchent toujours ; l'IA reprend demain.")
    a, ids = d["action"], d.get("ids") or []
    try:
        if a == "valider":
            return valider(ids or [r["id"] for r in db.q("SELECT id FROM offres WHERE statut='a_valider'")])
        if a == "ecarter":
            ecarter(ids)
        elif a == "details" and ids:
            return details(ids[0])
        elif a == "confirmer_rdv" and d.get("rdv_id"):
            return _fond(secretaire.confirmer, d["rdv_id"], d.get("creneau"))
        elif a == "autre_date" and d.get("rdv_id"):
            return _fond(secretaire.autre_date, d["rdv_id"], d.get("texte") or texte)
        elif a == "decliner_rdv" and d.get("rdv_id"):
            return _fond(secretaire.decliner, d["rdv_id"])
        elif a == "repondre_recruteur" and d.get("email_id"):
            return _fond(secretaire.repondre_question, d["email_id"], d.get("texte") or texte)
        elif a == "objectif":
            return coach.parler(texte)
        elif a == "profil":
            return coach.completer_profil(texte)
        elif a == "style":
            return noter_style(d.get("texte") or texte)
        elif a == "fait":
            return _fait(ids)
        elif a == "chercher":
            return _raccourci("cherche")
        elif a == "banc":
            return _fond(_banc)
        elif a == "essai" and re.search(r"https?://\S+", texte):
            return _fond(_essai, re.search(r"https?://\S+", texte).group(0))
        elif a == "aide":
            return messagerie.dire(coach.MENU)
        elif a == "pause":
            return pause(d.get("jusqu_au"), d.get("reponse"))
        elif a == "vrai_tout":
            return _vrai_tout()
        elif a in ("stats", "reprendre"):
            return _raccourci(a)
    except Exception as e:
        db.journal("concierge", f"action {a} en échec : {e}")
        return messagerie.dire(f"⚠️ Je n'ai pas réussi : {e}")
    messagerie.dire(d["reponse"])


# ---------- Actions ----------
def pause(jusqu_au: str | None = None, mot: str | None = None):
    """Pause simple, ou jusqu'à une date (blocus, examens, vacances) : reprise automatique ce jour-là."""
    from datetime import date
    try:
        fin = date.fromisoformat(jusqu_au[:10]) if jusqu_au else None
    except ValueError:
        fin = None
    if fin and fin <= date.today():
        fin = None
    db.kv_set("pause", True)
    db.kv_set("pause_jusqu", fin.isoformat() if fin else None)
    debut = (mot.strip() + "\n") if mot else ""
    if fin:
        messagerie.dire(f"{debut}En pause jusqu'au {fin:%d/%m} : je ne cherche et n'envoie rien. "
                        "Je reprends tout seul ce jour-là (ou « reprendre » avant).")
    else:
        messagerie.dire(f"{debut}En pause : plus aucune recherche ni envoi. « reprendre » quand tu veux.",
                        [[("▶️ Reprendre", "reprendre")]])



def valider(ids):
    n = 0
    for i in ids:
        o = db.q1("SELECT statut FROM offres WHERE id=?", i)
        if o and o["statut"] in ("a_valider", "repetee"):
            db.changer_statut(i, "validee", "concierge")
            n += 1
    if not n:
        return messagerie.dire("Rien à valider de ce côté 🤔")
    from .. import orchestrateur
    mode = config.regles_envoi()["mode"]
    if mode == "repetition":
        msg = f"{n} candidature(s) validée(s) : mode test, elles arrivent sur {config.email_test()}."
    else:
        msg = (f"✅ {n} candidature(s) validée(s). Elles partent une par une, aux heures de bureau "
               "(le matin d'abord : c'est là qu'il y a le plus de réponses).")
    messagerie.dire(msg)
    orchestrateur.envoyer_bientot()


def ecarter(ids):
    for i in ids:
        o = db.q1("SELECT statut FROM offres WHERE id=?", i)
        if o and "ecartee" in db.TRANSITIONS.get(o["statut"], set()):
            db.changer_statut(i, "ecartee", "concierge", "écartée par moi")


def details(i):
    o = db.q1("SELECT * FROM offres WHERE id=?", i)
    if not o:
        return messagerie.dire(f"Pas d'offre n°{i}.")
    texte, boutons = carte(o)
    messagerie.dire(f"{texte}\n{o['url']}\n\n✉️ *{o['objet_mail'] or '—'}*\n{o['corps_mail'] or ''}\n\n— Lettre —\n"
                    f"{(o['lettre'] or '(pas encore écrite)')[:2800]}",
                    boutons if o["statut"] in ("a_valider", "repetee") else None)
    if o["lettre"]:
        messagerie.fichier(chemin_lettre(i), "Lettre de motivation")
    if o["statut"] in ("a_valider", "repetee"):
        messagerie.dire("Pas parfaite ? Dis-moi ce qui ne va pas (« trop longue », « parle plus de mon mémoire »…), "
                        "je retiens pour toutes les prochaines lettres.")


def noter_style(remarque: str):
    """Tes remarques de style s'appliquent à TOUTES les lettres suivantes (apprentissage continu)."""
    f = config.PROFIL_DIR / "style.md"
    f.parent.mkdir(parents=True, exist_ok=True)
    with open(f, "a", encoding="utf-8") as fh:
        fh.write(f"- {remarque.strip()}\n")
    messagerie.dire("Noté : toutes les prochaines lettres en tiendront compte.")


def regler_budget(euros: float):
    """« budget 5 » : plafond de dépense IA par jour, enregistré dans les réglages (et appliqué tout de suite)."""
    import os
    euros = max(0.5, min(euros, 50.0))
    os.environ["BUDGET_JOUR_EUR"] = f"{euros:g}"
    try:
        from ..app import reglages
        reglages.ecrire_env(config.RACINE, {"BUDGET_JOUR_EUR": f"{euros:g}"})
    except Exception as e:
        db.journal("concierge", f"budget non enregistré dans .env : {e}")
    messagerie.dire(f"Budget IA : {euros:g} € par jour.")


def _changer_mode(mode: str):
    o = config.objectif()
    o.setdefault("envoi", {})["mode"] = mode
    config.sauver_objectif(o)
    texte = MODES[mode]
    if mode == "auto":
        texte = texte.replace("≥ 7/10", f"≥ {config.regles_envoi()['note_lettre_minimum']}/10")
    messagerie.dire(texte)
    if mode != "repetition":
        n = db.q1("SELECT COUNT(*) AS n FROM offres WHERE statut='repetee' AND date(envoye_le) >= date('now', '-14 days')")["n"]
        if n:
            messagerie.dire(f"Tu as {n} candidature(s) testée(s) ces 14 derniers jours (celles reçues par email). "
                            "Je les envoie pour de vrai aux employeurs ?",
                            [[(f"🚀 Oui, les {n}", "vrai tout"), ("Non", "aide")]])


def _vrai_tout():
    ids = [r["id"] for r in db.q("SELECT id FROM offres WHERE statut='repetee' AND date(envoye_le) >= date('now', '-14 days')")]
    if not ids:
        return messagerie.dire("Aucune candidature testée récente à envoyer.")
    db.kv_set("envoi_reel_ids", sorted(set(db.kv_get("envoi_reel_ids", [])) | set(ids)))
    for i in ids:
        db.changer_statut(i, "validee", "concierge", "envoi réel demandé")
    messagerie.dire(f"OK : {len(ids)} candidature(s) partent pour de vrai, une par une, aux heures ouvrables.")
    from .. import orchestrateur
    orchestrateur.envoyer_bientot()


def _vrai(i):
    o = db.q1("SELECT statut FROM offres WHERE id=?", i)
    if not o or o["statut"] != "repetee":
        return messagerie.dire("Cette candidature n'a pas été envoyée en test (ou est déjà partie).")
    db.kv_set("envoi_reel_ids", sorted(set(db.kv_get("envoi_reel_ids", [])) | {i}))
    db.changer_statut(i, "validee", "concierge", "envoi réel demandé")
    messagerie.dire("OK, celle-ci part pour de vrai chez l'employeur (aux heures de bureau).")
    from .. import orchestrateur
    orchestrateur.envoyer_bientot()


def _essai(url):
    from .essai import essayer
    essayer(url)


def _banc():
    """« teste-toi » / « je veux d'abord voir si ça marche » : 5 lettres d'exemple notées, rien n'est envoyé."""
    from .essai import banc_essai
    if not config.profil_texte().strip():
        return messagerie.dire("Envoie-moi d'abord ton CV en PDF : sans lui, je ne peux pas écrire de vraies lettres.")
    messagerie.dire("Je me teste sur 5 offres d'exemple (lettres + notes du Juge). Rien n'est envoyé. ~3 min, "
                    "je te préviens quand c'est prêt.")
    f = banc_essai()
    texte = f.read_text(encoding="utf-8")
    note = re.search(r"Note moyenne du Juge : ([\d.]+)/10\** · coût : ([\d.]+) €", texte)
    messagerie.dire(f"Test terminé : note moyenne {note.group(1)}/10, coût {note.group(2)} €. "
                    "Le rapport complet (les 5 lettres) est ci-dessous. Dis-moi ce qui te plaît ou pas, j'apprends.\n"
                    "Ensuite : envoie-moi « essai » + le lien d'une vraie offre.")
    messagerie.fichier(f, "Rapport du test")


def _fait(ids):
    from datetime import datetime
    for i in ids:
        o = db.q1("SELECT statut FROM offres WHERE id=?", i)
        if o and o["statut"] == "manuelle":
            db.changer_statut(i, "envoyee", "concierge", "postulé via le site",
                              envoye_le=datetime.now().isoformat(" ", "seconds"))
    messagerie.dire("Noté, je suis la réponse.")
