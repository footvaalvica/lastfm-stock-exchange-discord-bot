import os
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
STARTING_TIME = 1769385600
MARKET_CHANNEL_ID = int(os.getenv("MARKET_CHANNEL_ID", "0"))
MARKET_HOUR_UTC = int(os.getenv("MARKET_HOUR_UTC", "9"))

network = pylast.LastFMNetwork(
    api_key=LASTFM_API_KEY,
    api_secret=LASTFM_API_SECRET,
)
