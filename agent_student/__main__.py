"""Point d'entrée.

  python -m agent_student                 démarre l'agent (canal défini par CANAL dans .env)
  python -m agent_student --canal console discuter dans le terminal
  python -m agent_student --une-fois      un seul cycle complet (test)
  python -m agent_student --stats         bilan
  python -m agent_student --verifier      vérifie l'installation
  python -m agent_student --essai URL     toute la chaîne sur une offre, sans rien envoyer
  python -m agent_student --banc-essai    5 offres types → rapport qualité donnees/banc_essai.md (sans envoi)
"""
from __future__ import annotations

import argparse
import importlib
import shutil

from . import config, db


def verifier():
    ok = True

    def ligne(bon, texte, conseil=""):
        nonlocal ok
        ok &= bon
        print(("✅ " if bon else "❌ ") + texte + ("" if bon else f"  → {conseil}"))

    ligne(bool(config.env("ANTHROPIC_API_KEY")) or config.simulation(), "Clé API Claude",
          "ajoute ANTHROPIC_API_KEY dans .env")
    print("   mode :", "SIMULATION (aucun envoi réel)" if config.simulation() else "RÉEL")
    for mod, role in (("yaml", "pyyaml"), ("requests", "requests"), ("anthropic", "anthropic"),
                      ("fpdf", "fpdf2 (PDF des lettres)"), ("pypdf", "pypdf (lecture du CV)")):
        try:
            importlib.import_module(mod)
            ligne(True, role)
        except ImportError:
            if mod in ("yaml", "requests") or (mod == "anthropic" and not config.simulation()):
                ligne(False, role, "pip install -r requirements.txt")
            else:
                print("➖", role, "non installé (pip install -r requirements.txt)")
    for mod, role in (("jobspy", "JobSpy (Indeed, optionnel)"), ("faster_whisper", "vocaux en local (optionnel)")):
        try:
            importlib.import_module(mod)
            print("✅", role)
        except ImportError:
            print("➖", role, "non installé")
    canal = config.env("CANAL", "telegram")
    print("   canal :", canal)
    if canal == "whatsapp":
        ligne(shutil.which("node") is not None, "Node.js (pont WhatsApp)", "installe Node 20+")
    if canal == "telegram":
        ligne(bool(config.env("TELEGRAM_JETON")), "Jeton Telegram", "TELEGRAM_JETON dans .env")
    if not config.simulation():
        ligne(bool(config.env("EMAIL_ADRESSE") and config.env("EMAIL_MOT_DE_PASSE")), "Email d'envoi",
              "EMAIL_ADRESSE et EMAIL_MOT_DE_PASSE dans .env")
    ligne(config.chemin_cv().exists(), "CV (profil/cv.pdf)", "envoie-le à l'agent sur ta messagerie")
    return ok


def main():
    p = argparse.ArgumentParser(prog="agent_student")
    p.add_argument("--canal", choices=["console", "whatsapp", "telegram"])
    p.add_argument("--une-fois", action="store_true")
    p.add_argument("--stats", action="store_true")
    p.add_argument("--verifier", action="store_true")
    p.add_argument("--essai", metavar="URL")
    p.add_argument("--banc-essai", action="store_true")
    a = p.parse_args()
    db.connexion()
    if a.verifier:
        raise SystemExit(0 if verifier() else 1)
    from . import orchestrateur
    if a.stats:
        from .agents.rapporteur import stats
        return print(stats())
    if a.une_fois:
        return orchestrateur.un_cycle()
    if a.essai:
        from .agents.essai import essayer
        return essayer(a.essai)
    if a.banc_essai:
        from .agents.essai import banc_essai
        return print("Rapport :", banc_essai())
    from .canaux import choisir
    orchestrateur.demarrer(choisir(a.canal))


if __name__ == "__main__":
    main()
