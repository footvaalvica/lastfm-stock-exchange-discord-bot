import datetime
import time
import asyncio
import pylast
from services.database import (
    get_user, get_scrobbles, update_user_money_and_claim,
    get_closest_snapshot, get_snapshot, get_latest_snapshot, get_db,
    get_total_scrobbles_for_artist, get_price_changes, get_most_held_artists,
    get_snapshots_bulk, get_latest_snapshots_bulk, get_closest_snapshot_bulk,
    get_daily_scrobble_counts, _cap_daily_price
)
from services.lastfm import fetch_recent_tracks, get_artist_listener_count


class LastFMPrivacyError(Exception):
    pass


BASE_SHARE_VALUE = 10.0

DAILY_SCROBBLE_CAP = 200
MULTIPLIER_TIERS = [
    (100, 1.2),
    (50, 1.1),
]


def get_claim_multiplier(discord_id: int) -> float:
    daily_counts = get_daily_scrobble_counts(discord_id, days=30)
    if not daily_counts:
        return 1.0

    today = datetime.datetime.now(datetime.timezone.utc).date()
    streak_ok = True
    min_daily = None

    for i in range(1, 8):
        date = today - datetime.timedelta(days=i)
        date_str = date.isoformat().replace('-', '')
        count = daily_counts.get(date_str, 0)
        if count <= 0:
            streak_ok = False
            break
        if min_daily is None or count < min_daily:
            min_daily = count

    if not streak_ok or min_daily is None:
        return 1.0

    if min_daily >= 100:
        return 1.2
    if min_daily >= 50:
        return 1.1
    return 1.0

_ARTIST_INFO_CACHE_TTL = 300
_ARTIST_INFO_CACHE_MAX = 1000
_artist_info_cache: dict[str, tuple[float, dict | None]] = {}
_artist_info_locks: dict[str, asyncio.Lock] = {}
_artist_info_cache_lock = asyncio.Lock()


async def _get_artist_info_cached(artist_name: str, today_str: str) -> dict | None:
    cache_key = artist_name.lower()
    now = time.time()

    async with _artist_info_cache_lock:
        cached = _artist_info_cache.get(cache_key)
        if cached and now - cached[0] < _ARTIST_INFO_CACHE_TTL:
            return cached[1]

        _artist_info_cache.pop(cache_key, None)
        _artist_info_locks.pop(cache_key, None)

        if len(_artist_info_cache) >= _ARTIST_INFO_CACHE_MAX:
            oldest_key = min(_artist_info_cache, key=lambda k: _artist_info_cache[k][0])
            del _artist_info_cache[oldest_key]
            _artist_info_locks.pop(oldest_key, None)

        if cache_key not in _artist_info_locks:
            _artist_info_locks[cache_key] = asyncio.Lock()
        lock = _artist_info_locks[cache_key]

    async with lock:
        async with _artist_info_cache_lock:
            cached = _artist_info_cache.get(cache_key)
            if cached and now - cached[0] < _ARTIST_INFO_CACHE_TTL:
                return cached[1]

        existing = get_snapshot(artist_name, today_str)
        if existing:
            canonical_name = existing['artist_name']
            current_price = existing['listeners']
        else:
            listeners, canonical_name = await get_artist_listener_count(artist_name)
            upsert_snapshot(canonical_name, listeners, today_str)
            current_price = listeners

        total_shares = get_total_scrobbles_for_artist(canonical_name)

        today_date = datetime.date.fromisoformat(today_str)
        yesterday_str = (today_date - datetime.timedelta(days=1)).isoformat().replace('-', '')
        yesterday_snapshot = get_snapshot(canonical_name, yesterday_str)
        base_price = yesterday_snapshot['listeners'] if yesterday_snapshot else current_price
        capped_price = _cap_daily_price(base_price, current_price)

        gain_loss_percent = ((capped_price / base_price) - 1) * 100 if base_price > 0 else 0.0

        result = {
            'artist_name': canonical_name,
            'base_price': base_price,
            'current_price': capped_price,
            'base_value': BASE_SHARE_VALUE,
            'current_share_value': BASE_SHARE_VALUE * (capped_price / base_price) if base_price > 0 else BASE_SHARE_VALUE,
            'gain_loss_percent': gain_loss_percent,
            'total_shares': total_shares,
        }

        async with _artist_info_cache_lock:
            _artist_info_cache[cache_key] = (now, result)

        return result


