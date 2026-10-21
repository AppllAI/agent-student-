"""Agents Rédacteur & Juge (boucle « écrire → noter → corriger », motif evaluator-optimizer d'Anthropic).

  Rédacteur (modèle qualité) : écrit la lettre + l'email, nourri par
     – le profil (seule source de vérité),
     – les liens « pourquoi toi » trouvés par l'Évaluateur, les faits trouvés par l'Enquêteur,
     – tes notes de style (« plus court », « moins formel »…) et 2 lettres que tu as validées (il apprend ton goût).
  Juge (Sonnet sans réflexion, regard neuf) : liste chaque fait de la lettre avec sa preuve citée du profil ;
     le CODE vérifie chaque citation et chaque chiffre/durée/niveau de langue → zéro invention qui passe.
  Jusqu'à 3 versions ; on garde la meilleure. Sous la note minimale → elle t'est montrée avec un ⚠️."""
from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date

from .. import config, db, llm, messagerie

SCHEMA_LETTRE = {
    "type": "object",
    "properties": {"lettre": {"type": "string"}, "objet": {"type": "string"}, "email": {"type": "string"}},
    "required": ["lettre", "objet", "email"],
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


def ecrire_pdf(texte: str, chemin):
    """Lettre en PDF propre : A4, marges 25 mm, paragraphes justifiés et aérés, police lisible."""
    try:
        from fpdf import FPDF
    except ImportError:  # sans fpdf2 on garde un .txt lisible
        chemin.with_suffix(".txt").write_text(texte, encoding="utf-8")
        return chemin.with_suffix(".txt")
    pdf = FPDF(format="A4")
    pdf.set_margins(25, 22, 25)
    pdf.set_auto_page_break(True, margin=20)
    pdf.add_page()
    police = "Helvetica"
    for f in ("C:/Windows/Fonts/arial.ttf", "/System/Library/Fonts/Supplemental/Arial.ttf",
              "/Library/Fonts/Arial.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
        try:
            pdf.add_font("Texte", "", f)
            police = "Texte"
            break
        except Exception:
            continue
    pdf.set_font(police, size=10.5)
    if police == "Helvetica":
        texte = texte.encode("latin-1", "replace").decode("latin-1")
    blocs = [b.strip("\n") for b in texte.replace("\r", "").split("\n\n")]
    for b in blocs:
        if not b.strip():
            continue
        lignes = b.split("\n")
        court = all(len(l) < 60 for l in lignes)          # coordonnées, date, signature : alignés à gauche
        puces = any(l.lstrip().startswith(("- ", "• ", "– ")) for l in lignes)
        if puces:
            for l in lignes:
                pdf.multi_cell(0, 5.4, l.strip(), align="J", new_x="LMARGIN", new_y="NEXT")
        elif court:
            for l in lignes:
                pdf.cell(0, 5.2, l.strip(), new_x="LMARGIN", new_y="NEXT")
        else:
            pdf.multi_cell(0, 5.4, " ".join(l.strip() for l in lignes), align="J", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(3.2)
    pdf.output(str(chemin))
    return chemin


def ecrire_une(o) -> dict:
    """Écrit, fait juger, corrige. Renvoie la meilleure version {lettre, objet, email, note, corrections}."""
    regles = config.regles_envoi()
    profil = config.profil_texte()
    systeme_r = llm.prompt("redacteur") + "\n\n## Profil (seule source de vérité)\n" + profil
    systeme_j = llm.prompt("verificateur") + "\n\n## Profil\n" + profil
    aujourdhui = f"{date.today().day} {MOIS[date.today().month - 1]} {date.today().year}"
    demande = {"offre": _offre(o), "date_du_jour": aujourdhui, "notes_style": config.style_texte() or None,
               "lettres_aimees": lettres_aimees()}
    meilleure = None
    for _ in range(2):          # 1 réécriture max : au-delà, la qualité ne monte plus et le coût double
        r = llm.demander("redacteur", "qualite", systeme_r, json.dumps(demande, ensure_ascii=False),
                         SCHEMA_LETTRE, max_tokens=8000)
        if len((r.get("lettre") or "").split()) < 90 or not r.get("email") or not r.get("objet"):
            raise ValueError("lettre vide ou incomplète renvoyée par l'IA")
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
            meilleure = {**r, "note": note, "corrections": j.get("corrections") or []}
        if note >= regles["note_lettre_minimum"]:
            break
        demande["corrections_a_appliquer"] = j.get("corrections") or ["Rends la lettre plus spécifique à cet employeur."]
        demande["version_precedente"] = r
    return meilleure


def rediger(limite: int = 8):
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
                messagerie.dire(f"⚠️ Je n'arrive pas à écrire la lettre pour *{o['entreprise'] or o['titre']}* "
                                f"({type(e).__name__}). Je l'ai mise de côté.")
            return
        ecrire_pdf(v["lettre"], dossier / f"lettre_{o['id']}.pdf")
        bonne = v["note"] >= regles["note_lettre_minimum"]
        db.changer_statut(o["id"], "redigee", "redacteur",
                          None if bonne else "à relire : " + "; ".join(v["corrections"])[:300],
                          lettre=v["lettre"], objet_mail=v["objet"], corps_mail=v["email"], note_lettre=v["note"])
        auto = regles["mode"] == "auto" and bonne
        db.changer_statut(o["id"], "validee" if auto else "a_valider", "redacteur")

    # 3 lettres en même temps : 3× plus rapide, même coût
    with ThreadPoolExecutor(3) as ex:
        list(ex.map(traiter, offres))


def chemin_lettre(offre_id: int):
    base = config.DONNEES / "lettres" / f"lettre_{offre_id}"
    return base.with_suffix(".pdf") if base.with_suffix(".pdf").exists() else base.with_suffix(".txt")


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
    messagerie.dire(f"🔎 {len(lot)} candidature(s) prête(s) :")
    for o in lot:
        messagerie.dire(*carte(o))
    if len(lot) > 1:
        messagerie.dire("Ou d'un coup :", [[("✅ Tout envoyer", "ok tout")]])
    with db.tx() as c:
        c.executemany("UPDATE offres SET notifie=1 WHERE id=?", [(o["id"],) for o in lot])
