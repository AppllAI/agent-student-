"""Phase de test OBLIGATOIRE : rien ne part chez un employeur tant que la personne n'a pas jugé le travail.

  Test n°1 : l'agent fait tout (recherche, lettres, PDF) et envoie 3 candidatures à l'email de la PERSONNE,
             exactement comme le recruteur les recevrait.
  → Elle note sur 20 et dit ce qu'elle a aimé / moins aimé (ton, longueur, contenu).
  → Ses remarques entrent dans la mémoire du Rédacteur ; les mêmes candidatures sont réécrites = test n°2.
  Test réussi = au moins 2 tests ET une note ≥ 15/20. On lui DEMANDE alors l'autorisation ;
  si elle dit oui → envoi réel aux recruteurs (mode auto). Sinon, un test de plus, autant qu'elle veut.

L'état vit dans kv["phase_test"] : {tour, statut, ids, debut, notes, relance}.
  statut : en_cours (on envoie les tests du tour) · a_noter (on attend sa note) · attente_accord · reussi"""
from __future__ import annotations

import time

from .. import config, db, memoire, messagerie

TAILLE = 3            # candidatures par test
NOTE_MIN = 15         # sur 20
TOURS_MIN = 2         # au moins 2 tests avant d'écrire à un recruteur


def obligatoire() -> bool:
    return config.env("TEST_OBLIGATOIRE", "1") != "0"


def etat() -> dict:
    e = db.kv_get("phase_test") or {}
    return {"tour": 1, "statut": "en_cours", "ids": [], "debut": None, "notes": [], "relance": 0, **e}


def _sauver(e: dict):
    db.kv_set("phase_test", e)


def actif() -> bool:
    """Vrai tant que la phase de test n'est pas réussie (et qu'elle est obligatoire)."""
    return obligatoire() and etat()["statut"] != "reussi"


def taille() -> int:
    return config.env_int("TEST_TAILLE", TAILLE)


def peut_envoyer_test() -> bool:
    e = etat()
    return not actif() or (e["statut"] == "en_cours" and len(e["ids"]) < taille())


def resume() -> str:
    e = etat()
    if not obligatoire() or e["statut"] == "reussi":
        return "phase de test réussie : envois réels autorisés"
    notes = ", ".join(f"test {n['tour']} : {n['note']}/20" for n in e["notes"]) or "aucune note encore"
    etapes = {"en_cours": f"test n°{e['tour']} en préparation ({len(e['ids'])}/{taille()} candidatures envoyées "
                          f"sur {config.email_test()})",
              "a_noter": f"test n°{e['tour']} envoyé, j'attends ta note sur 20",
              "attente_accord": "test réussi, j'attends ton accord pour écrire aux recruteurs"}
    return f"Phase de test : {etapes[e['statut']]} · {notes} · il faut {TOURS_MIN} tests et ≥ {NOTE_MIN}/20."


def test_envoye(offre_id: int):
    """Appelé par l'Envoyeur après chaque candidature de test envoyée à la personne."""
    if not actif():
        return
    e = etat()
    if offre_id not in e["ids"]:
        e["ids"].append(offre_id)
        e["debut"] = e["debut"] or time.time()
    _sauver(e)
    if len(e["ids"]) >= taille():
        demander_note()


def demander_note():
    e = etat()
    e.update(statut="a_noter", relance=time.time())
    _sauver(e)
    n = len(e["ids"])
    messagerie.demander_note(
        f"🧪 Test n°{e['tour']} terminé : tu as reçu {n} candidature{'s' if n > 1 else ''} sur {config.email_test()}, "
        "exactement comme un recruteur les recevrait (rien n'est parti chez eux).\n"
        "Lis-les, puis donne-moi une *note sur 20* et dis-moi ce que tu as aimé et moins aimé "
        "(ton, longueur, ce qui manque…). Exemple : « note 13 : trop formel, mais j'aime l'accroche ».")


def verifier():
    """Toutes les heures : un test entamé depuis 6 h sans atteindre 3 candidatures est quand même soumis à sa note ;
    une demande de note restée sans réponse est rappelée une fois par jour."""
    if not actif():
        return
    e = etat()
    if e["statut"] == "en_cours" and e["ids"] and time.time() - (e["debut"] or time.time()) > 6 * 3600:
        demander_note()
    elif e["statut"] == "a_noter" and time.time() - (e["relance"] or 0) > 24 * 3600:
        demander_note()


