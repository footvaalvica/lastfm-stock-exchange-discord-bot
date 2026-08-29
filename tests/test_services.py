import os
import sys
import datetime
import pytest

os.environ.setdefault('LASTFM_API_KEY', 'test')
os.environ.setdefault('LASTFM_API_SECRET', 'test')
os.environ.setdefault('DISCORD_TOKEN', 'test')

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.database import (
    get_db, init_db, get_user, insert_user, get_scrobbles,
    insert_scrobble, update_user_money_and_claim,
    get_closest_snapshot, get_snapshot, upsert_snapshot, update_last_preview,
    update_user_money, get_price_changes, get_most_held_artists, get_transactions,
    get_daily_scrobble_counts, _cap_daily_price,
    add_user_to_guild, get_closest_snapshot_bulk,
    get_snapshots_bulk, get_latest_snapshots_bulk,
    get_total_scrobbles_for_artist, get_artist_scrobble_history,
    set_guild_config, get_all_guild_configs, migrate_fix_zero_purchase_prices
)
from services.portfolio import calculate_portfolio_value, get_portfolio_breakdown, get_artist_info, get_artist_price_history, get_market_overview, get_claim_multiplier, get_balance_stats, BASE_SHARE_VALUE, get_stock_rankings
from cogs.commands import format_daily_total

GUILD_ID = 1


@pytest.fixture
def tmp_db(tmp_path):
    db_path = str(tmp_path / "test.db")
    import services.database as db_module
    original_db_path = db_module.DB_PATH
    db_module.DB_PATH = db_path
    init_db()
    yield db_path
    db_module.DB_PATH = original_db_path


def test_insert_and_get_user(tmp_db):
    insert_user(123456789, "alice", "alice_lfm", 100.0, 1234567890, guild_id=GUILD_ID)
    user = get_user(123456789)
    assert user is not None
    assert user["lastfm_username"] == "alice_lfm"
    assert user["money"] == 100.0
    assert user["last_claim"] == 1234567890


def test_insert_user_relink_preserves_money(tmp_db):
    insert_user(123456789, "alice", "alice_lfm", 100.0, 1234567890, guild_id=GUILD_ID)
    insert_user(123456789, "alice", "new_lfm", guild_id=GUILD_ID)
    user = get_user(123456789)
    assert user["lastfm_username"] == "new_lfm"
    assert user["money"] == 100.0
    assert user["last_claim"] == 1234567890


def test_update_last_preview(tmp_db):
    insert_user(123456789, "alice", "alice_lfm", guild_id=GUILD_ID)
    update_last_preview(123456789, 9999999999)
    user = get_user(123456789)
    assert user["last_preview"] == 9999999999


def test_get_user_missing(tmp_db):
    assert get_user(999999999) is None


def test_insert_and_get_scrobbles(tmp_db):
    insert_user(123456789, "alice", "alice_lfm", guild_id=GUILD_ID)
    insert_scrobble(123456789, "Taylor Swift", 15250000, "20260722")
    insert_scrobble(123456789, "Drake", 11975000, "20260723")
    rows = get_scrobbles(123456789)
    assert len(rows) == 2
    artists = {row["artist_name"]: row["purchase_price"] for row in rows}
    assert artists["Taylor Swift"] == 15250000
    assert artists["Drake"] == 11975000


def test_insert_scrobble_duplicate_increments_count(tmp_db):
    insert_user(123456789, "alice", "alice_lfm", guild_id=GUILD_ID)
    insert_scrobble(123456789, "Taylor Swift", 15250000, "20260722")
    insert_scrobble(123456789, "Taylor Swift", 15250000, "20260722")
    rows = get_scrobbles(123456789)
    assert len(rows) == 1
    assert rows[0]["count"] == 2


def test_update_user_money_and_claim(tmp_db):
    insert_user(123456789, "alice", "alice_lfm", 100.0, 1234567890, guild_id=GUILD_ID)
    update_user_money_and_claim(123456789, 250.0, 9999999999)
    user = get_user(123456789)
    assert user["money"] == 250.0
    assert user["last_claim"] == 9999999999


def test_update_user_money(tmp_db):
    insert_user(123456789, "alice", "alice_lfm", 100.0, 1234567890, guild_id=GUILD_ID)
    update_user_money(123456789, 500.0)
    user = get_user(123456789)
    assert user["money"] == 500.0
    assert user["last_claim"] == 1234567890


def test_snapshot_crud(tmp_db):
    upsert_snapshot("Taylor Swift", 15000000, "20260722")
    row = get_snapshot("Taylor Swift", "20260722")
    assert row is not None
    assert row["daily_total"] == 15000000

    upsert_snapshot("Taylor Swift", 15100000, "20260722")
    row = get_snapshot("Taylor Swift", "20260722")
    assert row["daily_total"] == 15100000


def test_get_closest_snapshot(tmp_db):
    upsert_snapshot("Taylor Swift", 15000000, "20260720")
    upsert_snapshot("Taylor Swift", 15200000, "20260722")
    upsert_snapshot("Taylor Swift", 15100000, "20260721")

    daily_total = get_closest_snapshot("Taylor Swift", "20260721")
    assert daily_total == 15100000

    daily_total = get_closest_snapshot("Taylor Swift", "20260721")
    assert daily_total == 15100000


def test_get_closest_snapshot_missing(tmp_db):
    assert get_closest_snapshot("Unknown Artist", "20260722") is None


