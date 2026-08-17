import os
import sys
import sqlite3
import tempfile
import pytest

os.environ.setdefault('LASTFM_API_KEY', 'test')
os.environ.setdefault('LASTFM_API_SECRET', 'test')
os.environ.setdefault('DISCORD_TOKEN', 'test')

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.database import (
    get_db, init_db, get_user, insert_user, get_scrobbles,
    insert_scrobble, update_user_money_and_claim,
    get_closest_snapshot, get_snapshot, upsert_snapshot, update_last_preview
)
from services.portfolio import calculate_portfolio_value, get_portfolio_breakdown


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
    insert_user(123456789, "alice", "alice_lfm", 100.0, 1234567890)
    user = get_user(123456789)
    assert user is not None
    assert user["lastfm_username"] == "alice_lfm"
    assert user["money"] == 100.0
    assert user["last_claim"] == 1234567890


def test_insert_user_relink_preserves_money(tmp_db):
    insert_user(123456789, "alice", "alice_lfm", 100.0, 1234567890)
    insert_user(123456789, "alice", "new_lfm")
    user = get_user(123456789)
    assert user["lastfm_username"] == "new_lfm"
    assert user["money"] == 100.0
    assert user["last_claim"] == 1234567890


def test_update_last_preview(tmp_db):
    insert_user(123456789, "alice", "alice_lfm")
    update_last_preview(123456789, 9999999999)
    user = get_user(123456789)
    assert user["last_preview"] == 9999999999


def test_get_user_missing(tmp_db):
    assert get_user(999999999) is None


def test_insert_and_get_scrobbles(tmp_db):
    insert_user(123456789, "alice", "alice_lfm")
    insert_scrobble(123456789, "Taylor Swift", "Anti-Hero", "Midnights", 15250000, "20260722")
    insert_scrobble(123456789, "Drake", "God's Plan", "Scorpion", 11975000, "20260723")
    rows = get_scrobbles(123456789)
    assert len(rows) == 2
    assert rows[0]["artist_name"] == "Taylor Swift"
    assert rows[0]["purchase_price"] == 15250000
    assert rows[1]["artist_name"] == "Drake"
    assert rows[1]["purchase_price"] == 11975000


def test_insert_scrobble_duplicate_is_ignored(tmp_db):
    insert_user(123456789, "alice", "alice_lfm")
    insert_scrobble(123456789, "Taylor Swift", "Anti-Hero", "Midnights", 15250000, "20260722")
    insert_scrobble(123456789, "Taylor Swift", "Anti-Hero", "Midnights", 15250000, "20260722")
    rows = get_scrobbles(123456789)
    assert len(rows) == 1


def test_update_user_money_and_claim(tmp_db):
    insert_user(123456789, "alice", "alice_lfm")
    update_user_money_and_claim(123456789, 250.0, 9999999999)
    user = get_user(123456789)
    assert user["money"] == 250.0
    assert user["last_claim"] == 9999999999


def test_snapshot_crud(tmp_db):
    upsert_snapshot("Taylor Swift", 15000000, "20260722")
    row = get_snapshot("Taylor Swift", "20260722")
    assert row is not None
    assert row["listeners"] == 15000000

    upsert_snapshot("Taylor Swift", 15100000, "20260722")
    row = get_snapshot("Taylor Swift", "20260722")
    assert row["listeners"] == 15100000


def test_get_closest_snapshot(tmp_db):
    upsert_snapshot("Taylor Swift", 15000000, "20260720")
    upsert_snapshot("Taylor Swift", 15200000, "20260722")
    upsert_snapshot("Taylor Swift", 15100000, "20260721")

    listeners = get_closest_snapshot("Taylor Swift", "20260721")
    assert listeners == 15100000

    listeners = get_closest_snapshot("Taylor Swift", "20260721")
    assert listeners == 15100000


def test_get_closest_snapshot_missing(tmp_db):
    assert get_closest_snapshot("Unknown Artist", "20260722") is None


@pytest.mark.asyncio
async def test_calculate_portfolio_value_empty(tmp_db):
    insert_user(123456789, "alice", "alice_lfm")
    value, gain = await calculate_portfolio_value(123456789, "20260722")
    assert value == 0.0
    assert gain == 0.0


@pytest.mark.asyncio
async def test_calculate_portfolio_value_with_snapshot(tmp_db):
    insert_user(123456789, "alice", "alice_lfm")
    insert_scrobble(123456789, "Taylor Swift", "Anti-Hero", "Midnights", 15000000, "20260722")
    upsert_snapshot("Taylor Swift", 15200000, "20260722")

    value, gain = await calculate_portfolio_value(123456789, "20260722")
    assert value == pytest.approx(1.0133, abs=1e-3)
    assert gain == pytest.approx(1.3333, abs=1e-3)


@pytest.mark.asyncio
async def test_get_portfolio_breakdown(tmp_db):
    insert_user(123456789, "alice", "alice_lfm")
    insert_scrobble(123456789, "Taylor Swift", "Anti-Hero", "Midnights", 15000000, "20260722")
    insert_scrobble(123456789, "Drake", "God's Plan", "Scorpion", 11975000, "20260723")
    upsert_snapshot("Taylor Swift", 15200000, "20260722")
    upsert_snapshot("Drake", 12000000, "20260722")

    breakdown = await get_portfolio_breakdown(123456789, "20260722", sort_by="value")
    assert len(breakdown) == 2
    assert breakdown[0]['artist_name'] == "Taylor Swift"
    assert breakdown[0]['shares'] == 1
    assert breakdown[0]['current_value'] == pytest.approx(1.0133, abs=1e-3)
    assert breakdown[1]['artist_name'] == "Drake"
    assert breakdown[1]['current_value'] == pytest.approx(1.0021, abs=1e-3)

    breakdown_by_price = await get_portfolio_breakdown(123456789, "20260722", sort_by="price")
    assert breakdown_by_price[0]['artist_name'] == "Taylor Swift"

    breakdown_by_quantity = await get_portfolio_breakdown(123456789, "20260722", sort_by="quantity")
    assert breakdown_by_quantity[0]['shares'] == 1
