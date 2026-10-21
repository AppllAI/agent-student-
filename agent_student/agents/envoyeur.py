"""Agent Envoyeur (sans IA) : envoie CV + lettre par email, comme un humain le ferait.

Modes (config/objectif.yaml → envoi.mode) :
  • repetition : TEST. L'email complet (pièces jointes comprises) part vers TA boîte, jamais vers l'employeur.
                 Objet préfixé « [RÉPÉTITION → adresse de l'employeur] ». Tu vois exactement ce qui serait parti.
  • validation / auto : envoi réel à l'employeur.
Garde-fous : Contrôleur avant chaque envoi, heures de bureau (le matin d'abord), plafond journalier,
espacement entre envois, offres fraîches en priorité. En SIMULATION, rien ne sort : fichiers .eml dans donnees/sortie/."""
from __future__ import annotations

import random
import smtplib
import time
from datetime import date, datetime
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from zoneinfo import ZoneInfo

from .. import config, db, messagerie
from . import controle
from .redacteur import chemin_lettre

try:    # Windows n'a pas la base des fuseaux horaires : elle vient du paquet « tzdata »
    BXL = ZoneInfo("Europe/Brussels")
except Exception:
    BXL = None   # à défaut : l'heure locale du PC (en Belgique, c'est la même)


def heure_ouvrable(regles, maintenant=None) -> bool:
    m = maintenant or (datetime.now(BXL) if BXL else datetime.now())
    debut, fin = regles["heures_envoi"]
    return m.weekday() in regles["jours_envoi"] and debut <= m.hour < fin


def envoyes_aujourdhui() -> int:
    return db.q1("SELECT COUNT(*) AS n FROM offres WHERE statut<>'repetee' AND date(envoye_le)=?",
                 date.today().isoformat())["n"]


def construire(o, repetition: bool = False) -> EmailMessage:
    moi = config.env("EMAIL_ADRESSE", "moi@exemple.be")
    m = EmailMessage()
    m["From"] = f"{config.env('NOM_CANDIDAT')} <{moi}>" if config.env("NOM_CANDIDAT") else moi
    m["To"] = moi if repetition else o["email_contact"]
    m["Subject"] = (f"[RÉPÉTITION → {o['email_contact']}] " if repetition else "") + o["objet_mail"]
    m["Date"] = formatdate(localtime=True)
    m["Message-ID"] = make_msgid(domain=moi.split("@")[-1])
    corps = o["corps_mail"]
    if repetition:
        corps = ("🧪 RÉPÉTITION — cet email n'a PAS été envoyé à l'employeur. Voici exactement ce qu'il aurait reçu.\n"
                 f"Destinataire prévu : {o['email_contact']} · Offre : {o['url']}\n"
                 f"Adéquation {o['score']}/10 · note du Juge {o['note_lettre']}/10\n" + "─" * 40 + "\n\n" + corps)
    m.set_content(corps)
    cv = config.chemin_cv()
    if cv.exists():
        m.add_attachment(cv.read_bytes(), maintype="application", subtype="pdf", filename=_nom("CV", "pdf"))
    lettre = chemin_lettre(o["id"])
    if lettre.exists():
        pdf = lettre.suffix == ".pdf"
        m.add_attachment(lettre.read_bytes(), maintype="application" if pdf else "text", subtype="pdf" if pdf else "plain",
                         filename=_nom("Lettre_de_motivation", lettre.suffix[1:]))
    return m


def _nom(base, ext):
    nom = (config.env("NOM_CANDIDAT") or "").strip().replace(" ", "_")
    return f"{base}_{nom}.{ext}" if nom else f"{base}.{ext}"


