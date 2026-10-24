"""Agent 12 — Secrétaire : gère les rendez-vous et les réponses aux recruteurs.

  1. Un recruteur propose un entretien → elle vérifie tes conflits d'agenda et te demande sur ta messagerie.
  2. Tu réponds « oui » / « plutôt jeudi » / « non » → elle écrit et envoie l'email de réponse dans le même fil,
     puis t'envoie le rendez-vous en fichier .ics (un tap = ajouté à ton calendrier)."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

from .. import config, db, llm, messagerie
from .envoyeur import expedier, expediteur

JOURS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
MOIS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre",
        "novembre", "décembre"]
SCHEMA = {"type": "object", "properties": {"email": {"type": "string"}}, "required": ["email"]}


def joli(iso: str | None) -> str:
    if not iso:
        return "date à préciser"
    try:
        d = datetime.fromisoformat(iso)
    except ValueError:
        return iso
    h = f"{d.hour}h{d.minute:02d}" if d.minute else f"{d.hour}h"
    return f"{JOURS[d.weekday()]} {d.day} {MOIS[d.month - 1]} à {h}"


def conflits(iso: str | None) -> list:
    if not iso:
        return []
    try:
        d = datetime.fromisoformat(iso)
    except ValueError:
        return []
    return [r for r in db.q("SELECT * FROM rdv WHERE statut='confirme' AND debut IS NOT NULL")
            if abs(datetime.fromisoformat(r["debut"]) - d) < timedelta(hours=2)]


def proposer(email_id: int, offre_id: int | None, analyse: dict):
    employeur = analyse.get("employeur") or (db.q1("SELECT entreprise FROM offres WHERE id=?", offre_id) or
                                             {"entreprise": "Un recruteur"})["entreprise"]
    creneaux = analyse.get("creneaux") or [None]
    lignes, choix = [], []
    with db.tx() as c:
        for iso in creneaux:
            rid = c.execute("INSERT INTO rdv (offre_id, email_id, entreprise, debut, lieu) VALUES (?,?,?,?,?)",
                            (offre_id, email_id, employeur, iso, analyse.get("lieu"))).lastrowid
            alerte = " ⚠️ conflit avec un autre rdv" if conflits(iso) else ""
            lignes.append(f"• {joli(iso)}{alerte}")
            if iso:
                choix.append((f"✅ {joli(iso).split(' à ')[0].split()[0].capitalize()} {joli(iso).split(' à ')[-1]}",
                              f"rdv {rid} oui"))
    premier = rid if len(creneaux) == 1 else rid - len(creneaux) + 1
    boutons = [choix[i:i + 2] for i in range(0, len(choix), 2)] + \
              [[("📆 Autre date", f"rdv {premier} autre"), ("❌ Décliner", f"rdv {premier} non")]]
    messagerie.dire(f"📅 *{employeur}* te propose un entretien !\n" + "\n".join(lignes) +
                    f"\n📍 {analyse.get('lieu') or 'lieu à préciser'}\n{analyse.get('resume', '')}\n\n"
                    "Tu confirmes ? Touche un bouton, ou réponds en mots (« oui », « plutôt jeudi aprèm »…).",
                    boutons)


def _repondre(email_id: int, consigne: dict) -> None:
    e = db.q1("SELECT * FROM emails_recus WHERE id=?", email_id)
    if e is None:
        raise KeyError(f"email {email_id}")
    r = llm.demander("secretaire", "rapide", llm.prompt("secretaire") + "\n\n## Profil\n" + config.profil_texte(),
                     json.dumps({"email_recu": {"de": e["expediteur"], "sujet": e["sujet"], "corps": e["corps"][:3000]},
                                 "decision": consigne}, ensure_ascii=False), SCHEMA, max_tokens=600)
    m = EmailMessage()
    expediteur(m)
    m["To"] = e["expediteur"]
    m["Subject"] = e["sujet"] if (e["sujet"] or "").lower().startswith("re:") else f"Re: {e['sujet']}"
    m["Date"] = formatdate(localtime=True)
    m["Message-ID"] = make_msgid()
    if str(e["uid"]).startswith("<"):
        m["In-Reply-To"] = e["uid"]
        m["References"] = e["uid"]
    m.set_content(r["email"])
    expedier(m)
    with db.tx() as c:
        c.execute("UPDATE emails_recus SET traite=1 WHERE id=?", (email_id,))


def ics(rdv) -> "Path":
    d = datetime.fromisoformat(rdv["debut"])
    f = config.DONNEES / f"rdv_{rdv['id']}.ics"
    fmt = "%Y%m%dT%H%M%S"
    f.write_text("\r\n".join([
        "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//agent-student//FR", "BEGIN:VEVENT",
        f"UID:rdv-{rdv['id']}@agent-student", f"DTSTAMP:{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}",
        f"DTSTART;TZID=Europe/Brussels:{d:{fmt}}", f"DTEND;TZID=Europe/Brussels:{d + timedelta(hours=1):{fmt}}",
        f"SUMMARY:Entretien – {rdv['entreprise']}", f"LOCATION:{rdv['lieu'] or ''}",
        "BEGIN:VALARM", "TRIGGER:-PT2H", "ACTION:DISPLAY", "DESCRIPTION:Entretien", "END:VALARM",
        "END:VEVENT", "END:VCALENDAR", ""]), encoding="utf-8")
    return f


def confirmer(rdv_id: int, creneau: str | None = None):
    rdv = db.q1("SELECT * FROM rdv WHERE id=?", rdv_id)
    if rdv is None:
        return messagerie.dire("Je ne retrouve pas ce rendez-vous 🤔")
    if creneau:  # « oui pour le 2e » → le Concierge passe le créneau choisi
        autre = db.q1("SELECT * FROM rdv WHERE email_id=? AND debut=?", rdv["email_id"], creneau)
        rdv = autre or rdv
    _repondre(rdv["email_id"], {"type": "confirmation", "date": joli(rdv["debut"]), "lieu": rdv["lieu"]})
    with db.tx() as c:
        c.execute("UPDATE rdv SET statut='decline' WHERE email_id=? AND id<>?", (rdv["email_id"], rdv["id"]))
        c.execute("UPDATE rdv SET statut='confirme' WHERE id=?", (rdv["id"],))
    messagerie.dire(f"✅ Confirmé à *{rdv['entreprise']}* : {joli(rdv['debut'])}. Bonne chance !")
    if rdv["debut"]:
        messagerie.fichier(ics(db.q1("SELECT * FROM rdv WHERE id=?", rdv["id"])), "Ajoute-le à ton agenda")


def autre_date(rdv_id: int, disponibilites: str):
    rdv = db.q1("SELECT * FROM rdv WHERE id=?", rdv_id)
    _repondre(rdv["email_id"], {"type": "autre_date", "disponibilites_proposees": disponibilites})
    with db.tx() as c:
        c.execute("UPDATE rdv SET statut='decline' WHERE email_id=?", (rdv["email_id"],))
    messagerie.dire(f"J'ai proposé tes disponibilités à *{rdv['entreprise']}*. Je te préviens dès qu'ils répondent.")


def decliner(rdv_id: int):
    rdv = db.q1("SELECT * FROM rdv WHERE id=?", rdv_id)
    _repondre(rdv["email_id"], {"type": "declin_poli"})
    with db.tx() as c:
        c.execute("UPDATE rdv SET statut='decline' WHERE email_id=?", (rdv["email_id"],))
    messagerie.dire(f"J'ai décliné poliment auprès de *{rdv['entreprise']}*.")


def repondre_question(email_id: int, texte: str):
    _repondre(email_id, {"type": "reponse_question", "ce_que_je_veux_dire": texte})
    messagerie.dire("Réponse envoyée.")