def calculate_portfolio_value(discord_id: int, today_str: str) -> tuple[float, float]:
    rows = get_scrobbles(discord_id)
    if not rows:
        return 0.0, 0.0

    artist_names = list({row['artist_name'] for row in rows})
    today_prices = get_snapshots_bulk(artist_names, today_str)
    latest_prices = get_latest_snapshots_bulk(artist_names)

    today_date = datetime.date.fromisoformat(today_str)
    yesterday_str = (today_date - datetime.timedelta(days=1)).isoformat().replace('-', '')
    yesterday_prices = get_snapshots_bulk(artist_names, yesterday_str)

    total_value = 0.0
    total_gain_percent = 0.0
    total_shares = 0
    for row in rows:
        artist_name = row['artist_name']
        count = row['count']
        current_price = today_prices.get(artist_name) or latest_prices.get(artist_name, 0)
        yesterday_price = yesterday_prices.get(artist_name)
        if yesterday_price is not None and yesterday_price > 0:
            current_price = _cap_daily_price(yesterday_price, current_price)
        has_price_data = current_price > 0
        purchase_price = row['purchase_price']
        if not has_price_data or purchase_price <= 0:
            share_value = BASE_SHARE_VALUE * count
            gain_percent = 0.0
        else:
            share_value = BASE_SHARE_VALUE * (current_price / purchase_price) * count
            gain_percent = (current_price / purchase_price - 1) * 100
        total_value += share_value
        total_gain_percent += gain_percent * count
        total_shares += count

    avg_gain_loss_percent = (total_gain_percent / total_shares) if total_shares > 0 else 0.0
    return total_value, avg_gain_loss_percent


def get_balance_stats(discord_id: int, today_str: str, yesterday_str: str) -> dict:
    rows = get_scrobbles(discord_id)
    if not rows:
        return {
            'total_value': 0.0,
            'total_shares': 0,
            'overall_gain': 0.0,
            'today_change': None,
            'top_holding': "N/A",
            'diversity': 0,
        }

    total_shares = sum(row['count'] for row in rows)

    artist_data = {}
    for row in rows:
        artist_name = row['artist_name']
        count = row['count']
        if artist_name not in artist_data:
            artist_data[artist_name] = {'shares': 0, 'total_purchase': 0}
        artist_data[artist_name]['shares'] += count
        artist_data[artist_name]['total_purchase'] += row['purchase_price'] * count

    artist_names = list(artist_data.keys())
    today_prices = get_snapshots_bulk(artist_names, today_str)
    latest_prices = get_latest_snapshots_bulk(artist_names)
    yesterday_prices = get_closest_snapshot_bulk(artist_names, yesterday_str)

    breakdown = []
    total_value = 0.0
    total_gain_percent = 0.0
    total_shares_for_gain = 0
    for artist_name, data in artist_data.items():
        current_price = today_prices.get(artist_name) or latest_prices.get(artist_name, 0)
        yesterday_price = yesterday_prices.get(artist_name)
        if yesterday_price is not None and yesterday_price > 0:
            current_price = _cap_daily_price(yesterday_price, current_price)
        has_price_data = current_price > 0
        avg_purchase = data['total_purchase'] / data['shares']
        shares = data['shares']
        if not has_price_data or avg_purchase <= 0:
            current_value = BASE_SHARE_VALUE * shares
            gain_loss_percent = 0.0
        else:
            current_value = BASE_SHARE_VALUE * (current_price / avg_purchase) * shares
            gain_loss_percent = (current_price / avg_purchase - 1) * 100

        total_value += current_value
        total_gain_percent += gain_loss_percent * shares
        total_shares_for_gain += shares
        breakdown.append({
            'artist_name': artist_name,
            'shares': shares,
            'current_value': current_value,
        })

    overall_gain = (total_gain_percent / total_shares_for_gain) if total_shares_for_gain > 0 else 0.0

    yesterday_value = 0.0
    for row in rows:
        artist_name = row['artist_name']
        count = row['count']
        purchase_price = row['purchase_price']
        price = yesterday_prices.get(artist_name, 0)
        if price <= 0 or purchase_price <= 0:
            yesterday_value += BASE_SHARE_VALUE * count
        else:
            yesterday_value += BASE_SHARE_VALUE * (price / purchase_price) * count

    today_change = ((total_value - yesterday_value) / yesterday_value * 100) if yesterday_value > 0 else None
    top_holding = max(breakdown, key=lambda x: x['current_value'])['artist_name'] if breakdown else "N/A"
    diversity = len(breakdown)

    return {
        'total_value': total_value,
        'total_shares': total_shares,
        'overall_gain': overall_gain,
        'today_change': today_change,
        'top_holding': top_holding,
        'diversity': diversity,
    }


