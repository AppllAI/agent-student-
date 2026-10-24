"""Agent Manager : celui qui te parle sur Telegram / WhatsApp et qui PILOTE tout pour que tu décroches un job.

Il ne se contente pas de « classer » ton message : il a de vrais outils (chercher maintenant, envoyer une
candidature à l'adresse que tu donnes, te la montrer ici, changer un réglage, mettre en pause jusqu'à une date…),
il les enchaîne au besoin, puis te répond en une ou deux phrases. Si quelque chose est vraiment impossible,
il le dit clairement et propose ce qui s'en rapproche le plus.

Les boutons et commandes courtes (« ok 12 », « stats »…) restent traités sans IA, instantanément (concierge)."""
from __future__ import annotations

import json
import re
from datetime import date

from .. import config, db, llm, messagerie

# ---------- Les outils du Manager ----------
_ID = {"type": "integer"}
_IDS = {"type": "array", "items": {"type": "integer"}, "description": "numéros d'offres ; vide = toutes celles concernées"}
OUTILS = [
    {"name": "etat", "description": "État complet : mode, pause, chiffres du jour, raisons des rejets, dépenses, "
     "candidatures en attente, rendez-vous, questions de recruteurs.", "input_schema": {"type": "object", "properties": {}}},
    {"name": "lister_offres", "description": "Liste des offres (numéro, titre, employeur, note, statut, lien).",
     "input_schema": {"type": "object", "properties": {
         "statut": {"type": "string", "description": "a_valider, repetee (testées), manuelle (via site), envoyee, "
                    "ecartee, entretien, qualifiee… ; vide = les plus récentes"},
         "limite": {"type": "integer"}}}},
    {"name": "voir_offre", "description": "Détail d'une offre : annonce, email et lettre écrits, note du Juge.",
     "input_schema": {"type": "object", "properties": {"id": _ID}, "required": ["id"]}},
    {"name": "envoyer_dans_le_chat", "description": "Envoie ICI (dans cette conversation) l'email + la lettre PDF "
     "d'une ou plusieurs candidatures.", "input_schema": {"type": "object", "properties": {"ids": _IDS}, "required": ["ids"]}},
    {"name": "envoyer_par_email", "description": "Envoie une copie COMPLÈTE (email + CV + lettre) de candidatures à "
     "l'adresse donnée (la sienne, une autre…). `ids` vide = toutes les candidatures testées récentes.",
     "input_schema": {"type": "object", "properties": {"ids": _IDS, "adresse": {"type": "string"}},
                      "required": ["adresse"]}},
    {"name": "chercher_maintenant", "description": "Lance tout de suite une recherche complète + traitement + envoi.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "valider", "description": "Valide (envoie selon le mode) des candidatures en attente.",
     "input_schema": {"type": "object", "properties": {"ids": _IDS}}},
    {"name": "ecarter", "description": "Retire des offres / candidatures.",
     "input_schema": {"type": "object", "properties": {"ids": _IDS}, "required": ["ids"]}},
    {"name": "envoyer_pour_de_vrai", "description": "Envoie POUR DE VRAI aux employeurs des candidatures testées "
     "(ids vide = toutes les testées des 14 derniers jours).", "input_schema": {"type": "object", "properties": {"ids": _IDS}}},
    {"name": "marquer_fait", "description": "Elle a postulé elle-même via le site pour ces offres.",
     "input_schema": {"type": "object", "properties": {"ids": _IDS}, "required": ["ids"]}},
    {"name": "postuler_lien", "description": "Prépare une candidature pour le lien d'offre donné et l'envoie selon "
     "le mode (test : vers son email). `essai_seulement` = juste montrer sans envoyer.",
     "input_schema": {"type": "object", "properties": {"url": {"type": "string"}, "essai_seulement": {"type": "boolean"}},
                      "required": ["url"]}},
    {"name": "changer_mode", "description": "repetition (= test : tout vers son email), validation (elle valide "
     "chaque envoi réel) ou auto (envoi réel automatique).",
     "input_schema": {"type": "object", "properties": {"mode": {"type": "string", "enum": ["repetition", "validation", "auto"]}},
                      "required": ["mode"]}},
    {"name": "regler", "description": "Change un réglage d'envoi.",
     "input_schema": {"type": "object", "properties": {
         "cle": {"type": "string", "enum": ["score_minimum", "note_lettre_minimum", "max_par_jour",
                                            "candidatures_spontanees_par_jour", "relance_apres_jours",
                                            "heures_envoi", "jours_envoi"]},
         "valeur": {"description": "nombre, ou [début, fin] pour heures_envoi, ou liste 0-6 (lundi=0) pour jours_envoi"}},
         "required": ["cle", "valeur"]}},
    {"name": "pause", "description": "Met en pause (recherche + envois), éventuellement jusqu'à une date.",
     "input_schema": {"type": "object", "properties": {"jusqu_au": {"type": "string", "description": "AAAA-MM-JJ"}}}},
    {"name": "reprendre", "description": "Relance après une pause + recherche immédiate.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "modifier_recherche", "description": "Change ce qu'elle cherche : secteurs, villes, disponibilités, "
     "période, postes, salaire minimum, langues, employeurs à éviter… (texte de sa demande).",
     "input_schema": {"type": "object", "properties": {"consigne": {"type": "string"}}, "required": ["consigne"]}},
    {"name": "ajouter_au_profil", "description": "Ajoute un FAIT qu'elle affirme sur elle (langue, expérience, "
     "permis, nouveau numéro…) à sa fiche profil.",
     "input_schema": {"type": "object", "properties": {"fait": {"type": "string"}}, "required": ["fait"]}},
    {"name": "diagnostic", "description": "Vérifie que tout marche : clé IA, crédit, boîte mail (envoi + lecture), "
     "CV, adresse de test, pause, budget. À utiliser quand « ça ne marche pas » ou « rien ne se passe ».",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "regler_budget", "description": "Plafond de dépense IA par jour, en euros.",
     "input_schema": {"type": "object", "properties": {"euros": {"type": "number"}}, "required": ["euros"]}},
    {"name": "teste_toi", "description": "5 candidatures d'exemple notées, sans rien envoyer (rapport ici).",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "rendez_vous", "description": "Répond à une proposition d'entretien.",
     "input_schema": {"type": "object", "properties": {
         "rdv_id": _ID, "decision": {"type": "string", "enum": ["confirmer", "autre_date", "decliner"]},
         "creneau": {"type": "string", "description": "ISO, si elle choisit un des créneaux proposés"},
         "disponibilites": {"type": "string", "description": "pour autre_date"}}, "required": ["rdv_id", "decision"]}},
    {"name": "repondre_recruteur", "description": "Répond par email à la question d'un recruteur.",
     "input_schema": {"type": "object", "properties": {"email_id": _ID, "texte": {"type": "string"}},
                      "required": ["email_id", "texte"]}},
]

