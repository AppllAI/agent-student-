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
os.environ.update({"AGENT_STUDENT_RACINE": str(TMP), "SIMULATION": "1", "ANTHROPIC_API_KEY": "",
                   "EMAIL_ADRESSE": "moi@exemple.be"})
sys.path.insert(0, str(RACINE_PROJET))

from agent_student import config, db, messagerie, orchestrateur, outils  # noqa: E402
from agent_student.agents import controle, envoyeur, essai, garde, relance, sources, trieur  # noqa: E402
from agent_student.agents import concierge  # noqa: E402
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
        self.assertIn("🧪", self.dernier())                    # mode répétition par défaut, rappelé partout

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
        for f in sortis:
            m = email.message_from_bytes(f.read_bytes())
            self.assertEqual(m["To"], "alex.test@exemple.be")             # l'adresse du CV, jamais l'employeur
            self.assertIn("[TEST n°", str(email.header.make_header(email.header.decode_header(m["Subject"]))))
            self.assertEqual(len([p for p in m.walk() if p.get_filename()]), 2)   # CV + lettre
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
        self.assertIn("180 mots", config.style_texte())
        concierge.recevoir(Entrant(texte="mode validation"))
        self.assertEqual(config.regles_envoi()["mode"], "validation")
        self.assertNotIn("🧪", self.dernier())

    def test_7_test_en_langage_naturel(self):
        concierge.recevoir(Entrant(texte="j'aimerais d'abord essayer pour voir si ça marche"))
        self.assertIn("Test terminé", self.canal.messages[-1])
        self.assertTrue(any(f.name == "banc_essai.md" for f in self.canal.fichiers))
        concierge.recevoir(Entrant(texte="aide"))
        self.assertIn("teste-toi", self.dernier())

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


if __name__ == "__main__":
    unittest.main(verbosity=2)
