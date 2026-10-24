"""Agents Rédacteur & Juge (boucle « écrire → noter → corriger », motif evaluator-optimizer d'Anthropic).

  Rédacteur (modèle qualité) : écrit la lettre (en blocs : expéditeur, destinataire, objet, paragraphes…)
     + l'email d'accompagnement, nourri par
     – le profil (seule source de vérité),
     – les liens « pourquoi toi » trouvés par l'Évaluateur, les faits trouvés par l'Enquêteur sur le site officiel,
     – la mémoire : tes remarques de style et préférences (« plus court », « moins formel »…) et 2 lettres que tu as
       validées toi-même (il apprend ton goût).
  Juge (Sonnet sans réflexion, regard neuf) : liste chaque fait de la lettre avec sa preuve citée du profil ;
     le CODE vérifie chaque citation et chaque chiffre/durée/niveau de langue → zéro invention qui passe.
  Jusqu'à 2 versions ; on garde la meilleure. Sous la note minimale → elle t'est montrée avec un ⚠️.
  La lettre devient un PDF mis en page (agent_student/pdf.py) : c'est lui qui part en pièce jointe."""
from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date

from .. import config, db, llm, memoire, messagerie, pdf

_LIGNES = {"type": "array", "items": {"type": "string"}}
SCHEMA_LETTRE = {
    "type": "object",
    "properties": {
        "expediteur": {**_LIGNES, "description": "Ses coordonnées, une par ligne, UNIQUEMENT celles du profil : "
                                                 "Prénom Nom, adresse ou ville, téléphone, email"},
        "destinataire": {**_LIGNES, "description": "Employeur, puis la personne si elle est connue, puis l'adresse "
                                                   "seulement si elle figure dans l'offre ou les faits fournis"},
        "lieu_date": {"type": "string", "description": "« Bruxelles, le 24 octobre 2026 » (sa ville, date du jour)"},
        "objet_lettre": {"type": "string", "description": "Objet de la lettre, sans le mot « Objet : »"},
        "appel": {"type": "string", "description": "« Madame, Monsieur, » ou « Madame X, »"},
        "paragraphes": {**_LIGNES, "description": "Le corps de la lettre : 3 petits paragraphes"},
        "politesse": {"type": "string", "description": "La formule de politesse finale"},
        "signature": {"type": "string", "description": "Prénom Nom"},
        "objet": {"type": "string", "description": "Objet de l'EMAIL"},
        "email": {"type": "string", "description": "L'email de candidature (lu en premier), CV et lettre en PDF joints"},
    },
    "required": ["expediteur", "lieu_date", "objet_lettre", "appel", "paragraphes", "politesse", "signature",
                 "objet", "email"],
}
SCHEMA_JUGE = {
    "type": "object",
    "properties": {
        "affirmations": {"type": "array", "description": "Chaque fait sur la personne affirmé dans la lettre ou l'email",
                         "items": {"type": "object", "properties": {
                             "extrait_lettre": {"type": "string"},
                             "preuve_profil": {"type": ["string", "null"],
                                               "description": "Citation EXACTE, copiée du profil, qui prouve ce fait. null si absente."}},
                             "required": ["extrait_lettre", "preuve_profil"]}},
        "note": {"type": "integer", "minimum": 1, "maximum": 10}, "invention": {"type": "boolean"},
        "corrections": {"type": "array", "items": {"type": "string"}}},
    "required": ["affirmations", "note", "invention", "corrections"],
}
MOIS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre",
        "novembre", "décembre"]


def _offre(o) -> dict:
    infos = json.loads(o["infos_cabinet"] or "{}")
    return {"source": o["source"], "poste": o["titre"], "employeur": o["entreprise"], "ville": o["ville"],
            "secteur": o["secteur"], "contrat": o["contrat"], "langue": o["langue"],
            "horaires": o["horaires"],
            "description": (o["description"] or "")[:3000], "pourquoi": json.loads(o["pourquoi"] or "[]"),
            "faits_employeur": infos.get("faits", []), "destinataire": infos.get("destinataire")}


def lettres_aimees(n: int = 2) -> list[str]:
    """Les dernières lettres que tu as validées toi-même : le Rédacteur s'aligne sur ton goût."""
    return [r["lettre"][:1400] for r in db.q(
        "SELECT lettre FROM offres WHERE lettre IS NOT NULL AND id IN "
        "(SELECT offre_id FROM evenements WHERE agent='concierge' AND quoi='→ validee') ORDER BY id DESC LIMIT ?", n)]


def ecrire_pdf(lettre: dict | str, chemin):
    """La lettre (en blocs, ou ancienne lettre en texte) en PDF mis en page, sur une page."""
    return pdf.lettre(lettre, chemin)