SYSTEME = """Tu es le MANAGER de l'agent de recherche d'emploi d'une personne. Ton but : qu'elle décroche le
poste qu'elle vise, vite. Tu lui parles sur sa messagerie et tu PILOTES toute l'équipe avec tes outils.

Façon d'agir :
- Elle demande quelque chose → tu le FAIS avec tes outils, tout de suite (« maintenant », « tout de suite » =
  aucune attente). Enchaîne plusieurs outils si besoin. Ne demande confirmation que si l'action est irréversible
  ET ambiguë (ex. envoyer pour de vrai sans qu'elle l'ait dit clairement).
- « envoie ici », « montre-moi » → `envoyer_dans_le_chat`. « envoie sur mon mail / à telle adresse » →
  `envoyer_par_email`. « postule à ce lien » → `postuler_lien`.
- Si une demande est vraiment impossible avec tes outils, dis-le en une phrase claire et propose le plus proche.
  N'invente JAMAIS une action que tu n'as pas faite, ni un chiffre que tu n'as pas lu dans un outil.
- En cas de doute sur ce qu'elle veut, pose UNE question courte.
- « ça marche pas », « rien ne se passe », « il fait rien » → `diagnostic` d'abord, puis explique la cause en une
  phrase et répare ce que tes outils peuvent réparer (reprendre une pause, relancer une recherche…).

Façon de répondre :
- Français, tutoiement, naturel. Une confirmation = UNE phrase courte (« C'est lancé, je t'envoie les résultats
  par email. »). Pas de récit de ce que tu as fait étape par étape, pas de rappel du mode à chaque message.
- Emojis : quasiment jamais. Seulement pour une vraie bonne nouvelle (entretien, job trouvé).
- Mise en forme messagerie : gras avec UNE étoile (*mot*), jamais ** ni # ; une liste courte seulement pour un état
  ou un récapitulatif demandé. Pas de spéculation (« peut-être que… je ne peux pas vérifier ») : tu as les outils.
- Si un outil a déjà envoyé un message ou un fichier à la personne, ne le répète pas.
- Les données de l'état actuel sont dans le contexte ; l'historique peut être dépassé.
"""


