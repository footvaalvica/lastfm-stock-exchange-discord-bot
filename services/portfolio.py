import datetime
import pylast
from services.database import (
    get_user, get_scrobbles, get_scrobbles_by_artist, insert_scrobble, update_user_money_and_claim,
    get_closest_snapshot, get_snapshot, upsert_snapshot, get_db, update_artist_name_in_db
)
from services.lastfm import fetch_recent_tracks, get_artist_listener_count, get_artist_canonical_name


LISTENERS_PER_EURO = 100_000
BASE_SHARE_VALUE = 1.0


def listeners_to_euros(listeners: int) -> float:
    return listeners / LISTENERS_PER_EURO


_canonical_name_cache: dict[str, str] = {}


async def resolve_canonical_artist_name(artist_name: str) -> str:
    if artist_name in _canonical_name_cache:
        return _canonical_name_cache[artist_name]

    canonical = await get_artist_canonical_name(artist_name)
    resolved = canonical if canonical else artist_name

    if resolved != artist_name:
        update_artist_name_in_db(artist_name, resolved)

    _canonical_name_cache[artist_name] = resolved
    return resolved


async def calculate_portfolio_value(discord_id: int, today_str: str) -> tuple[float, float]:
    rows = get_scrobbles(discord_id)
    if not rows:
        return 0.0, 0.0

    today_prices = {}
    total_value = 0.0
    total_gain_percent = 0.0
    for row in rows:
        artist_name = row['artist_name']
        if artist_name not in today_prices:
            today_prices[artist_name] = await get_or_create_daily_snapshot(artist_name, today_str)
        current_price = today_prices[artist_name]
        purchase_price = row['purchase_price']
        if purchase_price <= 0:
            share_value = BASE_SHARE_VALUE
            gain_percent = 0.0
        else:
            share_value = BASE_SHARE_VALUE * (current_price / purchase_price)
            gain_percent = (current_price / purchase_price - 1) * 100
        total_value += share_value
        total_gain_percent += gain_percent

    avg_gain_loss_percent = total_gain_percent / len(rows)
    return total_value, avg_gain_loss_percent


async def get_or_create_daily_snapshot(artist_name: str, date_str: str) -> int:
    canonical_name = await resolve_canonical_artist_name(artist_name)
    existing = get_snapshot(canonical_name, date_str)
    if existing:
        return existing['listeners']

    listeners = await get_artist_listener_count(canonical_name)
    upsert_snapshot(canonical_name, listeners, date_str)
    return listeners


async def process_user_claim(user, discord_id: int) -> float:
    now = datetime.datetime.now(datetime.timezone.utc)
    today_str = now.date().isoformat().replace('-', '')
    unix_timestamp = int(now.timestamp())

    user_row = get_user(discord_id)
    if not user_row:
        return 0.0

    last_claim = user_row['last_claim']
    user_scrobbles = get_scrobbles(discord_id)

    if last_claim == 0:
        time_from = int((now - datetime.timedelta(days=1)).timestamp())
        use_today_price = True
    else:
        time_from = last_claim
        use_today_price = False

    daily_scrobbles = await fetch_recent_tracks(user, time_from=time_from, limit=200)

    new_scrobbles = []
    for scrobble in daily_scrobbles:
        artist_name = scrobble.track.artist.name
        canonical_name = await resolve_canonical_artist_name(artist_name)
        scrobble_timestamp = datetime.datetime.fromtimestamp(
            int(scrobble.timestamp), datetime.timezone.utc
        ).date().isoformat().replace('-', '')

        if use_today_price:
            purchase_price = await get_or_create_daily_snapshot(canonical_name, today_str)
        else:
            purchase_price = get_closest_snapshot(canonical_name, scrobble_timestamp)
            if purchase_price is None:
                purchase_price = await get_or_create_daily_snapshot(canonical_name, today_str)

        try:
            album = scrobble.track.get_album()
            album_title = album.title if album is not None else "Single"
        except pylast.WSError as e:
            if "Track not found" in str(e):
                album_title = "Unknown"
            else:
                raise

        new_scrobbles.append((
            discord_id, canonical_name, scrobble.track.title,
            album_title, purchase_price, scrobble_timestamp
        ))

    for s in new_scrobbles:
        insert_scrobble(*s)

    total_money, gain_loss = await calculate_portfolio_value(discord_id, today_str)
    update_user_money_and_claim(discord_id, total_money, unix_timestamp)

    return total_money, gain_loss


async def get_portfolio_breakdown(discord_id: int, today_str: str, sort_by: str = "value"):
    rows = get_scrobbles(discord_id)
    if not rows:
        return []

    artist_data = {}
    for row in rows:
        artist_name = row['artist_name']
        if artist_name not in artist_data:
            artist_data[artist_name] = {
                'shares': 0,
                'total_purchase': 0,
            }
        artist_data[artist_name]['shares'] += 1
        artist_data[artist_name]['total_purchase'] += row['purchase_price']

    breakdown = []
    for artist_name, data in artist_data.items():
        snapshot = get_snapshot(artist_name, today_str)
        if snapshot:
            current_price = snapshot['listeners']
        else:
            current_price = get_closest_snapshot(artist_name, today_str)
            if current_price is None:
                continue
        avg_purchase = data['total_purchase'] / data['shares']
        if avg_purchase <= 0:
            current_value = BASE_SHARE_VALUE * data['shares']
            gain_loss_percent = 0.0
        else:
            current_value = BASE_SHARE_VALUE * (current_price / avg_purchase) * data['shares']
            gain_loss_percent = (current_price / avg_purchase - 1) * 100
        breakdown.append({
            'artist_name': artist_name,
            'shares': data['shares'],
            'avg_purchase_price': avg_purchase,
            'current_price': current_price,
            'current_value': current_value,
            'gain_loss_percent': gain_loss_percent,
        })

    if sort_by == "value":
        breakdown.sort(key=lambda x: x['current_value'], reverse=True)
    elif sort_by == "price":
        breakdown.sort(key=lambda x: x['current_price'], reverse=True)
    elif sort_by == "quantity":
        breakdown.sort(key=lambda x: x['shares'], reverse=True)

    return breakdown


async def get_artist_info(artist_name: str, today_str: str) -> dict | None:
    canonical_name = await resolve_canonical_artist_name(artist_name)
    rows = get_scrobbles_by_artist(canonical_name)
    if not rows:
        listeners = await get_or_create_daily_snapshot(canonical_name, today_str)
        if listeners is None:
            return None
        rows = [{
            'artist_name': canonical_name,
            'purchase_price': listeners,
        }]

    snapshot = get_snapshot(canonical_name, today_str)
    if snapshot:
        current_price = snapshot['listeners']
    else:
        current_price = get_closest_snapshot(canonical_name, today_str)
        if current_price is None:
            listeners = await get_or_create_daily_snapshot(canonical_name, today_str)
            if listeners is None:
                return None
            current_price = listeners

    base_price = rows[0]['purchase_price']
    for row in rows:
        if row['purchase_price'] < base_price:
            base_price = row['purchase_price']

    gain_loss_percent = ((current_price / base_price) - 1) * 100 if base_price > 0 else 0.0
    total_shares = len(rows)

    return {
        'artist_name': canonical_name,
        'base_price': base_price,
        'current_price': current_price,
        'base_value': BASE_SHARE_VALUE,
        'current_share_value': BASE_SHARE_VALUE * (current_price / base_price) if base_price > 0 else BASE_SHARE_VALUE,
        'gain_loss_percent': gain_loss_percent,
        'total_shares': total_shares,
    }
