# PyInstaller : transforme le projet en une application à double-cliquer.
#   Windows → dist/AgentStudent.exe (un seul fichier, rien à extraire)
#   macOS   → dist/Agent Student.app
# Lancé automatiquement par GitHub Actions (.github/workflows/construire.yml).
import platform
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

WINDOWS = platform.system() == "Windows"
pont = Path("build/pont") / ("pont-whatsapp.exe" if WINDOWS else "pont-whatsapp")

a = Analysis(
    ["lanceur.py"],
    datas=[("agent_student/prompts", "agent_student/prompts"),
           ("agent_student/objectif_exemple.yaml", "agent_student"),
           ("agent_student/banc_essai.json", "agent_student"),
           ("assets/icone_256.png", "assets")] + collect_data_files("tzdata"),
    binaries=[(str(pont), "pont")] if pont.exists() else [],
    hiddenimports=collect_submodules("agent_student") + collect_submodules("anthropic") + ["qrcode", "pypdf", "fpdf", "tzdata"],
    excludes=["pytest", "numpy.tests"],
)
pyz = PYZ(a.pure)

if WINDOWS:
    exe = EXE(pyz, a.scripts, a.binaries, a.datas, name="AgentStudent", icon="assets/icone.ico",
              console=False, upx=False)
else:
    exe = EXE(pyz, a.scripts, exclude_binaries=True, name="AgentStudent", console=False, upx=False)
    coll = COLLECT(exe, a.binaries, a.datas, name="AgentStudent", upx=False)
    app = BUNDLE(coll, name="Agent Student.app", icon="assets/icone_1024.png",
                 bundle_identifier="be.agentstudent.app",
                 info_plist={"NSHighResolutionCapable": True, "CFBundleShortVersionString": "0.3.0",
                             "LSApplicationCategoryType": "public.app-category.productivity"})