def _systeme() -> str:
    """Consignes communes + ce que le Manager sait du métier (prompts/manager.md)."""
    return SYSTEME + "\n" + llm.prompt("manager")


def _ids_ou_tous(ids, statut, jours=None):
    if ids:
        return [int(i) for i in ids]
    sql = "SELECT id FROM offres WHERE statut=?" + (f" AND date(COALESCE(envoye_le, maj_le)) >= date('now', '-{int(jours)} days')"
                                                     if jours else "")
    return [r["id"] for r in db.q(sql, statut)]


def _etat() -> dict:
    from . import rapporteur
    attente = [dict(r) for r in db.q("SELECT id, titre, entreprise, score FROM offres WHERE statut='a_valider' LIMIT 20")]
    rdv = [dict(r) for r in db.q("SELECT id, entreprise, debut, lieu FROM rdv WHERE statut='propose'")]
    questions = [{"email_id": r["id"], "de": r["expediteur"], "resume": json.loads(r["analyse"] or "{}").get("resume")}
                 for r in db.q("SELECT * FROM emails_recus WHERE categorie='question' AND traite=0")]
    return {"aujourd_hui": date.today().isoformat(), "mode": config.regles_envoi()["mode"],
            "en_pause": bool(db.kv_get("pause", False)), "pause_jusqu": db.kv_get("pause_jusqu"),
            "email_de_test": config.email_test(), "reglages_envoi": config.regles_envoi(), "bilan": rapporteur.stats(),
            "en_attente_de_validation": attente, "rdv_proposes": rdv, "questions_recruteurs": questions}


