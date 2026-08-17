import datetime
import logging
import discord
from discord.ext import commands, tasks
from config import DISCORD_TOKEN
from services.database import init_db, get_all_guild_configs
from services.portfolio import get_market_overview
from cogs.commands import setup as commands_setup
from backup_db import run_backup

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
)

logger = logging.getLogger('lastfm_bot')

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents)


@bot.event
async def on_ready():
    init_db()
    try:
        synced = await bot.tree.sync()
        logger.info('Synced %d slash command(s)', len(synced))
    except Exception as e:
        logger.error('Failed to sync slash commands: %s', e)
    logger.info('Logged in as %s', bot.user)
    send_market_summary.start()
    daily_backup.start()


@tasks.loop(minutes=1)
async def send_market_summary():
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    if now_utc.minute != 0:
        return

    overview = get_market_overview()
    if not overview['gainers'] and not overview['losers']:
        return

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

    for config in get_all_guild_configs():
        channel_id = config.get('market_channel_id')
        market_hour = config.get('market_hour_local')
        market_timezone = config.get('market_timezone', 'UTC')
        if not channel_id or market_hour is None:
            continue

        try:
            tz = datetime.timezone(datetime.timedelta(hours=int(market_timezone)))
            local_now = now_utc.astimezone(tz)
        except Exception:
            local_now = now_utc

        if local_now.hour != market_hour or local_now.minute != 0:
            continue

        channel = bot.get_channel(channel_id)
        if not channel:
            continue

        try:
            await channel.send(embed=embed)
        except Exception as e:
            logger.error('Failed to send market summary to channel %d: %s', channel_id, e)


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


async def main():
    async with bot:
        await commands_setup(bot)
        await bot.start(DISCORD_TOKEN)


if __name__ == '__main__':
    logger.info('Starting bot...')
    import asyncio
    asyncio.run(main())
