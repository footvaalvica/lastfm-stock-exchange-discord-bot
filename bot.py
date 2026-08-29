import datetime
import logging
import logging.handlers
import os
import signal
import asyncio
import discord
from discord.ext import commands, tasks
from config import DISCORD_TOKEN
from services.database import init_db, get_all_guild_configs, migrate_fix_zero_purchase_prices, migrate_artist_scrobbles_to_float
from services.portfolio import get_market_overview
from cogs.commands import setup as commands_setup
from backup_db import run_backup

log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'bot.log')
handler = logging.handlers.RotatingFileHandler(log_path, maxBytes=5*1024*1024, backupCount=3, encoding='utf-8')
handler.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(name)s: %(message)s'))
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(name)s: %(message)s')
logging.getLogger().addHandler(handler)

logger = logging.getLogger('lastfm_bot')

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents)

_last_sync_ts = 0
_guild_config_cache: list[dict] = []
_guild_config_cache_ts = 0
GUILD_CONFIG_CACHE_TTL = 300
_migration_ran = False
_sent_market_dates: dict[int, str] = {}


def invalidate_guild_config_cache():
    global _guild_config_cache_ts
    _guild_config_cache_ts = 0


@bot.event
async def on_ready():
    init_db()
    global _migration_ran
    if not _migration_ran:
        try:
            migrate_fix_zero_purchase_prices()
            migrate_artist_scrobbles_to_float()
            _migration_ran = True
            logger.info('Migrated zero/NULL purchase_price rows to BASE_SHARE_VALUE')
            logger.info('Migrated artist_scrobbles daily_total to REAL')
        except Exception as e:
            logger.error('Failed to migrate: %s', e)
    global _last_sync_ts, _guild_config_cache, _guild_config_cache_ts
    now_ts = int(datetime.datetime.now(datetime.timezone.utc).timestamp())
    if now_ts - _last_sync_ts >= 3600:
        try:
            synced = await bot.tree.sync()
            logger.info('Synced %d slash command(s)', len(synced))
            _last_sync_ts = now_ts
        except Exception as e:
            logger.error('Failed to sync slash commands: %s', e)
    else:
        logger.info('Skipping slash command sync; last sync was %ds ago', now_ts - _last_sync_ts)
    logger.info('Logged in as %s', bot.user)
    if not send_market_summary.is_running():
        send_market_summary.start()
    if not daily_backup.is_running():
        daily_backup.start()


@bot.event
async def on_disconnect():
    logger.warning('Bot disconnected from Discord')


def _shutdown_handler(signum, frame):
    logger.info('Received shutdown signal %s, cleaning up...', signum)
    send_market_summary.stop()
    daily_backup.stop()
    raise SystemExit(0)


if hasattr(signal, 'SIGINT'):
    signal.signal(signal.SIGINT, _shutdown_handler)
if hasattr(signal, 'SIGTERM'):
    signal.signal(signal.SIGTERM, _shutdown_handler)


@tasks.loop(minutes=1)
async def send_market_summary():
    global _guild_config_cache, _guild_config_cache_ts, _sent_market_dates
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    now_ts = int(now_utc.timestamp())
    today_str = now_utc.date().isoformat()

    if now_ts - _guild_config_cache_ts >= GUILD_CONFIG_CACHE_TTL:
        try:
            _guild_config_cache = get_all_guild_configs()
            _guild_config_cache_ts = now_ts
        except Exception as e:
            logger.error('Failed to refresh guild config cache: %s', e)
            return

    for config in _guild_config_cache:
        channel_id = config.get('market_channel_id')
        market_hour = config.get('market_hour_local')
        market_timezone = config.get('market_timezone', 'UTC')
        guild_id = config.get('guild_id')
        if not channel_id or market_hour is None or guild_id is None:
            continue

        if _sent_market_dates.get(guild_id) == today_str:
            continue

        try:
            tz = datetime.timezone(datetime.timedelta(hours=int(market_timezone)))
            local_now = now_utc.astimezone(tz)
        except Exception:
            local_now = now_utc

        if local_now.hour != market_hour:
            continue

        overview = get_market_overview(guild_id)
        if not overview['gainers'] and not overview['losers']:
            logger.info('Skipping market summary for guild %s: no gainers/losers', guild_id)
            _sent_market_dates[guild_id] = today_str
            continue

        sections = []

        if overview['gainers']:
            lines = []
            for entry in overview['gainers']:
                lines.append(f"📈 **{entry['artist_name']}**: +{entry['change_percent']:.2f}%")
            sections.append("**Today's Biggest Winner**\n" + "\n".join(lines[:1]))

        if overview['losers']:
            lines = []
            for entry in overview['losers']:
                lines.append(f"📉 **{entry['artist_name']}**: {entry['change_percent']:.2f}%")
            sections.append("**Today's Biggest Loser**\n" + "\n".join(lines[:1]))

        body = "\n\n".join(sections)
        embed = discord.Embed(
            title="Daily Market Summary",
            description=body,
            color=discord.Color.blue()
        )

        channel = bot.get_channel(channel_id)
        if not channel:
            logger.warning('Market summary channel %s not found for guild %s', channel_id, guild_id)
            continue

        try:
            await channel.send(embed=embed)
            _sent_market_dates[guild_id] = today_str
            logger.info('Sent market summary to guild %s channel %s', guild_id, channel_id)
        except Exception as e:
            logger.error('Failed to send market summary to channel %s: %s', channel_id, e)

@tasks.loop(hours=1)
async def daily_backup():
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    if now_utc.hour != 3:
        return

    try:
        run_backup()
        logger.info('Daily database backup completed')
    except Exception as e:
        logger.error('Failed to create daily backup: %s', e)


@send_market_summary.error
async def send_market_summary_error(error):
    logger.error('Market summary task failed: %s', error, exc_info=error)
    if not send_market_summary.is_running():
        await asyncio.sleep(5)
        if not send_market_summary.is_running():
            send_market_summary.start()
            logger.info('Restarted market summary task after failure')


@daily_backup.error
async def daily_backup_error(error):
    logger.error('Daily backup task failed: %s', error, exc_info=error)
    if not daily_backup.is_running():
        await asyncio.sleep(5)
        if not daily_backup.is_running():
            daily_backup.start()
            logger.info('Restarted daily backup task after failure')


async def main():
    async with bot:
        await commands_setup(bot)
        await bot.start(DISCORD_TOKEN)


if __name__ == '__main__':
    logger.info('Starting bot...')
    asyncio.run(main())
