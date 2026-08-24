import sqlite3
import datetime
from config import DB_PATH

MAX_DAILY_CHANGE = 0.5
VOLATILITY_MULTIPLIER = 1


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
            discord_id INTEGER NOT NULL,
            artist_name TEXT NOT NULL,
            purchase_price INTEGER DEFAULT 10,
            scrobble_date TEXT NOT NULL,
            count INTEGER NOT NULL DEFAULT 1,
            FOREIGN KEY (discord_id) REFERENCES users(discord_id),
            UNIQUE(discord_id, artist_name, scrobble_date)
        )
    ''')

    conn.execute('''
        CREATE TABLE IF NOT EXISTS artist_scrobbles (
            artist_name TEXT NOT NULL,
            daily_total INTEGER NOT NULL,
            scrobble_date TEXT NOT NULL,
            PRIMARY KEY (artist_name, scrobble_date)
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

    conn.execute('CREATE INDEX IF NOT EXISTS idx_scrobbles_discord_id ON scrobbles(discord_id)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_scrobbles_artist ON scrobbles(artist_name)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_artist_scrobbles_artist_date ON artist_scrobbles(artist_name, scrobble_date)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_user_guilds_discord_id ON user_guilds(discord_id)')

    _migrate_schema(conn)

    conn.commit()
    conn.close()
    _db_initialized = True
    _db_path_at_init = DB_PATH


def _migrate_schema(conn):
    version_row = conn.execute("PRAGMA user_version").fetchone()
    current_version = version_row[0] if version_row else 0

    if current_version < 1:
        columns = [row['name'] for row in conn.execute("PRAGMA table_info(scrobbles)").fetchall()]
        if 'guild_id' in columns:
            conn.execute('''
                CREATE TABLE IF NOT EXISTS scrobbles_new (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    discord_id INTEGER NOT NULL,
                    artist_name TEXT NOT NULL,
                    purchase_price INTEGER DEFAULT 10,
                    scrobble_date TEXT NOT NULL,
                    count INTEGER NOT NULL DEFAULT 1,
                    FOREIGN KEY (discord_id) REFERENCES users(discord_id),
                    UNIQUE(discord_id, artist_name, scrobble_date)
                )
            ''')
            conn.execute('''
                INSERT INTO scrobbles_new (id, discord_id, artist_name, purchase_price, scrobble_date, count)
                SELECT id, discord_id, artist_name, purchase_price, scrobble_date, count FROM scrobbles
            ''')
            conn.execute('DROP TABLE scrobbles')
            conn.execute('ALTER TABLE scrobbles_new RENAME TO scrobbles')
        conn.execute("PRAGMA user_version = 1")


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


def insert_user(discord_id: int, username: str, lastfm_username: str, money: float = 0, last_claim: int = 0, guild_id: int = 0):
    conn = get_db()
    try:
        conn.execute(
            '''INSERT INTO users (discord_id, username, lastfm_username, money, last_claim, last_preview)
               VALUES (?, ?, ?, ?, ?, 0)
               ON CONFLICT(discord_id) DO UPDATE SET
                   username = excluded.username,
                   lastfm_username = excluded.lastfm_username''',
            (discord_id, username, lastfm_username, money, last_claim)
        )
        if guild_id:
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


def update_last_preview(discord_id: int, scrobble_date: int):
    conn = get_db()
    try:
        conn.execute(
            'UPDATE users SET last_preview = ? WHERE discord_id = ?',
            (scrobble_date, discord_id)
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


def insert_scrobble(discord_id: int, artist_name: str, purchase_price: int, scrobble_date: str, count: int = 1):
    conn = get_db()
    try:
        conn.execute(
            '''INSERT OR REPLACE INTO scrobbles
               (discord_id, artist_name, purchase_price, scrobble_date, count)
               VALUES (?, ?, ?, ?, COALESCE(
                   (SELECT count FROM scrobbles WHERE discord_id = ? AND artist_name = ? AND scrobble_date = ?),
                   0
               ) + ?)''',
            (discord_id, artist_name, purchase_price, scrobble_date,
             discord_id, artist_name, scrobble_date, count)
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
            '''SELECT daily_total, scrobble_date
               FROM artist_scrobbles
               WHERE artist_name = ? COLLATE NOCASE
               AND scrobble_date <= ?
               ORDER BY scrobble_date DESC
               LIMIT 1''',
            (artist_name, target_date_str)
        ).fetchone()
        after = conn.execute(
            '''SELECT daily_total, scrobble_date
               FROM artist_scrobbles
               WHERE artist_name = ? COLLATE NOCASE
               AND scrobble_date >= ?
               ORDER BY scrobble_date ASC
               LIMIT 1''',
            (artist_name, target_date_str)
        ).fetchone()
    finally:
        conn.close()

    candidates = []
    if before:
        candidates.append((abs(int(before['scrobble_date']) - target_ts), before['daily_total']))
    if after:
        candidates.append((abs(int(after['scrobble_date']) - target_ts), after['daily_total']))
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
            f'''SELECT artist_name, daily_total, scrobble_date
                FROM artist_scrobbles
                WHERE artist_name IN ({placeholders}) COLLATE NOCASE
                AND scrobble_date <= ?
                ORDER BY scrobble_date DESC''',
            artist_names + [target_date_str]
        ).fetchall()
        after_rows = conn.execute(
            f'''SELECT artist_name, daily_total, scrobble_date
                FROM artist_scrobbles
                WHERE artist_name IN ({placeholders}) COLLATE NOCASE
                AND scrobble_date >= ?
                ORDER BY scrobble_date ASC''',
            artist_names + [target_date_str]
        ).fetchall()
    finally:
        conn.close()

    best: dict[str, tuple[int, int]] = {}
    for row in before_rows:
        name = row['artist_name']
        dist = abs(int(row['scrobble_date']) - target_ts)
        if name not in best or dist < best[name][0]:
            best[name] = (dist, row['daily_total'])
    for row in after_rows:
        name = row['artist_name']
        dist = abs(int(row['scrobble_date']) - target_ts)
        if name not in best or dist < best[name][0]:
            best[name] = (dist, row['daily_total'])
    return {name: daily_total for name, (_, daily_total) in best.items()}


def get_snapshot(artist_name: str, date_str: str):
    conn = get_db()
    try:
        row = conn.execute(
            'SELECT artist_name, daily_total FROM artist_scrobbles WHERE artist_name = ? COLLATE NOCASE AND scrobble_date = ?',
            (artist_name, date_str)
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
            f'SELECT artist_name, daily_total FROM artist_scrobbles WHERE artist_name IN ({placeholders}) COLLATE NOCASE AND scrobble_date = ?',
            artist_names + [date_str]
        ).fetchall()
        return {row['artist_name']: row['daily_total'] for row in rows}
    finally:
        conn.close()


def get_latest_snapshots_bulk(artist_names: list[str]) -> dict[str, int]:
    if not artist_names:
        return {}
    conn = get_db()
    try:
        placeholders = ','.join('?' for _ in artist_names)
        rows = conn.execute(
            f'''SELECT artist_name, daily_total
                FROM artist_scrobbles
                WHERE artist_name IN ({placeholders}) COLLATE NOCASE
                ORDER BY scrobble_date DESC''',
            artist_names
        ).fetchall()
        latest = {}
        for row in rows:
            name = row['artist_name']
            if name not in latest:
                latest[name] = row['daily_total']
        return latest
    finally:
        conn.close()


def upsert_snapshot(artist_name: str, daily_total: int, scrobble_date: str):
    conn = get_db()
    try:
        existing = conn.execute(
            'SELECT artist_name, daily_total, scrobble_date FROM artist_scrobbles WHERE LOWER(artist_name) = LOWER(?)',
            (artist_name,)
        ).fetchall()
        if existing:
            conn.execute(
                'UPDATE artist_scrobbles SET artist_name = ? WHERE LOWER(artist_name) = LOWER(?)',
                (artist_name, artist_name)
            )
        conn.execute(
            'INSERT OR REPLACE INTO artist_scrobbles (artist_name, daily_total, scrobble_date) VALUES (?, ?, ?)',
            (artist_name, daily_total, scrobble_date)
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



def get_artist_scrobble_history(artist_name: str, days: int = 7) -> list[tuple[str, int]]:
    conn = get_db()
    try:
        now_utc = datetime.datetime.now(datetime.timezone.utc)
        cutoff_date = now_utc.date() - datetime.timedelta(days=days)
        cutoff = cutoff_date.isoformat().replace('-', '')
        today_str = now_utc.date().isoformat().replace('-', '')
        rows = conn.execute(
            "SELECT scrobble_date, SUM(count) as total FROM scrobbles WHERE artist_name = ? COLLATE NOCASE AND scrobble_date >= ? AND scrobble_date < ? GROUP BY scrobble_date ORDER BY scrobble_date ASC",
            (artist_name, cutoff, today_str)
        ).fetchall()
        result = [(row['scrobble_date'], row['total']) for row in rows]
        dates_in_result = {d for d, _ in result}
        for i in range(days):
            d = (cutoff_date + datetime.timedelta(days=i)).isoformat().replace('-', '')
            if d not in dates_in_result:
                result.append((d, 0))
        result.sort(key=lambda x: x[0])
        return result
    finally:
        conn.close()


def get_price_changes(days: int | str = 1) -> list[dict]:
    conn = get_db()
    try:
        now_utc = datetime.datetime.now(datetime.timezone.utc)
        today = now_utc.date().isoformat().replace('-', '')
        past_date_str = (now_utc.date() - datetime.timedelta(days=days if isinstance(days, int) else 1)).isoformat().replace('-', '')

        today_rows = conn.execute(
            'SELECT artist_name, daily_total FROM artist_scrobbles WHERE scrobble_date = ?',
            (today,)
        ).fetchall()

        if days == "alltime":
            past_rows = conn.execute(
                '''SELECT artist_name, daily_total FROM artist_scrobbles
                   WHERE scrobble_date = (
                       SELECT MIN(scrobble_date) FROM artist_scrobbles ap2
                       WHERE ap2.artist_name = artist_scrobbles.artist_name COLLATE NOCASE
                   )'''
            ).fetchall()
        elif isinstance(days, int) and days > 1:
            cutoff = (now_utc.date() - datetime.timedelta(days=days)).isoformat().replace('-', '')
            past_rows = conn.execute(
                '''SELECT artist_name, MIN(scrobble_date) as scrobble_date, daily_total
                   FROM artist_scrobbles
                   WHERE scrobble_date >= ? AND scrobble_date < ?
                   GROUP BY artist_name COLLATE NOCASE''',
                (cutoff, today)
            ).fetchall()
        else:
            past_rows = conn.execute(
                'SELECT artist_name, daily_total FROM artist_scrobbles WHERE scrobble_date = ?',
                (past_date_str,)
            ).fetchall()

        past_by_name = {row['artist_name']: row['daily_total'] for row in past_rows}
        result = []

        def _resolve_change(artist_name: str, today_daily_total: int, past_daily_total: int | None) -> dict | None:
            if past_daily_total is None:
                past_snapshot = get_closest_snapshot(artist_name, past_date_str)
                if past_snapshot is None:
                    return None
                past_daily_total = past_snapshot

            from services.portfolio import BASE_SHARE_VALUE
            total_snapshots = conn.execute(
                'SELECT COUNT(*) as cnt FROM artist_scrobbles WHERE artist_name = ? COLLATE NOCASE',
                (artist_name,)
            ).fetchone()
            if total_snapshots and total_snapshots['cnt'] == 1:
                past_daily_total = BASE_SHARE_VALUE

            capped_today = _cap_daily_price(past_daily_total, today_daily_total)
            capped_today = max(capped_today, 1)
            change = capped_today - past_daily_total
            change_percent = (change / past_daily_total * 100) if past_daily_total > 0 else 0.0
            return {
                'artist_name': artist_name,
                'today_daily_total': capped_today,
                'past_daily_total': past_daily_total,
                'change': change,
                'change_percent': change_percent,
            }

        for row in today_rows:
            artist_name = row['artist_name']
            entry = _resolve_change(artist_name, row['daily_total'], past_by_name.get(artist_name))
            if entry:
                result.append(entry)

        all_artists = conn.execute('SELECT DISTINCT artist_name FROM artist_scrobbles').fetchall()
        for artist_row in all_artists:
            artist_name = artist_row['artist_name']
            if any(r['artist_name'] == artist_name for r in result):
                continue
            latest = conn.execute(
                '''SELECT daily_total, scrobble_date FROM artist_scrobbles
                   WHERE artist_name = ? COLLATE NOCASE
                   ORDER BY scrobble_date DESC LIMIT 1''',
                (artist_name,)
            ).fetchone()
            if not latest:
                continue
            latest_date = datetime.datetime.strptime(latest['scrobble_date'], '%Y%m%d').date()
            if days == 'alltime':
                pass
            elif isinstance(days, int):
                cutoff = now_utc.date() - datetime.timedelta(days=days)
                if latest_date < cutoff:
                    continue
            entry = _resolve_change(artist_name, latest['daily_total'], None)
            if entry:
                result.append(entry)

        return result
    finally:
        conn.close()


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
                '''SELECT s.artist_name, SUM(s.count) as count
                   FROM scrobbles s
                   JOIN user_guilds ug ON s.discord_id = ug.discord_id
                   WHERE ug.guild_id = ?
                   GROUP BY s.artist_name COLLATE NOCASE
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


def get_all_artists() -> list[str]:
    conn = get_db()
    try:
        rows = conn.execute(
            'SELECT DISTINCT artist_name FROM artist_scrobbles ORDER BY artist_name COLLATE NOCASE'
        ).fetchall()
        return [row['artist_name'] for row in rows]
    finally:
        conn.close()


def migrate_fix_zero_purchase_prices(base_value: int = 10):
    conn = get_db()
    try:
        conn.execute(
            '''UPDATE scrobbles SET purchase_price = ? WHERE purchase_price IS NULL OR purchase_price <= 0''',
            (base_value,)
        )
        conn.commit()
    finally:
        conn.close()
