import os
import sys
import datetime
import pytest
from unittest.mock import MagicMock, patch, AsyncMock

os.environ.setdefault('LASTFM_API_KEY', 'test')
os.environ.setdefault('LASTFM_API_SECRET', 'test')
os.environ.setdefault('DISCORD_TOKEN', 'test')

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cogs.commands import (
    StockCommands, check_lastfm_cooldown, format_listeners,
    get_user_in_guild, _lastfm_cooldowns, ConfirmUnlinkView,
    add_user_to_guild
)
from services.database import (
    get_db, init_db, get_user, insert_user, get_scrobbles,
    insert_scrobble, update_user_money_and_claim,
    get_closest_snapshot, get_snapshot, upsert_snapshot, update_last_preview,
    update_user_money, get_price_changes, get_most_held_artists, get_transactions,
    get_daily_scrobble_counts, _cap_daily_price, get_all_guild_configs
)
from services.portfolio import (
    calculate_portfolio_value, get_portfolio_breakdown, get_artist_info,
    get_artist_price_history, get_market_overview, get_claim_multiplier,
    get_balance_stats, BASE_SHARE_VALUE
)

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


@pytest.fixture(autouse=True)
def clear_cooldowns():
    _lastfm_cooldowns.clear()
    yield
    _lastfm_cooldowns.clear()


def make_interaction(user_id=123456789, username="testuser", is_admin=False, guild_id=GUILD_ID):
    user = MagicMock()
    user.id = user_id
    user.name = username
    user.mention = f"<@{user_id}>"
    user.guild_permissions.administrator = is_admin

    guild = MagicMock()
    guild.id = guild_id

    response = MagicMock()
    response.send_message = AsyncMock()
    response.defer = AsyncMock()
    response.edit_message = AsyncMock()

    followup = MagicMock()
    followup.send = AsyncMock()

    interaction = MagicMock()
    interaction.user = user
    interaction.guild = guild
    interaction.response = response
    interaction.followup = followup
    interaction.channel = MagicMock()
    interaction.channel.mention = "#general"

    return interaction


class TestCheckLastfmCooldown:
    def test_allows_first_call(self):
        allowed, remaining = check_lastfm_cooldown(1)
        assert allowed is True
        assert remaining == 0

    def test_blocks_immediate_second_call(self):
        check_lastfm_cooldown(1)
        allowed, remaining = check_lastfm_cooldown(1)
        assert allowed is False
        assert remaining > 0

    def test_different_users_independent(self):
        check_lastfm_cooldown(1)
        allowed, remaining = check_lastfm_cooldown(2)
        assert allowed is True
        assert remaining == 0


class TestFormatListeners:
    def test_small_numbers(self):
        assert format_listeners(999) == "999"

    def test_thousands(self):
        assert format_listeners(1000) == "1.0k"
        assert format_listeners(25300) == "25.3k"

    def test_millions(self):
        assert format_listeners(1200000) == "1.2M"
        assert format_listeners(4500000) == "4.5M"


class TestGetUserInGuild:
    def test_returns_none_when_user_missing(self, tmp_db):
        row = get_user_in_guild(999999, GUILD_ID)
        assert row is None

    def test_returns_user_and_adds_guild(self, tmp_db):
        insert_user(123456789, GUILD_ID, "alice", "alice_lfm")
        row = get_user_in_guild(123456789, GUILD_ID)
        assert row is not None
        assert row["username"] == "alice"


class TestSlashLastfm:
    def test_rejects_empty_username(self, tmp_db):
        cmd = StockCommands(MagicMock())
        interaction = make_interaction()

        async def run():
            app_cmd = StockCommands.__dict__['slash_lastfm']
            await app_cmd.callback(cmd, interaction, "   ")

        import asyncio
        asyncio.run(run())

        interaction.response.send_message.assert_called_once()
        assert "valid Last.fm username" in interaction.response.send_message.call_args[0][0]

    def test_delegates_to_service_on_valid_input(self, tmp_db):
        cmd = StockCommands(MagicMock())
        interaction = make_interaction()

        with patch('cogs.commands.validate_lastfm_user', new_callable=AsyncMock) as mock_validate:
            with patch('cogs.commands.insert_user') as mock_insert:
                async def run():
                    app_cmd = StockCommands.__dict__['slash_lastfm']
                    await app_cmd.callback(cmd, interaction, "validuser")

                import asyncio
                asyncio.run(run())

                mock_validate.assert_called_once_with("validuser")
                mock_insert.assert_called_once()