@pytest.mark.asyncio
async def test_calculate_portfolio_value_empty(tmp_db):
    insert_user(123456789, "alice", "alice_lfm", guild_id=GUILD_ID)
    value, gain = calculate_portfolio_value(123456789, "20260722")
    assert value == 0.0
    assert gain == 0.0


@pytest.mark.asyncio
async def test_calculate_portfolio_value_with_snapshot(tmp_db):
    insert_user(123456789, "alice", "alice_lfm", guild_id=GUILD_ID)
    insert_scrobble(123456789, "Taylor Swift", 15000000, "20260722")
    upsert_snapshot("Taylor Swift", 15200000, "20260722")

    value, gain = calculate_portfolio_value(123456789, "20260722")
    assert value == pytest.approx(10.1333, abs=1e-3)
    assert gain == pytest.approx(1.3333, abs=1e-3)


@pytest.mark.asyncio
async def test_get_portfolio_breakdown(tmp_db):
    insert_user(123456789, "alice", "alice_lfm", guild_id=GUILD_ID)
    insert_scrobble(123456789, "Taylor Swift", 15000000, "20260722")
    insert_scrobble(123456789, "Drake", 11975000, "20260723")
    upsert_snapshot("Taylor Swift", 15200000, "20260722")
    upsert_snapshot("Drake", 12000000, "20260722")

    breakdown = get_portfolio_breakdown(123456789, "20260722")
    assert len(breakdown) == 2
    assert breakdown[0]['artist_name'] == "Taylor Swift"
    assert breakdown[0]['shares'] == 1
    assert breakdown[0]['current_value'] == pytest.approx(10.1333, abs=1e-3)
    assert breakdown[1]['artist_name'] == "Drake"
    assert breakdown[1]['current_value'] == pytest.approx(10.0209, abs=1e-3)


@pytest.mark.asyncio
async def test_get_artist_price_history(tmp_db):
    today = datetime.datetime.now(datetime.timezone.utc).date()
    dates = [(today - datetime.timedelta(days=i)).isoformat().replace('-', '') for i in range(3)]
    for i, date in enumerate(dates):
        upsert_snapshot("Taylor Swift", 15000000 + i * 100000, date)

    history = get_artist_price_history("Taylor Swift", days=3)
    assert len(history) == 3
    assert [h['daily_total'] for h in history] == [15200000, 15100000, 15000000]


@pytest.mark.asyncio
async def test_get_artist_price_history_deduplicates_unchanged(tmp_db):
    today = datetime.datetime.now(datetime.timezone.utc).date()
    dates = [(today - datetime.timedelta(days=i)).isoformat().replace('-', '') for i in range(4)]
    upsert_snapshot("Taylor Swift", 15000000, dates[0])
    upsert_snapshot("Taylor Swift", 15000000, dates[1])
    upsert_snapshot("Taylor Swift", 15100000, dates[2])
    upsert_snapshot("Taylor Swift", 15100000, dates[3])

    history = get_artist_price_history("Taylor Swift", days=4)
    assert len(history) == 2
    assert [h['daily_total'] for h in history] == [15100000, 15000000]


def test_snapshot_case_insensitive_hit(tmp_db):
    upsert_snapshot("Coldplay", 5000000, "20260722")
    row = get_snapshot("coldplay", "20260722")
    assert row is not None
    assert row["daily_total"] == 5000000


def test_snapshot_returns_artist_name(tmp_db):
    upsert_snapshot("Coldplay", 5000000, "20260722")
    row = get_snapshot("coldplay", "20260722")
    assert row["artist_name"] == "Coldplay"


def test_format_daily_total():
    assert format_daily_total(999) == "999"
    assert format_daily_total(1000) == "1.0k"
    assert format_daily_total(25300) == "25.3k"
    assert format_daily_total(1200000) == "1.2M"
    assert format_daily_total(4500000) == "4.5M"


def test_get_closest_snapshot_uses_historical_price(tmp_db):
    upsert_snapshot("Taylor Swift", 15000000, "20260720")
    upsert_snapshot("Taylor Swift", 15200000, "20260722")
    upsert_snapshot("Taylor Swift", 15100000, "20260721")

    daily_total = get_closest_snapshot("Taylor Swift", "20260720")
    assert daily_total == 15000000

    daily_total = get_closest_snapshot("Taylor Swift", "20260723")
    assert daily_total == 15200000


@pytest.mark.asyncio
async def test_calculate_portfolio_value_preserves_individual_gains(tmp_db):
    insert_user(123456789, "alice", "alice_lfm", guild_id=GUILD_ID)
    insert_scrobble(123456789, "Taylor Swift", 15000000, "20260720")
    insert_scrobble(123456789, "Taylor Swift", 15000000, "20260722")
    upsert_snapshot("Taylor Swift", 15000000, "20260720")
    upsert_snapshot("Taylor Swift", 15200000, "20260722")

    value, gain = calculate_portfolio_value(123456789, "20260722")
    assert value == pytest.approx(20.2667, abs=1e-3)


