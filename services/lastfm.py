import asyncio
import random
import time
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


async def get_artist_listener_count(artist_name: str) -> int:
    def _fetch():
        artist = pylast.Artist(artist_name, network)
        return int(artist.get_listener_count())
    return await _rate_limited_call(_fetch)


async def get_artist_canonical_name(artist_name: str) -> str | None:
    def _fetch():
        search = network.search_for_artist(artist_name)
        results = search.get_next_page()
        if results:
            return results[0].name
        return None
    try:
        return await _rate_limited_call(_fetch)
    except Exception as e:
        logger.error(f"Failed to resolve artist name for '{artist_name}': {e}")
        return None


async def validate_lastfm_user(lastfm_username: str):
    def _fetch():
        return network.get_user(lastfm_username)
    return await _rate_limited_call(_fetch)


async def get_lastfm_user(lastfm_username: str):
    def _fetch():
        return network.get_user(lastfm_username)
    return await _rate_limited_call(_fetch)