async def process_user_claim(user, discord_id: int, guild_id: int) -> tuple[float, float]:
    now = datetime.datetime.now(datetime.timezone.utc)
    today_str = now.date().isoformat().replace('-', '')
    unix_timestamp = int(now.timestamp())

    user_row = get_user(discord_id)
    if not user_row:
        return 0.0, 0.0

    last_claim = user_row['last_claim']

    if last_claim == 0:
        time_from = int((now - datetime.timedelta(days=1)).timestamp())
        use_today_price = True
    else:
        time_from = last_claim
        use_today_price = False

    try:
        daily_scrobbles = await fetch_recent_tracks(user, time_from=time_from, limit=100)
    except (pylast.WSError, pylast.PyLastError) as e:
        cause = getattr(e, '__cause__', None)
        ws_error = cause if (cause is not None and hasattr(cause, 'code')) else (e if hasattr(e, 'code') else None)
        if ws_error is not None:
            error_code = getattr(ws_error, 'code', None)
            if error_code not in (29, 16, 26):
                raise LastFMPrivacyError(f"Last.fm user {user_row['lastfm_username']} has private recent tracks")
        raise

    artist_plays: dict[str, int] = {}
    artist_dates: dict[str, str] = {}
    for scrobble in daily_scrobbles:
        artist_name = scrobble.track.artist.name
        scrobble_timestamp = datetime.datetime.fromtimestamp(
            int(scrobble.timestamp), datetime.timezone.utc
        ).date().isoformat().replace('-', '')
        artist_plays[artist_name] = artist_plays.get(artist_name, 0) + 1
        artist_dates[artist_name] = scrobble_timestamp

    canonical_names: dict[str, str] = {}
    artist_purchase_prices: dict[str, int] = {}
    artist_today_prices: dict[str, int] = {}
    scrobbles_to_insert = []
    snapshots_to_upsert = []

    unique_artists = list(artist_plays.keys())
    today_snapshots = get_snapshots_bulk(unique_artists, today_str)
    latest_snapshots = get_latest_snapshots_bulk(unique_artists)

    for artist_name, play_count in artist_plays.items():
        scrobble_timestamp = artist_dates[artist_name]
        existing = today_snapshots.get(artist_name)
        if existing is not None:
            listeners = existing
            canonical_name = artist_name
        else:
            listeners, canonical_name = await get_artist_listener_count(artist_name)
            snapshots_to_upsert.append((canonical_name, listeners, today_str))
        canonical_names[artist_name] = canonical_name
        artist_today_prices[artist_name] = listeners

        if use_today_price or scrobble_timestamp == today_str:
            purchase_price = listeners
        else:
            historical = get_closest_snapshot_bulk([canonical_name], scrobble_timestamp)
            purchase_price = historical.get(canonical_name)
            if purchase_price is None:
                latest = latest_snapshots.get(canonical_name)
                purchase_price = latest if latest is not None else listeners
        artist_purchase_prices[artist_name] = purchase_price

        scrobbles_to_insert.append((discord_id, guild_id, canonical_name, purchase_price, scrobble_timestamp, play_count))

    conn = get_db()
    try:
        for canonical_name, today_price, ts in snapshots_to_upsert:
            conn.execute(
                'INSERT OR REPLACE INTO artist_popularity (artist_name, listeners, timestamp) VALUES (?, ?, ?)',
                (canonical_name, today_price, ts)
            )
        for discord_id, guild_id, canonical_name, purchase_price, scrobble_timestamp, play_count in scrobbles_to_insert:
            conn.execute(
                '''INSERT OR REPLACE INTO scrobbles
                   (guild_id, discord_id, artist_name, purchase_price, scrobble_date, count)
                   VALUES (?, ?, ?, ?, ?, COALESCE(
                       (SELECT count FROM scrobbles WHERE guild_id = ? AND discord_id = ? AND artist_name = ? AND scrobble_date = ?),
                       0
                   ) + ?)''',
                (guild_id, discord_id, canonical_name, purchase_price, scrobble_timestamp,
                 guild_id, discord_id, canonical_name, scrobble_timestamp, play_count)
            )
        conn.commit()
    finally:
        conn.close()

    total_money = 0.0
    total_gain_percent = 0.0
    total_shares = 0
    for artist_name, play_count in artist_plays.items():
        canonical_name = canonical_names[artist_name]
        current_price = artist_today_prices[artist_name]
        purchase_price = artist_purchase_prices[artist_name]

        if purchase_price <= 0:
            share_value = BASE_SHARE_VALUE * play_count
            gain_percent = 0.0
        else:
            share_value = BASE_SHARE_VALUE * (current_price / purchase_price) * play_count
            gain_percent = (current_price / purchase_price - 1) * 100
        total_money += share_value
        total_gain_percent += gain_percent * play_count
        total_shares += play_count

    avg_gain_loss_percent = (total_gain_percent / total_shares) if total_shares > 0 else 0.0
    multiplier = get_claim_multiplier(discord_id)
    total_money *= multiplier
    update_user_money_and_claim(discord_id, total_money, unix_timestamp)

    return total_money, avg_gain_loss_percent