def test_get_price_changes(tmp_db):
    today = datetime.datetime.now(datetime.timezone.utc).date().isoformat().replace('-', '')
    yesterday = (datetime.datetime.now(datetime.timezone.utc).date() - datetime.timedelta(days=1)).isoformat().replace('-', '')
    upsert_snapshot("Taylor Swift", 15000000, yesterday)
    upsert_snapshot("Taylor Swift", 15200000, today)
    upsert_snapshot("Drake", 12000000, yesterday)
    upsert_snapshot("Drake", 11900000, today)

    changes = get_price_changes(days=1)
    assert len(changes) == 2
    taylor = next(c for c in changes if c['artist_name'] == 'Taylor Swift')
    assert taylor['change'] == pytest.approx(200000, abs=1e-3)
    assert taylor['change_percent'] == pytest.approx(1.3333, abs=1e-3)
    drake = next(c for c in changes if c['artist_name'] == 'Drake')
    assert drake['change'] == pytest.approx(-100000, abs=1e-3)
    assert drake['change_percent'] == pytest.approx(-0.8333, abs=1e-3)


def test_get_price_changes_week_uses_earliest_in_range(tmp_db):
    today = datetime.datetime.now(datetime.timezone.utc).date().isoformat().replace('-', '')
    five_days_ago = (datetime.datetime.now(datetime.timezone.utc).date() - datetime.timedelta(days=5)).isoformat().replace('-', '')
    upsert_snapshot("Taylor Swift", 10000000, five_days_ago)
    upsert_snapshot("Taylor Swift", 15000000, today)

    changes = get_price_changes(days=7)
    assert len(changes) == 1
    taylor = changes[0]
    assert taylor['artist_name'] == 'Taylor Swift'
    assert taylor['past_daily_total'] == 10000000
    assert taylor['change_percent'] == pytest.approx(50.0, abs=1e-3)


def test_get_price_changes_month_uses_earliest_in_range(tmp_db):
    today = datetime.datetime.now(datetime.timezone.utc).date().isoformat().replace('-', '')
    twenty_days_ago = (datetime.datetime.now(datetime.timezone.utc).date() - datetime.timedelta(days=20)).isoformat().replace('-', '')
    upsert_snapshot("Drake", 5000000, twenty_days_ago)
    upsert_snapshot("Drake", 8000000, today)

    changes = get_price_changes(days=30)
    assert len(changes) == 1
    drake = changes[0]
    assert drake['artist_name'] == 'Drake'
    assert drake['past_daily_total'] == 5000000
    assert drake['change_percent'] == pytest.approx(50.0, abs=1e-3)


def test_get_most_held_artists(tmp_db):
    insert_user(123456789, "alice", "alice_lfm", guild_id=GUILD_ID)
    insert_user(999999999, "bob", "bob_lfm", guild_id=GUILD_ID)
    insert_scrobble(123456789, "Taylor Swift", 15000000, "20260722")
    insert_scrobble(123456789, "Taylor Swift", 15000000, "20260722")
    insert_scrobble(999999999, "Drake", 12000000, "20260722")

    most_held = get_most_held_artists(limit=2, guild_id=GUILD_ID)
    assert len(most_held) == 2
    assert most_held[0]['artist_name'] == 'Taylor Swift'
    assert most_held[0]['count'] == 2
    assert most_held[1]['artist_name'] == 'Drake'
    assert most_held[1]['count'] == 1


@pytest.mark.asyncio
async def test_get_market_overview(tmp_db):
    today = datetime.datetime.now(datetime.timezone.utc).date().isoformat().replace('-', '')
    yesterday = (datetime.datetime.now(datetime.timezone.utc).date() - datetime.timedelta(days=1)).isoformat().replace('-', '')
    upsert_snapshot("Taylor Swift", 15000000, yesterday)
    upsert_snapshot("Taylor Swift", 15200000, today)
    upsert_snapshot("Drake", 12000000, yesterday)
    upsert_snapshot("Drake", 11900000, today)
    insert_user(123456789, "alice", "alice_lfm", guild_id=GUILD_ID)
    insert_scrobble(123456789, "Taylor Swift", 15000000, "20260722")
    insert_scrobble(123456789, "Drake", 12000000, "20260722")

    overview = get_market_overview()
    assert len(overview['gainers']) == 1
    assert overview['gainers'][0]['artist_name'] == 'Taylor Swift'
    assert 'current_share_value' in overview['gainers'][0]
    assert 'change_value' in overview['gainers'][0]
    assert len(overview['losers']) == 1
    assert overview['losers'][0]['artist_name'] == 'Drake'
    assert 'current_share_value' in overview['losers'][0]
    assert 'change_value' in overview['losers'][0]
    assert len(overview['most_held']) == 2


def test_get_stock_rankings(tmp_db):
    today = datetime.datetime.now(datetime.timezone.utc).date().isoformat().replace('-', '')
    yesterday = (datetime.datetime.now(datetime.timezone.utc).date() - datetime.timedelta(days=1)).isoformat().replace('-', '')
    upsert_snapshot("Expensive Artist", 1000000, yesterday)
    upsert_snapshot("Expensive Artist", 1500000, today)
    upsert_snapshot("Cheap Artist", 100000, yesterday)
    upsert_snapshot("Cheap Artist", 50000, today)
    upsert_snapshot("Mid Artist", 500000, yesterday)
    upsert_snapshot("Mid Artist", 500000, today)

    rankings = get_stock_rankings(limit=10)
    assert len(rankings['most_valuable']) == 3
    assert len(rankings['least_valuable']) == 3
    assert rankings['most_valuable'][0]['artist_name'] == 'Expensive Artist'
    assert rankings['least_valuable'][0]['artist_name'] == 'Cheap Artist'
    assert rankings['most_valuable'][0]['current_share_value'] > rankings['least_valuable'][0]['current_share_value']


