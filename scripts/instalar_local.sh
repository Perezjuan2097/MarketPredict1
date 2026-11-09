#!/usr/bin/env bash
# Instalación local en Linux/macOS: crea el entorno virtual, instala todo y verifica.
set -e
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements-dev.txt
python scripts/verify_install.py
