"""Agent Envoyeur (sans IA) : envoie CV + lettre par email, comme un humain le ferait.

Modes (config/objectif.yaml → envoi.mode) :
  • repetition : TEST. Chaque candidature (pièces jointes comprises) part vers l'adresse email de ton CV,
                 jamais vers l'employeur. Objet « [TEST n°12 → adresse de l'employeur] ». Rien sur ta messagerie.
  • validation / auto : envoi réel à l'employeur.
Garde-fous : Contrôleur avant chaque envoi, heures de bureau (le matin d'abord), plafond journalier,
espacement entre envois, offres fraîches en priorité. En SIMULATION, rien ne sort : fichiers .eml dans donnees/sortie/.

Pièces jointes : TOUJOURS des PDF (CV, lettre ; en test, un rapport). Le corps de l'email reste court."""
from __future__ import annotations

import json
import random
import re
import smtplib
import time
from datetime import date, datetime
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from zoneinfo import ZoneInfo

from .. import config, db, messagerie, outils, pdf
from . import controle, phase_test
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


def expediteur(m: EmailMessage) -> str:
    """De : « Prénom Nom <adresse d'envoi> ». Dans l'app, l'adresse d'envoi est celle du service et REPONSE_A
    (une adresse propre à chaque utilisateur) ramène les réponses des recruteurs vers SON espace."""
    moi = config.env("EMAIL_ADRESSE", "moi@exemple.be")
    m["From"] = f"{config.env('NOM_CANDIDAT')} <{moi}>" if config.env("NOM_CANDIDAT") else moi
    if config.env("REPONSE_A"):
        m["Reply-To"] = config.env("REPONSE_A")
    return moi


def construire(o, repetition: bool = False) -> EmailMessage:
    m = EmailMessage()
    moi = expediteur(m)
    m["To"] = config.email_test() if repetition else o["email_contact"]
    dest = o["email_contact"] or "via leur site"
    m["Subject"] = (f"[TEST n°{o['id']} → {dest}] " if repetition else "") + o["objet_mail"]
    m["Date"] = formatdate(localtime=True)
    m["Message-ID"] = make_msgid(domain=moi.split("@")[-1])
    corps = o["corps_mail"]
    if repetition:
        via = (f"destinataire prévu : {o['email_contact']}" if o["email_contact"] else
               "⚠️ se postule SUR LEUR SITE : ouvre le lien de l'offre et joins les 2 PDF")
        soucis = o["_soucis"] if "_soucis" in o.keys() else None
        rapport = _nom(f"Rapport_test_{o['id']}")
        corps = (f"🧪 TEST n°{o['id']} — rien n'a été envoyé à l'employeur ({via}).\n"
                 + (f"🛑 En vrai, je l'aurais BLOQUÉE : {soucis}\n" if soucis else "")
                 + f"Le détail (offre, note, remarques du Juge) est dans {rapport}.\n"
                 + f"Pour l'envoyer pour de vrai : écris « vrai {o['id']} » à l'agent.\n"
                 + "─" * 30 + "\nCe que l'employeur recevrait :\n\n" + corps)
    m.set_content(corps)
    _joindre_pdf(m, config.chemin_cv(), _nom("CV"))
    _joindre_pdf(m, chemin_lettre(o["id"]), _nom("Lettre_de_motivation"))
    if repetition:
        _joindre_pdf(m, rapport_test(o), _nom(f"Rapport_test_{o['id']}"))
    return m


def _joindre_pdf(m: EmailMessage, chemin, nom: str):
    """Une vraie pièce jointe PDF (jamais affichée dans le corps de l'email)."""
    if chemin and chemin.exists():
        m.add_attachment(chemin.read_bytes(), maintype="application", subtype="pdf", filename=nom,
                         disposition="attachment")


def _nom(base: str) -> str:
    """CV_Camille_Dupont.pdf : sans accents ni espaces, pour tous les logiciels de messagerie."""
    nom = re.sub(r"[^A-Za-z0-9]+", "_", outils.sans_accents(config.env("NOM_CANDIDAT") or "").title()).strip("_")
    return f"{base}_{nom}.pdf" if nom else f"{base}.pdf"


def rapport_test(o) -> "Path":
    """En mode test : la fiche de la candidature (offre, adéquation, avis du Juge) en PDF, jointe à l'email."""
    o = dict(o)
    pourquoi = json.loads(o.get("pourquoi") or "[]")
    remarque = (o.get("raison") or "")[10:] if (o.get("raison") or "").startswith("à relire") else ""
    envoi = o.get("email_contact") or "via le site de l'employeur (formulaire)"
    lignes = [f"## {o.get('titre') or '?'}", f"- Employeur : {o.get('entreprise') or '?'} ({o.get('ville') or '?'})",
              f"- Offre : {o.get('url') or '?'}", f"- Envoi prévu : {envoi}"]
    if o.get("publie_le"):
        lignes.append(f"- Publiée le : {o['publie_le']}")
    lignes += ["", "## Évaluation", f"- Adéquation : {o.get('score') or '?'}/10",
               f"- Note de la lettre (Juge) : {o.get('note_lettre') or '?'}/10"]
    lignes += [f"- {p}" for p in pourquoi[:3]]
    if remarque:
        lignes += ["", "## À relire", remarque]
    if o.get("_soucis"):
        lignes += ["", "## Bloquée par le Contrôleur en mode réel", o["_soucis"]]
    lignes += ["", "## Pour l'envoyer pour de vrai", f"Écris « vrai {o['id']} » à l'agent."]
    if o.get("description"):
        lignes += ["", "## L'annonce (résumé)", (o["description"] or "")[:1800]]
    return pdf.document(f"Candidature test n°{o['id']}", "\n".join(lignes),
                        config.DONNEES / "rapports" / f"rapport_test_{o['id']}.pdf",
                        "Rien n'a été envoyé à l'employeur.")


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
        if repetition and not phase_test.peut_envoyer_test():
            continue                     # phase de test : on attend sa note avant d'envoyer le test suivant
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
            phase_test.test_envoye(o["id"])
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