def test_get_stock_rankings_empty(tmp_db):
    rankings = get_stock_rankings(limit=10)
    assert rankings['most_valuable'] == []
    assert rankings['least_valuable'] == []


@pytest.mark.asyncio
async def test_get_artist_info_uses_yesterday_as_base(tmp_db):
    today = datetime.datetime.now(datetime.timezone.utc).date().isoformat().replace('-', '')
    yesterday = (datetime.datetime.now(datetime.timezone.utc).date() - datetime.timedelta(days=1)).isoformat().replace('-', '')
    upsert_snapshot("Phoebe Bridgers", 2251500, yesterday)
    upsert_snapshot("Phoebe Bridgers", 2253102, today)
    insert_user(123456789, "alice", "alice_lfm", guild_id=GUILD_ID)
    insert_scrobble(123456789, "Phoebe Bridgers", 2251500, yesterday)

    info = await get_artist_info("Phoebe Bridgers", today)
    assert info is not None
    assert info['current_price'] == 2253102
    assert info['base_price'] == 2251500
    assert info['gain_loss_percent'] == pytest.approx(0.07115, abs=1e-3)


@pytest.mark.asyncio
async def test_get_artist_info_fallback_to_current_when_no_history(tmp_db):
    today = datetime.datetime.now(datetime.timezone.utc).date().isoformat().replace('-', '')
    upsert_snapshot("Coldplay", 5000000, today)

    info = await get_artist_info("Coldplay", today)
    assert info is not None
    assert info['base_price'] == 5000000
    assert info['gain_loss_percent'] == 0.0


def test_get_transactions_all(tmp_db):
    insert_user(123456789, "alice", "alice_lfm", guild_id=GUILD_ID)
    insert_scrobble(123456789, "Taylor Swift", 15250000, "20260722")
    insert_scrobble(123456789, "Drake", 11975000, "20260723")
    insert_scrobble(123456789, "Taylor Swift", 15100000, "20260724")

    txs = get_transactions(123456789)
    assert len(txs) == 3
    assert txs[0]['artist_name'] == "Taylor Swift"
    assert txs[1]['artist_name'] == "Drake"
    assert txs[2]['artist_name'] == "Taylor Swift"


def test_get_transactions_filtered_by_artist(tmp_db):
    insert_user(123456789, "alice", "alice_lfm", guild_id=GUILD_ID)
    insert_scrobble(123456789, "Taylor Swift", 15250000, "20260722")
    insert_scrobble(123456789, "Drake", 11975000, "20260723")

    txs = get_transactions(123456789, artist_name="Taylor Swift")
    assert len(txs) == 1
    assert txs[0]['artist_name'] == "Taylor Swift"


def test_get_daily_scrobble_counts(tmp_db):
    insert_user(123456789, "alice", "alice_lfm", guild_id=GUILD_ID)
    today = datetime.datetime.now(datetime.timezone.utc).date()
    dates = [(today - datetime.timedelta(days=i)).isoformat().replace('-', '') for i in range(1, 4)]
    insert_scrobble(123456789, "Taylor Swift", 15000000, dates[0], count=10)
    insert_scrobble(123456789, "Drake", 12000000, dates[0], count=5)
    insert_scrobble(123456789, "Taylor Swift", 15100000, dates[1], count=3)

    counts = get_daily_scrobble_counts(123456789, days=7)
    assert counts.get(dates[0]) == 15
    assert counts.get(dates[1]) == 3


def test_get_claim_multiplier_no_scrobbles(tmp_db):
    insert_user(123456789, "alice", "alice_lfm", guild_id=GUILD_ID)
    assert get_claim_multiplier(123456789) == 1.0


def test_get_claim_multiplier_below_thresholds(tmp_db):
    insert_user(123456789, "alice", "alice_lfm", guild_id=GUILD_ID)
    insert_scrobble(123456789, "Taylor Swift", 15000000, "20260720", count=10)
    assert get_claim_multiplier(123456789) == 1.0


def test_get_claim_multiplier_1_1x_tier(tmp_db):
    insert_user(123456789, "alice", "alice_lfm", guild_id=GUILD_ID)
    for day in range(1, 8):
        date_str = (datetime.datetime.now(datetime.timezone.utc).date() - datetime.timedelta(days=day)).isoformat().replace('-', '')
        insert_scrobble(123456789, "Taylor Swift", 15000000, date_str, count=60)
    assert get_claim_multiplier(123456789) == pytest.approx(1.1)


def test_get_claim_multiplier_1_2x_tier(tmp_db):
    insert_user(123456789, "alice", "alice_lfm", guild_id=GUILD_ID)
    for day in range(1, 8):
        date_str = (datetime.datetime.now(datetime.timezone.utc).date() - datetime.timedelta(days=day)).isoformat().replace('-', '')
        insert_scrobble(123456789, "Taylor Swift", 15000000, date_str, count=120)
    assert get_claim_multiplier(123456789) == pytest.approx(1.2)


def test_get_claim_multiplier_caps_daily_count(tmp_db):
    insert_user(123456789, "alice", "alice_lfm", guild_id=GUILD_ID)
    for day in range(1, 8):
        date_str = (datetime.datetime.now(datetime.timezone.utc).date() - datetime.timedelta(days=day)).isoformat().replace('-', '')
        insert_scrobble(123456789, "Taylor Swift", 15000000, date_str, count=300)
    assert get_claim_multiplier(123456789) == pytest.approx(1.2)


