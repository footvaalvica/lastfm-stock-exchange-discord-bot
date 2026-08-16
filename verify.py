import os
import asyncio
os.environ.setdefault('LASTFM_API_KEY', 'test')
os.environ.setdefault('LASTFM_API_SECRET', 'test')
os.environ.setdefault('DISCORD_TOKEN', 'test')

import sqlite3
import datetime
from services.database import get_user, get_scrobbles, get_snapshot
from services.portfolio import calculate_portfolio_value

DB_PATH = 'db.sqlite3'


async def verify_claims():
    today = datetime.datetime.now(datetime.timezone.utc).date()
    today_str = today.isoformat().replace('-', '')

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    users = conn.execute('SELECT username FROM users').fetchall()
    conn.close()

    for user_row in users:
        username = user_row['username']
        value = await calculate_portfolio_value(username, today_str)
        print(f"{username}: {value:.2f}€")

        user_scrobbles = get_scrobbles(username)
        print(f"  Shares: {len(user_scrobbles)}")
        for s in user_scrobbles:
            current = get_snapshot(s['artist_name'], today_str)
            current_price = current['listeners'] if current else s['purchase_price']
            diff = current_price - s['purchase_price']
            diff_euros = diff / 100_000
            current_euros = current_price / 100_000
            purchase_euros = s['purchase_price'] / 100_000
            print(f"    {s['artist_name']} | bought: {purchase_euros:.2f}€ | now: {current_euros:.2f}€ | diff: {diff_euros:+.2f}€")
        print()


if __name__ == '__main__':
    asyncio.run(verify_claims())