def _structure(r: dict) -> dict:
    cles = ("expediteur", "destinataire", "lieu_date", "objet_lettre", "appel", "paragraphes", "politesse", "signature")
    l = {k: r.get(k) for k in cles}
    for k in ("expediteur", "destinataire", "paragraphes"):
        v = l.get(k) or []
        l[k] = [x.strip() for x in (v.split("\n") if isinstance(v, str) else v) if x and x.strip()]
    return l


def ecrire_une(o) -> dict:
    """Écrit, fait juger, corrige. Renvoie la meilleure version
    {lettre (texte), structure (blocs pour le PDF), objet, email, note, corrections}."""
    regles = config.regles_envoi()
    profil = config.profil_texte()
    systeme_r = llm.prompt("redacteur") + "\n\n## Profil (seule source de vérité)\n" + profil
    systeme_j = llm.prompt("verificateur") + "\n\n## Profil\n" + profil
    aujourdhui = f"{date.today().day} {MOIS[date.today().month - 1]} {date.today().year}"
    demande = {"offre": _offre(o), "date_du_jour": aujourdhui, "notes_style": memoire.notes_style() or None,
               "lettres_aimees": lettres_aimees()}
    meilleure = None
    for _ in range(2):          # 1 réécriture max : au-delà, la qualité ne monte plus et le coût double
        brut = llm.demander("redacteur", "qualite", systeme_r, json.dumps(demande, ensure_ascii=False),
                            SCHEMA_LETTRE, max_tokens=4000, reflexion=False)
        structure = _structure(brut)
        corps = " ".join(structure["paragraphes"])
        if len(corps.split()) < 90 or not brut.get("email") or not brut.get("objet"):
            raise ValueError("lettre vide ou incomplète renvoyée par l'IA")
        r = {"lettre": pdf.texte_lettre(structure), "objet": brut["objet"].strip(), "email": brut["email"].strip()}
        # Juge = Sonnet sans réflexion : testé contre la vraie API, il attrape TOUTES les inventions glissées
        # (Haiku en ratait) et ne sous-note pas les bonnes lettres → moins de réécritures inutiles.
        j = llm.demander("juge", "qualite", systeme_j,
                         json.dumps({"offre": demande["offre"], "candidature": r}, ensure_ascii=False),
                         SCHEMA_JUGE, max_tokens=3000, reflexion=False)
        note = 1 if j.get("invention") else int(j.get("note") or 1)
        from .controle import chiffres_inventes, preuve_valide
        # 1) Chaque fait doit être prouvé par une citation du profil, vérifiée PAR LE CODE (pas par l'IA)
        sans_preuve = [a.get("extrait_lettre", "") for a in j.get("affirmations") or []
                       if isinstance(a, dict) and not preuve_valide(a.get("preuve_profil"), profil)]
        # 2) Chaque chiffre / durée doit exister dans le profil ou l'offre
        suspects = chiffres_inventes(f"{r.get('lettre', '')}\n{r.get('email', '')}",
                                     f"{profil}\n{json.dumps(demande['offre'], ensure_ascii=False)}\n{aujourdhui}")
        alertes = [f"Retire ou reformule « {x[:120]} » : rien dans le profil ne le prouve." for x in sans_preuve if x] + \
                  [f"Retire ou corrige « {s} » : ce chiffre/cette durée n'est ni dans le profil ni dans l'offre."
                   for s in suspects]
        if alertes:
            note = min(note, 4)
            j["corrections"] = alertes[:4] + list(j.get("corrections") or [])[:1]
        if meilleure is None or note > meilleure["note"]:
            meilleure = {**r, "structure": structure, "note": note, "corrections": j.get("corrections") or []}
        if note >= regles["note_lettre_minimum"]:
            break
        demande["corrections_a_appliquer"] = j.get("corrections") or ["Rends la lettre plus spécifique à cet employeur."]
        demande["version_precedente"] = brut
    return meilleure