def test_get_claim_multiplier_streak_broken_by_missing_day(tmp_db):
    insert_user(123456789, "alice", "alice_lfm", guild_id=GUILD_ID)
    for day in range(1, 8):
        date_str = (datetime.datetime.now(datetime.timezone.utc).date() - datetime.timedelta(days=day)).isoformat().replace('-', '')
        insert_scrobble(123456789, "Taylor Swift", 15000000, date_str, count=60)
    # Remove day 4 to break streak
    missing_date = (datetime.datetime.now(datetime.timezone.utc).date() - datetime.timedelta(days=4)).isoformat().replace('-', '')
    conn = get_db()
    try:
        conn.execute('DELETE FROM scrobbles WHERE discord_id = ? AND scrobble_date = ?', (123456789, missing_date))
        conn.commit()
    finally:
        conn.close()
    assert get_claim_multiplier(123456789) == 1.0


def test_get_claim_multiplier_exactly_50_per_day(tmp_db):
    insert_user(123456789, "alice", "alice_lfm", guild_id=GUILD_ID)
    for day in range(1, 8):
        date_str = (datetime.datetime.now(datetime.timezone.utc).date() - datetime.timedelta(days=day)).isoformat().replace('-', '')
        insert_scrobble(123456789, "Taylor Swift", 15000000, date_str, count=50)
    assert get_claim_multiplier(123456789) == pytest.approx(1.1)


def test_get_claim_multiplier_exactly_100_per_day(tmp_db):
    insert_user(123456789, "alice", "alice_lfm", guild_id=GUILD_ID)
    for day in range(1, 8):
        date_str = (datetime.datetime.now(datetime.timezone.utc).date() - datetime.timedelta(days=day)).isoformat().replace('-', '')
        insert_scrobble(123456789, "Taylor Swift", 15000000, date_str, count=100)
    assert get_claim_multiplier(123456789) == pytest.approx(1.2)


def test_get_claim_multiplier_one_day_below_50_breaks_streak(tmp_db):
    insert_user(123456789, "alice", "alice_lfm", guild_id=GUILD_ID)
    for day in range(1, 8):
        date_str = (datetime.datetime.now(datetime.timezone.utc).date() - datetime.timedelta(days=day)).isoformat().replace('-', '')
        count = 60 if day != 3 else 49
        insert_scrobble(123456789, "Taylor Swift", 15000000, date_str, count=count)
    assert get_claim_multiplier(123456789) == 1.0


def test_get_daily_scrobble_counts_excludes_today(tmp_db):
    insert_user(123456789, "alice", "alice_lfm", guild_id=GUILD_ID)
    today = datetime.datetime.now(datetime.timezone.utc).date()
    today_str = today.isoformat().replace('-', '')
    yesterday_str = (today - datetime.timedelta(days=1)).isoformat().replace('-', '')
    insert_scrobble(123456789, "Taylor Swift", 15000000, today_str, count=100)
    insert_scrobble(123456789, "Drake", 12000000, yesterday_str, count=50)

    counts = get_daily_scrobble_counts(123456789, days=7)
    assert today_str not in counts
    assert counts.get(yesterday_str) == 50


def test_get_claim_multiplier_ignores_today_scrobbles(tmp_db):
    insert_user(123456789, "alice", "alice_lfm", guild_id=GUILD_ID)
    today = datetime.datetime.now(datetime.timezone.utc).date()
    today_str = today.isoformat().replace('-', '')
    insert_scrobble(123456789, "Taylor Swift", 15000000, today_str, count=200)
    assert get_claim_multiplier(123456789) == 1.0


def test_get_claim_multiplier_first_time_user_no_bonus(tmp_db):
    insert_user(123456789, "alice", "alice_lfm", guild_id=GUILD_ID)
    today = datetime.datetime.now(datetime.timezone.utc).date()
    today_str = today.isoformat().replace('-', '')
    for _ in range(5):
        insert_scrobble(123456789, "Taylor Swift", 15000000, today_str, count=10)
    assert get_claim_multiplier(123456789) == 1.0


def test_get_claim_multiplier_two_days_heavy_no_bonus(tmp_db):
    insert_user(123456789, "alice", "alice_lfm", guild_id=GUILD_ID)
    today = datetime.datetime.now(datetime.timezone.utc).date()
    yesterday = today - datetime.timedelta(days=1)
    yesterday_str = yesterday.isoformat().replace('-', '')
    insert_scrobble(123456789, "Taylor Swift", 15000000, yesterday_str, count=200)
    insert_scrobble(123456789, "Taylor Swift", 15000000, yesterday_str, count=200)
    assert get_claim_multiplier(123456789) == 1.0


def test_cap_daily_price_positive_gain():
    assert _cap_daily_price(1000, 5000) == 1500


def test_cap_daily_price_negative_gain():
    assert _cap_daily_price(1000, 300) == 500


def test_cap_daily_price_within_cap():
    assert _cap_daily_price(1000, 1200) == 1200


def test_cap_daily_price_zero_base():
    assert _cap_daily_price(0, 100) == 100


def test_cap_daily_price_no_change():
    assert _cap_daily_price(1000, 1000) == 1000


