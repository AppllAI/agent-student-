"""Tests de la partie « application de bureau » (sans ouvrir de fenêtre)."""
import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from agent_student.app import reglages  # noqa: E402


class Reglages(unittest.TestCase):
    def test_formulaire_gmail(self):
        v = reglages.reglages_depuis_formulaire(" Alex.Juriste@gmail.com ", "abcd efgh ijkl mnop", " sk-ant-x ",
                                                "whatsapp", "0470 12 34 56")
        self.assertEqual(v["EMAIL_MOT_DE_PASSE"], "abcdefghijklmnop")
        self.assertEqual((v["SMTP_HOTE"], v["SMTP_PORT"], v["IMAP_HOTE"]), ("smtp.gmail.com", 465, "imap.gmail.com"))
        self.assertEqual(v["WHATSAPP_MON_NUMERO"], "32470123456")
        self.assertEqual(v["ANTHROPIC_API_KEY"], "sk-ant-x")

    def test_copier_coller_gmail(self):
        v = reglages.reglages_depuis_formulaire("a@gmail.com", "rqdw\u00a0thww\u00a0hmaq\u00a0aqzj",
                                                "sk-ant-abc\u200b\n", "telegram", "", " 123:ABC\u00a0")
        self.assertEqual(v["EMAIL_MOT_DE_PASSE"], "rqdwthwwhmaqaqzj")
        self.assertEqual((v["ANTHROPIC_API_KEY"], v["TELEGRAM_JETON"]), ("sk-ant-abc", "123:ABC"))
        self.assertIsNone(reglages.probleme_formulaire(v))
        v2 = reglages.reglages_depuis_formulaire("a@gmail.com", "rądw thww hmaq aqzj", "sk-ant-x")
        self.assertIn("16 lettres", reglages.probleme_formulaire(v2))

    def test_numero_international(self):
        v = reglages.reglages_depuis_formulaire("a@outlook.com", "x", "k", "whatsapp", "+32 470 12 34 56")
        self.assertEqual(v["WHATSAPP_MON_NUMERO"], "32470123456")
        self.assertEqual(v["SMTP_PORT"], 587)

    def test_env_aller_retour(self):
        d = Path(tempfile.mkdtemp())
        reglages.ecrire_env(d, {"EMAIL_ADRESSE": "a@gmail.com", "EMAIL_MOT_DE_PASSE": "p#ss word"})
        reglages.ecrire_env(d, {"CANAL": "telegram"})
        e = reglages.lire_env(d)
        self.assertEqual((e["EMAIL_ADRESSE"], e["CANAL"]), ("a@gmail.com", "telegram"))

    def test_dossier(self):
        d = tempfile.mkdtemp()
        os.environ["AGENT_STUDENT_RACINE"] = d
        self.assertEqual(reglages.preparer_dossier(), Path(d))
        self.assertTrue((Path(d) / "profil").is_dir())

    def test_cle_mal_formee(self):
        self.assertIn("sk-ant-", reglages.tester_cle("abc"))

    def test_fournisseur_inconnu(self):
        self.assertIsNotNone(reglages.tester_email("a@inconnu.be", "x", "", 465, ""))

    @unittest.skipUnless(importlib.util.find_spec("qrcode"), "qrcode non installé")
    def test_qr(self):
        m = reglages.matrice_qr("2@abc,def,ghi")
        self.assertGreater(len(m), 20)

    @unittest.skipUnless(importlib.util.find_spec("tkinter"), "tkinter absent")
    def test_fenetre_importable(self):
        import agent_student.app.fenetre as f
        self.assertTrue(hasattr(f, "Application"))


class Empaquetage(unittest.TestCase):
    def test_fichiers_de_donnees_dans_l_app(self):
        """Tout fichier non-Python du paquet doit être embarqué dans l'application (.exe / .app)."""
        racine = Path(__file__).resolve().parent.parent
        spec = (racine / "agent_student.spec").read_text(encoding="utf-8")
        for f in (racine / "agent_student").rglob("*"):
            if f.is_file() and f.suffix not in (".py", ".pyc") and "__pycache__" not in f.parts:
                rel = f.relative_to(racine).as_posix()
                self.assertTrue(rel in spec or rel.rsplit("/", 1)[0] in spec, f"{rel} absent de agent_student.spec")


class LettrePDF(unittest.TestCase):
    @unittest.skipUnless(importlib.util.find_spec("fpdf"), "fpdf2 non installé (testé sur Windows/Mac en CI)")
    def test_pdf_genere(self):
        from agent_student.agents.redacteur import ecrire_pdf
        lettre = ("Camille Dupont\nBruxelles\n\nBruxelles, le 18 octobre 2026\n\nMadame, Monsieur,\n\n"
                  + "Mon mémoire « La copropriété » — noté 16/20 — m’a appris l’essentiel. " * 12
                  + "\n\n- En 2025, stage à l’étude Lambert & Fils.\n- En 2024, Immo Sablon.\n\n"
                  "Je vous prie d'agréer, Madame, Monsieur, l'expression de mes salutations distinguées.\n\nCamille Dupont")
        f = ecrire_pdf(lettre, Path(tempfile.mkdtemp()) / "lettre.pdf")
        self.assertTrue(f.exists() and f.stat().st_size > 1000)


class ReponsesIncompletes(unittest.TestCase):
    def test_completer(self):
        from agent_student import llm
        schema = {"required": ["note", "invention", "corrections"],
                  "properties": {"note": {"type": "integer"}, "invention": {"type": "boolean"},
                                 "corrections": {"type": "array"}}}
        self.assertEqual(llm.completer({"note": 9}, schema), {"note": 9, "invention": False, "corrections": []})


if __name__ == "__main__":
    unittest.main(verbosity=2)
