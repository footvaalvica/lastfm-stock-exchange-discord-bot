import asyncio
import random
import logging
import pylast
from config import network

logger = logging.getLogger('lastfm_bot')

MAX_RETRIES = 3
BASE_DELAY = 1.0
MAX_DELAY = 10.0
RATE_LIMIT_DELAY = 0.2
_lastfm_lock = asyncio.Lock()


async def _fetch_with_retry(fn, *args, **kwargs):
    last_exception = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return await asyncio.to_thread(fn, *args, **kwargs)
        except pylast.WSError as e:
            last_exception = e
            error_code = getattr(e, 'code', None)
            if error_code == 29:
                delay = min(BASE_DELAY * (2 ** (attempt - 1)) + random.uniform(0, 0.5), MAX_DELAY)
                logger.warning(f"Last.fm rate limited (attempt {attempt}/{MAX_RETRIES}), backing off {delay:.1f}s")
                await asyncio.sleep(delay)
            elif error_code in (16, 26):
                logger.error(f"Last.fm permanent error {error_code}: {e}")
                raise
            else:
                logger.error(f"Last.fm API error {error_code}: {e}")
                raise
        except Exception as e:
            last_exception = e
            logger.error(f"Unexpected Last.fm error on attempt {attempt}: {type(e).__name__}: {e}")
            if attempt < MAX_RETRIES:
                delay = min(BASE_DELAY * (2 ** (attempt - 1)) + random.uniform(0, 0.5), MAX_DELAY)
                await asyncio.sleep(delay)
    raise last_exception


async def _rate_limited_call(fn, *args, **kwargs):
    async with _lastfm_lock:
        result = await _fetch_with_retry(fn, *args, **kwargs)
        await asyncio.sleep(RATE_LIMIT_DELAY)
        return result


async def fetch_recent_tracks(user, time_from=None, limit=200):
    def _fetch():
        return user.get_recent_tracks(now_playing=False, limit=limit, time_from=time_from)
    return await _rate_limited_call(_fetch)


async def get_artist_listener_count(artist_name: str) -> tuple[int, str]:
    def _fetch():
        artist = pylast.Artist(artist_name, network)
        response = artist._request("artist.getInfo")
        canonical_name = pylast._extract(response, "name")
        listeners = int(pylast._extract(response, "listeners"))
        return listeners, canonical_name
    return await _rate_limited_call(_fetch)


async def validate_lastfm_user(lastfm_username: str):
    def _fetch():
        return network.get_user(lastfm_username)
    return await _rate_limited_call(_fetch)


async def get_lastfm_user(lastfm_username: str):
    def _fetch():
        return network.get_user(lastfm_username)
    return await _rate_limited_call(_fetch)
