import sqlite3
import datetime
from config import DB_PATH


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()

    conn.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            discord_id INTEGER NOT NULL UNIQUE,
            guild_id INTEGER NOT NULL DEFAULT 0,
            username TEXT NOT NULL,
            lastfm_username TEXT NOT NULL,
            money REAL DEFAULT 0,
            last_claim INTEGER DEFAULT 0,
            last_preview INTEGER DEFAULT 0
        )
    ''')

    conn.execute('''
        CREATE TABLE IF NOT EXISTS scrobbles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL DEFAULT 0,
            discord_id INTEGER NOT NULL,
            artist_name TEXT NOT NULL,
            purchase_price INTEGER,
            scrobble_date TEXT NOT NULL,
            count INTEGER NOT NULL DEFAULT 1,
            FOREIGN KEY (discord_id) REFERENCES users(discord_id),
            UNIQUE(guild_id, discord_id, artist_name, scrobble_date)
        )
    ''')

    conn.execute('''
        CREATE TABLE IF NOT EXISTS artist_popularity (
            artist_name TEXT NOT NULL,
            listeners INTEGER NOT NULL,
            timestamp TEXT NOT NULL,
            PRIMARY KEY (artist_name, timestamp)
        )
    ''')

    conn.execute('''
        CREATE TABLE IF NOT EXISTS guild_config (
            guild_id INTEGER PRIMARY KEY,
            market_channel_id INTEGER,
            market_hour_local INTEGER DEFAULT 9,
            market_timezone TEXT DEFAULT 'UTC'
        )
    ''')

    conn.execute('''
        CREATE TABLE IF NOT EXISTS user_guilds (
            discord_id INTEGER NOT NULL,
            guild_id INTEGER NOT NULL,
            PRIMARY KEY (discord_id, guild_id)
        )
    ''')

    conn.execute('''
        CREATE TABLE IF NOT EXISTS migrations (
            name TEXT PRIMARY KEY,
            applied_at INTEGER DEFAULT (strftime('%s', 'now'))
        )
    ''')

    def applied(name: str) -> bool:
        row = conn.execute('SELECT name FROM migrations WHERE name = ?', (name,)).fetchone()
        return row is not None

    def apply(name: str, sql: str):
        try:
            conn.execute(sql)
        except sqlite3.OperationalError as e:
            msg = str(e).lower()
            if 'duplicate column' not in msg and 'already exists' not in msg and 'no such column' not in msg:
                raise
        conn.execute('INSERT OR IGNORE INTO migrations (name) VALUES (?)', (name,))

    if not applied('add_guild_id_to_users'):
        apply('add_guild_id_to_users', 'ALTER TABLE users ADD COLUMN guild_id INTEGER DEFAULT 0')
    if not applied('add_guild_id_to_scrobbles'):
        apply('add_guild_id_to_scrobbles', 'ALTER TABLE scrobbles ADD COLUMN guild_id INTEGER DEFAULT 0')
    if not applied('add_count_to_scrobbles'):
        apply('add_count_to_scrobbles', 'ALTER TABLE scrobbles ADD COLUMN count INTEGER DEFAULT 1')

    if not applied('remove_title_album_from_scrobbles'):
        cols = [c[1] for c in conn.execute('PRAGMA table_info(scrobbles)').fetchall()]
        if 'title' in cols or 'album' in cols:
            apply('remove_title_album_from_scrobbles', '''
                CREATE TABLE scrobbles_new (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL DEFAULT 0,
                    discord_id INTEGER NOT NULL,
                    artist_name TEXT NOT NULL,
                    purchase_price INTEGER,
                    scrobble_date TEXT NOT NULL,
                    count INTEGER NOT NULL DEFAULT 1,
                    FOREIGN KEY (discord_id) REFERENCES users(discord_id),
                    UNIQUE(guild_id, discord_id, artist_name, scrobble_date)
                )
            ''')
            conn.execute('''
                INSERT OR IGNORE INTO scrobbles_new (id, guild_id, discord_id, artist_name, purchase_price, scrobble_date, count)
                SELECT id, guild_id, discord_id, artist_name, purchase_price, scrobble_date, COUNT(*) as count
                FROM scrobbles
                GROUP BY guild_id, discord_id, artist_name, scrobble_date
            ''')
            conn.execute('DROP TABLE scrobbles')
            conn.execute('ALTER TABLE scrobbles_new RENAME TO scrobbles')

    if not applied('add_market_timezone_to_guild_config'):
        apply('add_market_timezone_to_guild_config', "ALTER TABLE guild_config ADD COLUMN market_timezone TEXT DEFAULT 'UTC'")
    if not applied('rename_market_hour_utc_to_local'):
        apply('rename_market_hour_utc_to_local', 'ALTER TABLE guild_config RENAME COLUMN market_hour_utc TO market_hour_local')
    if not applied('drop_guilds_table'):
        apply('drop_guilds_table', 'DROP TABLE IF EXISTS guilds')
    if not applied('drop_bot_config_table'):
        apply('drop_bot_config_table', 'DROP TABLE IF EXISTS bot_config')

    if not applied('drop_fetched_at_from_artist_popularity'):
        apply('drop_fetched_at_from_artist_popularity', 'ALTER TABLE artist_popularity DROP COLUMN fetched_at')

    if not applied('create_indexes'):
        apply('create_indexes', '''
            CREATE INDEX IF NOT EXISTS idx_scrobbles_guild ON scrobbles(guild_id)
        ''')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_scrobbles_discord_id ON scrobbles(discord_id)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_scrobbles_artist ON scrobbles(artist_name)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_artist_popularity_artist ON artist_popularity(artist_name)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_artist_popularity_artist_ts ON artist_popularity(artist_name, timestamp)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_user_guilds_discord_id ON user_guilds(discord_id)')

    if not applied('create_user_guilds_index'):
        apply('create_user_guilds_index', '''
            CREATE INDEX IF NOT EXISTS idx_user_guilds_discord_id ON user_guilds(discord_id)
        ''')

    conn.commit()
    conn.close()


def get_user(discord_id: int):
    conn = get_db()
    row = conn.execute(
        'SELECT * FROM users WHERE discord_id = ?',
        (discord_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def insert_user(discord_id: int, guild_id: int, username: str, lastfm_username: str, money: float = 0, last_claim: int = 0):
    conn = get_db()
    conn.execute(
        '''INSERT INTO users (discord_id, guild_id, username, lastfm_username, money, last_claim, last_preview)
           VALUES (?, ?, ?, ?, ?, ?, 0)
           ON CONFLICT(discord_id) DO UPDATE SET
               username = excluded.username,
               lastfm_username = excluded.lastfm_username''',
        (discord_id, guild_id, username, lastfm_username, money, last_claim)
    )
    conn.execute(
        'INSERT OR IGNORE INTO user_guilds (discord_id, guild_id) VALUES (?, ?)',
        (discord_id, guild_id)
    )
    conn.commit()
    conn.close()


def add_user_to_guild(discord_id: int, guild_id: int):
    conn = get_db()
    conn.execute(
        'INSERT OR IGNORE INTO user_guilds (discord_id, guild_id) VALUES (?, ?)',
        (discord_id, guild_id)
    )
    conn.commit()
    conn.close()


def get_user_guilds(discord_id: int) -> list[int]:
    conn = get_db()
    rows = conn.execute(
        'SELECT guild_id FROM user_guilds WHERE discord_id = ?',
        (discord_id,)
    ).fetchall()
    conn.close()
    return [row['guild_id'] for row in rows]


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
        'SELECT artist_name, purchase_price, scrobble_date, count FROM scrobbles WHERE discord_id = ?',
        (discord_id,)
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_transactions(discord_id: int, artist_name: str | None = None) -> list[dict]:
    conn = get_db()
    if artist_name:
        rows = conn.execute(
            '''SELECT artist_name, purchase_price, scrobble_date, count
               FROM scrobbles
               WHERE discord_id = ? AND artist_name = ? COLLATE NOCASE
               ORDER BY scrobble_date DESC''',
            (discord_id, artist_name)
        ).fetchall()
    else:
        rows = conn.execute(
            '''SELECT artist_name, purchase_price, scrobble_date, count
               FROM scrobbles
               WHERE discord_id = ?
               ORDER BY scrobble_date DESC''',
            (discord_id,)
        ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def insert_scrobble(discord_id: int, guild_id: int, artist_name: str, purchase_price: int, scrobble_date: str, count: int = 1):
    conn = get_db()
    conn.execute(
        '''INSERT OR REPLACE INTO scrobbles
           (guild_id, discord_id, artist_name, purchase_price, scrobble_date, count)
           VALUES (?, ?, ?, ?, ?, COALESCE(
               (SELECT count FROM scrobbles WHERE guild_id = ? AND discord_id = ? AND artist_name = ? AND scrobble_date = ?),
               0
           ) + ?)''',
        (guild_id, discord_id, artist_name, purchase_price, scrobble_date,
         guild_id, discord_id, artist_name, scrobble_date, count)
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
    target_ts = int(target_date_str)
    conn = get_db()
    before = conn.execute(
        '''SELECT listeners, timestamp
           FROM artist_popularity
           WHERE artist_name = ? COLLATE NOCASE
           AND timestamp <= ?
           ORDER BY timestamp DESC
           LIMIT 1''',
        (artist_name, target_date_str)
    ).fetchone()
    after = conn.execute(
        '''SELECT listeners, timestamp
           FROM artist_popularity
           WHERE artist_name = ? COLLATE NOCASE
           AND timestamp >= ?
           ORDER BY timestamp ASC
           LIMIT 1''',
        (artist_name, target_date_str)
    ).fetchone()
    conn.close()

    candidates = []
    if before:
        candidates.append((abs(int(before['timestamp']) - target_ts), before['listeners']))
    if after:
        candidates.append((abs(int(after['timestamp']) - target_ts), after['listeners']))
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0])
    return candidates[0][1]


def get_snapshot(artist_name: str, date_str: str):
    conn = get_db()
    row = conn.execute(
        'SELECT artist_name, listeners FROM artist_popularity WHERE artist_name = ? COLLATE NOCASE AND timestamp = ?',
        (artist_name, date_str)
    ).fetchone()
    conn.close()
    result = dict(row) if row else None
    return result


def get_latest_snapshot(artist_name: str):
    conn = get_db()
    row = conn.execute(
        'SELECT artist_name, listeners, timestamp FROM artist_popularity WHERE artist_name = ? COLLATE NOCASE ORDER BY timestamp DESC LIMIT 1',
        (artist_name,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def upsert_snapshot(artist_name: str, listeners: int, timestamp: str):
    conn = get_db()
    existing = conn.execute(
        'SELECT artist_name FROM artist_popularity WHERE LOWER(artist_name) = LOWER(?)',
        (artist_name,)
    ).fetchone()
    canonical_name = existing['artist_name'] if existing else artist_name
    conn.execute(
        'INSERT OR REPLACE INTO artist_popularity (artist_name, listeners, timestamp) VALUES (?, ?, ?)',
        (canonical_name, listeners, timestamp)
    )
    conn.commit()
    conn.close()


def get_total_scrobbles_for_artist(artist_name: str) -> int:
    conn = get_db()
    row = conn.execute(
        'SELECT SUM(count) as total FROM scrobbles WHERE artist_name = ? COLLATE NOCASE',
        (artist_name,)
    ).fetchone()
    conn.close()
    return row['total'] if row and row['total'] is not None else 0


def get_price_changes(days: int = 1) -> list[dict]:
    conn = get_db()
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    today = now_utc.date().isoformat().replace('-', '')
    past = (now_utc.date() - datetime.timedelta(days=days)).isoformat().replace('-', '')

    today_rows = conn.execute(
        'SELECT artist_name, listeners FROM artist_popularity WHERE timestamp = ?',
        (today,)
    ).fetchall()

    past_rows = conn.execute(
        'SELECT artist_name, listeners FROM artist_popularity WHERE timestamp = ?',
        (past,)
    ).fetchall()
    conn.close()

    past_by_name = {row['artist_name']: row['listeners'] for row in past_rows}
    result = []
    for row in today_rows:
        artist_name = row['artist_name']
        today_listeners = row['listeners']
        past_listeners = past_by_name.get(artist_name)
        if past_listeners is None:
            continue
        change = today_listeners - past_listeners
        change_percent = (change / past_listeners * 100) if past_listeners > 0 else 0.0
        result.append({
            'artist_name': artist_name,
            'today_listeners': today_listeners,
            'past_listeners': past_listeners,
            'change': change,
            'change_percent': change_percent,
        })
    return result


def get_most_held_artists(limit: int = 5, guild_id: int = 0) -> list[dict]:
    conn = get_db()
    if guild_id == 0:
        rows = conn.execute(
            '''SELECT artist_name, SUM(count) as count
               FROM scrobbles
               GROUP BY artist_name COLLATE NOCASE
               ORDER BY count DESC
               LIMIT ?''',
            (limit,)
        ).fetchall()
    else:
        rows = conn.execute(
            '''SELECT artist_name, SUM(count) as count
               FROM scrobbles
               WHERE guild_id = ?
               GROUP BY artist_name COLLATE NOCASE
               ORDER BY count DESC
               LIMIT ?''',
            (guild_id, limit)
        ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def set_guild_config(guild_id: int, market_channel_id: int, market_hour_local: int, market_timezone: str = 'UTC'):
    conn = get_db()
    conn.execute(
        'INSERT OR REPLACE INTO guild_config (guild_id, market_channel_id, market_hour_local, market_timezone) VALUES (?, ?, ?, ?)',
        (guild_id, market_channel_id, market_hour_local, market_timezone)
    )
    conn.commit()
    conn.close()


def get_all_guild_configs() -> list[dict]:
    conn = get_db()
    rows = conn.execute('SELECT * FROM guild_config WHERE market_channel_id IS NOT NULL').fetchall()
    conn.close()
    return [dict(row) for row in rows]
