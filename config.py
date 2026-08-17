import os
from dotenv import load_dotenv

load_dotenv()

import pylast


def required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


DB_PATH = 'db.sqlite3'
LASTFM_API_KEY = required_env("LASTFM_API_KEY")
LASTFM_API_SECRET = required_env("LASTFM_API_SECRET")
DISCORD_TOKEN = required_env("DISCORD_TOKEN")

network = pylast.LastFMNetwork(
    api_key=LASTFM_API_KEY,
    api_secret=LASTFM_API_SECRET,
)
