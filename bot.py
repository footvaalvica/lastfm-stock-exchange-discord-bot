import datetime
import logging
import discord
from discord.ext import commands
from config import DISCORD_TOKEN
from services.database import init_db
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


async def main():
    async with bot:
        await commands_setup(bot)
        await bot.start(DISCORD_TOKEN)


if __name__ == '__main__':
    import asyncio
    logger.info('Starting bot...')
    asyncio.run(main())