class TestSlashClaim:
    def test_no_account_returns_error(self, tmp_db):
        cmd = StockCommands(MagicMock())
        interaction = make_interaction(user_id=999999)

        async def run():
            app_cmd = StockCommands.__dict__['slash_claim']
            await app_cmd.callback(cmd, interaction)

        import asyncio
        asyncio.run(run())

        interaction.response.send_message.assert_called_once()
        assert "set up your account" in interaction.response.send_message.call_args[0][0]

    def test_24h_cooldown_blocks_claim(self, tmp_db):
        insert_user(123456789, GUILD_ID, "alice", "alice_lfm", money=0, last_claim=9999999999)
        cmd = StockCommands(MagicMock())
        interaction = make_interaction()

        async def run():
            app_cmd = StockCommands.__dict__['slash_claim']
            await app_cmd.callback(cmd, interaction)

        import asyncio
        asyncio.run(run())

        interaction.response.send_message.assert_called_once()
        assert "24 hours" in interaction.response.send_message.call_args[0][0]

    def test_lastfm_cooldown_blocks_before_claim(self, tmp_db):
        insert_user(123456789, GUILD_ID, "alice", "alice_lfm", money=0, last_claim=0)
        cmd = StockCommands(MagicMock())
        interaction = make_interaction()

        check_lastfm_cooldown(123456789)

        async def run():
            app_cmd = StockCommands.__dict__['slash_claim']
            await app_cmd.callback(cmd, interaction)

        import asyncio
        asyncio.run(run())

        interaction.response.send_message.assert_called_once()
        assert "wait" in interaction.response.send_message.call_args[0][0].lower()


class TestSlashCheck:
    def test_non_admin_cooldown_blocks_check(self, tmp_db):
        insert_user(123456789, GUILD_ID, "alice", "alice_lfm")
        update_last_preview(123456789, 9999999999)
        cmd = StockCommands(MagicMock())
        interaction = make_interaction(is_admin=False)

        async def run():
            app_cmd = StockCommands.__dict__['slash_check']
            await app_cmd.callback(cmd, interaction)

        import asyncio
        asyncio.run(run())

        interaction.response.send_message.assert_called_once()
        assert "1 hour" in interaction.response.send_message.call_args[0][0]

    def test_admin_bypasses_cooldown(self, tmp_db):
        insert_user(123456789, GUILD_ID, "alice", "alice_lfm")
        update_last_preview(123456789, 9999999999)
        cmd = StockCommands(MagicMock())
        interaction = make_interaction(is_admin=True)

        with patch('cogs.commands.calculate_portfolio_value', return_value=(100.0, 5.0)):
            with patch('cogs.commands.update_user_money') as mock_update:
                with patch('cogs.commands.update_last_preview') as mock_preview:
                    async def run():
                        app_cmd = StockCommands.__dict__['slash_check']
                        await app_cmd.callback(cmd, interaction)

                    import asyncio
                    asyncio.run(run())

                    mock_update.assert_called_once()
                    mock_preview.assert_not_called()


class TestSlashBalance:
    def test_empty_portfolio_returns_na(self, tmp_db):
        insert_user(123456789, GUILD_ID, "alice", "alice_lfm")
        cmd = StockCommands(MagicMock())
        interaction = make_interaction()

        async def run():
            app_cmd = StockCommands.__dict__['slash_balance']
            await app_cmd.callback(cmd, interaction)

        import asyncio
        asyncio.run(run())

        embed = interaction.followup.send.call_args[1]["embed"]
        assert embed is not None

    def test_with_scrobbles_returns_stats(self, tmp_db):
        insert_user(123456789, GUILD_ID, "alice", "alice_lfm")
        insert_scrobble(123456789, GUILD_ID, "Taylor Swift", 15000000, "20260722")
        cmd = StockCommands(MagicMock())
        interaction = make_interaction()

        async def run():
            app_cmd = StockCommands.__dict__['slash_balance']
            await app_cmd.callback(cmd, interaction)

        import asyncio
        asyncio.run(run())

        embed = interaction.followup.send.call_args[1]["embed"]
        assert embed is not None


class TestSlashPortfolio:
    def test_empty_portfolio_message(self, tmp_db):
        insert_user(123456789, GUILD_ID, "alice", "alice_lfm")
        cmd = StockCommands(MagicMock())
        interaction = make_interaction()

        async def run():
            app_cmd = StockCommands.__dict__['slash_portfolio']
            await app_cmd.callback(cmd, interaction)

        import asyncio
        asyncio.run(run())

        interaction.followup.send.assert_called_once()
        assert "no shares" in interaction.followup.send.call_args[0][0].lower()

    def test_with_shows_breakdown(self, tmp_db):
        insert_user(123456789, GUILD_ID, "alice", "alice_lfm")
        insert_scrobble(123456789, GUILD_ID, "Taylor Swift", 15000000, "20260722")
        upsert_snapshot("Taylor Swift", 15200000, "20260722")
        cmd = StockCommands(MagicMock())
        interaction = make_interaction()

        async def run():
            app_cmd = StockCommands.__dict__['slash_portfolio']
            await app_cmd.callback(cmd, interaction)

        import asyncio
        asyncio.run(run())

        interaction.followup.send.assert_called_once()
        embed = interaction.followup.send.call_args[1]["embed"]
        assert embed is not None


