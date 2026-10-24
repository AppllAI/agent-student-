"""Test de bout en bout en SIMULATION : recherche → règles → lettres → répétition → envoi réel →
entretien → rendez-vous → relance → refus silencieux. Lancer : python -m unittest discover tests -v"""
import json
import os
import sys
import tempfile
import unittest
from email.message import EmailMessage
from pathlib import Path

RACINE_PROJET = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp(prefix="agent_student_test_"))
os.environ.update({"AGENT_STUDENT_RACINE": str(TMP), "SIMULATION": "1", "ANTHROPIC_API_KEY": "", "TEST_OBLIGATOIRE": "0",
                   "EMAIL_ADRESSE": "moi@exemple.be"})
sys.path.insert(0, str(RACINE_PROJET))

from agent_student import config, db, messagerie, orchestrateur, outils  # noqa: E402
from agent_student.agents import controle, envoyeur, essai, garde, relance, sources, trieur  # noqa: E402
from agent_student.agents import coach, concierge  # noqa: E402
from agent_student.agents.concierge import Entrant  # noqa: E402


class CanalTest:
    nom = "test"

    def __init__(self):
        self.messages, self.boutons, self.fichiers = [], [], []

    def envoyer(self, t, boutons=None):
        self.messages.append(t)
        self.boutons.append(boutons)

    def envoyer_fichier(self, c, legende=""):
        self.fichiers.append(Path(c))


def _hors_ligne(*a, **k):
    raise ConnectionError("hors ligne (test)")


def emails_sortis():
    return sorted((config.DONNEES / "sortie").glob("*.eml"))


def offre(entreprise):
    return db.q1("SELECT * FROM offres WHERE entreprise=?", entreprise)


