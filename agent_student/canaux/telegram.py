"""Canal Telegram — le canal recommandé : API officielle et gratuite, zéro risque de blocage.

Ce que tu vis sur ton téléphone :
  • l'agent t'écrit comme un contact ; tu reçois une notification normale, même appli fermée ;
  • boutons sous les messages (✅ Envoyer, 👁 Lettre, ❌ Non, ✅ Jeudi 16h30…) ;
  • « … écrit » s'affiche dès que ton message est pris en compte ;
  • vocaux 🎙️ → transcrits → compris ; PDF (ton CV) → lu ;
  • menu des commandes (/stats, /cherche, /mode, /aide).

Mise en place : @BotFather → /newbot → jeton dans TELEGRAM_JETON. Puis ouvre le lien d'appairage
affiché au démarrage (t.me/<ton_bot>?start=<code>) : seul TON compte sera écouté."""
from __future__ import annotations

import random
import time
from pathlib import Path

import requests

from .. import config, db


class Telegram:
    nom = "telegram"

    def __init__(self):
        self.jeton = config.env("TELEGRAM_JETON")
        if not self.jeton:
            raise SystemExit("TELEGRAM_JETON manquant dans .env")
        self.api = f"https://api.telegram.org/bot{self.jeton}"
        self.chat = db.kv_get("telegram_chat")
        self.code = db.kv_get("telegram_code") or str(random.randint(100000, 999999))
        db.kv_set("telegram_code", self.code)
        self.bot = None

    # ---------- envoi ----------
    def _post(self, methode, **kw):
        r = requests.post(f"{self.api}/{methode}", timeout=70, **kw)
        if r.status_code == 429:                       # Telegram demande de ralentir
            time.sleep(r.json().get("parameters", {}).get("retry_after", 3))
            r = requests.post(f"{self.api}/{methode}", timeout=70, **kw)
        r.raise_for_status()
        return r.json()["result"]

    @staticmethod
    def _clavier(boutons):
        if not boutons:
            return None
        import json
        return json.dumps({"inline_keyboard": [[{"text": lib[:40], "callback_data": cmd[:64]} for lib, cmd in ligne]
                                               for ligne in boutons]})

    def envoyer(self, texte, boutons=None):
        if not self.chat:
            print(f"(Telegram pas encore appairé) {texte}", flush=True)
            return
        morceaux = [texte[i:i + 4000] for i in range(0, len(texte), 4000)] or [""]
        for k, m in enumerate(morceaux):
            data = {"chat_id": self.chat, "text": m, "parse_mode": "Markdown", "disable_web_page_preview": True}
            clavier = self._clavier(boutons) if k == len(morceaux) - 1 else None
            if clavier:
                data["reply_markup"] = clavier
            try:
                self._post("sendMessage", data=data)
            except requests.HTTPError:                 # Markdown invalide (ex. « _ » dans un email) → texte brut
                data.pop("parse_mode")
                self._post("sendMessage", data=data)

    def envoyer_fichier(self, chemin: Path, legende=""):
        if self.chat and Path(chemin).exists():
            with open(chemin, "rb") as f:
                self._post("sendDocument", data={"chat_id": self.chat, "caption": legende[:1000]}, files={"document": f})

    def occupe(self):
        if self.chat:
            self._post("sendChatAction", data={"chat_id": self.chat, "action": "typing"})

    # ---------- appairage ----------
    def lien_appairage(self) -> str | None:
        try:
            self.bot = self.bot or self._post("getMe")["username"]
            return f"https://t.me/{self.bot}?start={self.code}"
        except Exception:
            return None

    def _menu(self):
        cmds = [("stats", "Où en est la recherche"), ("cherche", "Chercher maintenant"),
                ("mode", "Répétition / validation / auto"), ("pause", "Tout mettre en pause"),
                ("reprendre", "Reprendre"), ("aide", "Ce que je sais faire")]
        import json
        try:
            self._post("setMyCommands", data={"commands": json.dumps([{"command": c, "description": d} for c, d in cmds])})
        except Exception:
            pass

    def _telecharger(self, file_id, nom):
        info = self._post("getFile", data={"file_id": file_id})
        dossier = config.DONNEES / "recus"
        dossier.mkdir(parents=True, exist_ok=True)
        dest = dossier / f"{int(time.time())}_{Path(nom).name}"
        dest.write_bytes(requests.get(f"https://api.telegram.org/file/bot{self.jeton}/{info['file_path']}",
                                      timeout=120).content)
        return dest

    # ---------- écoute ----------
    def ecouter(self, callback):
        from ..agents.concierge import Entrant

        try:   # un webhook éventuel empêcherait de recevoir les messages
            self._post("deleteWebhook", data={"drop_pending_updates": "false"})
        except Exception as e:
            print("Telegram : jeton refusé ou pas d'internet :", e, flush=True)
        self._menu()
        print("Telegram : à l'écoute des messages", flush=True)
        if not self.chat:
            lien = self.lien_appairage()
            print(f"\n👉 Ouvre ce lien sur ton téléphone pour relier ton Telegram : {lien}\n"
                  f"   (ou envoie ce code à ton bot : {self.code})\n", flush=True)
        decalage = None
        while True:
            try:
                maj = self._post("getUpdates", data={"timeout": 50, "offset": decalage,
                                                     "allowed_updates": '["message","callback_query"]'})
            except requests.HTTPError as e:
                if e.response is not None and e.response.status_code == 409:
                    print("Telegram : ce bot est déjà utilisé par un AUTRE programme (agent ouvert deux fois, "
                          "ou sur un autre PC). Ferme l'autre.", flush=True)
                    time.sleep(30)
                elif e.response is not None and e.response.status_code == 401:
                    print("Telegram : jeton refusé. Vérifie-le dans ⚙ Réglages (ou BotFather → Revoke).", flush=True)
                    time.sleep(60)
                else:
                    print("Telegram :", e, flush=True)
                    time.sleep(10)
                continue
            except Exception as e:
                print("Telegram : pas de connexion internet ?", type(e).__name__, flush=True)
                time.sleep(10)
                continue
            for u in maj:
                decalage = u["update_id"] + 1
                try:
                    self._traiter(u, callback, Entrant)
                except Exception as e:
                    print("Telegram (message) :", e, flush=True)

    def _traiter(self, u, callback, Entrant):
        if "callback_query" in u:                      # un bouton touché
            q = u["callback_query"]
            if q["message"]["chat"]["id"] != self.chat:
                return
            self._post("answerCallbackQuery", data={"callback_query_id": q["id"], "text": "👍"})
            try:   # on retire les boutons du message pour ne pas valider deux fois
                self._post("editMessageReplyMarkup", data={"chat_id": self.chat,
                                                           "message_id": q["message"]["message_id"]})
            except Exception:
                pass
            return callback(Entrant(texte=q["data"]))
        m = u.get("message") or {}
        chat = m.get("chat", {}).get("id")
        texte = (m.get("text") or "").strip()
        if not self.chat:
            code = texte.removeprefix("/start").strip()
            if code == self.code:
                self.chat = chat
                db.kv_set("telegram_chat", chat)
                db.kv_set("bienvenue_envoyee", True)
                print(f"✅ Telegram relié (conversation {chat})", flush=True)
                from ..agents import coach
                coach.bienvenue()
            elif chat:
                print(f"Telegram : message reçu (« {texte[:30]} ») mais pas le bon code d'appairage", flush=True)
                self._post("sendMessage", data={"chat_id": chat, "text":
                           "🔒 Pour relier ce bot, envoie ici le code à 6 chiffres affiché dans l'application "
                           "Agent Student sur ton ordinateur (sous le QR code)."})
            return
        if chat != self.chat:
            return
        if "voice" in m or "audio" in m:
            a = m.get("voice") or m.get("audio")
            self.occupe()
            callback(Entrant(audio=self._telecharger(a["file_id"], "vocal.ogg")))
        elif "document" in m:
            d = m["document"]
            callback(Entrant(fichier=self._telecharger(d["file_id"], d.get("file_name", "doc")),
                             nom_fichier=d.get("file_name", ""), texte=m.get("caption", "")))
        elif texte and not texte.startswith("/start"):
            callback(Entrant(texte=texte))