def rediger(limite: int = 12):
    if not config.profil_texte().strip():
        db.journal("redacteur", "profil vide : en attente du CV")
        return
    regles = config.regles_envoi()
    dossier = config.DONNEES / "lettres"
    dossier.mkdir(parents=True, exist_ok=True)
    # les offres les plus fraîches d'abord : postuler dans les 4 premiers jours multiplie les chances
    offres = db.q("SELECT * FROM offres WHERE statut='qualifiee' AND infos_cabinet IS NOT NULL "
                  "ORDER BY COALESCE(publie_le, date(cree_le)) DESC, score DESC LIMIT ?", limite)
    budget_epuise = threading.Event()

    def traiter(o):
        if budget_epuise.is_set():
            return
        try:
            v = ecrire_une(o)
        except llm.BudgetDepasse:
            budget_epuise.set()
            return
        except Exception as e:
            essais = (o["tentatives"] or 0) + 1
            db.maj(o["id"], tentatives=essais)
            db.journal("redacteur", f"échec {essais}/3 : {type(e).__name__}: {e}", o["id"])
            if essais >= 3:   # on arrête de payer pour une offre qui plante
                db.changer_statut(o["id"], "erreur", "redacteur", f"lettre impossible : {e}")
                messagerie.signaler(f"⚠️ Je n'arrive pas à écrire la lettre pour *{o['entreprise'] or o['titre']}* "
                                f"({type(e).__name__}). Je l'ai mise de côté.")
            return
        ecrire_pdf(v["structure"], dossier / f"lettre_{o['id']}.pdf")
        bonne = v["note"] >= regles["note_lettre_minimum"]
        db.changer_statut(o["id"], "redigee", "redacteur",
                          None if bonne else "à relire : " + "; ".join(v["corrections"])[:300],
                          lettre=v["lettre"], objet_mail=v["objet"], corps_mail=v["email"], note_lettre=v["note"],
                          lettre_json=json.dumps(v["structure"], ensure_ascii=False))
        # Mode test : tout part tout seul vers ta boîte (même les lettres moyennes, avec leur note) pour que tu juges
        auto = (regles["mode"] == "auto" and bonne) or regles["mode"] == "repetition"
        db.changer_statut(o["id"], "validee" if auto else "a_valider", "redacteur")

    # 4 lettres en même temps : 4× plus rapide, même coût
    with ThreadPoolExecutor(4) as ex:
        list(ex.map(traiter, offres))


def chemin_lettre(offre_id: int):
    """Le PDF de la lettre. Absent (lettre écrite par une ancienne version, en .txt) : il est refait à partir
    de la lettre enregistrée — une pièce jointe est TOUJOURS un PDF."""
    chemin = config.DONNEES / "lettres" / f"lettre_{offre_id}.pdf"
    if not chemin.exists():
        o = db.q1("SELECT lettre, lettre_json FROM offres WHERE id=?", offre_id)
        if o and (o["lettre_json"] or o["lettre"]):
            ecrire_pdf(json.loads(o["lettre_json"]) if o["lettre_json"] else o["lettre"], chemin)
    return chemin


def carte(o) -> tuple[str, list]:
    """Une candidature prête, présentée en 5 lignes avec ses boutons."""
    pourquoi = json.loads(o["pourquoi"] or "[]")
    alerte = "\n⚠️ À relire : " + o["raison"][10:160] if (o["raison"] or "").startswith("à relire") else ""
    via = "📧 par email" if o["email_contact"] else "🌐 via leur site (je te prépare tout)"
    spont = "✨ Spontanée · " if o["source"] == "spontanee" else ""
    frais = f" · publiée le {o['publie_le']}" if o["publie_le"] else ""
    texte = (f"*{o['id']}. {o['titre']}*\n{spont}{o['entreprise'] or '?'} — {o['ville'] or '?'}{frais}\n"
             f"Adéquation {o['score']}/10 · lettre {o['note_lettre'] or '?'}/10 · {via}\n"
             + "".join(f"• {p}\n" for p in pourquoi[:2]) + alerte)
    boutons = [[("✅ Envoyer", f"ok {o['id']}"), ("👁 Lettre", f"montre {o['id']}"), ("❌ Non", f"ecarte {o['id']}")]]
    return texte.strip(), boutons


def proposer_lot():
    """Chaque nouvelle candidature prête t'arrive en carte, avec ses boutons (une seule fois chacune)."""
    lot = db.q("SELECT * FROM offres WHERE statut='a_valider' AND notifie=0 ORDER BY score DESC, note_lettre DESC")
    if not lot:
        return
    if len(lot) <= 5:
        messagerie.dire(f"{len(lot)} candidature(s) prête(s) :")
        for o in lot:
            messagerie.dire(*carte(o))
        if len(lot) > 1:
            messagerie.dire("Ou d'un coup :", [[("✅ Tout envoyer", "ok tout")]])
    else:                       # beaucoup d'un coup : une liste compacte au lieu de 40 messages
        for i in range(0, len(lot), 25):
            part = lot[i:i + 25]
            lignes = [f"*{o['id']}.* {o['titre'][:45]} — {(o['entreprise'] or '?')[:30]} · {o['score']}/10"
                      + ("" if o["email_contact"] else " 🌐") for o in part]
            messagerie.dire(f"{len(lot)} candidatures prêtes" + (" (suite)" if i else "") + " :\n"
                            + "\n".join(lignes))
        messagerie.dire("« montre 12 » pour en lire une · « ok sauf 3 7 » pour retirer · 🌐 = via leur site",
                        [[("✅ Tout envoyer", "ok tout")]])
    with db.tx() as c:
        c.executemany("UPDATE offres SET notifie=1 WHERE id=?", [(o["id"],) for o in lot])
