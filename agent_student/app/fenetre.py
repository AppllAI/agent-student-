"""La fenêtre de l'application (tkinter : intégré à Python, marche pareil sur Windows et Mac).

Écran 1 — Réglages : email, mot de passe d'application, clé API, messagerie. Bouton « Vérifier et démarrer ».
Écran 2 — Tableau de bord : QR code WhatsApp si besoin, discussion avec l'agent (aussi possible ici),
          journal, pause / reprendre."""
from __future__ import annotations

import io
import os
import platform
import queue
import subprocess
import sys
import threading
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import reglages

MARINE, OR, FOND, TEXTE, GRIS, ROUGE, VERT = "#14213d", "#c9a227", "#f6f4ef", "#1d1d1f", "#6b6b70", "#b3261e", "#2e7d32"


def ressource(nom: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
    return base / "assets" / nom


class _Sortie(io.TextIOBase):
    """Redirige les print() des agents vers le journal de la fenêtre."""

    def __init__(self, file_):
        self.file = file_

    def write(self, s):
        if s.strip():
            self.file.put(("journal", s.rstrip()))
        return len(s)


class CanalApp:
    """Le canal vu par l'agent : tout s'affiche dans la fenêtre ET part sur WhatsApp/Telegram si branché."""
    nom = "application"

    def __init__(self, file_, interne=None):
        self.file, self.interne, self.callback = file_, interne, None
        if interne:
            self.nom = f"application + {interne.nom}"

    def envoyer(self, texte, boutons=None):
        self.file.put(("agent", (texte, boutons)))
        if self.interne:
            self.interne.envoyer(texte, boutons)

    def occupe(self):
        if self.interne and hasattr(self.interne, "occupe"):
            self.interne.occupe()

    def envoyer_fichier(self, chemin, legende=""):
        self.file.put(("fichier", (str(chemin), legende)))
        if self.interne:
            self.interne.envoyer_fichier(chemin, legende)

    def ecouter(self, callback):
        self.callback = callback
        if self.interne:
            self.interne.ecouter(callback)
        else:
            threading.Event().wait()


class Application(tk.Tk):
    def __init__(self):
        super().__init__()
        self.racine = reglages.preparer_dossier()
        self.title("Agent Student")
        self.geometry("560x760")
        self.minsize(480, 620)
        self.configure(bg=FOND)
        self.file: queue.Queue = queue.Queue()
        self.canal = None
        self.pont = None
        self.demarre = False
        try:
            self.logo = tk.PhotoImage(file=str(ressource("icone_256.png")))
            self.iconphoto(True, self.logo)
            self.petit_logo = self.logo.subsample(4, 4)
            self.logo_moyen = self.logo.subsample(2, 2)
        except Exception:
            self.logo = self.petit_logo = self.logo_moyen = None
        self._styles()
        self.protocol("WM_DELETE_WINDOW", self.quitter)
        self.after(120, self._vider_file)
        env = reglages.lire_env(self.racine)
        if env.get("ANTHROPIC_API_KEY") and env.get("EMAIL_ADRESSE") and env.get("EMAIL_MOT_DE_PASSE"):
            self.ecran_tableau()
        else:
            self.ecran_reglages()

    # ---------- apparence ----------
    def _styles(self):
        s = ttk.Style(self)
        s.theme_use("clam")
        s.configure(".", background=FOND, foreground=TEXTE, font=("Helvetica", 12))
        s.configure("Titre.TLabel", font=("Helvetica", 22, "bold"), foreground=MARINE)
        s.configure("Sous.TLabel", foreground=GRIS, font=("Helvetica", 11))
        s.configure("Lien.TLabel", foreground="#1a5fb4", font=("Helvetica", 11, "underline"))
        s.configure("Erreur.TLabel", foreground=ROUGE, font=("Helvetica", 11))
        s.configure("Ok.TLabel", foreground=VERT, font=("Helvetica", 11, "bold"))
        s.configure("TEntry", padding=7, fieldbackground="white")
        s.configure("Principal.TButton", background=MARINE, foreground="white", padding=(16, 10),
                    font=("Helvetica", 13, "bold"), borderwidth=0)
        s.map("Principal.TButton", background=[("active", "#22355f"), ("disabled", "#8a93a8")])
        s.configure("TButton", padding=(10, 6))
        s.configure("TRadiobutton", background=FOND)

    def _vider(self):
        for w in self.winfo_children():
            w.destroy()

    def _lien(self, parent, texte, url):
        l = ttk.Label(parent, text=texte, style="Lien.TLabel", cursor="hand2")
        l.bind("<Button-1>", lambda _e: webbrowser.open(url))
        return l

    # ---------- écran 1 : réglages ----------
    def ecran_reglages(self):
        self._vider()
        env = reglages.lire_env(self.racine)
        self.ecran = "reglages"
        # Barre du bas TOUJOURS visible (bouton + message), quelle que soit la taille de l'écran
        bas = ttk.Frame(self, padding=(28, 8, 28, 14))
        bas.pack(side="bottom", fill="x")
        self.msg = ttk.Label(bas, text="", style="Erreur.TLabel", wraplength=900, justify="left")
        self.msg.pack(fill="x", pady=(0, 4))
        self.bouton = ttk.Button(bas, text="Vérifier et démarrer  ⏎", style="Principal.TButton", command=self._valider)
        self.bouton.pack(fill="x")
        ttk.Label(bas, text="Tout reste sur ton ordinateur. Rien n'est envoyé ailleurs qu'à ton email et à Claude.",
                  style="Sous.TLabel", justify="center").pack(pady=(6, 0))
        self.bind("<Return>", lambda _e: self._valider() if getattr(self, "ecran", "") == "reglages" else None)

        # Contenu défilant (molette) pour les petits écrans
        zone = tk.Canvas(self, bg=FOND, highlightthickness=0)
        barre = ttk.Scrollbar(self, orient="vertical", command=zone.yview)
        zone.configure(yscrollcommand=barre.set)
        barre.pack(side="right", fill="y")
        zone.pack(side="top", fill="both", expand=True)
        cadre = ttk.Frame(zone, padding=(28, 14, 28, 8))
        fen = zone.create_window((0, 0), window=cadre, anchor="nw")
        cadre.bind("<Configure>", lambda _e: zone.configure(scrollregion=zone.bbox("all")))
        zone.bind("<Configure>", lambda e: zone.itemconfigure(fen, width=e.width))
        self.bind_all("<MouseWheel>", lambda e: zone.yview_scroll(int(-e.delta / 120) or (-1 if e.delta > 0 else 1), "units")
                      if getattr(self, "ecran", "") == "reglages" and zone.winfo_exists() else None)
        if self.petit_logo:
            tk.Label(cadre, image=self.petit_logo, bg=FOND).pack()
        ttk.Label(cadre, text="Agent Student", style="Titre.TLabel").pack(pady=(4, 0))
        ttk.Label(cadre, text="Il postule à ta place et te prévient seulement quand il faut décider.",
                  style="Sous.TLabel", wraplength=460, justify="center").pack(pady=(2, 8))

        self.v_email = tk.StringVar(value=env.get("EMAIL_ADRESSE", ""))
        self.v_mdp = tk.StringVar(value=env.get("EMAIL_MOT_DE_PASSE", ""))
        self.v_cle = tk.StringVar(value=env.get("ANTHROPIC_API_KEY", ""))
        self.v_canal = tk.StringVar(value=env.get("CANAL", "telegram") if env.get("CANAL") != "console" else "app")
        self.v_openai = tk.StringVar(value=env.get("OPENAI_API_KEY", ""))
        self.v_numero = tk.StringVar(value=env.get("WHATSAPP_MON_NUMERO", ""))
        self.v_jeton = tk.StringVar(value=env.get("TELEGRAM_JETON", ""))

        def champ(titre, var, aide=None, secret=False, lien=None):
            ttk.Label(cadre, text=titre, font=("Helvetica", 12, "bold")).pack(anchor="w", pady=(6, 2))
            e = ttk.Entry(cadre, textvariable=var, show="•" if secret else "")
            e.pack(fill="x")
            ligne = ttk.Frame(cadre)
            ligne.pack(fill="x")
            if aide:
                ttk.Label(ligne, text=aide, style="Sous.TLabel").pack(side="left")
            if lien:
                self._lien(ligne, lien[0], lien[1]).pack(side="right")
            return ligne

        champ("1. Adresse email des candidatures", self.v_email,
              "Une adresse dédiée, idéalement Gmail. Les employeurs te répondront là.")
        self.ligne_mdp = champ("2. Mot de passe d'application", self.v_mdp,
                               "Pas ton mot de passe habituel.", secret=True,
                               lien=("Comment l'obtenir ?", reglages.AIDE_MOT_DE_PASSE["gmail"]))
        champ("3. Clé API Claude", self.v_cle, "Commence par sk-ant-…", secret=True,
              lien=("Créer ma clé", reglages.LIEN_CLE_API))

        ttk.Label(cadre, text="Où l'agent te parle", font=("Helvetica", 12, "bold")).pack(anchor="w", pady=(16, 2))
        choix = ttk.Frame(cadre)
        choix.pack(fill="x")
        for texte, val in (("Telegram (conseillé)", "telegram"), ("WhatsApp", "whatsapp"), ("Ici seulement", "app")):
            ttk.Radiobutton(choix, text=texte, value=val, variable=self.v_canal,
                            command=self._maj_canal).pack(side="left", padx=(0, 14))
        self.zone_canal = ttk.Frame(cadre)
        self.zone_canal.pack(fill="x")
        self._maj_canal()


    def _maj_canal(self):
        for w in self.zone_canal.winfo_children():
            w.destroy()
        c = self.v_canal.get()
        if c == "whatsapp":
            ttk.Label(self.zone_canal, text="Ton numéro WhatsApp (facultatif)", style="Sous.TLabel").pack(anchor="w", pady=(8, 0))
            ttk.Entry(self.zone_canal, textvariable=self.v_numero).pack(fill="x")
            ttk.Label(self.zone_canal, text="Vide = l'agent t'écrit dans « Message à moi-même ». Un QR code s'affichera.",
                      style="Sous.TLabel", wraplength=480).pack(anchor="w")
        elif c == "telegram":
            l = ttk.Frame(self.zone_canal)
            l.pack(fill="x", pady=(8, 0))
            ttk.Label(l, text="Jeton de ton bot Telegram", style="Sous.TLabel").pack(side="left")
            self._lien(l, "Ouvrir @BotFather", "https://t.me/BotFather").pack(side="right")
            ttk.Entry(self.zone_canal, textvariable=self.v_jeton).pack(fill="x")
            ttk.Label(self.zone_canal, wraplength=480, style="Sous.TLabel", justify="left",
                      text="2 min : dans Telegram, ouvre @BotFather → /newbot → choisis un nom → copie le jeton ici.").pack(anchor="w")
        if c in ("telegram", "whatsapp"):
            l = ttk.Frame(self.zone_canal)
            l.pack(fill="x", pady=(8, 0))
            ttk.Label(l, text="Clé OpenAI pour les vocaux (facultatif)", style="Sous.TLabel").pack(side="left")
            self._lien(l, "Pourquoi ?", "https://platform.openai.com/api-keys").pack(side="right")
            ttk.Entry(self.zone_canal, textvariable=self.v_openai, show="•").pack(fill="x")

    def _valider(self):
        if self.bouton.instate(["disabled"]):     # déjà en cours de vérification
            return
        email, mdp, cle, canal = self.v_email.get().strip(), self.v_mdp.get(), self.v_cle.get().strip(), self.v_canal.get()
        if not (email and mdp and cle):
            return self.msg.configure(text="Remplis les trois cases.", style="Erreur.TLabel")
        if "@" not in email:
            return self.msg.configure(text="Adresse email invalide.", style="Erreur.TLabel")
        if canal == "telegram" and not self.v_jeton.get().strip():
            return self.msg.configure(text="Colle le jeton de ton bot Telegram.", style="Erreur.TLabel")
        self.bouton.state(["disabled"])
        self.msg.configure(text="Vérification de l'email et de la clé…", style="Sous.TLabel")
        v = reglages.reglages_depuis_formulaire(email, mdp, cle, "console" if canal == "app" else canal,
                                                self.v_numero.get(), self.v_jeton.get())
        if self.v_openai.get().strip():
            v["OPENAI_API_KEY"] = reglages.nettoyer(self.v_openai.get())
        souci = reglages.probleme_formulaire(v)
        if souci:
            self.bouton.state(["!disabled"])
            return self.msg.configure(text=souci, style="Erreur.TLabel")

        def verifier():
            err = reglages.tester_email(v["EMAIL_ADRESSE"], v["EMAIL_MOT_DE_PASSE"], v["SMTP_HOTE"],
                                        int(v["SMTP_PORT"]), v["IMAP_HOTE"]) or reglages.tester_cle(v["ANTHROPIC_API_KEY"])
            self.file.put(("verifie", (err, v)))

        threading.Thread(target=verifier, daemon=True).start()

    def _apres_verification(self, err, v):
        self.bouton.state(["!disabled"])
        if err:
            return self.msg.configure(text=err, style="Erreur.TLabel")
        reglages.ecrire_env(self.racine, v)
        self.msg.configure(text="✓ Tout est bon !", style="Ok.TLabel")
        self.after(500, self.ecran_tableau)

    # ---------- écran 2 : tableau de bord ----------
    def ecran_tableau(self):
        self.ecran = "tableau"
        self._vider()
        haut = tk.Frame(self, bg=MARINE)
        haut.pack(fill="x")
        if self.petit_logo:
            tk.Label(haut, image=self.petit_logo, bg=MARINE).pack(side="left", padx=(14, 8), pady=10)
        tk.Label(haut, text="Agent Student", bg=MARINE, fg="white", font=("Helvetica", 16, "bold")).pack(side="left")
        self.pastille = tk.Label(haut, text="● démarrage…", bg=MARINE, fg=OR, font=("Helvetica", 11, "bold"))
        self.pastille.pack(side="right", padx=14)

        barre = ttk.Frame(self, padding=(12, 8))
        barre.pack(fill="x")
        self.b_pause = ttk.Button(barre, text="⏸ Pause", command=self._pause)
        self.b_pause.pack(side="left")
        ttk.Button(barre, text="📁 Dossier", command=self._ouvrir_dossier).pack(side="left", padx=6)
        ttk.Button(barre, text="⚙ Réglages", command=self._vers_reglages).pack(side="right")

        self.zone_qr = ttk.Frame(self, padding=(12, 0))
        self.zone_qr.pack(fill="x")

        onglets = ttk.Notebook(self)
        onglets.pack(fill="both", expand=True, padx=12, pady=(4, 0))
        f_chat, f_journal = ttk.Frame(onglets), ttk.Frame(onglets)
        onglets.add(f_chat, text="  Discussion  ")
        onglets.add(f_journal, text="  Ce que font les agents  ")

        self.chat = tk.Text(f_chat, wrap="word", bg="white", relief="flat", padx=12, pady=10,
                            font=("Helvetica", 12), state="disabled")
        self.chat.pack(fill="both", expand=True)
        self.chat.tag_configure("agent", foreground=TEXTE, spacing3=10, lmargin1=4, lmargin2=4)
        self.chat.tag_configure("moi", foreground=MARINE, justify="right", spacing3=10,
                                font=("Helvetica", 12, "bold"))
        self.chat.tag_configure("info", foreground=GRIS, font=("Helvetica", 10, "italic"))
        self.journal = tk.Text(f_journal, wrap="word", bg="#fbfbfa", relief="flat", padx=10, pady=8,
                               font=("Menlo" if platform.system() == "Darwin" else "Consolas", 10), state="disabled")
        self.journal.pack(fill="both", expand=True)

        bas = ttk.Frame(self, padding=12)
        bas.pack(fill="x")
        ttk.Button(bas, text="📎 CV", width=5, command=self._envoyer_cv).pack(side="left")
        self.saisie = ttk.Entry(bas)
        self.saisie.pack(side="left", fill="x", expand=True, padx=8)
        self.saisie.bind("<Return>", lambda _e: self._envoyer_texte())
        ttk.Button(bas, text="Envoyer", command=self._envoyer_texte).pack(side="right")
        self._demarrer_agent()

    def _ajouter(self, widget, texte, tag=None):
        widget.configure(state="normal")
        widget.insert("end", texte + "\n", tag)
        if int(widget.index("end-1c").split(".")[0]) > 3000:     # l'app tourne H24 : on garde les 3000 dernières lignes
            widget.delete("1.0", "1000.0")
        widget.configure(state="disabled")
        widget.see("end")

    def _boutons(self, boutons):
        """Les boutons de l'agent, cliquables directement dans la fenêtre."""
        if not boutons:
            return
        self.chat.configure(state="normal")
        for ligne in boutons:
            for lib, cmd in ligne:
                b = ttk.Button(self.chat, text=lib, command=lambda c=cmd: self._commande(c))
                self.chat.window_create("end", window=b, padx=3, pady=2)
            self.chat.insert("end", "\n")
        self.chat.configure(state="disabled")
        self.chat.see("end")

    def _commande(self, cmd):
        if self.canal and self.canal.callback:
            from ..agents.concierge import Entrant
            self._ajouter(self.chat, f"▸ {cmd}", "moi")
            self.canal.callback(Entrant(texte=cmd))

    def _demarrer_agent(self):
        if self.demarre:
            return self._rafraichir_etat()
        self.demarre = True
        sys.stdout = sys.stderr = _Sortie(self.file)
        from .. import config  # noqa: E402  (importé après la préparation du dossier)
        config.recharger_env()
        canal_nom = config.env("CANAL", "console")
        interne = None
        if canal_nom == "whatsapp":
            self.pont = reglages.lancer_pont(lambda t: self.file.put(("journal", t)))
            from ..canaux.whatsapp import WhatsApp
            interne = WhatsApp()
            self.after(1500, self._suivre_whatsapp)
        elif canal_nom == "telegram":
            from ..canaux.telegram import Telegram
            interne = Telegram()
            self.after(1500, lambda: self._suivre_telegram(interne))
        self.canal = CanalApp(self.file, interne)
        empecher_veille()
        demarrage_automatique(lambda t: self.file.put(("journal", t)))

        def lancer():
            from .. import orchestrateur
            try:
                orchestrateur.demarrer(self.canal)
            except Exception as e:
                self.file.put(("journal", f"Erreur fatale : {e}"))

        threading.Thread(target=lancer, daemon=True).start()
        self._ajouter(self.chat, "L'agent démarre… Tu peux lui parler ici ou sur ta messagerie.", "info")
        self.after(1500, self._rafraichir_etat)

    def _rafraichir_etat(self):
        try:
            from .. import db
            pause = db.kv_get("pause", False)
        except Exception:
            pause = False
        self.pastille.configure(text="● en pause" if pause else "● en marche", fg=OR if pause else "#7bd88f")
        self.b_pause.configure(text="▶ Reprendre" if pause else "⏸ Pause")

    def _suivre_whatsapp(self):
        if not self.winfo_exists() or not hasattr(self, "zone_qr"):
            return
        e = reglages.etat_pont()
        for w in self.zone_qr.winfo_children():
            w.destroy()
        if e.get("qr") and not e.get("connecte"):
            ttk.Label(self.zone_qr, text="Scanne avec WhatsApp › Réglages › Appareils connectés › Connecter",
                      font=("Helvetica", 12, "bold"), wraplength=500).pack(pady=(4, 6))
            self._dessiner_qr(e["qr"])
        elif e.get("connecte"):
            ttk.Label(self.zone_qr, text=f"✓ WhatsApp connecté ({e.get('numero')})", style="Ok.TLabel").pack(anchor="w")
            self.after(30000, self._suivre_whatsapp)
            return
        else:
            ttk.Label(self.zone_qr, text="Connexion à WhatsApp…", style="Sous.TLabel").pack(anchor="w")
        self.after(2500, self._suivre_whatsapp)

    def _suivre_telegram(self, tg):
        """Tant que ton Telegram n'est pas relié : QR code + lien d'appairage dans la fenêtre."""
        if not self.winfo_exists() or not hasattr(self, "zone_qr"):
            return
        for w in self.zone_qr.winfo_children():
            w.destroy()
        if tg.chat:
            ttk.Label(self.zone_qr, text="✓ Telegram relié — tu peux fermer cette fenêtre de l'esprit, "
                                         "tout se passe sur ton téléphone.", style="Ok.TLabel", wraplength=500).pack(anchor="w")
            return
        lien = tg.lien_appairage()
        if lien:
            ttk.Label(self.zone_qr, text="Scanne avec l'appareil photo de ton téléphone pour relier Telegram :",
                      font=("Helvetica", 12, "bold"), wraplength=500).pack(pady=(4, 6))
            self._dessiner_qr(lien)
            self._lien(self.zone_qr, lien, lien).pack(pady=(4, 0))
            ttk.Label(self.zone_qr, text=f"ou envoie ce code au bot : {tg.code}", font=("Helvetica", 13, "bold")).pack()
        else:
            ttk.Label(self.zone_qr, text="Jeton Telegram refusé : vérifie-le dans ⚙ Réglages.", style="Erreur.TLabel").pack()
        self.after(3000, lambda: self._suivre_telegram(tg))

    def _dessiner_qr(self, texte):
        try:
            m = reglages.matrice_qr(texte)
        except Exception as e:
            return ttk.Label(self.zone_qr, text=f"QR indisponible : {e}", style="Erreur.TLabel").pack()
        taille = 230
        case = taille / len(m)
        c = tk.Canvas(self.zone_qr, width=taille, height=taille, bg="white", highlightthickness=0)
        c.pack()
        for y, ligne in enumerate(m):
            for x, plein in enumerate(ligne):
                if plein:
                    c.create_rectangle(x * case, y * case, (x + 1) * case, (y + 1) * case, fill="black", width=0)

    # ---------- actions ----------
    def _envoyer_texte(self):
        t = self.saisie.get().strip()
        if not t or not self.canal or not self.canal.callback:
            return
        self.saisie.delete(0, "end")
        self._ajouter(self.chat, t, "moi")
        from ..agents.concierge import Entrant
        self.canal.callback(Entrant(texte=t))
        self.after(800, self._rafraichir_etat)

    def _envoyer_cv(self):
        f = filedialog.askopenfilename(title="Choisis ton CV", filetypes=[("PDF", "*.pdf")])
        if f and self.canal and self.canal.callback:
            from ..agents.concierge import Entrant
            self._ajouter(self.chat, f"📄 {Path(f).name}", "moi")
            self.canal.callback(Entrant(fichier=Path(f), nom_fichier=Path(f).name))

    def _pause(self):
        from .. import db
        db.kv_set("pause", not db.kv_get("pause", False))
        self._rafraichir_etat()

    def _ouvrir_dossier(self):
        ouvrir(self.racine)

    def _vers_reglages(self):
        if self.demarre:
            messagebox.showinfo("Réglages", "Les nouveaux réglages s'appliqueront au prochain lancement de l'application.")
        self.ecran_reglages()

    def _vider_file(self):
        try:
            while True:
                genre, x = self.file.get_nowait()
                if genre == "verifie":
                    self._apres_verification(*x)
                elif not hasattr(self, "chat") or not self.chat.winfo_exists():
                    continue
                elif genre == "agent":
                    texte, boutons = x if isinstance(x, tuple) else (x, None)
                    self._ajouter(self.chat, texte, "agent")
                    self._boutons(boutons)
                elif genre == "fichier":
                    self._ajouter(self.chat, f"📎 {x[1]} — {Path(x[0]).name}", "info")
                elif genre == "journal":
                    self._ajouter(self.journal, x)
        except queue.Empty:
            pass
        self.after(150, self._vider_file)

    def quitter(self):
        if self.demarre and not messagebox.askyesno(
                "Quitter", "Si tu fermes l'application, l'agent s'arrête.\nTu peux plutôt la réduire. Quitter quand même ?"):
            return
        if self.pont:
            self.pont.terminate()
        self.destroy()
        os._exit(0)


def ouvrir(chemin: Path):
    if platform.system() == "Windows":
        os.startfile(chemin)  # type: ignore[attr-defined]
    elif platform.system() == "Darwin":
        subprocess.Popen(["open", str(chemin)])
    else:
        subprocess.Popen(["xdg-open", str(chemin)])


def demarrage_automatique(journal):
    """Windows : l'agent se relance tout seul après un redémarrage du PC (mises à jour Windows…)."""
    if platform.system() != "Windows" or not reglages.gele():
        return
    try:
        dossier = Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
        f = dossier / "AgentStudent.cmd"
        contenu = f'@echo off\r\nstart "" "{sys.executable}"\r\n'
        if not f.exists() or f.read_text(encoding="utf-8", errors="ignore") != contenu:
            f.write_text(contenu, encoding="utf-8")
            journal("Démarrage automatique activé : l'agent se relancera après un redémarrage du PC.")
    except Exception as e:
        journal(f"Démarrage automatique impossible : {e}")


def empecher_veille():
    """L'agent doit tourner H24 : on empêche la mise en veille tant que l'application est ouverte."""
    try:
        if platform.system() == "Windows":
            import ctypes
            ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | 0x00000001)  # CONTINUOUS | SYSTEM_REQUIRED
        elif platform.system() == "Darwin":
            subprocess.Popen(["caffeinate", "-i", "-w", str(os.getpid())])
    except Exception:
        pass


def main():
    if not reglages.verrou_instance():
        racine = tk.Tk()
        racine.withdraw()
        messagebox.showinfo("Agent Student", "Agent Student est déjà ouvert sur cet ordinateur.\n"
                                            "Regarde dans la barre des tâches (ou ferme-le avant de le relancer).")
        return
    Application().mainloop()
