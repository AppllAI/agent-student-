"""Tout ce que fait l'application en coulisses (sans interface graphique, donc testable) :
où ranger les données, enregistrer les réglages, deviner les serveurs email, tester les accès,
lancer le pont WhatsApp."""
from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

NOM_APP = "AgentStudent"

# Serveurs des fournisseurs courants → la personne n'a que son adresse à taper.
FOURNISSEURS = {
    "gmail.com": ("smtp.gmail.com", 465, "imap.gmail.com"),
    "googlemail.com": ("smtp.gmail.com", 465, "imap.gmail.com"),
    "outlook.com": ("smtp-mail.outlook.com", 587, "outlook.office365.com"),
    "hotmail.com": ("smtp-mail.outlook.com", 587, "outlook.office365.com"),
    "hotmail.be": ("smtp-mail.outlook.com", 587, "outlook.office365.com"),
    "live.be": ("smtp-mail.outlook.com", 587, "outlook.office365.com"),
    "live.com": ("smtp-mail.outlook.com", 587, "outlook.office365.com"),
    "icloud.com": ("smtp.mail.me.com", 587, "imap.mail.me.com"),
    "me.com": ("smtp.mail.me.com", 587, "imap.mail.me.com"),
    "yahoo.com": ("smtp.mail.yahoo.com", 465, "imap.mail.yahoo.com"),
    "yahoo.fr": ("smtp.mail.yahoo.com", 465, "imap.mail.yahoo.com"),
    "gmx.com": ("mail.gmx.com", 465, "imap.gmx.com"),
    "gmx.fr": ("mail.gmx.com", 465, "imap.gmx.com"),
}
AIDE_MOT_DE_PASSE = {
    "gmail": "https://myaccount.google.com/apppasswords",
    "icloud": "https://account.apple.com/account/manage",
    "yahoo": "https://login.yahoo.com/account/security",
}
LIEN_CLE_API = "https://console.anthropic.com/settings/keys"


_verrou = None


def verrou_instance() -> bool:
    """True si on est la seule application ouverte. Deux instances = emails envoyés en double et
    Telegram qui se bloque (erreur 409) : on l'empêche."""
    global _verrou
    import socket
    _verrou = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        _verrou.bind(("127.0.0.1", 47614))
        _verrou.listen(1)
        return True
    except OSError:
        return False


def gele() -> bool:
    """True quand on tourne dans l'application empaquetée (PyInstaller)."""
    return getattr(sys, "frozen", False)


def dossier_donnees() -> Path:
    """Dossier perso de l'application (jamais à côté du .exe, qui peut être en lecture seule)."""
    if os.getenv("AGENT_STUDENT_RACINE"):
        return Path(os.environ["AGENT_STUDENT_RACINE"])
    if platform.system() == "Windows":
        base = Path(os.getenv("APPDATA", Path.home() / "AppData" / "Roaming"))
    elif platform.system() == "Darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.getenv("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / NOM_APP


def preparer_dossier() -> Path:
    d = dossier_donnees()
    for sous in ("", "config", "profil", "donnees"):
        (d / sous).mkdir(parents=True, exist_ok=True)
    os.environ["AGENT_STUDENT_RACINE"] = str(d)
    return d


def serveurs(adresse: str) -> tuple[str, int, str] | None:
    dom = adresse.strip().lower().rsplit("@", 1)[-1]
    return FOURNISSEURS.get(dom)


def lien_aide_mot_de_passe(adresse: str) -> str | None:
    dom = adresse.lower()
    for cle, url in AIDE_MOT_DE_PASSE.items():
        if cle in dom or (cle == "gmail" and "googlemail" in dom) or (cle == "icloud" and dom.endswith("@me.com")):
            return url
    return None


# ---------- .env ----------
def lire_env(racine: Path) -> dict:
    f = racine / ".env"
    sortie = {}
    if f.exists():
        for ligne in f.read_text(encoding="utf-8").splitlines():
            if "=" in ligne and not ligne.lstrip().startswith("#"):
                k, v = ligne.split("=", 1)
                sortie[k.strip()] = v.strip().strip('"')
    return sortie