class Circuit(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        outils.telecharger = _hors_ligne
        sources._get_json = _hors_ligne                         # aucun vrai site pendant les tests
        sources._jobspy = lambda dom: []                        # ni Indeed ni Google
        from agent_student.agents import connecteurs
        connecteurs._get = connecteurs._post = _hors_ligne      # ni EURES, ni Forem, ni recruteurs
        orchestrateur.en_fond = lambda f, *a: f(*a)             # tout en synchrone
        orchestrateur.envoyer_bientot = orchestrateur._envoyer
        orchestrateur.declencher = lambda: None
        cls.canal = CanalTest()
        messagerie.brancher(cls.canal)

    def dernier(self):
        return self.canal.messages[-1]

    def test_1_onboarding(self):
        cv = TMP / "mon_cv.pdf"
        cv.write_bytes(b"%PDF-1.4 faux")
        concierge.recevoir(Entrant(fichier=cv, nom_fichier="mon_cv.pdf"))
        self.assertTrue(config.chemin_cv().exists())
        (config.PROFIL_DIR / "profil.md").write_text("# Profil\n- Nom : Alex Test\n- Email : alex.test@exemple.be\n- Bachelier en sciences éco ULB\n- Caissier, Delhaize Ixelles\n",
                                                      encoding="utf-8")
        concierge.recevoir(Entrant(texte="Je veux que de la vente à Bruxelles, pas d'horeca, dispo le samedi"))
        dom = config.domaine_actif()
        self.assertEqual(dom["secteurs_uniquement"], ["vente"])
        self.assertEqual(dom["secteurs_exclus"], ["horeca"])
        self.assertIn("samedi", config.profil_texte())                # dispos vérifiables par le Juge
        concierge.recevoir(Entrant(texte="ok lance"))
        self.assertTrue(db.kv_get("onboarding_fini"))
        self.assertEqual(config.regles_envoi()["mode"], "repetition")     # mode test par défaut

    def test_2_recherche_et_tri(self):
        avant = len(self.canal.messages)
        orchestrateur.pipeline()
        st = {r["entreprise"]: (r["statut"], r["raison"]) for r in db.q("SELECT * FROM offres")}
        self.assertEqual(st["Brasserie Themis"][0], "ecartee")            # horeca exclu
        self.assertIn("secteur exclu", st["Brasserie Themis"][1])
        self.assertEqual(st["Nordlog"][0], "ecartee")                     # hors secteur / néerlandais
        # Mode test : tout est fait seul et part vers l'email du CV ; « via leur site » reste à faire plus tard
        for e, attendu in (("Boutique Lumen", "repetee"), ("Librairie Horizon", "repetee"), ("Delta Store", "manuelle")):
            self.assertEqual(st[e][0], attendu, e)
        self.assertNotIn("Sans Email & Co", st)                            # spontanée sans email vérifié : non
        self.assertEqual(offre("Librairie Horizon")["source"], "spontanee")
        self.assertEqual(offre("Boutique Lumen")["note_lettre"], 9)
        self.assertIn("caisse", offre("Boutique Lumen")["pourquoi"])
        self.assertEqual(self.canal.messages[avant:], [])                 # mode test : RIEN sur la messagerie
        self.assertEqual(self.canal.fichiers, [])
        sortis = emails_sortis()
        self.assertEqual(len(sortis), 3)                                   # y compris l'offre « via leur site »
        import email
        import email.policy
        for f in sortis:
            m = email.message_from_bytes(f.read_bytes(), policy=email.policy.default)
            self.assertEqual(m["To"], "alex.test@exemple.be")             # l'adresse du CV, jamais l'employeur
            self.assertIn("[TEST n°", m["Subject"])
            joints = [p for p in m.walk() if p.get_filename()]
            self.assertEqual(len(joints), 3)                              # CV + lettre + rapport de test
            self.assertTrue(all(p.get_content_type() == "application/pdf" for p in joints))
            self.assertTrue(all(p.get_content_disposition() == "attachment" for p in joints))
            corps = m.get_body(("plain",)).get_content()
            self.assertNotIn("Je vous prie d'agréer", corps)              # la lettre n'est PAS dans l'email
        orchestrateur.pipeline()                                           # 2e passage : aucun doublon
        self.assertEqual(db.q1("SELECT COUNT(*) AS n FROM offres")["n"], 5)

    def test_3_repetition_puis_vrai(self):
        lumen, delta = offre("Boutique Lumen"), offre("Delta Store")
        concierge.recevoir(Entrant(texte=f"vrai {lumen['id']}"))          # « L'envoyer pour de vrai »
        self.assertEqual(offre("Boutique Lumen")["statut"], "envoyee")
        self.assertTrue(offre("Boutique Lumen")["message_id"])
        self.assertIn(b"To: jobs@lumen-shop.be", emails_sortis()[-1].read_bytes())
        concierge.recevoir(Entrant(texte=f"fait {delta['id']}"))
        self.assertEqual(offre("Delta Store")["statut"], "envoyee")

    def test_4_reponses_recruteurs(self):
        lumen = offre("Boutique Lumen")
        boite = config.DONNEES / "boite_simulee"
        boite.mkdir(parents=True, exist_ok=True)
        m = EmailMessage()
        m["From"], m["Subject"], m["Message-ID"] = "rh@lumen-shop.be", "Votre candidature", "<rep1@lumen>"
        m["In-Reply-To"] = lumen["message_id"]
        m.set_content("Bonjour, nous aimerions vous rencontrer en entretien le 24 décembre à 16h30.")
        (boite / "1.eml").write_bytes(bytes(m))
        for i, (de, sujet) in enumerate((("promo@boutique.be", "-50% ce week-end"),
                                          ("moi@exemple.be", "[RÉPÉTITION → x] Candidature"))):
            p = EmailMessage()
            p["From"], p["Subject"], p["Message-ID"] = de, sujet, f"<bruit{i}@x>"
            p.set_content("…")
            (boite / f"2{i}.eml").write_bytes(bytes(p))

        avant = len(self.canal.messages)
        trieur.relever()
        nouveaux = self.canal.messages[avant:]
        self.assertEqual(len(nouveaux), 1)                                 # ni la pub ni nos copies
        self.assertIn("entretien", nouveaux[0])
        self.assertIn("jeudi 24 décembre à 16h30", nouveaux[0])
        rid = db.q1("SELECT id FROM rdv")["id"]
        self.assertIn(("✅ Jeudi 16h30", f"rdv {rid} oui"), self.canal.boutons[-1][0])
        self.assertEqual(offre("Boutique Lumen")["statut"], "entretien")

        concierge.recevoir(Entrant(texte=f"rdv {rid} oui"))               # bouton touché
        self.assertEqual(db.q1("SELECT statut FROM rdv")["statut"], "confirme")
        self.assertIn("Confirmé", self.dernier())
        self.assertTrue(any(f.suffix == ".ics" for f in self.canal.fichiers))

    def test_5_refus_silencieux_et_relance(self):
        delta = offre("Delta Store")
        db.maj(delta["id"], envoye_le="2026-01-01 09:00:00", email_contact="rh@delta.be")
        self.assertEqual(relance.relancer(), 1)
        self.assertEqual(offre("Delta Store")["statut"], "relancee")
        m = EmailMessage()
        m["From"], m["Subject"], m["Message-ID"] = "rh@delta.be", "Votre candidature", "<ref@delta>"
        m.set_content("Nous avons le regret de vous informer que votre profil n'a pas été retenu.")
        (config.DONNEES / "boite_simulee" / "3.eml").write_bytes(bytes(m))
        avant = len(self.canal.messages)
        trieur.relever()
        self.assertEqual(len(self.canal.messages), avant)                  # aucun message pour un refus
        self.assertEqual(offre("Delta Store")["statut"], "refus")

    def test_6_commandes(self):
        concierge.recevoir(Entrant(texte="/stats"))
        self.assertIn("Bilan", self.dernier())
        concierge.recevoir(Entrant(texte="pause"))
        self.assertTrue(db.kv_get("pause"))
        concierge.recevoir(Entrant(texte="reprendre"))
        self.assertFalse(db.kv_get("pause"))
        concierge.recevoir(Entrant(texte="je suis en blocus, mets en pause jusqu'au 20 juin"))
        self.assertTrue(db.kv_get("pause"))
        self.assertEqual(db.kv_get("pause_jusqu"), "2099-06-20")
        self.assertIn("20/06", self.dernier())
        db.kv_set("pause_jusqu", "2000-01-01")                         # la date est passée → reprise seule
        self.assertFalse(orchestrateur.en_pause())
        self.assertFalse(db.kv_get("pause"))
        concierge.recevoir(Entrant(texte="mes lettres sont trop longues"))
        from agent_student import memoire
        self.assertIn("180 mots", memoire.notes_style())                 # retenu pour toutes les lettres
        concierge.recevoir(Entrant(texte="mode validation"))
        self.assertEqual(config.regles_envoi()["mode"], "validation")
        self.assertNotIn("🧪", self.dernier())

    def test_7_test_en_langage_naturel(self):
        concierge.recevoir(Entrant(texte="j'aimerais d'abord essayer pour voir si ça marche"))
        self.assertTrue(any("Test terminé" in m for m in self.canal.messages[-3:]))
        self.assertTrue(any(f.name == "banc_essai.pdf" for f in self.canal.fichiers))
        concierge.recevoir(Entrant(texte="aide"))
        self.assertIn("teste-toi", self.dernier())

    def test_7b_manager(self):
        """Le Manager AGIT : chercher maintenant, envoyer à une adresse, régler, envoyer ici."""
        appels = []
        orchestrateur.declencher = lambda: appels.append("recherche")
        concierge.recevoir(Entrant(texte="lance une recherche maintenant stp"))
        self.assertEqual(appels, ["recherche"])
        self.assertIn("lancé", self.dernier())
        avant = len(emails_sortis())
        db.q("SELECT 1")
        with db.tx() as c:                                       # une candidature testée récente
            c.execute("UPDATE offres SET statut='repetee', envoye_le=datetime('now') WHERE entreprise='Librairie Horizon'")
        concierge.recevoir(Entrant(texte="envoie-les sur mon autre mail alex.perso@exemple.com"))
        nouveaux = emails_sortis()[avant:]
        self.assertGreaterEqual(len(nouveaux), 1)
        self.assertIn(b"To: alex.perso@exemple.com", nouveaux[0].read_bytes())
        self.assertIn("alex.perso@exemple.com", self.dernier())
        concierge.recevoir(Entrant(texte="mets le score minimum à 4"))
        self.assertEqual(config.regles_envoi()["score_minimum"], 4)
        n = len(self.canal.fichiers)
        concierge.recevoir(Entrant(texte="envoie ici la lettre"))
        self.assertGreater(len(self.canal.fichiers), n)              # la lettre arrive dans la conversation
        orchestrateur.declencher = lambda: None

    def test_8_banc_essai(self):
        rapport = essai.banc_essai()
        texte = rapport.read_text(encoding="utf-8")
        self.assertIn("Note moyenne du Juge", texte)
        self.assertEqual(texte.count("\n## "), 5)


class Controleur(unittest.TestCase):
    def test_attrape_les_erreurs(self):
        base = {"id": 999, "entreprise": "Boutique Lumen", "email_contact": "a@b.be", "objet_mail": "Candidature",
                "corps_mail": "Bonjour", "lettre": "Boutique Lumen " + "mot " * 180}
        self.assertNotIn("le nom", " ".join(controle.problemes(base)))
        p = controle.problemes({**base, "lettre": "Boutique Delta " + "mot " * 180})
        self.assertTrue(any("n'apparaît pas" in x for x in p))
        p = controle.problemes({**base, "lettre": base["lettre"] + " [Nom de l'employeur]"})
        self.assertTrue(any("trous" in x for x in p))
        p = controle.problemes({**base, "lettre": "Boutique Lumen court"})
        self.assertTrue(any("longueur" in x for x in p))


class ChiffresInventes(unittest.TestCase):
    def test_duree_inventee_detectee(self):
        profil = "Caissier, Delhaize Ixelles, 2025 (3 mois). Moyenne (16/20). Babysitting 2024 (2 mois). 0470 12 34 56"
        self.assertEqual(controle.chiffres_inventes("Pendant un an, j'ai tenu une caisse.", profil), ["1 an"])
        self.assertEqual(controle.chiffres_inventes("Mon job de trois mois en caisse, en 2025, avec 16/20 de moyenne.", profil), [])
        self.assertEqual(controle.chiffres_inventes("Une équipe de 40 vendeurs.", profil), ["40"])
        self.assertEqual(controle.chiffres_inventes("Néerlandais B2.", profil + " Néerlandais : B1"), ["niveau B2"])
        self.assertEqual(controle.chiffres_inventes("Néerlandais B1.", profil + " Néerlandais : B1"), [])

    def test_dates_et_telephone_ignores(self):
        profil = "Tél. 0470 12 34 56 · caissier 2025 (3 mois)"
        self.assertEqual(controle.chiffres_inventes(
            "Bruxelles, le 24 novembre 2026. Joignable au 0470/12.34.56 ou +32 470 12 34 56.", profil), [])

    def test_preuves(self):
        profil = "- Caissier, Delhaize Ixelles\n- Tenue de caisse et réassort des rayons"
        self.assertTrue(controle.preuve_valide("Tenue de caisse et réassort", profil))
        self.assertFalse(controle.preuve_valide("service en salle dans un restaurant", profil))
        self.assertFalse(controle.preuve_valide(None, profil))


class ListesEtudiantes(unittest.TestCase):
    def test_liens_annonces(self):
        page = ('<a href="/fr/jobs-etudiants/boutique-lumen-vendeur-week-end/?ref=x">Vendeur week-end</a>'
                '<a href="/fr/bruxelles/jobs-etudiants/">Tous les jobs</a>'
                '<a href="https://fr.studentjob.be/offre/4567-hote-accueil"><img src="x.png"></a>'
                '<a href="/fr/contact">Contact</a>')
        liens = sources._liens_annonces(page, "https://www.student.be/fr/bruxelles/jobs-etudiants/")
        self.assertEqual(liens, [
            ("https://www.student.be/fr/jobs-etudiants/boutique-lumen-vendeur-week-end/", "Vendeur week-end"),
            ("https://fr.studentjob.be/offre/4567-hote-accueil", "Hote accueil")])


    def test_vraie_page_student_be(self):           # extrait réel (octobre 2026) : carte entière dans le lien
        u = "https://www.student.be/fr/jobs-etudiants/exki-job-etudiant-equipier-e-exki-arts-loi-de83cd2d-4c31-4917-9cc2-fb1718cd87d5/"
        page = (f"[Travailler en tant que étudiant chez EXKiEXKiEXKiJob étudiant - Equipier(e) - EXKI ARTS-LOIEn semaine"
                f"VacancesWeekendBruxelles79]({u})\n[umbrella-beach Job d'été](https://www.student.be/fr/jobs-etudiants/job-d-ete/)"
                f'\n<a class="card" href="{u.replace("arts-loi", "tongres")}">Equipier</a>')
        liens = sources._liens_annonces(page, "https://www.student.be/fr/bruxelles/jobs-etudiants/")
        self.assertEqual([x[0] for x in liens], [u.replace("arts-loi", "tongres"), u])   # pas la catégorie
        self.assertEqual(dict((a, b) for a, b in liens)[u], "Exki job etudiant equipier e exki arts loi")

    def test_liens_dans_json(self):                # site construit en JavaScript
        page = '<script>{"jobs":[{"url":"\\/fr\\/jobs-etudiants\\/exki-equipier-arts-loi\\/"}]}</script>'
        liens = sources._liens_annonces(page, "https://www.student.be/fr/bruxelles/jobs-etudiants/")
        self.assertEqual([u for u, _ in liens], ["https://www.student.be/fr/jobs-etudiants/exki-equipier-arts-loi/"])


class AnnoncePrecise(unittest.TestCase):
    def test_pages_de_liste_rejetees(self):
        a = sources.annonce_precise
        self.assertFalse(a({"entreprise": "X", "url": "https://www.student.be/fr/bruxelles/jobs-etudiants/"}))
        self.assertFalse(a({"entreprise": None, "url": "https://fr.studentjob.be/offre/123-vendeur"}))
        self.assertFalse(a({"entreprise": "Shop", "url": "https://www.stepstone.be/emplois/"}))
        self.assertTrue(a({"entreprise": "Shop", "url": "https://fr.studentjob.be/offre/123-vendeur"}))
        self.assertTrue(a({"entreprise": "Shop", "url": "https://www.student.be/fr/jobs-etudiants/exki-equipier-arts-loi/"}))


class Garde(unittest.TestCase):
    def test_synonymes_langues_salaire(self):
        dom = {"secteurs_exclus": ["horeca"], "langues": ["français"], "secteurs_uniquement": []}
        regles = config.regles_envoi()
        v = lambda **o: garde.verdict({"id": 0, **o}, dom, regles)  # noqa: E731
        self.assertIsNotNone(v(titre="Serveuse – brasserie du centre"))
        self.assertIsNone(v(titre="Hôtesse d'embarquement"))                  # « bar » ≠ « embarquement »
        self.assertIsNotNone(v(titre="Vendeur", langue="néerlandais courant"))
        self.assertIsNone(v(titre="Vendeur", langue="français"))
        from datetime import date
        an = date.today().year
        self.assertIn("trop ancienne", v(titre="Vendeur", publie_le=f"{an - 3}-05-02") or "")
        self.assertIsNone(v(titre="Vendeur", publie_le=f"{an - 2}-05-02"))       # un peu vieille : on tente
        self.assertIn("date limite", v(titre="Vendeur", date_limite=f"{an - 1}-01-01") or "")
        dom["secteurs_uniquement"] = ["garde d'enfants"]
        self.assertIsNotNone(v(titre="Caissier", secteur="vente"))
        self.assertIsNone(v(titre="Babysitter 3 soirs", secteur="baby-sitting"))
        dom.update(secteurs_uniquement=[], contrats=["contrat étudiant"])
        self.assertIsNone(v(titre="Vendeur", langue="anglais"))                 # atout, pas exigé
        self.assertIsNotNone(v(titre="Vendeur", langue="anglais courant exigé"))
        self.assertIsNotNone(v(titre="Comptable junior", contrat="CDI temps plein"))
        self.assertIsNone(v(titre="Animateur de stages sportifs (étudiant)"))
        self.assertIsNotNone(v(titre="Sondages rémunérés en ligne"))
        dom["secteurs_uniquement"] = ["garde d'enfants"]
        dom["salaire_min_horaire"] = 12
        self.assertIsNotNone(v(titre="Babysitter", secteur="baby-sitting", salaire_horaire=9.0))
        self.assertIsNone(v(titre="Babysitter", secteur="baby-sitting", salaire_horaire=12.5))


class Outils(unittest.TestCase):
    def test_doublons(self):
        a = outils.cle_offre("https://www.student.be/x?utm_source=a", "Boutique Lumen", "Vendeur week-end")
        b = outils.cle_offre("https://studentjob.be/y", "boutique lumen", "Vendeur Week-end")
        self.assertEqual(a, b)

    def test_jsonld(self):
        page = ('<script type="application/ld+json">{"@type":"JobPosting","title":"Vendeur",'
                '"hiringOrganization":{"name":"X"},"jobLocation":{"address":{"addressLocality":"Liège"}},'
                '"description":"<p>Mission</p>"}</script>')
        jp = outils.jobposting_jsonld(page)
        self.assertEqual((jp["titre"], jp["entreprise"], jp["ville"]), ("Vendeur", "X", "Liège"))

    def test_emails(self):
        self.assertEqual(outils.emails_dans("écrire à jobs@cab.be ou noreply@cab.be"), ["jobs@cab.be"])

    def test_heures(self):
        from datetime import datetime
        r = config.regles_envoi()
        self.assertTrue(envoyeur.heure_ouvrable(r, datetime(2026, 10, 19, 9)))    # lundi 9h
        self.assertFalse(envoyeur.heure_ouvrable(r, datetime(2026, 10, 18, 9)))   # dimanche
        self.assertTrue(envoyeur.heure_ouvrable(r, datetime(2026, 10, 24, 10)))   # samedi 10h
        self.assertFalse(envoyeur.heure_ouvrable(r, datetime(2026, 10, 19, 19)))  # après 19h

    def test_banc_valide(self):
        self.assertEqual(len(json.loads(essai.BANC.read_text(encoding="utf-8"))), 5)


class Memoire(unittest.TestCase):
    """Il se souvient de ce qu'on lui a dit il y a des jours, sans relire tout l'historique (coût minime)."""

    def test_souvenir_d_il_y_a_5_jours(self):
        from agent_student import memoire
        with db.tx() as c:
            c.execute("INSERT INTO conversation (role, texte, le) VALUES ('moi', 'Le cabinet Verhaegen à Namur "
                      "m''intéresse, je préfère les cabinets moyens', datetime('now', '-5 days'))")
            c.execute("INSERT INTO conversation (role, texte, le) VALUES ('agent', 'Noté !', datetime('now', '-5 days'))")
        from datetime import date, timedelta
        il_y_a_6 = (date.today() - timedelta(days=6)).isoformat()
        trouves = memoire.chercher("verhaegen", il_y_a_6)
        self.assertTrue(trouves and "Namur" in trouves[0])
        self.assertTrue(memoire.chercher("", il_y_a_6, (date.today() - timedelta(days=4)).isoformat()))
        self.assertEqual(memoire.chercher("verhaegen", date.today().isoformat()), [])   # hors période
        # Le carnet résume chaque journée UNE fois et garde ce qui reste vrai
        self.assertGreaterEqual(memoire.mettre_a_jour_carnet(), 1)
        avant = db.q1("SELECT COUNT(*) AS n FROM carnet")["n"]
        memoire.mettre_a_jour_carnet()
        self.assertEqual(db.q1("SELECT COUNT(*) AS n FROM carnet")["n"], avant)        # pas payé deux fois
        self.assertIn("cabinets de taille moyenne", memoire.contexte())
        self.assertIn("Carnet de bord", memoire.contexte())

    def test_retenir_et_oublier(self):
        from agent_student import memoire
        self.assertTrue(memoire.retenir("Ne plus postuler chez Nordlex.", "decision"))
        self.assertFalse(memoire.retenir("ne plus postuler chez nordlex.", "decision"))  # pas de doublon
        self.assertIn("Nordlex", memoire.contexte())
        self.assertEqual(memoire.oublier("nordlex"), 1)
        self.assertNotIn("Nordlex", memoire.contexte())

    def test_manager_se_souvient(self):
        from agent_student.agents import manager
        with db.tx() as c:
            c.execute("INSERT INTO conversation (role, texte, le) VALUES ('moi', 'Pense au cabinet Lambrechts', "
                      "datetime('now', '-9 days'))")
        self.assertIn("Lambrechts", manager.executer("se_souvenir", {"mots": "Lambrechts"}))
        self.assertIn("mémoire", manager._systeme())


class PhaseDeTest(unittest.TestCase):
    """Obligatoire : 2 tests notés sur 20, ≥ 15, puis l'accord de la personne avant tout envoi réel."""

    def setUp(self):
        os.environ["TEST_OBLIGATOIRE"] = "1"
        db.kv_set("phase_test", None)
        o = config.objectif()
        o.setdefault("envoi", {})["mode"] = "repetition"
        config.sauver_objectif(o)
        self.canal = CanalTest()
        messagerie.brancher(self.canal)
        self.ids = []
        with db.tx() as c:
            for i in range(3):
                self.ids.append(c.execute(
                    "INSERT INTO offres (cle, url, titre, entreprise, statut, infos_cabinet, lettre, envoye_le) "
                    "VALUES (?, ?, 'Juriste', ?, 'repetee', '{}', 'Lettre', datetime('now'))",
                    (f"pt{i}{self._testMethodName}", f"https://x.be/pt{i}", f"Cabinet PT{i}")).lastrowid)
        orchestrateur.en_fond = lambda f, *a: None              # pas de réécriture réelle pendant ce test

    def tearDown(self):
        os.environ["TEST_OBLIGATOIRE"] = "0"
        orchestrateur.en_fond = lambda f, *a: f(*a)

    def test_parcours_complet(self):
        from agent_student import memoire
        from agent_student.agents import phase_test
        concierge.recevoir(Entrant(texte="mode auto"))
        self.assertIn("Pas encore", self.canal.messages[-1])               # rien de réel avant le test
        for oid in self.ids:
            self.assertTrue(phase_test.peut_envoyer_test())
            phase_test.test_envoye(oid)
        self.assertFalse(phase_test.peut_envoyer_test())                    # on attend sa note
        self.assertIn("note sur 20", self.canal.messages[-1])
        concierge.recevoir(Entrant(texte="note 12 : trop formel, j'aime l'accroche"))
        e = phase_test.etat()
        self.assertEqual((e["tour"], e["statut"], e["ids"]), (2, "en_cours", []))
        self.assertIn("trop formel", memoire.notes_style())                 # ses remarques guident les lettres
        self.assertEqual({db.q1("SELECT statut FROM offres WHERE id=?", i)["statut"] for i in self.ids}, {"qualifiee"})
        for oid in self.ids:                                                # test n°2 envoyé
            phase_test.test_envoye(oid)
        concierge.recevoir(Entrant(texte="note 16/20 parfait"))
        self.assertEqual(phase_test.etat()["statut"], "attente_accord")
        self.assertEqual(config.regles_envoi()["mode"], "repetition")       # toujours rien sans son accord
        concierge.recevoir(Entrant(texte="autoriser envoi"))
        self.assertFalse(phase_test.actif())
        self.assertEqual(config.regles_envoi()["mode"], "auto")
        o = config.objectif()
        o["envoi"]["mode"] = "repetition"
        config.sauver_objectif(o)

    def test_une_bonne_note_au_premier_test_ne_suffit_pas(self):
        from agent_student.agents import phase_test
        for oid in self.ids:
            phase_test.test_envoye(oid)
        phase_test.noter(18, "super")
        self.assertEqual(phase_test.etat()["tour"], 2)                      # au moins 2 tests
        self.assertTrue(phase_test.actif())


class CanalApp(unittest.TestCase):
    def test_aller_retour(self):
        import threading
        import time as t
        from agent_student.canaux.app import App, ouvrir
        canal = App()
        canal.envoyer("Bonjour", [[("✅ Envoyer", "ok 1")]])
        canal.demander_note("Donne-moi une note sur 20")
        canal.envoyer_fichier(TMP / "x.pdf", "Lettre")
        c = ouvrir(config.RACINE)
        rows = c.execute("SELECT genre, texte, boutons FROM sortie ORDER BY id").fetchall()
        self.assertEqual([r["genre"] for r in rows], ["texte", "notation", "fichier"])
        self.assertIn('"commande": "ok 1"', rows[0]["boutons"])
        recus = []
        threading.Thread(target=canal.ecouter, args=(recus.append,), daemon=True).start()
        c.execute("INSERT INTO entree (texte) VALUES ('salut')")
        c.commit()
        for _ in range(30):
            if recus:
                break
            t.sleep(0.1)
        self.assertEqual(recus[0].texte, "salut")

    def test_mode_econome_s_arrete_seul(self):
        import threading
        from agent_student.canaux.app import App
        os.environ["QUITTER_APRES_MIN"] = "0.01"            # 0,6 s sans message → l'agent rend la main
        try:
            t = threading.Thread(target=App().ecouter, args=(lambda m: None,), daemon=True)
            t.start()
            t.join(5)
            self.assertFalse(t.is_alive())
        finally:
            del os.environ["QUITTER_APRES_MIN"]

    def test_tournee(self):
        from agent_student import orchestrateur
        orchestrateur.tournee()                              # une tournée complète, sans planter
        self.assertTrue(db.kv_get("derniere_recherche_web"))


class Connecteurs(unittest.TestCase):
    """Les sources communes à tous les agents, lues hors ligne avec des réponses enregistrées."""
    DOM = {"mots_cles_recherche": ["juriste Bruxelles"], "postes_vises": ["juriste"], "villes": ["Bruxelles"]}

    def setUp(self):
        from agent_student.agents import connecteurs
        self.c = connecteurs
        self.anciens = (connecteurs._get, connecteurs._post)

    def tearDown(self):
        self.c._get, self.c._post = self.anciens

    @staticmethod
    def reponse(donnees=None, contenu=b""):
        return type("R", (), {"json": lambda self: donnees, "content": contenu})()

    def test_eures(self):
        import time as t
        recent = int(t.time() * 1000) - 86400000
        vieux = recent - 40 * 86400000
        envoye = {}

        def faux(url, corps):
            envoye.update(corps)
            return self.reponse({"jvs": [
                {"id": "AB1", "title": "Jurist", "creationDate": recent, "employer": {"name": "Cabinet Nord"},
                 "translations": {"nl": {"title": "Jurist vastgoed", "description": "<p>Dossiers</p>"}}},
                {"id": "AB2", "title": "Orderpicker", "creationDate": recent, "translations": {}},     # hors sujet
                {"id": "AB3", "title": "Juriste", "creationDate": vieux, "translations": {}}]})        # trop vieille
        self.c._post = faux
        o = self.c.eures(self.DOM)
        self.assertEqual([x["titre"] for x in o], ["Jurist vastgoed"])
        self.assertEqual(envoye["locationCodes"], ["be1"])                    # Bruxelles → région be1
        self.assertEqual(envoye["keywords"][0]["keyword"], "juriste")         # la ville est filtrée à part
        self.assertIn("jv-details/AB1", o[0]["url"])

    def test_forem(self):
        from datetime import date
        self.c._get = lambda url, **k: self.reponse({"results": [{
            "numerooffreforem": "1", "titreoffre": "Juriste (H/F/X)", "nomemployeur": "Ville de Namur",
            "lieuxtravaillocalite": "Namur", "typecontrat": "CDI", "langues": ["Français"], "source": "Jobat",
            "datedebutdiffusion": date.today().isoformat(), "url": "https://www.leforem.be/offre/1"}]})
        o = self.c.forem(self.DOM)
        self.assertEqual((o[0]["entreprise"], o[0]["source"]), ("Ville de Namur", "forem"))
        self.assertIn("Jobat", o[0]["description"])

    def test_recruteurs_decouverts_puis_surveilles(self):
        from datetime import date
        trouves = self.c.detecter_ats('<a href="https://boards.greenhouse.io/lumenlaw">Jobs</a> '
                                      "https://jobs.lever.co/nordlex/abc https://acme.recruitee.com/o/juriste")
        self.assertEqual(trouves, {("greenhouse", "lumenlaw"), ("lever", "nordlex"), ("recruitee", "acme")})
        db.kv_set("tableaux_ats", None)
        self.c.noter_ats({("greenhouse", "lumenlaw")}, "test")
        self.c._get = lambda url, **k: self.reponse({"jobs": [
            {"title": "Juriste corporate", "absolute_url": "https://boards.greenhouse.io/lumenlaw/jobs/1",
             "location": {"name": "Brussels, Belgium"}, "first_published": date.today().isoformat(), "content": "&lt;p&gt;Droit&lt;/p&gt;"},
            {"title": "Juriste", "absolute_url": "https://boards.greenhouse.io/lumenlaw/jobs/2",
             "location": {"name": "New York"}, "first_published": date.today().isoformat()},               # hors Belgique
            {"title": "Office manager", "absolute_url": "https://boards.greenhouse.io/lumenlaw/jobs/3",
             "location": {"name": "Brussels"}, "first_published": date.today().isoformat()}]})              # hors sujet
        o = self.c.ats(self.DOM)
        self.assertEqual([x["url"] for x in o], ["https://boards.greenhouse.io/lumenlaw/jobs/1"])
        self.assertEqual(o[0]["description"], "Droit")

    def test_alerte_linkedin(self):
        texte = ("Vos alertes emploi\n\nJuriste en droit des sociétés\nCabinet Delta\nBruxelles, Belgique\n"
                 "Voir l'offre : https://www.linkedin.com/comm/jobs/view/4012345678/?trackingId=x\n\n"
                 "Stagiaire avocat\nLumen Law\nNamur\nhttps://www.linkedin.com/comm/jobs/view/4012345679/\n")
        o = self.c.offres_d_une_alerte("LinkedIn <jobalerts-noreply@linkedin.com>", "Nouvelles offres", texte)
        self.assertEqual([(x["titre"], x["entreprise"], x["ville"]) for x in o],
                         [("Juriste en droit des sociétés", "Cabinet Delta", "Bruxelles, Belgique"),
                          ("Stagiaire avocat", "Lumen Law", "Namur")])
        self.assertEqual(o[0]["url"], "https://www.linkedin.com/jobs/view/4012345678")
        self.assertTrue(self.c.lecture_interdite(o[0]["url"]))            # jamais ouvert automatiquement

    def test_alerte_transferee_entre_dans_le_circuit(self):
        m = EmailMessage()
        m["From"], m["Subject"], m["Message-ID"] = "alex.test@exemple.be", "Fwd: alertes LinkedIn", "<alerte1@x>"
        m.set_content("Juriste immobilier\nCabinet Alerte Un\nBruxelles\nhttps://www.linkedin.com/jobs/view/111/\n"
                      "Juriste fiscal\nCabinet Alerte Deux\nLiège\nhttps://www.linkedin.com/jobs/view/222/\n")
        (config.DONNEES / "boite_simulee").mkdir(parents=True, exist_ok=True)
        (config.DONNEES / "boite_simulee" / "alerte.eml").write_bytes(bytes(m))
        trieur.relever()
        self.assertEqual(offre("Cabinet Alerte Un")["source"], "alerte")
        self.assertEqual(db.q1("SELECT categorie FROM emails_recus WHERE uid='<alerte1@x>'")["categorie"], "alerte")


class SitesEtEmails(unittest.TestCase):
    """Les bonnes infos sur les bons sites, et aucune adresse email devinée."""

    def test_domaine_officiel(self):
        self.assertEqual(outils.domaine_officiel("https://www.student.be/fr/x", "jobs@lumen-shop.be"), "lumen-shop.be")
        self.assertEqual(outils.domaine_officiel("https://www.delta-store.be/jobs/vendeur", None), "delta-store.be")
        self.assertEqual(outils.domaine_officiel("https://fr.studentjob.be/offre/1", "alex@gmail.com"), "")

    def test_email_verifie_par_le_code(self):
        pages = {"https://boutique-x.be": "<p>Boutique X</p> <a href='mailto:recrutement@boutique-x.be'>",
                 "https://boutique-x.be/contact": "info@boutique-x.be"}
        ancien = outils.telecharger
        outils.telecharger = lambda u, timeout=20: pages.get(u.rstrip("/"), "") or _hors_ligne()
        try:
            # lue sur le site → gardée ; devinée → remplacée par celle que le site publie vraiment
            self.assertEqual(outils.verifier_email("recrutement@boutique-x.be", "Boutique X"), "recrutement@boutique-x.be")
            self.assertEqual(outils.verifier_email("jobs@boutique-x.be", "Boutique X"), "recrutement@boutique-x.be")
            self.assertIsNone(outils.verifier_email("jobs@gmail.com", "Boutique X"))
            self.assertIsNone(outils.verifier_email("rh@inconnu-sans-site.be", "Boutique X"))
        finally:
            outils.telecharger = ancien
        # site officiel connu mais illisible : le domaine officiel fait foi
        self.assertEqual(outils.verifier_email("stage@horizon.be", "Horizon", "horizon.be"), "stage@horizon.be")

    def test_recherche_web_ciblee(self):
        from agent_student import llm
        o = llm._recherche_web("claude-haiku-5-5", 2, None, ["student.be"])
        self.assertEqual(o["type"], "web_search_20250305")
        self.assertIn("student.be", o["blocked_domains"])
        o = llm._recherche_web("claude-sonnet-5-5", 2, ["boutique.be"], ["student.be"])
        self.assertEqual((o["type"], o["allowed_domains"]), ("web_search_20260209", ["boutique.be"]))
        self.assertNotIn("blocked_domains", o)                      # jamais les deux à la fois
        self.assertFalse(llm._forcer("claude-sonnet-5-5"))
        self.assertTrue(llm._forcer("claude-haiku-5-5"))

    def test_lettre_toujours_en_pdf(self):
        from agent_student import pdf
        from agent_student.agents.redacteur import chemin_lettre
        with db.tx() as c:          # lettre d'une ancienne version : texte seulement, pas de PDF
            oid = c.execute("INSERT INTO offres (cle, url, titre, lettre, statut) VALUES "
                            "('ancienne', 'https://x.be/1', 'Poste', 'Madame, Monsieur,\n\nTexte.', 'a_valider')").lastrowid
        f = chemin_lettre(oid)
        self.assertEqual(f.suffix, ".pdf")
        self.assertTrue(f.read_bytes().startswith(b"%PDF"))
        l = {"expediteur": ["Alex Test"], "lieu_date": "Bruxelles, le 1er mars 2027", "objet_lettre": "Candidature",
             "appel": "Madame, Monsieur,", "paragraphes": ["Un.", "Deux."], "politesse": "Salutations.",
             "signature": "Alex Test"}
        self.assertIn("Objet : Candidature\n\nMadame, Monsieur,\n\nUn.\n\nDeux.", pdf.texte_lettre(l))
        self.assertNotIn("🎯", pdf.propre("Fiche 🎯 **gras**"))


class ZRobustesse(unittest.TestCase):
    """Ce qui fait qu'un agent « ne fait rien » doit être dit clairement, une seule fois."""

    def setUp(self):
        self.canal = CanalTest()
        messagerie.brancher(self.canal)

    def test_ia_indisponible_bloque_sans_insister(self):
        from agent_student import llm
        db.kv_set("ia_bloquee_jusqu", None)
        with self.assertRaises(llm.IAIndisponible):
            llm._bloquer("crédit Anthropic épuisé")
        with self.assertRaises(llm.BudgetDepasse):          # les agents l'attrapent comme un budget atteint
            llm._verifier_disponible()
        db.kv_set("ia_bloquee_jusqu", 0)
        llm._verifier_disponible()                          # délai passé : on réessaie

    def test_alerte_une_seule_fois(self):
        from agent_student import llm
        llm._alerte("essai_unique", "Ton crédit Claude est épuisé")
        llm._alerte("essai_unique", "Ton crédit Claude est épuisé")
        self.assertEqual(sum("crédit" in m for m in self.canal.messages), 1)

    def test_budget_reglable(self):
        concierge.recevoir(Entrant(texte="budget 5"))
        self.assertEqual(config.env("BUDGET_JOUR_EUR"), "5")
        self.assertIn("5 €", self.canal.messages[-1])

    def test_diagnostic(self):
        from agent_student.agents import diagnostic
        texte = diagnostic.rapport(envoyer=False)
        self.assertIn("SIMULATION", texte)
        db.kv_set("pause", True)
        self.assertIn("PAUSE", diagnostic.rapport(envoyer=False))
        db.kv_set("pause", False)

    def test_tout_effacer(self):
        from agent_student import memoire
        memoire.retenir("Souvenir à effacer", "fait")
        avant = db.q1("SELECT COUNT(*) AS n FROM souvenirs")["n"]
        concierge.recevoir(Entrant(texte="tout effacer"))
        self.assertEqual(db.q1("SELECT COUNT(*) AS n FROM souvenirs")["n"], avant)   # rien sans confirmation
        concierge.recevoir(Entrant(texte="tout effacer confirme"))
        self.assertEqual(db.q1("SELECT COUNT(*) AS n FROM souvenirs")["n"], 0)
        self.assertFalse(config.chemin_cv().exists())
        self.assertFalse(coach.onboarding_fini())

    def test_rappel_via_site_une_fois_par_jour(self):
        from agent_student.agents import rapporteur
        o = config.objectif()
        o.setdefault("envoi", {})["mode"] = "validation"
        config.sauver_objectif(o)
        with db.tx() as c:
            c.execute("INSERT OR IGNORE INTO offres (cle, url, titre, entreprise, statut) VALUES "
                      "('rappel1', 'https://exemple.be/offre/1', 'Poste', 'Employeur Rappel', 'manuelle')")
        db.kv_set("dernier_rappel_site", None)
        rapporteur.rappel_via_site(heure=10)
        rapporteur.rappel_via_site(heure=10)                     # 2e fois le même jour : rien
        self.assertEqual(sum("Employeur Rappel" in m for m in self.canal.messages), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