def noter(note: int, commentaire: str = ""):
    e = etat()
    if not obligatoire() or e["statut"] == "reussi":
        return messagerie.dire("La phase de test est déjà réussie 👍 Tes remarques sur les lettres restent les "
                               "bienvenues (« trop long »…), je les retiens.")
    if e["statut"] == "attente_accord":
        return messagerie.dire("Ton test est déjà réussi : j'attends juste ton accord pour écrire aux recruteurs.",
                               [[("✅ Oui, envoie aux recruteurs", "autoriser envoi"), ("🔁 Encore un test", "refaire un test")]])
    if not e["ids"]:
        return messagerie.dire("Je n'ai encore rien envoyé à noter : le test n°{} est en préparation, tu le recevras "
                               "par email.".format(e["tour"]))
    note = max(0, min(20, int(note)))
    commentaire = (commentaire or "").strip(" :,.-")
    e["notes"].append({"tour": e["tour"], "note": note, "commentaire": commentaire[:1000]})
    if commentaire:          # ses remarques deviennent la mémoire du Rédacteur pour toutes les lettres suivantes
        memoire.retenir(f"Retour sur le test n°{e['tour']} ({note}/20) : {commentaire}", "style")
    if note >= NOTE_MIN and e["tour"] >= TOURS_MIN:
        e["statut"] = "attente_accord"
        _sauver(e)
        db.journal("phase_test", f"test n°{e['tour']} réussi ({note}/20)")
        return messagerie.dire(f"🎉 {note}/20 au test n°{e['tour']} : la qualité est au rendez-vous.\n"
                               "Je peux maintenant envoyer tes candidatures *directement aux recruteurs* "
                               "(et celles que tu viens de noter). Tu me donnes ton accord ?",
                               [[("✅ Oui, envoie aux recruteurs", "autoriser envoi"),
                                 ("🔁 Encore un test", "refaire un test")]])
    _sauver(e)
    debut = (f"Merci ! {note}/20, c'est un bon début. Pour être sûr, on fait un 2e test" if note >= NOTE_MIN else
             f"Merci pour ta note ({note}/20). Je réécris ces candidatures")
    nouveau_tour(reecrire=True)
    messagerie.dire(f"{debut} en tenant compte de tes remarques : tu reçois le test n°{etat()['tour']} par email "
                    "dans quelques minutes.")


def nouveau_tour(reecrire: bool):
    """Tour suivant. reecrire=True : les candidatures du tour sont réécrites avec ses remarques (elle compare)."""
    e = etat()
    if reecrire:
        for oid in e["ids"]:
            o = db.q1("SELECT statut FROM offres WHERE id=?", oid)
            if o and o["statut"] in ("repetee", "manuelle"):
                db.changer_statut(oid, "qualifiee", "phase_test", f"réécriture pour le test n°{e['tour'] + 1}",
                                  lettre=None, lettre_json=None, objet_mail=None, corps_mail=None, note_lettre=None,
                                  envoye_le=None, notifie=0)
                (config.DONNEES / "lettres" / f"lettre_{oid}.pdf").unlink(missing_ok=True)
    e.update(tour=e["tour"] + 1, statut="en_cours", ids=[], debut=None, relance=0)
    _sauver(e)
    from .. import orchestrateur
    orchestrateur.en_fond(orchestrateur.traitement)


def autoriser():
    e = etat()
    if e["statut"] != "attente_accord":
        return messagerie.dire(resume())
    e["statut"] = "reussi"
    _sauver(e)
    db.journal("phase_test", "accord donné : envois réels")
    from . import concierge
    messagerie.dire("C'est parti 🚀 Phase de test réussie : à partir de maintenant, j'écris directement aux recruteurs.")
    concierge._changer_mode("auto")


def refaire():
    e = etat()
    if e["statut"] not in ("attente_accord", "a_noter"):
        return messagerie.dire(resume())
    nouveau_tour(reecrire=False)
    messagerie.dire(f"OK, je prépare le test n°{etat()['tour']} sur de nouvelles offres. Tu le reçois par email.")