def get_portfolio_breakdown(discord_id: int, today_str: str) -> list[dict]:
    rows = get_scrobbles(discord_id)
    if not rows:
        return []

    artist_data = {}
    for row in rows:
        artist_name = row['artist_name']
        count = row['count']
        if artist_name not in artist_data:
            artist_data[artist_name] = {
                'shares': 0,
                'total_purchase': 0,
            }
        artist_data[artist_name]['shares'] += count
        artist_data[artist_name]['total_purchase'] += row['purchase_price'] * count

    artist_names = list(artist_data.keys())
    today_prices = get_snapshots_bulk(artist_names, today_str)
    latest_prices = get_latest_snapshots_bulk(artist_names)
    today_date = datetime.date.fromisoformat(today_str)
    yesterday_str = (today_date - datetime.timedelta(days=1)).isoformat().replace('-', '')
    yesterday_prices = get_snapshots_bulk(artist_names, yesterday_str)

    breakdown = []
    for artist_name, data in artist_data.items():
        current_price = today_prices.get(artist_name) or latest_prices.get(artist_name, 0)
        yesterday_price = yesterday_prices.get(artist_name)
        if yesterday_price is not None and yesterday_price > 0:
            current_price = _cap_daily_price(yesterday_price, current_price)
        has_price_data = current_price > 0
        canonical_name = artist_name
        avg_purchase = data['total_purchase'] / data['shares']
        if not has_price_data or avg_purchase <= 0:
            current_value = BASE_SHARE_VALUE * data['shares']
            gain_loss_percent = 0.0
        else:
            current_value = BASE_SHARE_VALUE * (current_price / avg_purchase) * data['shares']
            gain_loss_percent = (current_price / avg_purchase - 1) * 100
        breakdown.append({
            'artist_name': canonical_name,
            'shares': data['shares'],
            'avg_purchase_price': avg_purchase,
            'current_price': current_price,
            'current_value': current_value,
            'gain_loss_percent': gain_loss_percent,
        })

    breakdown.sort(key=lambda x: x['current_value'], reverse=True)

    return breakdown


def get_artist_price_history(artist_name: str, days: int = 30) -> list[dict]:
    conn = get_db()
    try:
        today = datetime.datetime.now(datetime.timezone.utc).date()
        cutoff = (today - datetime.timedelta(days=days)).isoformat().replace('-', '')
        rows = conn.execute(
            '''SELECT artist_name, listeners, timestamp
               FROM artist_popularity
               WHERE artist_name = ? COLLATE NOCASE
               AND timestamp >= ?
               ORDER BY timestamp DESC''',
            (artist_name, cutoff)
        ).fetchall()
    finally:
        conn.close()

    seen_dates = set()
    daily = []
    for row in rows:
        ts = row['timestamp']
        if ts in seen_dates:
            continue
        seen_dates.add(ts)
        daily.append({'date': ts, 'listeners': row['listeners'], 'artist_name': row['artist_name']})

    daily.reverse()

    filtered = []
    prev_listeners = None
    for entry in daily:
        if prev_listeners is None or entry['listeners'] != prev_listeners:
            filtered.append(entry)
            prev_listeners = entry['listeners']

    return filtered


async def get_artist_info(artist_name: str, today_str: str) -> dict | None:
    return await _get_artist_info_cached(artist_name, today_str)


def get_market_overview(guild_id: int = 0, days: int = 1) -> dict:
    changes = get_price_changes(days=days)
    for entry in changes:
        past_listeners = entry.get('past_listeners', 0)
        capped_today = entry.get('today_listeners', 0)
        if past_listeners > 0:
            entry['current_share_value'] = BASE_SHARE_VALUE * (capped_today / past_listeners)
            entry['change_value'] = entry['current_share_value'] - BASE_SHARE_VALUE
        else:
            entry['current_share_value'] = BASE_SHARE_VALUE
            entry['change_value'] = 0.0
    gainers = sorted([c for c in changes if c['change_percent'] > 0], key=lambda x: x['change_percent'], reverse=True)[:5]
    losers = sorted([c for c in changes if c['change_percent'] < 0], key=lambda x: x['change_percent'])[:5]
    most_held = get_most_held_artists(limit=5, guild_id=guild_id)
    return {
        'gainers': gainers,
        'losers': losers,
        'most_held': most_held,
        'days': days,
    }
