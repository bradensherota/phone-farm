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
LICENSE_URL = "https://phone-farm-license.bradensherota.workers.dev"  # Braden's license service (see license.py)

# WebDriverAgent must live OUTSIDE iCloud-synced folders (Desktop/Documents), or code signing fails.
PHONEFARM_HOME = Path.home() / "PhoneFarm"
WDA_SRC_DIR = PHONEFARM_HOME / "WebDriverAgent"
WDA_BUILD_DIR = PHONEFARM_HOME / "wda-build"
WDA_BASE_PORT = 8100  # phone N listens on WDA_BASE_PORT + N locally

for d in (DATA_DIR, THUMB_DIR, SHOT_DIR, SIM_MEDIA_DIR, DATA_DIR / "logs"):
    d.mkdir(parents=True, exist_ok=True)
