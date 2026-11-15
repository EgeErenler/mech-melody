"""Where things live. Everything is relative to the repo root."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHIMERA = Path(os.environ.get("CHIMERA_BIN", ROOT / "target" / "release" / "chimera"))
SONGS = ROOT / "songs"
DATA = ROOT / "data"
OUT = ROOT / "out"
SHOWS = ROOT / "public" / "shows"