def ecrire_env(racine: Path, valeurs: dict) -> None:
    """Fusionne avec l'existant et écrit .env (valeurs entre guillemets, droits restreints)."""
    actuel = lire_env(racine)
    actuel.update({k: str(v) for k, v in valeurs.items() if v is not None})
    f = racine / ".env"
    f.write_text("# Réglages enregistrés par l'application Agent Student\n" +
                 "".join(f"{k}={_guillemets(v)}\n" for k, v in actuel.items()), encoding="utf-8")
    try:
        os.chmod(f, 0o600)   # lisible par toi seul
    except OSError:
        pass


def _guillemets(v: str) -> str:
    return f"'{v}'" if '"' in v else f'"{v}"'


_INVISIBLES = dict.fromkeys(map(ord, "\u200b\u200c\u200d\u2060\ufeff\u00ad"), None)


def nettoyer(texte: str) -> str:
    """Retire les caractères invisibles qu'un copier-coller ramène souvent (espaces insécables, zéro-largeur…)."""
    return "".join((texte or "").translate(_INVISIBLES).split())


def nettoyer_mot_de_passe(email: str, mdp: str) -> str:
    if "gmail" in email.lower() or "googlemail" in email.lower():
        # Google affiche le code en 4 blocs avec des espaces (souvent insécables) : on ne garde que les lettres
        return "".join(c for c in mdp if c.isascii() and c.isalpha())
    return (mdp or "").translate(_INVISIBLES).strip()


def probleme_formulaire(v: dict) -> str | None:
    """Vérifications instantanées avant de contacter les serveurs."""
    gmail = "gmail" in v["EMAIL_ADRESSE"].lower()
    if gmail and len(v["EMAIL_MOT_DE_PASSE"]) != 16:
        return (f"Le code Gmail doit faire 16 lettres (j'en lis {len(v['EMAIL_MOT_DE_PASSE'])}). "
                "Recopie-le depuis myaccount.google.com/apppasswords, lettres a-z seulement.")
    for nom, cle in (("mot de passe", "EMAIL_MOT_DE_PASSE"), ("clé API", "ANTHROPIC_API_KEY"),
                     ("jeton Telegram", "TELEGRAM_JETON")):
        if not str(v.get(cle, "")).isascii():
            mauvais = next(c for c in v[cle] if not c.isascii())
            return f"Ton {nom} contient un caractère spécial (« {mauvais} ») : retape-le à la main."
    if v.get("TELEGRAM_JETON") and ":" not in v["TELEGRAM_JETON"]:
        return "Le jeton Telegram doit ressembler à 123456789:ABC… (avec deux-points)."
    return None


def reglages_depuis_formulaire(email: str, mot_de_passe: str, cle_api: str, canal: str = "whatsapp",
                               numero: str = "", jeton_telegram: str = "", serveurs_manuels=None) -> dict:
    email = nettoyer(email)
    smtp = serveurs_manuels or serveurs(email) or ("", 465, "")
    v = {"ANTHROPIC_API_KEY": nettoyer(cle_api), "EMAIL_ADRESSE": email,
         "EMAIL_MOT_DE_PASSE": nettoyer_mot_de_passe(email, mot_de_passe),
         "SMTP_HOTE": smtp[0], "SMTP_PORT": smtp[1], "IMAP_HOTE": smtp[2], "CANAL": canal,
         "WHATSAPP_MON_NUMERO": "".join(c for c in numero if c.isdigit()).lstrip("0")}
    if v["WHATSAPP_MON_NUMERO"] and len(v["WHATSAPP_MON_NUMERO"]) <= 9:   # 0470… → 32470…
        v["WHATSAPP_MON_NUMERO"] = "32" + v["WHATSAPP_MON_NUMERO"]
    if jeton_telegram:
        v["TELEGRAM_JETON"] = nettoyer(jeton_telegram)
    return v


