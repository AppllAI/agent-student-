@echo off
chcp 65001 >nul
REM ===== Agent Student : double-clique pour lancer. Installe tout au premier lancement. =====
cd /d %~dp0

where python >nul 2>nul || (echo Installe Python 3.11+ depuis python.org ^(coche "Add to PATH"^) & pause & exit /b)
if not exist .venv (
  echo Installation des dependances Python...
  python -m venv .venv && .venv\Scripts\pip install -q -r requirements.txt
)
if not exist .env (
  copy .env.example .env >nul
  echo Remplis le fichier .env qui vient de s'ouvrir, enregistre, puis relance.
  notepad .env
  exit /b
)
findstr /b /c:"CANAL=whatsapp" .env >nul && (
  where node >nul 2>nul || (echo Installe Node.js 20+ depuis nodejs.org & pause & exit /b)
  if not exist pont_whatsapp\node_modules (cd pont_whatsapp && call npm install --silent && cd ..)
  start "Pont WhatsApp (scanne le QR code)" cmd /k "cd pont_whatsapp && npm start"
  timeout /t 4 >nul
)
.venv\Scripts\python -m agent_student --verifier
.venv\Scripts\python -m agent_student
pause