def connexion_smtp():
    """SMTP chiffré : port 465 (SSL direct) ou 587 (STARTTLS), selon le fournisseur.
    local_hostname fixé : sinon Python envoie le nom du PC, qui plante s'il contient un accent."""
    hote, port = config.env("SMTP_HOTE", "smtp.gmail.com"), config.env_int("SMTP_PORT", 465)
    if port == 465:
        s = smtplib.SMTP_SSL(hote, port, timeout=30, local_hostname="localhost")
    else:
        s = smtplib.SMTP(hote, port, timeout=30, local_hostname="localhost")
        s.starttls()
    s.login(config.env("EMAIL_ADRESSE"), config.env("EMAIL_MOT_DE_PASSE"))
    return s


def expedier(m: EmailMessage):
    if config.simulation():
        sortie = config.DONNEES / "sortie"
        sortie.mkdir(parents=True, exist_ok=True)
        (sortie / f"{datetime.now():%Y%m%d_%H%M%S_%f}.eml").write_bytes(bytes(m))
        return
    with connexion_smtp() as s:
        s.send_message(m)


def _formulaire(o):
    db.changer_statut(o["id"], "manuelle", "envoyeur", "candidature via le site de l'employeur")
    messagerie.dire(f"🌐 *{o['entreprise'] or o['titre']}* se postule sur leur site. Ta lettre est prête "
                    f"(PDF ci-dessous) : ouvre le lien, colle-la, envoie.\n{o['url']}",
                    [[("✅ C'est fait", f"fait {o['id']}"), ("❌ Laisser tomber", f"ecarte {o['id']}")]])
    messagerie.fichier(chemin_lettre(o["id"]), f"Lettre – {o['entreprise'] or ''}")


def envoyer(max_par_cycle: int = 6, pause_secondes: tuple = (90, 300)) -> int:
    regles = config.regles_envoi()
    reels = set(db.kv_get("envoi_reel_ids", []))   # « L'envoyer pour de vrai » pendant la répétition
    envoyees = 0
    offres = db.q("SELECT * FROM offres WHERE statut='validee' "
                  "ORDER BY COALESCE(publie_le, date(cree_le)) DESC, score DESC")
    for o in offres:
        if not o["email_contact"]:
            _formulaire(o)
            continue
        repetition = regles["mode"] == "repetition" and o["id"] not in reels
        soucis = controle.problemes(o)
        if soucis:
            db.changer_statut(o["id"], "a_valider", "controleur", "à relire : contrôle — " + " ; ".join(soucis))
            messagerie.dire(f"🛑 J'ai bloqué l'envoi à *{o['entreprise']}* : " + " ; ".join(soucis),
                            [[("👁 Voir", f"montre {o['id']}"), ("❌ Écarter", f"ecarte {o['id']}")]])
            continue
        if not repetition and not config.simulation():
            if not heure_ouvrable(regles) or envoyes_aujourdhui() >= regles["max_par_jour"] or envoyees >= max_par_cycle:
                break
        try:
            m = construire(o, repetition)
            expedier(m)
        except Exception as e:
            db.changer_statut(o["id"], "erreur", "envoyeur", f"envoi impossible : {e}")
            messagerie.dire(f"⚠️ Envoi impossible à {o['entreprise']} : {e}")
            continue
        maintenant = datetime.now().isoformat(" ", "seconds")
        if repetition:
            db.changer_statut(o["id"], "repetee", "envoyeur", "répétition : envoyé à toi seulement", envoye_le=maintenant)
            messagerie.dire(f"📬 Répétition : la candidature pour *{o['entreprise']}* est dans ta boîte mail "
                            "(rien n'est parti chez l'employeur). Regarde si tu l'aurais envoyée telle quelle.",
                            [[("🚀 L'envoyer pour de vrai", f"vrai {o['id']}"), ("✍️ À améliorer", f"montre {o['id']}")]])
        else:
            db.changer_statut(o["id"], "envoyee", "envoyeur", message_id=m["Message-ID"], envoye_le=maintenant)
        envoyees += 1
        if not repetition and not config.simulation():
            time.sleep(random.randint(*pause_secondes))  # rythme humain, évite les filtres anti-spam
    return envoyees
