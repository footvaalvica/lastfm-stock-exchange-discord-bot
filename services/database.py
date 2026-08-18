import sqlite3
import datetime
from config import DB_PATH

MAX_DAILY_CHANGE = 0.5
VOLATILITY_MULTIPLIER = 3


def _cap_daily_price(base_price: int, current_price: int, volatility_multiplier: float = VOLATILITY_MULTIPLIER) -> int:
    if base_price <= 0:
        return current_price
    raw_ratio = current_price / base_price
    centered = raw_ratio - 1.0
    scaled = centered * volatility_multiplier
    capped_scaled = max(-MAX_DAILY_CHANGE, min(scaled, MAX_DAILY_CHANGE))
    capped_ratio = 1.0 + capped_scaled
    return int(base_price * capped_ratio)


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA synchronous=NORMAL')
    return conn


_db_initialized = False
_db_path_at_init = None


def init_db():
    global _db_initialized, _db_path_at_init
    if _db_initialized and _db_path_at_init == DB_PATH:
        return
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

    conn.execute('CREATE INDEX IF NOT EXISTS idx_scrobbles_guild ON scrobbles(guild_id)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_scrobbles_discord_id ON scrobbles(discord_id)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_scrobbles_artist ON scrobbles(artist_name)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_artist_popularity_artist ON artist_popularity(artist_name)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_artist_popularity_artist_ts ON artist_popularity(artist_name, timestamp)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_user_guilds_discord_id ON user_guilds(discord_id)')
    conn.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_users_discord_id ON users(discord_id)')

    conn.commit()
    conn.close()
    _db_initialized = True
    _db_path_at_init = DB_PATH


