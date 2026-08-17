import sqlite3
import datetime

DB_PATH = 'db.sqlite3'

def seed_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    guild_id = 1

    conn.execute('DELETE FROM scrobbles')
    conn.execute('DELETE FROM users')
    conn.execute('DELETE FROM artist_popularity')

    users = [
        (123456789, guild_id, 'alice', 'alice_lastfm', 0, 0, 0),
        (987654321, guild_id, 'bob', 'bob_lastfm', 0, 0, 0),
        (555555555, guild_id, 'charlie', 'charlie_lastfm', 0, 0, 0),
    ]
    conn.executemany(
        'INSERT INTO users (discord_id, guild_id, username, lastfm_username, money, last_claim, last_preview) VALUES (?, ?, ?, ?, ?, ?, ?)',
        users
    )

    today = datetime.datetime.now(datetime.timezone.utc).date()
    snapshots = []
    for days_ago in range(30, -1, -1):
        date = (today - datetime.timedelta(days=days_ago)).isoformat().replace('-', '')
        snapshots.extend([
            ('Taylor Swift', 15000000 + days_ago * 100, date),
            ('Drake', 12000000 - days_ago * 50, date),
            ('Radiohead', 4500000 + days_ago * 20, date),
            ('Death Grips', 350000 - days_ago * 10, date),
            ('Floating Points', 120000 + days_ago * 5, date),
            ('Khruangbin', 800000 + days_ago * 15, date),
        ])
    conn.executemany(
        'INSERT OR REPLACE INTO artist_popularity (artist_name, listeners, timestamp) VALUES (?, ?, ?)',
        snapshots
    )

    scrobbles = []
    base_date = today - datetime.timedelta(days=25)

    alice_scrobbles = [
        (123456789, guild_id, 'Taylor Swift', 15250000, (base_date).isoformat().replace('-', ''), 1),
        (123456789, guild_id, 'Drake', 11975000, (base_date + datetime.timedelta(days=1)).isoformat().replace('-', ''), 1),
        (123456789, guild_id, 'Radiohead', 4520000, (base_date + datetime.timedelta(days=2)).isoformat().replace('-', ''), 1),
        (123456789, guild_id, 'Death Grips', 351000, (base_date + datetime.timedelta(days=3)).isoformat().replace('-', ''), 1),
        (123456789, guild_id, 'Floating Points', 121000, (base_date + datetime.timedelta(days=4)).isoformat().replace('-', ''), 1),
    ]
    scrobbles.extend(alice_scrobbles)

    bob_scrobbles = [
        (987654321, guild_id, 'Death Grips', 349000, (base_date + datetime.timedelta(days=5)).isoformat().replace('-', ''), 1),
        (987654321, guild_id, 'Floating Points', 119000, (base_date + datetime.timedelta(days=6)).isoformat().replace('-', ''), 1),
        (987654321, guild_id, 'Khruangbin', 802000, (base_date + datetime.timedelta(days=7)).isoformat().replace('-', ''), 1),
        (987654321, guild_id, 'Radiohead', 4510000, (base_date + datetime.timedelta(days=8)).isoformat().replace('-', ''), 1),
    ]
    scrobbles.extend(bob_scrobbles)

    charlie_scrobbles = [
        (555555555, guild_id, 'Taylor Swift', 15100000, (base_date + datetime.timedelta(days=9)).isoformat().replace('-', ''), 1),
        (555555555, guild_id, 'Drake', 12050000, (base_date + datetime.timedelta(days=10)).isoformat().replace('-', ''), 1),
    ]
    scrobbles.extend(charlie_scrobbles)

    conn.executemany(
        'INSERT OR IGNORE INTO scrobbles (discord_id, guild_id, artist_name, purchase_price, scrobble_date, count) VALUES (?, ?, ?, ?, ?, ?)',
        scrobbles
    )

    conn.commit()
    conn.close()
    print("Sample data inserted into db.sqlite3")
    print("Users: alice (123456789), bob (987654321), charlie (555555555)")
    print(f"Artist snapshots: {len(snapshots)} entries across 6 artists over 30 days")
    print(f"Scrobbles: {len(scrobbles)} total")

if __name__ == '__main__':
    seed_db()
