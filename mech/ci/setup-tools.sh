#!/usr/bin/env bash
# Everything a render needs: espeak-ng (lyrics), ffmpeg, FluidSynth + the MuseScore General
# SoundFont (studio-quality instruments), Python packages, the wasm engine and the CLI.
set -euo pipefail
sudo apt-get update -q
sudo apt-get install -y -q espeak-ng ffmpeg fluidsynth musescore-general-soundfont-lossless
pip install -q -r mech/requirements.txt
bun install --frozen-lockfile
bun run wasm
cargo build --release -p chimera-cli