class TestSlashArtist:
    def test_rejects_empty_name(self, tmp_db):
        cmd = StockCommands(MagicMock())
        interaction = make_interaction()

        async def run():
            app_cmd = StockCommands.__dict__['slash_artist']
            await app_cmd.callback(cmd, interaction, "   ")

        import asyncio
        asyncio.run(run())

        interaction.response.send_message.assert_called_once()
        assert "valid artist name" in interaction.response.send_message.call_args[0][0]

    def test_rejects_name_over_100_chars(self, tmp_db):
        cmd = StockCommands(MagicMock())
        interaction = make_interaction()

        async def run():
            app_cmd = StockCommands.__dict__['slash_artist']
            await app_cmd.callback(cmd, interaction, "a" * 101)

        import asyncio
        asyncio.run(run())

        interaction.response.send_message.assert_called_once()
        assert "valid artist name" in interaction.response.send_message.call_args[0][0]

    def test_cooldown_blocks_artist_lookup(self, tmp_db):
        cmd = StockCommands(MagicMock())
        interaction = make_interaction()

        check_lastfm_cooldown(123456789)

        async def run():
            app_cmd = StockCommands.__dict__['slash_artist']
            await app_cmd.callback(cmd, interaction, "Taylor Swift")

        import asyncio
        asyncio.run(run())

        interaction.response.send_message.assert_called_once()
        assert "wait" in interaction.response.send_message.call_args[0][0].lower()


class TestSlashHistory:
    def test_rejects_empty_name(self, tmp_db):
        cmd = StockCommands(MagicMock())
        interaction = make_interaction()

        async def run():
            app_cmd = StockCommands.__dict__['slash_history']
            await app_cmd.callback(cmd, interaction, "   ")

        import asyncio
        asyncio.run(run())

        interaction.response.send_message.assert_called_once()
        assert "valid artist name" in interaction.response.send_message.call_args[0][0]

    def test_cooldown_blocks_history(self, tmp_db):
        cmd = StockCommands(MagicMock())
        interaction = make_interaction()

        check_lastfm_cooldown(123456789)

        async def run():
            app_cmd = StockCommands.__dict__['slash_history']
            await app_cmd.callback(cmd, interaction, "Taylor Swift")

        import asyncio
        asyncio.run(run())

        interaction.response.send_message.assert_called_once()
        assert "wait" in interaction.response.send_message.call_args[0][0].lower()


class TestSlashTransactions:
    def test_no_transactions_message(self, tmp_db):
        insert_user(123456789, GUILD_ID, "alice", "alice_lfm")
        cmd = StockCommands(MagicMock())
        interaction = make_interaction()

        async def run():
            app_cmd = StockCommands.__dict__['slash_transactions']
            await app_cmd.callback(cmd, interaction)

        import asyncio
        asyncio.run(run())

        interaction.response.send_message.assert_called_once()
        assert "No transactions" in interaction.response.send_message.call_args[0][0]

    def test_with_transactions_shows_view(self, tmp_db):
        insert_user(123456789, GUILD_ID, "alice", "alice_lfm")
        insert_scrobble(123456789, GUILD_ID, "Taylor Swift", 15000000, "20260722")
        cmd = StockCommands(MagicMock())
        interaction = make_interaction()

        async def run():
            app_cmd = StockCommands.__dict__['slash_transactions']
            await app_cmd.callback(cmd, interaction)

        import asyncio
        asyncio.run(run())

        interaction.response.send_message.assert_called_once()
        args = interaction.response.send_message.call_args
        assert args[1]["view"] is not None


class TestSlashMarket:
    def test_no_data_message(self, tmp_db):
        cmd = StockCommands(MagicMock())
        interaction = make_interaction()

        async def run():
            app_cmd = StockCommands.__dict__['slash_market']
            await app_cmd.callback(cmd, interaction)

        import asyncio
        asyncio.run(run())

        interaction.followup.send.assert_called_once()
        assert "No market data" in interaction.followup.send.call_args[0][0]

    def test_cooldown_blocks_market(self, tmp_db):
        cmd = StockCommands(MagicMock())
        interaction = make_interaction()

        check_lastfm_cooldown(123456789)

        async def run():
            app_cmd = StockCommands.__dict__['slash_market']
            await app_cmd.callback(cmd, interaction)

        import asyncio
        asyncio.run(run())

        interaction.response.send_message.assert_called_once()
        assert "wait" in interaction.response.send_message.call_args[0][0].lower()


