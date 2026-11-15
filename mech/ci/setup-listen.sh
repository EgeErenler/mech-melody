#!/usr/bin/env bash
# The listening tools: Demucs (stems), faster-whisper (words), Basic Pitch (notes), librosa.
set -euo pipefail
pip install -q torch torchaudio --index-url https://download.pytorch.org/whl/cpu
pip install -q -r mech/requirements-listen.txt
pip install -q --no-deps basic-pitch==0.4.0
