#!/usr/bin/env bash
set -euo pipefail
python -m PyInstaller build/kajovospend.spec --noconfirm --clean
