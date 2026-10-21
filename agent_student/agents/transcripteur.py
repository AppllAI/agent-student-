"""Agent 17 — Transcripteur : tes vocaux deviennent du texte.

Trois moteurs, choisis automatiquement (ou via TRANSCRIPTION dans .env) :
  • local  : faster-whisper sur ton PC — gratuit, privé (pip install faster-whisper)
  • openai : API Whisper / gpt-4o-transcribe — ~0,006 $ la minute, très précis (OPENAI_API_KEY)
  • aucun  : l'agent te demande d'écrire"""
from __future__ import annotations

from pathlib import Path

from .. import config

_modele_local = None


def moteur() -> str:
    choix = config.env("TRANSCRIPTION", "auto")
    if choix != "auto":
        return choix
    if config.simulation():
        return "simulation"
    try:
        import faster_whisper  # noqa: F401
        return "local"
    except ImportError:
        return "openai" if config.env("OPENAI_API_KEY") else "aucun"


def transcrire(audio: Path) -> str | None:
    m = moteur()
    if m == "simulation":
        f = Path(audio).with_suffix(".txt")
        return f.read_text(encoding="utf-8") if f.exists() else "[vocal]"
    if m == "local":
        global _modele_local
        from faster_whisper import WhisperModel

        if _modele_local is None:
            _modele_local = WhisperModel(config.env("WHISPER_MODELE", "small"), device="auto", compute_type="int8")
        segments, _ = _modele_local.transcribe(str(audio), language="fr", vad_filter=True)
        return " ".join(s.text.strip() for s in segments)
    if m == "openai":
        import requests

        with open(audio, "rb") as f:
            r = requests.post("https://api.openai.com/v1/audio/transcriptions",
                              headers={"Authorization": f"Bearer {config.env('OPENAI_API_KEY')}"},
                              data={"model": config.env("OPENAI_TRANSCRIPTION_MODELE", "whisper-1"), "language": "fr"},
                              files={"file": (Path(audio).name, f)}, timeout=120)
        r.raise_for_status()
        return r.json()["text"]
    return None