class TestSlashLeaderboard:
    def test_empty_leaderboard(self, tmp_db):
        cmd = StockCommands(MagicMock())
        interaction = make_interaction()

        async def run():
            app_cmd = StockCommands.__dict__['slash_leaderboard']
            await app_cmd.callback(cmd, interaction)

        import asyncio
        asyncio.run(run())

        embed = interaction.followup.send.call_args[1]["embed"]
        assert "No users" in embed.description

    def test_with_users_sorted_by_money(self, tmp_db):
        insert_user(111, GUILD_ID, "alice", "alice_lfm", money=100.0)
        insert_user(222, GUILD_ID, "bob", "bob_lfm", money=300.0)
        add_user_to_guild(111, GUILD_ID)
        add_user_to_guild(222, GUILD_ID)
        cmd = StockCommands(MagicMock())
        interaction = make_interaction()

        async def run():
            app_cmd = StockCommands.__dict__['slash_leaderboard']
            await app_cmd.callback(cmd, interaction)

        import asyncio
        asyncio.run(run())

        embed = interaction.followup.send.call_args[1]["embed"]
        lines = embed.description.split("\n")
        assert "bob" in lines[0]
        assert "alice" in lines[1]


class TestSlashUnlink:
    def test_no_account_returns_error(self, tmp_db):
        cmd = StockCommands(MagicMock())
        interaction = make_interaction(user_id=999999)

        async def run():
            app_cmd = StockCommands.__dict__['slash_unlink']
            await app_cmd.callback(cmd, interaction)

        import asyncio
        asyncio.run(run())

        interaction.response.send_message.assert_called_once()
        assert "don't have an account" in interaction.response.send_message.call_args[0][0]

    def test_shows_confirmation_view(self, tmp_db):
        insert_user(123456789, GUILD_ID, "alice", "alice_lfm")
        insert_scrobble(123456789, GUILD_ID, "Taylor Swift", 15000000, "20260722")
        cmd = StockCommands(MagicMock())
        interaction = make_interaction()

        async def run():
            app_cmd = StockCommands.__dict__['slash_unlink']
            await app_cmd.callback(cmd, interaction)

        import asyncio
        asyncio.run(run())

        interaction.response.send_message.assert_called_once()
        view = interaction.response.send_message.call_args[1]["view"]
        assert view is not None
        assert isinstance(view, ConfirmUnlinkView)

    def test_confirm_deletes_user_data(self, tmp_db):
        insert_user(123456789, GUILD_ID, "alice", "alice_lfm")
        insert_scrobble(123456789, GUILD_ID, "Taylor Swift", 15000000, "20260722")
        view = ConfirmUnlinkView(123456789)
        interaction = make_interaction()

        async def run():
            confirm_btn = view.children[0]
            await confirm_btn.callback(interaction)

        import asyncio
        asyncio.run(run())

        from services.database import get_user, get_scrobbles
        assert get_user(123456789) is None
        assert get_scrobbles(123456789) == []


class TestSlashMarketconfig:
    def test_non_admin_cannot_set(self, tmp_db):
        cmd = StockCommands(MagicMock())
        interaction = make_interaction(is_admin=False)
        interaction.data = {"options": []}
        channel = MagicMock()
        channel.id = 123456789
        channel.mention = "#general"

        async def run():
            app_cmd = StockCommands.__dict__['slash_marketconfig']
            await app_cmd.callback(cmd, interaction, channel, "09:00", "+0")

        import asyncio
        asyncio.run(run())

        interaction.response.send_message.assert_called_once()
        assert "administrator" in interaction.response.send_message.call_args[0][0].lower()

    def test_admin_sets_config(self, tmp_db):
        cmd = StockCommands(MagicMock())
        interaction = make_interaction(is_admin=True)
        channel = MagicMock()
        channel.id = 123456789
        channel.mention = "#general"

        async def run():
            app_cmd = StockCommands.__dict__['slash_marketconfig']
            await app_cmd.callback(cmd, interaction, channel, "09:00", "+0")

        import asyncio
        asyncio.run(run())

        interaction.response.send_message.assert_called_once()
        configs = get_all_guild_configs()
        assert len(configs) == 1
        assert configs[0]["market_hour_local"] == 9
        assert configs[0]["market_timezone"] == "+0"


class TestClaimMultiplierNoBonusOnFirstDay:
    def test_first_day_scrobbles_do_not_trigger_bonus(self, tmp_db):
        insert_user(123456789, GUILD_ID, "alice", "alice_lfm")
        today = datetime.datetime.now(datetime.timezone.utc).date().isoformat().replace('-', '')
        insert_scrobble(123456789, GUILD_ID, "Taylor Swift", 15000000, today, count=100)

        multiplier = get_claim_multiplier(123456789)
        assert multiplier == 1.0
