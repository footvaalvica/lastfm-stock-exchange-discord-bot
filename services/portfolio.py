import datetime
import pylast
from services.database import (
    get_user, get_scrobbles, insert_scrobble, update_user_money_and_claim,
    get_closest_snapshot, get_snapshot, upsert_snapshot,
    get_total_scrobbles_for_artist
)
from services.lastfm import fetch_recent_tracks, get_artist_listener_count


BASE_SHARE_VALUE = 1.0


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
            price, _ = await get_or_create_daily_snapshot(artist_name, today_str)
            today_prices[artist_name] = price
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


async def get_or_create_daily_snapshot(artist_name: str, date_str: str) -> tuple[int, str]:
    existing = get_snapshot(artist_name, date_str)
    if existing:
        return existing['listeners'], existing['artist_name']

    listeners, canonical_name = await get_artist_listener_count(artist_name)
    upsert_snapshot(canonical_name, listeners, date_str)
    return listeners, canonical_name


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
        scrobble_timestamp = datetime.datetime.fromtimestamp(
            int(scrobble.timestamp), datetime.timezone.utc
        ).date().isoformat().replace('-', '')

        existing = get_snapshot(artist_name, today_str)
        if existing:
            canonical_name = existing['artist_name']
            today_price = existing['listeners']
        else:
            today_price, canonical_name = await get_artist_listener_count(artist_name)
            upsert_snapshot(canonical_name, today_price, today_str)

        if use_today_price or scrobble_timestamp == today_str:
            purchase_price = today_price
        else:
            purchase_price = get_closest_snapshot(canonical_name, scrobble_timestamp)
            if purchase_price is None:
                purchase_price = today_price

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
        current_price, canonical_name = await get_or_create_daily_snapshot(artist_name, today_str)
        avg_purchase = data['total_purchase'] / data['shares']
        if avg_purchase <= 0:
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

    if sort_by == "value":
        breakdown.sort(key=lambda x: x['current_value'], reverse=True)
    elif sort_by == "price":
        breakdown.sort(key=lambda x: x['current_price'], reverse=True)
    elif sort_by == "quantity":
        breakdown.sort(key=lambda x: x['shares'], reverse=True)

    return breakdown


async def get_artist_info(artist_name: str, today_str: str) -> dict | None:
    existing = get_snapshot(artist_name, today_str)
    if existing:
        canonical_name = existing['artist_name']
    else:
        listeners, canonical_name = await get_artist_listener_count(artist_name)
        upsert_snapshot(canonical_name, listeners, today_str)
        existing = get_snapshot(canonical_name, today_str)

    current_price = existing['listeners']
    total_shares = get_total_scrobbles_for_artist(canonical_name)
    base_price = current_price

    gain_loss_percent = ((current_price / base_price) - 1) * 100 if base_price > 0 else 0.0

    return {
        'artist_name': canonical_name,
        'base_price': base_price,
        'current_price': current_price,
        'base_value': BASE_SHARE_VALUE,
        'current_share_value': BASE_SHARE_VALUE * (current_price / base_price) if base_price > 0 else BASE_SHARE_VALUE,
        'gain_loss_percent': gain_loss_percent,
        'total_shares': total_shares,
    }