def test_cap_daily_price_large_loss():
    assert _cap_daily_price(10000, 1000) == 5000


def test_cap_daily_price_volatility_amplifies_small_gain():
    assert _cap_daily_price(1000, 1020) == 1020


def test_cap_daily_price_volatility_amplifies_small_loss():
    assert _cap_daily_price(1000, 980) == 980


def test_get_price_changes_respects_daily_cap(tmp_db):
    today = datetime.datetime.now(datetime.timezone.utc).date().isoformat().replace('-', '')
    yesterday = (datetime.datetime.now(datetime.timezone.utc).date() - datetime.timedelta(days=1)).isoformat().replace('-', '')
    upsert_snapshot("Taylor Swift", 1000, yesterday)
    upsert_snapshot("Taylor Swift", 5000, today)

    changes = get_price_changes(days=1)
    taylor = next(c for c in changes if c['artist_name'] == 'Taylor Swift')
    assert taylor['change_percent'] == pytest.approx(50.0, abs=1e-3)
    assert taylor['today_daily_total'] == 1500


def test_get_price_changes_respects_daily_cap_loss(tmp_db):
    today = datetime.datetime.now(datetime.timezone.utc).date().isoformat().replace('-', '')
    yesterday = (datetime.datetime.now(datetime.timezone.utc).date() - datetime.timedelta(days=1)).isoformat().replace('-', '')
    upsert_snapshot("Drake", 1000, yesterday)
    upsert_snapshot("Drake", 300, today)

    changes = get_price_changes(days=1)
    drake = next(c for c in changes if c['artist_name'] == 'Drake')
    assert drake['change_percent'] == pytest.approx(-50.0, abs=1e-3)
    assert drake['today_daily_total'] == 500


@pytest.mark.asyncio
async def test_calculate_portfolio_value_respects_daily_cap(tmp_db):
    insert_user(123456789, "alice", "alice_lfm", guild_id=GUILD_ID)
    insert_scrobble(123456789, "Taylor Swift", 1000, "20260722")
    today = datetime.datetime.now(datetime.timezone.utc).date().isoformat().replace('-', '')
    yesterday = (datetime.datetime.now(datetime.timezone.utc).date() - datetime.timedelta(days=1)).isoformat().replace('-', '')
    upsert_snapshot("Taylor Swift", 1000, yesterday)
    upsert_snapshot("Taylor Swift", 5000, today)

    value, gain = calculate_portfolio_value(123456789, today)
    expected_value = BASE_SHARE_VALUE * (1500 / 1000)
    assert value == pytest.approx(expected_value, abs=1e-3)


@pytest.mark.asyncio
async def test_get_balance_stats_respects_daily_cap(tmp_db):
    insert_user(123456789, "alice", "alice_lfm", guild_id=GUILD_ID)
    insert_scrobble(123456789, "Taylor Swift", 1000, "20260722")
    today = datetime.datetime.now(datetime.timezone.utc).date().isoformat().replace('-', '')
    yesterday = (datetime.datetime.now(datetime.timezone.utc).date() - datetime.timedelta(days=1)).isoformat().replace('-', '')
    upsert_snapshot("Taylor Swift", 1000, yesterday)
    upsert_snapshot("Taylor Swift", 5000, today)

    stats = get_balance_stats(123456789, today, yesterday)
    assert stats['today_change'] == pytest.approx(50.0, abs=1e-3)


@pytest.mark.asyncio
async def test_get_artist_info_respects_daily_cap(tmp_db):
    today = datetime.datetime.now(datetime.timezone.utc).date().isoformat().replace('-', '')
    yesterday = (datetime.datetime.now(datetime.timezone.utc).date() - datetime.timedelta(days=1)).isoformat().replace('-', '')
    upsert_snapshot("Taylor Swift", 1000, yesterday)
    upsert_snapshot("Taylor Swift", 5000, today)

    info = await get_artist_info("Taylor Swift", today)
    assert info is not None
    assert info['current_price'] == 1500
    assert info['gain_loss_percent'] == pytest.approx(50.0, abs=1e-3)


def test_cap_daily_price_boosts_are_capped(tmp_db):
    base = 500000
    boosted = 800000
    capped = _cap_daily_price(base, boosted)
    assert capped == 750000


def test_cap_daily_price_normalization_within_cap(tmp_db):
    yesterday = 750000
    today = 500000
    capped = _cap_daily_price(yesterday, today)
    assert capped == 500000


def test_cap_daily_price_normalization_multiple_days(tmp_db):
    today = datetime.datetime.now(datetime.timezone.utc).date().isoformat().replace('-', '')
    base_day = (datetime.datetime.now(datetime.timezone.utc).date() - datetime.timedelta(days=5)).isoformat().replace('-', '')
    spike_day = (datetime.datetime.now(datetime.timezone.utc).date() - datetime.timedelta(days=4)).isoformat().replace('-', '')
    drop_day1 = (datetime.datetime.now(datetime.timezone.utc).date() - datetime.timedelta(days=3)).isoformat().replace('-', '')
    drop_day2 = (datetime.datetime.now(datetime.timezone.utc).date() - datetime.timedelta(days=2)).isoformat().replace('-', '')
    drop_day3 = (datetime.datetime.now(datetime.timezone.utc).date() - datetime.timedelta(days=1)).isoformat().replace('-', '')

    upsert_snapshot("The Hype Band", 500000, base_day)
    upsert_snapshot("The Hype Band", 750000, spike_day)
    upsert_snapshot("The Hype Band", 650000, drop_day1)
    upsert_snapshot("The Hype Band", 550000, drop_day2)
    upsert_snapshot("The Hype Band", 500000, drop_day3)
    upsert_snapshot("The Hype Band", 500000, today)

    changes = get_price_changes(days=1)
    band = next(c for c in changes if c['artist_name'] == "The Hype Band")
    assert band['change_percent'] == pytest.approx(0.0, abs=1e-3)
    assert band['today_daily_total'] == 500000


