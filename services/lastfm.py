import asyncio
import random
import logging
import pylast
from config import network

logger = logging.getLogger('lastfm_bot')

MAX_RETRIES = 3
BASE_DELAY = 1.0
MAX_DELAY = 10.0
RATE_LIMIT_DELAY = 0.3
FETCH_TIMEOUT = 30


async def _fetch_with_retry(fn, *args, **kwargs):
    last_exception = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            result = await asyncio.wait_for(asyncio.to_thread(fn, *args, **kwargs), timeout=FETCH_TIMEOUT)
            await asyncio.sleep(RATE_LIMIT_DELAY)
            return result
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
        except pylast.PyLastError as e:
            cause = getattr(e, '__cause__', None)
            if cause is not None and hasattr(cause, 'code'):
                last_exception = cause
                error_code = cause.code
                if error_code == 29:
                    delay = min(BASE_DELAY * (2 ** (attempt - 1)) + random.uniform(0, 0.5), MAX_DELAY)
                    logger.warning(f"Last.fm rate limited (attempt {attempt}/{MAX_RETRIES}), backing off {delay:.1f}s")
                    await asyncio.sleep(delay)
                elif error_code in (16, 26):
                    logger.error(f"Last.fm permanent error {error_code}: {e}")
                    raise last_exception
                else:
                    logger.error(f"Last.fm API error {error_code}: {e}")
                    raise last_exception
            else:
                last_exception = e
                logger.error(f"Unexpected Last.fm error on attempt {attempt}: {type(e).__name__}: {e}")
                if attempt < MAX_RETRIES:
                    delay = min(BASE_DELAY * (2 ** (attempt - 1)) + random.uniform(0, 0.5), MAX_DELAY)
                    await asyncio.sleep(delay)
        except asyncio.TimeoutError:
            last_exception = TimeoutError(f"Last.fm request timed out after {FETCH_TIMEOUT}s on attempt {attempt}")
            logger.error(str(last_exception))
            if attempt < MAX_RETRIES:
                delay = min(BASE_DELAY * (2 ** (attempt - 1)) + random.uniform(0, 0.5), MAX_DELAY)
                await asyncio.sleep(delay)
        except Exception as e:
            last_exception = e
            logger.error(f"Unexpected Last.fm error on attempt {attempt}: {type(e).__name__}: {e}")
            if attempt < MAX_RETRIES:
                delay = min(BASE_DELAY * (2 ** (attempt - 1)) + random.uniform(0, 0.5), MAX_DELAY)
                await asyncio.sleep(delay)
    raise last_exception


async def fetch_recent_tracks(user, time_from=None, limit=100):
    def _fetch():
        return user.get_recent_tracks(now_playing=False, limit=limit, time_from=time_from)
    return await _fetch_with_retry(_fetch)




async def validate_lastfm_user(lastfm_username: str):
    def _fetch():
        return network.get_user(lastfm_username)
    return await _fetch_with_retry(_fetch)


async def get_canonical_artist_name(artist_name: str) -> str:
    def _fetch():
        return network.get_artist(artist_name)
    try:
        artist = await _fetch_with_retry(_fetch)
        correction = artist.get_correction()
        return correction or artist.name or artist_name
    except Exception:
        return artist_name



