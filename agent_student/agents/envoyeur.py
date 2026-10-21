"""Agent Envoyeur (sans IA) : envoie CV + lettre par email, comme un humain le ferait.

Modes (config/objectif.yaml → envoi.mode) :
  • repetition : TEST. Chaque candidature (pièces jointes comprises) part vers l'adresse email de ton CV,
                 jamais vers l'employeur. Objet « [TEST n°12 → adresse de l'employeur] ». Rien sur ta messagerie.
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
    m["To"] = config.email_test() if repetition else o["email_contact"]
    dest = o["email_contact"] or "via leur site"
    m["Subject"] = (f"[TEST n°{o['id']} → {dest}] " if repetition else "") + o["objet_mail"]
    m["Date"] = formatdate(localtime=True)
    m["Message-ID"] = make_msgid(domain=moi.split("@")[-1])
    corps = o["corps_mail"]
    if repetition:
        via = (f"Destinataire prévu : {o['email_contact']}" if o["email_contact"] else
               "⚠️ Cette offre se postule SUR LEUR SITE : ouvre le lien, colle ce message et joins les 2 PDF")
        soucis = o["_soucis"] if "_soucis" in o.keys() else None
        corps = ("🧪 TEST — rien n'a été envoyé à l'employeur. Voici exactement ce qu'il recevrait.\n"
                 f"{o['titre']} — {o['entreprise'] or '?'} ({o['ville'] or '?'})\n{via}\nOffre : {o['url']}\n"
                 f"Adéquation {o['score']}/10 · note du Juge {o['note_lettre']}/10"
                 + (f"\n🛑 En vrai, je l'aurais BLOQUÉE : {soucis}" if soucis else "")
                 + "\nPour l'envoyer pour de vrai : écris « vrai " + str(o["id"]) + " » à l'agent.\n"
                 + "─" * 40 + "\n\n" + corps)
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


def envoyer_copies(ids: list[int], adresse: str) -> int:
    """« Envoie-les sur mon autre mail » : copie complète (email + CV + lettre) de candidatures déjà écrites,
    vers l'adresse demandée. Jamais vers un employeur."""
    n = 0
    for i in ids:
        o = db.q1("SELECT * FROM offres WHERE id=?", i)
        if not o or not o["lettre"]:
            continue
        o = dict(o)
        o["_soucis"] = None
        m = construire(o, repetition=True)
        m.replace_header("To", adresse)
        expedier(m)
        n += 1
    return n


def _formulaire(o, seul: bool = True):
    db.changer_statut(o["id"], "manuelle", "envoyeur", "candidature via le site de l'employeur")
    if not seul:
        return
    messagerie.dire(f"*{o['entreprise'] or o['titre']}* se postule sur leur site. Ta lettre est prête "
                    f"(PDF ci-dessous) : ouvre le lien, colle-la, envoie.\n{o['url']}",
                    [[("✅ C'est fait", f"fait {o['id']}"), ("❌ Laisser tomber", f"ecarte {o['id']}")]])
    messagerie.fichier(chemin_lettre(o["id"]), f"Lettre – {o['entreprise'] or ''}")


