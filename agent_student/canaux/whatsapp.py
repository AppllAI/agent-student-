"""Canal WhatsApp — via le petit pont Node `pont_whatsapp/` (librairie Baileys, la plus utilisée en open source).

Le pont se connecte à TON WhatsApp comme un « appareil connecté » (QR code, comme WhatsApp Web).
L'agent t'écrit dans ta discussion « Message à moi-même » et tu lui réponds là (texte, vocal, PDF).

  Python  ──POST /envoyer, /fichier──►  pont Node  ──►  WhatsApp
  Python  ◄──POST /entrant────────────  pont Node  ◄──  tes messages
"""
from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import requests

from .. import config


class WhatsApp:
    nom = "whatsapp"

    def __init__(self):
        self.pont = config.env("WHATSAPP_PONT_URL", "http://127.0.0.1:3020")
        self.port = config.env_int("PORT_AGENT", 3021)

    def envoyer(self, texte, boutons=None):
        if boutons:   # pas de boutons sur WhatsApp non officiel : on indique quoi répondre
            texte += "\n\n" + "\n".join(f"→ « {cmd} » : {lib}" for ligne in boutons for lib, cmd in ligne)
        try:
            requests.post(f"{self.pont}/envoyer", json={"texte": texte}, timeout=20)
        except Exception as e:
            print(f"(pont WhatsApp injoignable : {e})\n🤖 {texte}")

    def envoyer_fichier(self, chemin: Path, legende=""):
        try:
            requests.post(f"{self.pont}/fichier", json={"chemin": str(Path(chemin).resolve()), "legende": legende},
                          timeout=60)
        except Exception as e:
            print(f"(pont WhatsApp injoignable : {e})")

    def ecouter(self, callback):
        from ..agents.concierge import Entrant

        class Recepteur(BaseHTTPRequestHandler):
            def do_POST(self):
                d = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"ok")
                callback(Entrant(texte=d.get("texte", ""),
                                 audio=Path(d["audio"]) if d.get("audio") else None,
                                 fichier=Path(d["fichier"]) if d.get("fichier") else None,
                                 nom_fichier=d.get("nom_fichier", "")))

            def log_message(self, *a):
                pass

        print(f"Agent à l'écoute du pont WhatsApp sur 127.0.0.1:{self.port}", flush=True)
        ThreadingHTTPServer(("127.0.0.1", self.port), Recepteur).serve_forever()
