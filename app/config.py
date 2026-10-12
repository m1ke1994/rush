from pathlib import Path
import os

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
DATABASE = DATA_DIR / "rush.sqlite3"
PROFILE_DIR = DATA_DIR / "browser_profile"
LEAGUE_NAME = "FC 26. 5x5 Rush. Суперлига"
LEAGUE_URL = "https://1xlite-12669.pro/ru/live/fifa/2986291-fc-26-5x5-rush-superleague"
HOST = os.getenv("RUSH_HOST", "127.0.0.1")
PORT = int(os.getenv("RUSH_PORT", "8011"))
POLL_SECONDS = float(os.getenv("RUSH_POLL_SECONDS", "1.0"))
