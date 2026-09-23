from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data"
THUMB_DIR = DATA_DIR / "thumbs"
SHOT_DIR = DATA_DIR / "screenshots"
SIM_MEDIA_DIR = ROOT / "sim_media"
DB_PATH = DATA_DIR / "farm.db"
FRONTEND_DIST = ROOT / "frontend" / "dist"

HOST = "127.0.0.1"
PORT = 8765

for d in (DATA_DIR, THUMB_DIR, SHOT_DIR, SIM_MEDIA_DIR):
    d.mkdir(parents=True, exist_ok=True)