@pytest.mark.asyncio
async def test_get_artist_info_boost_then_normalize(tmp_db):
    today = datetime.datetime.now(datetime.timezone.utc).date().isoformat().replace('-', '')
    yesterday = (datetime.datetime.now(datetime.timezone.utc).date() - datetime.timedelta(days=1)).isoformat().replace('-', '')
    upsert_snapshot("The Hype Band", 500000, yesterday)
    upsert_snapshot("The Hype Band", 500000, today)

    info = await get_artist_info("The Hype Band", today)
    assert info['current_price'] == 500000
    assert info['gain_loss_percent'] == pytest.approx(0.0, abs=1e-3)


def test_get_price_changes_large_boost_normalizes_over_days(tmp_db):
    today = datetime.datetime.now(datetime.timezone.utc).date().isoformat().replace('-', '')
    yesterday = (datetime.datetime.now(datetime.timezone.utc).date() - datetime.timedelta(days=1)).isoformat().replace('-', '')
    upsert_snapshot("The Hype Band", 500000, yesterday)
    upsert_snapshot("The Hype Band", 1000000, today)

    changes = get_price_changes(days=1)
    band = next(c for c in changes if c['artist_name'] == "The Hype Band")
    assert band['change_percent'] == pytest.approx(50.0, abs=1e-3)
    assert band['today_daily_total'] == 750000


@pytest.mark.asyncio
async def test_calculate_portfolio_value_after_boost_and_drop(tmp_db):
    insert_user(123456789, "alice", "alice_lfm", guild_id=GUILD_ID)
    insert_scrobble(123456789, "The Hype Band", 500000, "20260720", count=10)
    today = datetime.datetime.now(datetime.timezone.utc).date().isoformat().replace('-', '')
    yesterday = (datetime.datetime.now(datetime.timezone.utc).date() - datetime.timedelta(days=1)).isoformat().replace('-', '')
    upsert_snapshot("The Hype Band", 500000, yesterday)
    upsert_snapshot("The Hype Band", 500000, today)

    value, gain = calculate_portfolio_value(123456789, today)
    assert value == pytest.approx(BASE_SHARE_VALUE * 10, abs=1e-3)
    assert gain == pytest.approx(0.0, abs=1e-3)


def test_add_user_to_guild(tmp_db):
    insert_user(123456789, "alice", "alice_lfm", guild_id=GUILD_ID)
    add_user_to_guild(123456789, 999)
    user = get_user(123456789)
    assert user is not None
    assert user["username"] == "alice"


def test_get_closest_snapshot_bulk(tmp_db):
    upsert_snapshot("Taylor Swift", 15000000, "20260720")
    upsert_snapshot("Taylor Swift", 15200000, "20260722")
    upsert_snapshot("Drake", 12000000, "20260720")
    upsert_snapshot("Drake", 11900000, "20260722")
    
    result = get_closest_snapshot_bulk(["Taylor Swift", "Drake"], "20260721")
    assert "Taylor Swift" in result
    assert "Drake" in result
    assert result["Taylor Swift"] == 15000000
    assert result["Drake"] == 12000000


def test_get_snapshots_bulk(tmp_db):
    upsert_snapshot("Taylor Swift", 15000000, "20260722")
    upsert_snapshot("Drake", 12000000, "20260722")
    
    result = get_snapshots_bulk(["Taylor Swift", "Drake"], "20260722")
    assert result["Taylor Swift"] == 15000000
    assert result["Drake"] == 12000000


def test_get_latest_snapshots_bulk(tmp_db):
    upsert_snapshot("Taylor Swift", 15000000, "20260720")
    upsert_snapshot("Taylor Swift", 15200000, "20260722")
    upsert_snapshot("Drake", 12000000, "20260720")
    upsert_snapshot("Drake", 11900000, "20260722")
    
    result = get_latest_snapshots_bulk(["Taylor Swift", "Drake"])
    assert result["Taylor Swift"] == 15200000
    assert result["Drake"] == 11900000


def test_get_total_scrobbles_for_artist(tmp_db):
    insert_user(123456789, "alice", "alice_lfm", guild_id=GUILD_ID)
    insert_scrobble(123456789, "Taylor Swift", 100, "20260720", count=5)
    insert_scrobble(123456789, "Taylor Swift", 100, "20260721", count=3)
    
    total = get_total_scrobbles_for_artist("Taylor Swift")
    assert total == 8


