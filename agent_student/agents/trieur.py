"""Agent 11 — Trieur : lit la boîte mail. Refus → classés en silence. Entretien → Secrétaire. Question → toi.

Économie : un email n'est montré à l'IA QUE s'il est relié à une candidature (réponse au fil,
ou même domaine que l'employeur) ou s'il contient un mot-clé de recrutement. Pubs et newsletters : 0 token."""
from __future__ import annotations

import email
import imaplib
import json
import re
from email.header import decode_header, make_header
from email.utils import parsedate_to_datetime
from pathlib import Path

from .. import config, db, llm, messagerie, outils

SCHEMA = {
    "type": "object",
    "properties": {
        "categorie": {"type": "string", "enum": ["refus", "entretien", "question", "accuse", "autre"]},
        "resume": {"type": "string"},
        "employeur": {"type": ["string", "null"]},
        "creneaux": {"type": "array", "items": {"type": "string"}},
        "lieu": {"type": ["string", "null"]},
    },
    "required": ["categorie", "resume", "creneaux"],
}
MOTS = re.compile(r"candidature|entretien|rendez-vous|rencontr|disponibilit|stage|poste|cv\b|application|interview|"
                  r"sollicitation|recrutement|profil", re.I)


def _dec(v):
    return str(make_header(decode_header(v or "")))


def _corps(msg) -> str:
    parts = msg.walk() if msg.is_multipart() else [msg]
    html_ = ""
    for p in parts:
        ct = p.get_content_type()
        if p.get_content_disposition() == "attachment":
            continue
        charge = p.get_payload(decode=True)
        if not charge:
            continue
        t = charge.decode(p.get_content_charset() or "utf-8", "replace")
        if ct == "text/plain":
            return t
        if ct == "text/html":
            html_ = outils.texte_html(t)
    return html_


def _html(msg) -> str:
    for p in (msg.walk() if msg.is_multipart() else [msg]):
        if p.get_content_type() == "text/html" and p.get_content_disposition() != "attachment":
            charge = p.get_payload(decode=True)
            if charge:
                return charge.decode(p.get_content_charset() or "utf-8", "replace")
    return ""


def _alerte(exp, sujet, corps, msg) -> int | None:
    """Alerte emploi (LinkedIn, Jobat, StepStone, Indeed…), reçue directement ou transférée par la personne :
    chaque offre de l'email entre dans le circuit, sans jamais ouvrir ces sites. Renvoie le nombre d'offres, ou None."""
    from . import connecteurs, sources
    offres = connecteurs.offres_d_une_alerte(exp, sujet, corps, _html(msg))
    if not offres or (not connecteurs.est_alerte(exp) and len(offres) < 2):
        return None
    return sources.enregistrer(offres)


def _relier(msg, expediteur):
    refs = f"{msg.get('In-Reply-To', '')} {msg.get('References', '')}"
    for mid in re.findall(r"<[^>]+>", refs):
        r = db.q1("SELECT id FROM offres WHERE message_id=?", mid)
        if r:
            return r["id"]
    dom = outils.domaine_email(expediteur)
    if dom and dom not in ("gmail.com", "outlook.com", "hotmail.com", "yahoo.com", "skynet.be", "proximus.be"):
        r = db.q1("SELECT id FROM offres WHERE envoye_le IS NOT NULL AND lower(email_contact) LIKE ? "
                  "ORDER BY envoye_le DESC", f"%@{dom}")
        if r:
            return r["id"]
    return None


