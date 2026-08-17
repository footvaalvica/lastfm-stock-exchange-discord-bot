import sqlite3
import datetime
from config import DB_PATH


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS users (
            discord_id INTEGER PRIMARY KEY,
            username TEXT NOT NULL,
            lastfm_username TEXT NOT NULL,
            money REAL DEFAULT 0,
            last_claim INTEGER DEFAULT 0,
            last_preview INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS scrobbles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            discord_id INTEGER NOT NULL,
            artist_name TEXT NOT NULL,
            title TEXT,
            album TEXT,
            purchase_price INTEGER,
            scrobble_date TEXT NOT NULL,
            FOREIGN KEY (discord_id) REFERENCES users(discord_id),
            UNIQUE(discord_id, artist_name, title, scrobble_date)
        );
        CREATE TABLE IF NOT EXISTS artist_popularity (
            artist_name TEXT NOT NULL,
            listeners INTEGER NOT NULL,
            timestamp TEXT NOT NULL,
            PRIMARY KEY (artist_name, timestamp)
        );
        CREATE INDEX IF NOT EXISTS idx_scrobbles_discord_id ON scrobbles(discord_id);
        CREATE INDEX IF NOT EXISTS idx_scrobbles_artist ON scrobbles(artist_name);
    ''')
    try:
        conn.execute('ALTER TABLE artist_popularity DROP COLUMN fetched_at')
    except sqlite3.OperationalError:
        pass
    conn.commit()
    conn.close()


def get_user(discord_id: int):
    conn = get_db()
    row = conn.execute('SELECT * FROM users WHERE discord_id = ?', (discord_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def insert_user(discord_id: int, username: str, lastfm_username: str, money: float = 0, last_claim: int = 0):
    conn = get_db()
    conn.execute(
        '''INSERT INTO users (discord_id, username, lastfm_username, money, last_claim, last_preview)
           VALUES (?, ?, ?, ?, ?, 0)
           ON CONFLICT(discord_id) DO UPDATE SET
               username = excluded.username,
               lastfm_username = excluded.lastfm_username''',
        (discord_id, username, lastfm_username, money, last_claim)
    )
    conn.commit()
    conn.close()


def update_last_preview(discord_id: int, timestamp: int):
    conn = get_db()
    conn.execute(
        'UPDATE users SET last_preview = ? WHERE discord_id = ?',
        (timestamp, discord_id)
    )
    conn.commit()
    conn.close()


def get_scrobbles(discord_id: int):
    conn = get_db()
    rows = conn.execute(
        'SELECT artist_name, title, album, purchase_price, scrobble_date FROM scrobbles WHERE discord_id = ?',
        (discord_id,)
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def insert_scrobble(discord_id: int, artist_name: str, title: str, album: str, purchase_price: int, scrobble_date: str):
    conn = get_db()
    conn.execute(
        '''INSERT OR IGNORE INTO scrobbles
           (discord_id, artist_name, title, album, purchase_price, scrobble_date)
           VALUES (?, ?, ?, ?, ?, ?)''',
        (discord_id, artist_name, title, album, purchase_price, scrobble_date)
    )
    conn.commit()
    conn.close()


def update_user_money_and_claim(discord_id: int, money: float, last_claim: int):
    conn = get_db()
    conn.execute(
        'UPDATE users SET money = ?, last_claim = ? WHERE discord_id = ?',
        (money, last_claim, discord_id)
    )
    conn.commit()
    conn.close()


def update_user_money(discord_id: int, money: float):
    conn = get_db()
    conn.execute(
        'UPDATE users SET money = ? WHERE discord_id = ?',
        (money, discord_id)
    )
    conn.commit()
    conn.close()


def get_closest_snapshot(artist_name: str, target_date_str: str):
    conn = get_db()
    rows = conn.execute(
        'SELECT listeners, timestamp FROM artist_popularity WHERE artist_name = ? COLLATE NOCASE',
        (artist_name,)
    ).fetchall()
    conn.close()
    if not rows:
        return None

    target_ts = int(target_date_str)
    closest = min(rows, key=lambda x: abs(int(x['timestamp']) - target_ts))
    return closest['listeners']


def get_snapshot(artist_name: str, date_str: str):
    conn = get_db()
    row = conn.execute(
        'SELECT artist_name, listeners FROM artist_popularity WHERE artist_name = ? COLLATE NOCASE AND timestamp = ?',
        (artist_name, date_str)
    ).fetchone()
    conn.close()
    result = dict(row) if row else None
    return result


def upsert_snapshot(artist_name: str, listeners: int, timestamp: str):
    conn = get_db()
    conn.execute(
        'INSERT OR REPLACE INTO artist_popularity (artist_name, listeners, timestamp) VALUES (?, ?, ?)',
        (artist_name, listeners, timestamp)
    )
    conn.commit()
    conn.close()


def get_total_scrobbles_for_artist(artist_name: str) -> int:
    conn = get_db()
    row = conn.execute(
        'SELECT COUNT(*) as cnt FROM scrobbles WHERE artist_name = ? COLLATE NOCASE',
        (artist_name,)
    ).fetchone()
    conn.close()
    return row['cnt'] if row else 0


def get_price_changes(days: int = 1) -> list[dict]:
    conn = get_db()
    today = datetime.datetime.now(datetime.timezone.utc).date().isoformat().replace('-', '')
    past = (datetime.datetime.now(datetime.timezone.utc).date() - datetime.timedelta(days=days)).isoformat().replace('-', '')
    rows = conn.execute(
        '''SELECT
            t1.artist_name,
            t1.listeners as today_listeners,
            t2.listeners as past_listeners
           FROM artist_popularity t1
           LEFT JOIN artist_popularity t2
           ON t1.artist_name = t2.artist_name COLLATE NOCASE
           AND t2.timestamp = ?
           WHERE t1.timestamp = ?''',
        (past, today)
    ).fetchall()
    conn.close()
    result = []
    for row in rows:
        if row['past_listeners'] is None or row['today_listeners'] is None:
            continue
        change = row['today_listeners'] - row['past_listeners']
        change_percent = (change / row['past_listeners'] * 100) if row['past_listeners'] > 0 else 0.0
        result.append({
            'artist_name': row['artist_name'],
            'today_listeners': row['today_listeners'],
            'past_listeners': row['past_listeners'],
            'change': change,
            'change_percent': change_percent,
        })
    return result


def get_most_held_artists(limit: int = 5) -> list[dict]:
    conn = get_db()
    rows = conn.execute(
        '''SELECT artist_name, COUNT(*) as count
           FROM scrobbles
           GROUP BY artist_name COLLATE NOCASE
           ORDER BY count DESC
           LIMIT ?''',
        (limit,)
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]



