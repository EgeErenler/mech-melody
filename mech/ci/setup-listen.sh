#!/usr/bin/env bash
# The listening tools: Demucs (stems), faster-whisper (words), Basic Pitch (notes), librosa.
set -euo pipefail
# torchaudio 2.9+ saves only through torchcodec, which Demucs 4.0.1 does not expect
pip install -q torch==2.5.1 torchaudio==2.5.1 --index-url https://download.pytorch.org/whl/cpu
pip install -q -r mech/requirements-listen.txt
pip install -q --no-deps basic-pitch==0.4.0