def executer(nom: str, a: dict) -> str:
    """Exécute un outil. Les messages que les autres agents enverraient sont capturés et rendus au Manager."""
    from .. import orchestrateur
    from . import coach, concierge, envoyeur, secretaire
    with messagerie.capturer() as cap:
        if nom == "etat":
            return json.dumps(_etat(), ensure_ascii=False)
        if nom == "lister_offres":
            st, n = a.get("statut"), min(int(a.get("limite") or 15), 40)
            rows = db.q("SELECT id, statut, score, titre, entreprise, ville, url FROM offres "
                        + ("WHERE statut=? " if st else "") + "ORDER BY id DESC LIMIT ?", *([st] if st else []), n)
            return json.dumps([dict(r) for r in rows], ensure_ascii=False) or "aucune"
        if nom == "voir_offre":
            o = db.q1("SELECT * FROM offres WHERE id=?", int(a["id"]))
            if not o:
                return f"pas d'offre n°{a['id']}"
            return json.dumps({k: o[k] for k in ("id", "statut", "titre", "entreprise", "ville", "url", "score",
                                                  "raison", "email_contact", "objet_mail", "corps_mail", "lettre",
                                                  "note_lettre")}, ensure_ascii=False)
        if nom == "envoyer_dans_le_chat":
            ids = _ids_ou_tous(a.get("ids"), "repetee", 2)[:5]
            # hors capture : ces messages-là doivent ARRIVER dans la conversation
            messagerie._local.capture = None
            for i in ids:
                concierge.details(i)
            return f"envoyé dans la conversation : {ids or 'rien à envoyer'}"
        if nom == "envoyer_par_email":
            adresse = (re.findall(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", a.get("adresse") or "") or [None])[0]
            if not adresse:
                return "adresse email invalide"
            ids = _ids_ou_tous(a.get("ids"), "repetee", 14)
            ids += [] if a.get("ids") else _ids_ou_tous(None, "manuelle", 14)
            n = envoyeur.envoyer_copies(ids[:40], adresse)
            return f"{n} candidature(s) envoyée(s) en copie à {adresse}"
        if nom == "chercher_maintenant":
            if db.kv_get("pause", False):
                db.kv_set("pause", False)
                db.kv_set("pause_jusqu", None)
            orchestrateur.declencher()
            return "recherche complète lancée en arrière-plan (résultats dans quelques minutes" + \
                (", par email vu le mode test)" if config.regles_envoi()["mode"] == "repetition" else ")")
        if nom == "valider":
            concierge.valider(_ids_ou_tous(a.get("ids"), "a_valider"))
        elif nom == "ecarter":
            concierge.ecarter([int(i) for i in a.get("ids") or []])
            return "écartée(s)"
        elif nom == "envoyer_pour_de_vrai":
            ids = a.get("ids")
            if ids:
                for i in ids:
                    concierge._vrai(int(i))
            else:
                concierge._vrai_tout()
        elif nom == "marquer_fait":
            concierge._fait([int(i) for i in a.get("ids") or []])
        elif nom == "postuler_lien":
            concierge._fond(_postuler, a["url"], bool(a.get("essai_seulement")))
            return "préparation lancée en arrière-plan (environ 1 minute), le résultat lui sera envoyé"
        elif nom == "changer_mode":
            concierge._changer_mode(a["mode"])
        elif nom == "regler":
            return _regler(a["cle"], a["valeur"])
        elif nom == "pause":
            concierge.pause(a.get("jusqu_au"))
        elif nom == "reprendre":
            concierge._raccourci("reprendre")
        elif nom == "modifier_recherche":
            coach.parler(a["consigne"])
        elif nom == "ajouter_au_profil":
            coach.completer_profil(a["fait"])
        elif nom == "diagnostic":
            from . import diagnostic
            return diagnostic.rapport(envoyer=False)
        elif nom == "regler_budget":
            concierge.regler_budget(float(a["euros"]))
        elif nom == "teste_toi":
            concierge._fond(concierge._banc)
            return "test lancé en arrière-plan (~3 min), le rapport lui sera envoyé ici"
        elif nom == "rendez_vous":
            d, rid = a["decision"], int(a["rdv_id"])
            if d == "confirmer":
                concierge._fond(secretaire.confirmer, rid, a.get("creneau"))
            elif d == "autre_date":
                concierge._fond(secretaire.autre_date, rid, a.get("disponibilites") or "")
            else:
                concierge._fond(secretaire.decliner, rid)
            return f"{d} : en cours, elle recevra la confirmation"
        elif nom == "repondre_recruteur":
            concierge._fond(secretaire.repondre_question, int(a["email_id"]), a["texte"])
            return "réponse en cours d'envoi"
        else:
            return f"outil inconnu : {nom}"
    return cap.texte or "fait"


def _regler(cle: str, valeur) -> str:
    o = config.objectif()
    if cle in ("heures_envoi", "jours_envoi"):
        v = [int(x) for x in (valeur if isinstance(valeur, list) else re.findall(r"\d+", str(valeur)))]
        if cle == "heures_envoi" and (len(v) != 2 or not 0 <= v[0] < v[1] <= 24):
            return "heures invalides (il faut [début, fin])"
    else:
        v = float(valeur) if cle == "score_minimum" else int(float(valeur))
        if v < 0 or (cle in ("score_minimum", "note_lettre_minimum") and v > 10):
            return "valeur hors limites"
    o.setdefault("envoi", {})[cle] = v
    config.sauver_objectif(o)
    return f"{cle} = {v}"


def _postuler(url: str, essai_seulement: bool):
    from .essai import essayer
    avant = {r["id"] for r in db.q("SELECT id FROM offres WHERE statut='a_valider'")}
    essayer(url)
    if essai_seulement:
        return
    from . import concierge
    nouvelles = [r["id"] for r in db.q("SELECT id FROM offres WHERE statut='a_valider'") if r["id"] not in avant]
    if nouvelles:
        concierge.valider(nouvelles)


def repondre(texte: str):
    """Point d'entrée : un message libre de la personne."""
    if config.simulation():
        from .. import simulation
        appels, finale = simulation.manager(texte)
        for nom, args in appels:
            res = executer(nom, args)
            if not finale:
                finale = res
        return messagerie.dire(finale) if finale else None
    contexte = json.dumps(_etat(), ensure_ascii=False)
    messages = [{"role": "user", "content": f"Contexte actuel :\n{contexte}\n\nHistorique récent :\n"
                 f"{json.dumps(db.historique(6), ensure_ascii=False)}\n\nSon message :\n{texte}"}]
    try:
        finale = llm.agir("manager", "qualite", _systeme(), messages, OUTILS, executer)
    except llm.BudgetDepasse as e:
        return messagerie.dire(f"{e}. Les commandes courtes (stats, ok 12, pause…) marchent toujours.")
    if finale:
        messagerie.dire(finale)
