"""Agent Student — une équipe d'agents IA qui postule à ta place et te parle sur WhatsApp."""
__version__ = "0.3.3"

import sys as _sys

# Sous Windows, la console n'accepte pas toujours les accents et emojis (cp1252) : on évite tout plantage.
for _flux in (_sys.stdout, _sys.stderr):
    try:
        _flux.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