def envoyer(max_par_cycle: int = 6, pause_secondes: tuple = (90, 300)) -> int:
    """Mode TEST (répétition) : TOUT part vers l'adresse de ton CV, même les offres « via leur site » et celles
    que le Contrôleur bloquerait (avec l'explication), pour que tu vérifies forme et pièces jointes.
    Rien sur ta messagerie. Mode réel : envoi aux employeurs, aux heures ouvrables, au rythme humain."""
    regles = config.regles_envoi()
    reels = set(db.kv_get("envoi_reel_ids", []))   # « vrai 12 » pendant le test
    envoyees, via_site = 0, []
    offres = [dict(o) for o in db.q("SELECT * FROM offres WHERE statut='validee' "
                                    "ORDER BY COALESCE(publie_le, date(cree_le)) DESC, score DESC")]
    for o in offres:
        repetition = regles["mode"] == "repetition" and o["id"] not in reels
        soucis = controle.problemes(o) if o["email_contact"] or repetition else []
        if not repetition:
            if not o["email_contact"]:
                via_site.append(o)
                _formulaire(o, seul=len(via_site) <= 3)   # au-delà de 3 : une seule liste à la fin
                continue
            if soucis:
                db.changer_statut(o["id"], "a_valider", "controleur", "à relire : contrôle — " + " ; ".join(soucis))
                messagerie.dire(f"J'ai bloqué l'envoi à *{o['entreprise']}* : " + " ; ".join(soucis),
                                [[("👁 Voir", f"montre {o['id']}"), ("❌ Écarter", f"ecarte {o['id']}")]])
                continue
            if not config.simulation() and (not heure_ouvrable(regles) or envoyees >= max_par_cycle
                                            or envoyes_aujourdhui() >= regles["max_par_jour"]):
                break
        o["_soucis"] = " ; ".join(soucis) if soucis else None
        try:
            m = construire(o, repetition)
            expedier(m)
        except (smtplib.SMTPAuthenticationError, smtplib.SMTPConnectError, smtplib.SMTPServerDisconnected,
                OSError) as e:
            # Souci de BOÎTE MAIL (mot de passe changé, pas d'internet, Gmail qui limite) : ce n'est pas la faute
            # de cette candidature → on la garde, on arrête ce tour, et on te prévient UNE fois par demi-journée.
            db.journal("envoyeur", f"boîte mail indisponible : {type(e).__name__}: {e}")
            if time.time() - float(db.kv_get("alerte_smtp", 0) or 0) > 12 * 3600:
                db.kv_set("alerte_smtp", time.time())
                auth = isinstance(e, smtplib.SMTPAuthenticationError)
                messagerie.dire("Je n'arrive pas à me connecter à ta boîte mail"
                                + (" : le mot de passe d'application est refusé. Recrée-en un (Google → Sécurité → "
                                   "Mots de passe des applications) et colle-le dans ⚙ Réglages." if auth else
                                   " (internet coupé ou Gmail qui freine). Je réessaie toutes les 10 min.")
                                + " Tes candidatures sont gardées, rien n'est perdu.")
            break
        except Exception as e:
            db.changer_statut(o["id"], "erreur", "envoyeur", f"envoi impossible : {e}")
            messagerie.signaler(f"⚠️ Envoi impossible à {o['entreprise']} : {e}")
            continue
        maintenant = datetime.now().isoformat(" ", "seconds")
        if repetition:
            if o["email_contact"]:
                db.changer_statut(o["id"], "repetee", "envoyeur", f"test : envoyé à {config.email_test()}",
                                  envoye_le=maintenant)
            else:   # via leur site : reste « à faire via site » pour plus tard, sans te déranger
                db.changer_statut(o["id"], "manuelle", "envoyeur", f"test (via site) : envoyé à {config.email_test()}")
            db.journal("envoyeur", f"test n°{o['id']} → {config.email_test()} ({o['entreprise'] or o['titre']})")
        else:
            db.changer_statut(o["id"], "envoyee", "envoyeur", message_id=m["Message-ID"], envoye_le=maintenant)
        envoyees += 1
        if not repetition and not config.simulation():
            time.sleep(random.randint(*pause_secondes))  # rythme humain, évite les filtres anti-spam
    if len(via_site) > 3:
        messagerie.dire(f"Et {len(via_site) - 3} autres se postulent sur leur site (lettre prête pour chacune) :\n"
                        + "\n".join(f"*{o['id']}.* {o['entreprise'] or o['titre']} — {o['url']}" for o in via_site[3:20])
                        + "\n« montre 12 » pour la lettre · « fait 12 » quand c'est envoyé.")
    return envoyees