# ---------- Tests d'accès ----------
def tester_email(email: str, mot_de_passe: str, hote_smtp: str, port: int, hote_imap: str) -> str | None:
    """Renvoie None si tout va bien, sinon un message clair."""
    import imaplib
    import smtplib

    if not hote_smtp or not hote_imap:
        return "Je ne connais pas ce fournisseur : utilise une adresse Gmail (recommandé)."
    try:
        # local_hostname fixé : sinon Python envoie le nom du PC (plante s'il contient un accent)
        s = (smtplib.SMTP_SSL(hote_smtp, port, timeout=20, local_hostname="localhost") if port == 465
             else smtplib.SMTP(hote_smtp, port, timeout=20, local_hostname="localhost"))
        if port != 465:
            s.starttls()
        s.login(email, mot_de_passe)
        s.quit()
        m = imaplib.IMAP4_SSL(hote_imap, timeout=20)
        m.login(email, mot_de_passe)
        m.logout()
    except UnicodeEncodeError as e:
        return f"Caractère spécial refusé par le serveur email ({str(e)[:100]}). Envoie-moi ce message."
    except (smtplib.SMTPAuthenticationError, imaplib.IMAP4.error):
        return ("Adresse ou mot de passe refusé. Pour Gmail, il faut un « mot de passe d'application » "
                "(16 lettres), pas ton mot de passe habituel.")
    except Exception as e:
        return f"Connexion impossible au serveur email ({type(e).__name__} : {str(e)[:120]})."
    return None


def tester_cle(cle: str) -> str | None:
    if not cle.strip().startswith("sk-ant-"):
        return "La clé API doit commencer par « sk-ant- »."
    try:
        import anthropic

        anthropic.Anthropic(api_key=cle.strip(), max_retries=1).models.list(limit=1)
    except Exception as e:
        nom = type(e).__name__
        if "Authentication" in nom or "Permission" in nom:
            return "Clé API refusée. Recopie-la depuis console.anthropic.com."
        return f"Impossible de vérifier la clé ({nom}). Vérifie ta connexion internet."
    return None


# ---------- Pont WhatsApp ----------
def commande_pont() -> list[str] | None:
    """Exécutable du pont : intégré à l'application, sinon `node pont.cjs` en mode développeur."""
    nom = "pont-whatsapp.exe" if platform.system() == "Windows" else "pont-whatsapp"
    if gele():
        f = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)) / "pont" / nom
        return [str(f)] if f.exists() else None
    script = Path(__file__).resolve().parents[2] / "pont_whatsapp" / "pont.cjs"
    node = shutil.which("node")
    if node and script.exists() and (script.parent / "node_modules").exists():
        return [node, str(script)]
    return None


def lancer_pont(journal) -> subprocess.Popen | None:
    cmd = commande_pont()
    if cmd is None:
        journal("Pont WhatsApp introuvable (en mode développeur : cd pont_whatsapp && npm install).")
        return None
    options = {"creationflags": 0x08000000} if platform.system() == "Windows" else {}   # pas de fenêtre noire
    p = subprocess.Popen(cmd, env={**os.environ}, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         text=True, encoding="utf-8", errors="replace", **options)

    def lire():
        for ligne in p.stdout:
            ligne = ligne.rstrip()
            if ligne and not set(ligne) <= set("▄▀█ "):   # on n'affiche pas le QR en caractères
                journal(f"WhatsApp : {ligne}")

    import threading
    threading.Thread(target=lire, daemon=True).start()
    return p


def etat_pont() -> dict:
    import requests

    try:
        return requests.get(os.getenv("WHATSAPP_PONT_URL", "http://127.0.0.1:3020") + "/etat", timeout=2).json()
    except Exception:
        return {"connecte": False, "qr": None, "erreur": "pont injoignable"}


def matrice_qr(texte: str) -> list[list[bool]]:
    import qrcode

    q = qrcode.QRCode(border=2, error_correction=qrcode.constants.ERROR_CORRECT_M)
    q.add_data(texte)
    q.make(fit=True)
    return q.get_matrix()
