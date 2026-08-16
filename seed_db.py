import sqlite3
import datetime

DB_PATH = 'db.sqlite3'

def seed_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    
    # Clear existing data
    conn.execute('DELETE FROM scrobbles')
    conn.execute('DELETE FROM users')
    conn.execute('DELETE FROM artist_popularity')
    
    # Sample users
    users = [
        ('alice', 'alice_lastfm', 0, 0),
        ('bob', 'bob_lastfm', 0, 0),
        ('charlie', 'charlie_lastfm', 0, 0),
    ]
    conn.executemany(
        'INSERT INTO users (username, lastfm_username, money, last_claim) VALUES (?, ?, ?, ?)',
        users
    )
    
    # Sample artist popularity snapshots (listeners over time)
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
    
    # Sample scrobbles (shares purchased at different prices/dates)
    scrobbles = []
    base_date = today - datetime.timedelta(days=25)
    
    # Alice's portfolio: mix of big and niche artists
    alice_scrobbles = [
        ('Taylor Swift', 'Anti-Hero', 'Midnights', 15250000, (base_date).isoformat().replace('-', '')),
        ('Drake', 'God\'s Plan', 'Scorpion', 11975000, (base_date + datetime.timedelta(days=1)).isoformat().replace('-', '')),
        ('Radiohead', 'Karma Police', 'OK Computer', 4520000, (base_date + datetime.timedelta(days=2)).isoformat().replace('-', '')),
        ('Death Grips', 'Guillotine', 'The Money Store', 351000, (base_date + datetime.timedelta(days=3)).isoformat().replace('-', '')),
        ('Floating Points', 'LesAlpx', 'Crush', 121000, (base_date + datetime.timedelta(days=4)).isoformat().replace('-', '')),
    ]
    for s in alice_scrobbles:
        scrobbles.append(('alice', *s))
    
    # Bob's portfolio: more niche focus
    bob_scrobbles = [
        ('Death Grips', 'Takyon', 'Death Grips', 349000, (base_date + datetime.timedelta(days=5)).isoformat().replace('-', '')),
        ('Floating Points', 'Last Bloom', 'Crush', 119000, (base_date + datetime.timedelta(days=6)).isoformat().replace('-', '')),
        ('Khruangbin', 'Maria También', 'The Universe Smiles Upon You', 802000, (base_date + datetime.timedelta(days=7)).isoformat().replace('-', '')),
        ('Radiohead', 'Weird Fishes', 'In Rainbows', 4510000, (base_date + datetime.timedelta(days=8)).isoformat().replace('-', '')),
    ]
    for s in bob_scrobbles:
        scrobbles.append(('bob', *s))
    
    # Charlie's portfolio: one big artist, one that crashed
    charlie_scrobbles = [
        ('Taylor Swift', 'Cruel Summer', 'Lover', 15100000, (base_date + datetime.timedelta(days=9)).isoformat().replace('-', '')),
        ('Drake', 'Hotline Bling', 'Views', 12050000, (base_date + datetime.timedelta(days=10)).isoformat().replace('-', '')),
    ]
    for s in charlie_scrobbles:
        scrobbles.append(('charlie', *s))
    
    conn.executemany(
        '''INSERT OR IGNORE INTO scrobbles
           (username, artist_name, title, album, purchase_price, scrobble_date)
           VALUES (?, ?, ?, ?, ?, ?)''',
        scrobbles
    )
    
    conn.commit()
    conn.close()
    print("Sample data inserted into db.sqlite3")
    print("Users: alice, bob, charlie")
    print(f"Artist snapshots: {len(snapshots)} entries across 6 artists over 30 days")
    print(f"Scrobbles: {len(scrobbles)} total")

if __name__ == '__main__':
    seed_db()
