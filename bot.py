import datetime
import logging
import discord
from discord.ext import commands, tasks
from config import DISCORD_TOKEN
from services.database import init_db, get_bot_config
from services.portfolio import get_market_overview
from cogs.commands import setup as commands_setup

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


@tasks.loop(hours=1)
async def send_market_summary():
    channel_id = get_bot_config('market_channel_id')
    if not channel_id:
        return
    channel = bot.get_channel(int(channel_id))
    if not channel:
        return

    now_utc = datetime.datetime.now(datetime.timezone.utc)
    market_hour = int(get_bot_config('market_hour_utc') or '9')
    if now_utc.hour != market_hour or now_utc.minute != 0:
        return

    overview = get_market_overview()
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

    if not sections:
        return

    body = "\n\n".join(sections)
    embed = discord.Embed(
        title="Daily Market Summary",
        description=body,
        color=discord.Color.blue()
    )
    await channel.send(embed=embed)


async def main():
    async with bot:
        await commands_setup(bot)
        await bot.start(DISCORD_TOKEN)


if __name__ == '__main__':
    logger.info('Starting bot...')
    import asyncio
    asyncio.run(main())