def test_get_artist_scrobble_history(tmp_db):
    today = datetime.datetime.now(datetime.timezone.utc).date()
    yesterday = today - datetime.timedelta(days=1)
    two_days_ago = today - datetime.timedelta(days=2)
    three_days_ago = today - datetime.timedelta(days=3)

    today_str = today.isoformat().replace('-', '')
    yesterday_str = yesterday.isoformat().replace('-', '')
    two_days_ago_str = two_days_ago.isoformat().replace('-', '')
    three_days_ago_str = three_days_ago.isoformat().replace('-', '')

    insert_user(123456789, "alice", "alice_lfm", guild_id=GUILD_ID)
    insert_scrobble(123456789, "Taylor Swift", 15000000, three_days_ago_str, count=5)
    insert_scrobble(123456789, "Taylor Swift", 15000000, two_days_ago_str, count=10)
    insert_scrobble(123456789, "Taylor Swift", 15000000, yesterday_str, count=15)

    history = get_artist_scrobble_history("Taylor Swift", days=3)
    assert len(history) == 4
    assert history[0] == (three_days_ago_str, 5)
    assert history[1] == (two_days_ago_str, 10)
    assert history[2] == (yesterday_str, 15)
    assert history[3] == (today_str, 0)


def test_set_and_get_guild_config(tmp_db):
    set_guild_config(12345, 67890, 10, "America/New_York")
    configs = get_all_guild_configs()
    assert len(configs) == 1
    assert configs[0]["guild_id"] == 12345
    assert configs[0]["market_channel_id"] == 67890
    assert configs[0]["market_hour_local"] == 10
    assert configs[0]["market_timezone"] == "America/New_York"


def test_historical_snapshot_zero_daily_total_returns_raw_zero(tmp_db):
    upsert_snapshot("Taylor Swift", 0, "20260720")
    result = get_closest_snapshot("Taylor Swift", "20260720")
    assert result == 0


@pytest.mark.asyncio
async def test_calculate_portfolio_value_floors_zero_purchase_price_to_base(tmp_db):
    insert_user(123456789, "alice", "alice_lfm", guild_id=GUILD_ID)
    insert_scrobble(123456789, "Taylor Swift", 0, "20260722")
    upsert_snapshot("Taylor Swift", 15200000, "20260722")

    value, gain = calculate_portfolio_value(123456789, "20260722")
    assert value == pytest.approx(BASE_SHARE_VALUE * (15200000 / BASE_SHARE_VALUE), abs=1e-3)
    assert gain == pytest.approx(((15200000 / BASE_SHARE_VALUE) - 1) * 100, abs=1e-3)


@pytest.mark.asyncio
async def test_get_balance_stats_floors_zero_purchase_price_to_base(tmp_db):
    insert_user(123456789, "alice", "alice_lfm", guild_id=GUILD_ID)
    insert_scrobble(123456789, "Taylor Swift", 0, "20260722")
    today = datetime.datetime.now(datetime.timezone.utc).date().isoformat().replace('-', '')
    yesterday = (datetime.datetime.now(datetime.timezone.utc).date() - datetime.timedelta(days=1)).isoformat().replace('-', '')
    upsert_snapshot("Taylor Swift", 15200000, today)
    upsert_snapshot("Taylor Swift", 15000000, yesterday)

    stats = get_balance_stats(123456789, today, yesterday)
    capped_today = _cap_daily_price(15000000, 15200000)
    expected_value = BASE_SHARE_VALUE * (capped_today / BASE_SHARE_VALUE)
    assert stats['total_value'] == pytest.approx(expected_value, abs=1e-3)


def test_migrate_fix_zero_purchase_prices(tmp_db):
    insert_user(123456789, "alice", "alice_lfm", guild_id=GUILD_ID)
    conn = get_db()
    try:
        conn.execute('INSERT INTO scrobbles (discord_id, artist_name, purchase_price, scrobble_date, count) VALUES (?, ?, ?, ?, ?)',
                     (123456789, "Taylor Swift", 0, "20260722", 1))
        conn.execute('INSERT INTO scrobbles (discord_id, artist_name, purchase_price, scrobble_date, count) VALUES (?, ?, ?, ?, ?)',
                     (123456789, "Drake", None, "20260723", 2))
        conn.commit()
    finally:
        conn.close()

    migrate_fix_zero_purchase_prices()

    rows = get_scrobbles(123456789)
    prices = {row['artist_name']: row['purchase_price'] for row in rows}
    assert prices["Taylor Swift"] == 10
    assert prices["Drake"] == 10


@pytest.mark.asyncio
async def test_process_user_claim_floors_historical_zero_snapshot_to_base(tmp_db):
    from unittest.mock import AsyncMock, MagicMock, patch

    insert_user(123456789, "alice", "alice_lfm", guild_id=GUILD_ID)
    yesterday_ts = int((datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=1)).timestamp())
    conn = get_db()
    try:
        conn.execute('UPDATE users SET last_claim = ? WHERE discord_id = ?', (yesterday_ts, 123456789))
        conn.commit()
    finally:
        conn.close()

    upsert_snapshot("Taylor Swift", 0, "20260720")

    mock_user = MagicMock()
    mock_scrobble = MagicMock()
    mock_scrobble.track.artist.name = "Taylor Swift"
    mock_scrobble.timestamp = yesterday_ts

    with patch(
        'services.portfolio.fetch_recent_tracks',
        new_callable=AsyncMock,
        return_value=[mock_scrobble]
    ):
        import services.portfolio as portfolio_module
        total_money, gain_loss = await portfolio_module.process_user_claim(mock_user, 123456789, GUILD_ID)

    rows = get_scrobbles(123456789)
    taylor = next(r for r in rows if r['artist_name'] == 'Taylor Swift')
    assert taylor['purchase_price'] == BASE_SHARE_VALUE