def get_user(discord_id: int):
    conn = get_db()
    try:
        row = conn.execute(
            'SELECT * FROM users WHERE discord_id = ?',
            (discord_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def insert_user(discord_id: int, guild_id: int, username: str, lastfm_username: str, money: float = 0, last_claim: int = 0):
    conn = get_db()
    try:
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
    finally:
        conn.close()


def add_user_to_guild(discord_id: int, guild_id: int):
    conn = get_db()
    try:
        conn.execute(
            'INSERT OR IGNORE INTO user_guilds (discord_id, guild_id) VALUES (?, ?)',
            (discord_id, guild_id)
        )
        conn.commit()
    finally:
        conn.close()


def update_last_preview(discord_id: int, timestamp: int):
    conn = get_db()
    try:
        conn.execute(
            'UPDATE users SET last_preview = ? WHERE discord_id = ?',
            (timestamp, discord_id)
        )
        conn.commit()
    finally:
        conn.close()


def get_scrobbles(discord_id: int):
    conn = get_db()
    try:
        rows = conn.execute(
            'SELECT artist_name, purchase_price, scrobble_date, count FROM scrobbles WHERE discord_id = ?',
            (discord_id,)
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def get_transactions(discord_id: int, artist_name: str | None = None, limit: int = 100) -> list[dict]:
    conn = get_db()
    try:
        if artist_name:
            rows = conn.execute(
                '''SELECT artist_name, purchase_price, scrobble_date, count
                   FROM scrobbles
                   WHERE discord_id = ? AND artist_name = ? COLLATE NOCASE
                   ORDER BY scrobble_date DESC
                   LIMIT ?''',
                (discord_id, artist_name, limit)
            ).fetchall()
        else:
            rows = conn.execute(
                '''SELECT artist_name, purchase_price, scrobble_date, count
                   FROM scrobbles
                   WHERE discord_id = ?
                   ORDER BY scrobble_date DESC
                   LIMIT ?''',
                (discord_id, limit)
            ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def insert_scrobble(discord_id: int, guild_id: int, artist_name: str, purchase_price: int, scrobble_date: str, count: int = 1):
    conn = get_db()
    try:
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
    finally:
        conn.close()


def update_user_money_and_claim(discord_id: int, money: float, last_claim: int):
    conn = get_db()
    try:
        conn.execute(
            'UPDATE users SET money = ?, last_claim = ? WHERE discord_id = ?',
            (money, last_claim, discord_id)
        )
        conn.commit()
    finally:
        conn.close()


def update_user_money(discord_id: int, money: float):
    conn = get_db()
    try:
        conn.execute(
            'UPDATE users SET money = ? WHERE discord_id = ?',
            (money, discord_id)
        )
        conn.commit()
    finally:
        conn.close()


def get_closest_snapshot(artist_name: str, target_date_str: str):
    target_ts = int(target_date_str)
    conn = get_db()
    try:
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
    finally:
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


def get_closest_snapshot_bulk(artist_names: list[str], target_date_str: str) -> dict[str, int]:
    if not artist_names:
        return {}
    target_ts = int(target_date_str)
    conn = get_db()
    try:
        placeholders = ','.join('?' for _ in artist_names)
        before_rows = conn.execute(
            f'''SELECT artist_name, listeners, timestamp
                FROM artist_popularity
                WHERE artist_name IN ({placeholders}) COLLATE NOCASE
                AND timestamp <= ?
                ORDER BY timestamp DESC''',
            artist_names + [target_date_str]
        ).fetchall()
        after_rows = conn.execute(
            f'''SELECT artist_name, listeners, timestamp
                FROM artist_popularity
                WHERE artist_name IN ({placeholders}) COLLATE NOCASE
                AND timestamp >= ?
                ORDER BY timestamp ASC''',
            artist_names + [target_date_str]
        ).fetchall()
    finally:
        conn.close()

    best: dict[str, tuple[int, int]] = {}
    for row in before_rows:
        name = row['artist_name']
        dist = abs(int(row['timestamp']) - target_ts)
        if name not in best or dist < best[name][0]:
            best[name] = (dist, row['listeners'])
    for row in after_rows:
        name = row['artist_name']
        dist = abs(int(row['timestamp']) - target_ts)
        if name not in best or dist < best[name][0]:
            best[name] = (dist, row['listeners'])
    return {name: listeners for name, (_, listeners) in best.items()}


def get_snapshot(artist_name: str, date_str: str):
    conn = get_db()
    try:
        row = conn.execute(
            'SELECT artist_name, listeners FROM artist_popularity WHERE artist_name = ? COLLATE NOCASE AND timestamp = ?',
            (artist_name, date_str)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_latest_snapshot(artist_name: str):
    conn = get_db()
    try:
        row = conn.execute(
            'SELECT artist_name, listeners, timestamp FROM artist_popularity WHERE artist_name = ? COLLATE NOCASE ORDER BY timestamp DESC LIMIT 1',
            (artist_name,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_snapshots_bulk(artist_names: list[str], date_str: str) -> dict[str, int]:
    if not artist_names:
        return {}
    conn = get_db()
    try:
        placeholders = ','.join('?' for _ in artist_names)
        rows = conn.execute(
            f'SELECT artist_name, listeners FROM artist_popularity WHERE artist_name IN ({placeholders}) COLLATE NOCASE AND timestamp = ?',
            artist_names + [date_str]
        ).fetchall()
        return {row['artist_name']: row['listeners'] for row in rows}
    finally:
        conn.close()


def get_latest_snapshots_bulk(artist_names: list[str]) -> dict[str, int]:
    if not artist_names:
        return {}
    conn = get_db()
    try:
        placeholders = ','.join('?' for _ in artist_names)
        rows = conn.execute(
            f'''SELECT artist_name, listeners
                FROM artist_popularity
                WHERE artist_name IN ({placeholders}) COLLATE NOCASE
                ORDER BY timestamp DESC''',
            artist_names
        ).fetchall()
        latest = {}
        for row in rows:
            name = row['artist_name']
            if name not in latest:
                latest[name] = row['listeners']
        return latest
    finally:
        conn.close()


def upsert_snapshot(artist_name: str, listeners: int, timestamp: str):
    conn = get_db()
    try:
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
    finally:
        conn.close()


def get_total_scrobbles_for_artist(artist_name: str) -> int:
    conn = get_db()
    try:
        row = conn.execute(
            'SELECT SUM(count) as total FROM scrobbles WHERE artist_name = ? COLLATE NOCASE',
            (artist_name,)
        ).fetchone()
        return row['total'] if row and row['total'] is not None else 0
    finally:
        conn.close()


def get_price_changes(days: int | str = 1) -> list[dict]:
    conn = get_db()
    try:
        now_utc = datetime.datetime.now(datetime.timezone.utc)
        today = now_utc.date().isoformat().replace('-', '')

        today_rows = conn.execute(
            'SELECT artist_name, listeners FROM artist_popularity WHERE timestamp = ?',
            (today,)
        ).fetchall()

        if days == "alltime":
            past_rows = conn.execute(
                '''SELECT artist_name, listeners FROM artist_popularity
                   WHERE timestamp = (
                       SELECT MIN(timestamp) FROM artist_popularity ap2
                       WHERE ap2.artist_name = artist_popularity.artist_name COLLATE NOCASE
                   )'''
            ).fetchall()
        else:
            past = (now_utc.date() - datetime.timedelta(days=days)).isoformat().replace('-', '')
            past_rows = conn.execute(
                'SELECT artist_name, listeners FROM artist_popularity WHERE timestamp = ?',
                (past,)
            ).fetchall()
    finally:
        conn.close()

    past_by_name = {row['artist_name']: row['listeners'] for row in past_rows}
    result = []
    for row in today_rows:
        artist_name = row['artist_name']
        today_listeners = row['listeners']
        past_listeners = past_by_name.get(artist_name)
        if past_listeners is None:
            continue
        capped_today = _cap_daily_price(past_listeners, today_listeners)
        capped_today = max(capped_today, 1)
        change = capped_today - past_listeners
        change_percent = (change / past_listeners * 100) if past_listeners > 0 else 0.0
        result.append({
            'artist_name': artist_name,
            'today_listeners': capped_today,
            'past_listeners': past_listeners,
            'change': change,
            'change_percent': change_percent,
        })
    return result


def get_most_held_artists(limit: int = 5, guild_id: int = 0) -> list[dict]:
    conn = get_db()
    try:
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
        return [dict(row) for row in rows]
    finally:
        conn.close()


def set_guild_config(guild_id: int, market_channel_id: int, market_hour_local: int, market_timezone: str = 'UTC'):
    conn = get_db()
    try:
        conn.execute(
            'INSERT OR REPLACE INTO guild_config (guild_id, market_channel_id, market_hour_local, market_timezone) VALUES (?, ?, ?, ?)',
            (guild_id, market_channel_id, market_hour_local, market_timezone)
        )
        conn.commit()
    finally:
        conn.close()


def get_all_guild_configs() -> list[dict]:
    conn = get_db()
    try:
        rows = conn.execute('SELECT * FROM guild_config WHERE market_channel_id IS NOT NULL').fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def get_daily_scrobble_counts(discord_id: int, days: int = 7) -> dict[str, int]:
    conn = get_db()
    try:
        now_utc = datetime.datetime.now(datetime.timezone.utc)
        cutoff = (now_utc.date() - datetime.timedelta(days=days)).isoformat().replace('-', '')
        today_str = now_utc.date().isoformat().replace('-', '')
        rows = conn.execute(
            '''SELECT scrobble_date, SUM(count) as total
               FROM scrobbles
               WHERE discord_id = ? AND scrobble_date >= ? AND scrobble_date < ?
               GROUP BY scrobble_date''',
            (discord_id, cutoff, today_str)
        ).fetchall()
        return {row['scrobble_date']: row['total'] for row in rows}
    finally:
        conn.close()
