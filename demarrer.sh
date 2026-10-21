#!/usr/bin/env bash
# Agent Student (macOS / Linux) : ./demarrer.sh
set -e
cd "$(dirname "$0")"
[ -d .venv ] || { python3 -m venv .venv && .venv/bin/pip install -q -r requirements.txt; }
[ -f .env ] || { cp .env.example .env; echo "Remplis .env puis relance."; exit 0; }
if grep -q '^CANAL=whatsapp' .env; then
  [ -d pont_whatsapp/node_modules ] || (cd pont_whatsapp && npm install --silent)
  (cd pont_whatsapp && npm start) &
  sleep 4
fi
.venv/bin/python -m agent_student --verifier || true
exec .venv/bin/python -m agent_student