def _boite():
    """Renvoie une liste de (uid, octets_du_mail)."""
    if config.simulation() or config.env("BOITE_DOSSIER"):
        # Simulation, ou app : le serveur dépose ici les seuls emails adressés à CET utilisateur (jamais toute la boîte)
        dossier = Path(config.env("BOITE_DOSSIER")) if config.env("BOITE_DOSSIER") else config.DONNEES / "boite_simulee"
        dossier.mkdir(parents=True, exist_ok=True)
        for f in sorted(dossier.glob("*.eml")):
            yield f.name, f.read_bytes()
            f.rename(f.with_suffix(".lu"))
        return
    # On ne relit jamais deux fois le même email : on retient le numéro (UID) du dernier email vu.
    m = imaplib.IMAP4_SSL(config.env("IMAP_HOTE", "imap.gmail.com"), timeout=60)
    try:
        m.login(config.env("EMAIL_ADRESSE"), config.env("EMAIL_MOT_DE_PASSE"))
        m.select("INBOX", readonly=True)            # lecture seule : tes emails restent « non lus »
        dernier = db.kv_get("imap_dernier_uid")
        if dernier is None:                          # 1er lancement : on ignore l'historique de la boîte
            _, d = m.uid("search", None, "ALL")
            uids = d[0].split()
            db.kv_set("imap_dernier_uid", int(uids[-1]) if uids else 0)
            return
        _, d = m.uid("search", None, f"UID {int(dernier) + 1}:*")
        for uid in [u for u in d[0].split() if int(u) > int(dernier)][:50]:
            _, data = m.uid("fetch", uid, "(BODY.PEEK[])")
            if data and isinstance(data[0], tuple):
                yield None, data[0][1]
            db.kv_set("imap_dernier_uid", int(uid))
    finally:
        try:
            m.logout()
        except Exception:
            pass


def relever():
    from . import secretaire

    for uid, brut in _boite():
        msg = email.message_from_bytes(brut)
        uid = msg.get("Message-ID") or uid
        if db.q1("SELECT 1 FROM emails_recus WHERE uid=?", uid):
            continue
        exp, sujet, corps = _dec(msg.get("From")), _dec(msg.get("Subject")), _corps(msg)[:5000]
        moi = (config.env("EMAIL_ADRESSE") or "").lower()
        if moi and moi in exp.lower():          # nos propres emails (répétitions, copies) : ignorés, 0 token
            _stocker(uid, None, exp, sujet, corps, "autre", {})
            continue
        n = _alerte(exp, sujet, corps, msg)
        if n is not None:                          # alerte emploi : des offres, pas une réponse de recruteur
            _stocker(uid, None, exp, sujet, corps[:2000], "alerte", {"offres": n})
            db.journal("trieur", f"alerte emploi lue ({exp[:40]}) : {n} nouvelle(s) offre(s)")
            continue
        offre_id = _relier(msg, exp)
        if offre_id is None and not MOTS.search(f"{sujet} {corps[:1500]}"):
            _stocker(uid, None, exp, sujet, corps, "autre", {})
            continue
        try:
            date_mail = parsedate_to_datetime(msg.get("Date")).isoformat() if msg.get("Date") else ""
        except Exception:
            date_mail = ""
        try:
            a = llm.demander("trieur", "rapide", llm.prompt("trieur"),
                             json.dumps({"date_email": date_mail, "de": exp, "sujet": sujet, "corps": corps,
                                         "offre_liee": _resume_offre(offre_id)}, ensure_ascii=False),
                             SCHEMA, max_tokens=500, reflexion=False, effort="low")
        except llm.BudgetDepasse:
            return
        except Exception as e:
            db.journal("trieur", f"échec : {e}")
            continue
        email_id = _stocker(uid, offre_id, exp, sujet, corps, a["categorie"], a)
        cat = a["categorie"]
        if offre_id and cat == "refus":
            _statut(offre_id, "refus")                         # silencieux, comme demandé
        elif cat == "entretien":
            if offre_id:
                _statut(offre_id, "entretien")
            secretaire.proposer(email_id, offre_id, a)
        elif cat == "question":
            messagerie.dire(f"❓ *{a.get('employeur') or exp}* te pose une question (#{email_id}) :\n"
                            f"{a['resume']}\nDis-moi quoi répondre, je m'occupe de l'email.")


def _resume_offre(offre_id):
    if not offre_id:
        return None
    o = db.q1("SELECT titre, entreprise FROM offres WHERE id=?", offre_id)
    return dict(o) if o else None


def _statut(offre_id, s):
    o = db.q1("SELECT statut FROM offres WHERE id=?", offre_id)
    if o and s in db.TRANSITIONS.get(o["statut"], set()):
        db.changer_statut(offre_id, s, "trieur")


def _stocker(uid, offre_id, exp, sujet, corps, cat, analyse) -> int:
    with db.tx() as c:
        cur = c.execute("INSERT INTO emails_recus (uid, offre_id, expediteur, sujet, corps, categorie, analyse) "
                        "VALUES (?,?,?,?,?,?,?)",
                        (uid, offre_id, exp, sujet, corps, cat, json.dumps(analyse, ensure_ascii=False)))
        return cur.lastrowid
