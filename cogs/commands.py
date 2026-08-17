import logging
import datetime
import discord
from discord import app_commands
from discord.ext import commands
import pylast
from services.database import get_user, insert_user, update_last_preview
from services.portfolio import process_user_claim, calculate_portfolio_value, get_portfolio_breakdown, BASE_SHARE_VALUE, get_artist_info
from services.lastfm import validate_lastfm_user, get_lastfm_user

logger = logging.getLogger('lastfm_bot')


class MusicCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot


    @commands.command(name="claim")
    async def prefix_claim(self, ctx):
        user_row = get_user(ctx.author.id)
        if not user_row:
            await ctx.send(f"{ctx.author.name}, set up your account first by setting your Last.fm username.")
            return

        last_claim = user_row['last_claim']
        now_ts = int(discord.utils.utcnow().timestamp())
        if now_ts - last_claim < 86400:
            remaining = 86400 - (now_ts - last_claim)
            hours = remaining // 3600
            minutes = (remaining % 3600) // 60
            await ctx.send(f"{ctx.author.name}, you can only claim once every 24 hours. Try again in {hours}h {minutes}m.")
            return

        logger.info("Fetching money for user: %s", ctx.author.name)
        processing_msg = await ctx.send(f"{ctx.author.mention}, processing your claim... this may take a moment.")
        user = await get_lastfm_user(user_row['lastfm_username'])
        total_money, gain_loss = await process_user_claim(user, ctx.author.id)
        gain_str = f" (+{gain_loss:.2f}%)" if gain_loss >= 0 else f" ({gain_loss:.2f}%)"
        await processing_msg.edit(content=f"{ctx.author.mention}, your portfolio is worth **{total_money:.2f}€**{gain_str}")


    @app_commands.command(name="claim", description="Claim your daily portfolio value")
    async def slash_claim(self, interaction: discord.Interaction):
        user_row = get_user(interaction.user.id)
        if not user_row:
            await interaction.response.send_message(
                f"{interaction.user.name}, set up your account first by setting your Last.fm username.",
                ephemeral=True
            )
            return

        last_claim = user_row['last_claim']
        now_ts = int(discord.utils.utcnow().timestamp())
        if now_ts - last_claim < 86400:
            remaining = 86400 - (now_ts - last_claim)
            hours = remaining // 3600
            minutes = (remaining % 3600) // 60
            await interaction.response.send_message(
                f"{interaction.user.mention}, you can only claim once every 24 hours. Try again in {hours}h {minutes}m.",
                ephemeral=True
            )
            return

        await interaction.response.defer()
        logger.info("Fetching money for user: %s", interaction.user.name)
        user = await get_lastfm_user(user_row['lastfm_username'])
        total_money, gain_loss = await process_user_claim(user, interaction.user.id)
        gain_str = f" (+{gain_loss:.2f}%)" if gain_loss >= 0 else f" ({gain_loss:.2f}%)"
        await interaction.followup.send(f"{interaction.user.mention}, your portfolio is worth **{total_money:.2f}€**{gain_str}")


    @commands.command(name="check")
    async def prefix_check(self, ctx):
        user_row = get_user(ctx.author.id)
        if not user_row:
            await ctx.send(f"{ctx.author.name}, set up your account first by setting your Last.fm username.")
            return

        now_ts = int(discord.utils.utcnow().timestamp())
        is_admin = ctx.author.guild_permissions.administrator if ctx.guild else False

        if not is_admin:
            last_preview = user_row.get('last_preview', 0)
            if now_ts - last_preview < 3600:
                remaining = 3600 - (now_ts - last_preview)
                minutes = remaining // 60
                await ctx.send(f"{ctx.author.name}, you can only check once every 1 hour. Try again in {minutes}m.")
                return

        logger.info("Checking portfolio for user: %s (admin=%s)", ctx.author.name, is_admin)
        today_str = datetime.datetime.now(datetime.timezone.utc).date().isoformat().replace('-', '')
        total_money, gain_loss = await calculate_portfolio_value(ctx.author.id, today_str)
        gain_str = f" (+{gain_loss:.2f}%)" if gain_loss >= 0 else f" ({gain_loss:.2f}%)"
        if not is_admin:
            update_last_preview(ctx.author.id, now_ts)
        await ctx.send(f"{ctx.author.mention}, your portfolio is worth **{total_money:.2f}€**{gain_str}")


    @app_commands.command(name="check", description="Recalculate your portfolio value (1h cooldown, admin bypass)")
    async def slash_check(self, interaction: discord.Interaction):
        user_row = get_user(interaction.user.id)
        if not user_row:
            await interaction.response.send_message(
                f"{interaction.user.name}, set up your account first by setting your Last.fm username.",
                ephemeral=True
            )
            return

        now_ts = int(discord.utils.utcnow().timestamp())
        is_admin = interaction.user.guild_permissions.administrator if interaction.guild else False

        if not is_admin:
            last_preview = user_row.get('last_preview', 0)
            if now_ts - last_preview < 3600:
                remaining = 3600 - (now_ts - last_preview)
                minutes = remaining // 60
                await interaction.response.send_message(
                    f"{interaction.user.mention}, you can only check once every 1 hour. Try again in {minutes}m.",
                    ephemeral=True
                )
                return

        await interaction.response.defer()
        logger.info("Checking portfolio for user: %s (admin=%s)", interaction.user.name, is_admin)
        today_str = datetime.datetime.now(datetime.timezone.utc).date().isoformat().replace('-', '')
        total_money, gain_loss = await calculate_portfolio_value(interaction.user.id, today_str)
        gain_str = f" (+{gain_loss:.2f}%)" if gain_loss >= 0 else f" ({gain_loss:.2f}%)"
        if not is_admin:
            update_last_preview(interaction.user.id, now_ts)
        await interaction.followup.send(f"{interaction.user.mention}, your portfolio is worth **{total_money:.2f}€**{gain_str}")


    @commands.command(name="portfolio")
    async def prefix_portfolio(self, ctx):
        user_row = get_user(ctx.author.id)
        if not user_row:
            await ctx.send(f"{ctx.author.name}, set up your account first by setting your Last.fm username.")
            return

        today_str = datetime.datetime.now(datetime.timezone.utc).date().isoformat().replace('-', '')
        breakdown = await get_portfolio_breakdown(ctx.author.id, today_str, sort_by="value")
        if not breakdown:
            await ctx.send(f"{ctx.author.mention}, you have no shares yet.")
            return

        total_value = sum(item['current_value'] for item in breakdown)
        total_shares = sum(item['shares'] for item in breakdown)
        total_base = BASE_SHARE_VALUE * total_shares
        total_gain_percent = ((total_value - total_base) / total_base * 100) if total_base > 0 else 0.0
        embed_color = discord.Color.green() if total_gain_percent >= 0 else discord.Color.red()

        lines = []
        for item in breakdown:
            gain = item['gain_loss_percent']
            gain_str = f"{gain:+.2f}%"
            lines.append(f"**{item['artist_name']}**\n💰 {item['current_value']:.2f}€ | 📈 {gain_str}")

        body = "\n".join(lines)
        embed = discord.Embed(
            title=f"{ctx.author.name}'s Portfolio",
            description=body,
            color=embed_color
        )

        await ctx.send(embed=embed)


    @app_commands.command(name="portfolio", description="View your portfolio breakdown")
    async def slash_portfolio(self, interaction: discord.Interaction, sort_by: str = "value"):
        user_row = get_user(interaction.user.id)
        if not user_row:
            await interaction.response.send_message(
                f"{interaction.user.name}, set up your account first by setting your Last.fm username.",
                ephemeral=True
            )
            return

        valid_sorts = ["value", "price", "quantity"]
        if sort_by not in valid_sorts:
            await interaction.response.send_message(
                f"Invalid sort option. Use: {', '.join(valid_sorts)}",
                ephemeral=True
            )
            return

        await interaction.response.defer()
        today_str = datetime.datetime.now(datetime.timezone.utc).date().isoformat().replace('-', '')
        breakdown = await get_portfolio_breakdown(interaction.user.id, today_str, sort_by=sort_by)
        if not breakdown:
            await interaction.followup.send(f"{interaction.user.mention}, you have no shares yet.")
            return

        total_value = sum(item['current_value'] for item in breakdown)
        total_shares = sum(item['shares'] for item in breakdown)
        total_base = BASE_SHARE_VALUE * total_shares
        total_gain_percent = ((total_value - total_base) / total_base * 100) if total_base > 0 else 0.0
        embed_color = discord.Color.green() if total_gain_percent >= 0 else discord.Color.red()

        lines = []
        for item in breakdown:
            gain = item['gain_loss_percent']
            gain_str = f"{gain:+.2f}%"
            lines.append(f"**{item['artist_name']}**\n💰 {item['current_value']:.2f}€ | 📈 {gain_str}")

        body = "\n".join(lines)
        embed = discord.Embed(
            title=f"{interaction.user.name}'s Portfolio",
            description=body,
            color=embed_color
        )

        await interaction.followup.send(embed=embed)


    @commands.command(name="leaderboard")
    async def prefix_leaderboard(self, ctx):
        from services.database import get_db
        conn = get_db()
        rows = conn.execute('SELECT username, money FROM users ORDER BY money DESC').fetchall()
        conn.close()

        if not rows:
            embed = discord.Embed(
                title="Leaderboard",
                description="No users yet.",
                color=discord.Color.gold()
            )
            await ctx.send(embed=embed)
            return

        lines = []
        for rank, row in enumerate(rows, start=1):
            lines.append(f"**{rank}.** {row['username']} — {row['money']:.2f}€")

        embed = discord.Embed(
            title="Leaderboard",
            description="\n".join(lines),
            color=discord.Color.gold()
        )
        await ctx.send(embed=embed)


    @app_commands.command(name="leaderboard", description="View the leaderboard")
    async def slash_leaderboard(self, interaction: discord.Interaction):
        from services.database import get_db
        conn = get_db()
        rows = conn.execute('SELECT username, money FROM users ORDER BY money DESC').fetchall()
        conn.close()

        if not rows:
            embed = discord.Embed(
                title="Leaderboard",
                description="No users yet.",
                color=discord.Color.gold()
            )
            await interaction.response.send_message(embed=embed)
            return

        lines = []
        for rank, row in enumerate(rows, start=1):
            lines.append(f"**{rank}.** {row['username']} — {row['money']:.2f}€")

        embed = discord.Embed(
            title="Leaderboard",
            description="\n".join(lines),
            color=discord.Color.gold()
        )
        await interaction.response.send_message(embed=embed)


    @commands.command(name="setlastfm")
    async def prefix_setlastfm(self, ctx):
        try:
            lastfm_username = ctx.message.content.split(' ')[1]
            await validate_lastfm_user(lastfm_username)
            insert_user(ctx.author.id, ctx.author.name, lastfm_username)
            await ctx.send(f"{ctx.author.mention}, your Last.fm username has been set to {lastfm_username}.")
        except IndexError:
            await ctx.send("Please provide a Last.fm username.")
        except pylast.WSError:
            await ctx.send("Last.fm user not found.")


    @app_commands.command(name="setlastfm", description="Set your Last.fm username")
    async def slash_setlastfm(self, interaction: discord.Interaction, lastfm_username: str):
        try:
            await validate_lastfm_user(lastfm_username)
            insert_user(interaction.user.id, interaction.user.name, lastfm_username)
            await interaction.response.send_message(f"{interaction.user.mention}, your Last.fm username has been set to {lastfm_username}.")
        except pylast.WSError:
            await interaction.response.send_message("Last.fm user not found.", ephemeral=True)




    @commands.command(name="artist")
    async def prefix_artist(self, ctx, *, artist_name: str = None):
        if not artist_name:
            await ctx.send("Please provide an artist name. Usage: `!artist <name>`")
            return

        today_str = datetime.datetime.now(datetime.timezone.utc).date().isoformat().replace('-', '')
        info = await get_artist_info(artist_name, today_str)
        if not info:
            await ctx.send(f"No data found for **{artist_name}**. It may not be tracked yet.")
            return

        gain = info['gain_loss_percent']
        if gain > 0.5:
            emoji = "📈"
        elif gain < -0.5:
            emoji = "📉"
        else:
            emoji = "➡️"

        embed = discord.Embed(
            title=f"{info['artist_name']} {emoji}",
            color=discord.Color.blue()
        )
        embed.add_field(name="Value", value=f"{info['current_share_value']:.2f}€", inline=True)
        embed.add_field(name="Shares", value=str(info['total_shares']), inline=True)
        embed.add_field(name="Change", value=f"{gain:+.2f}%", inline=True)

        await ctx.send(embed=embed)


    @app_commands.command(name="artist", description="Look up an artist's stock info")
    async def slash_artist(self, interaction: discord.Interaction, artist_name: str):
        today_str = datetime.datetime.now(datetime.timezone.utc).date().isoformat().replace('-', '')
        info = await get_artist_info(artist_name, today_str)
        if not info:
            await interaction.response.send_message(
                f"No data found for **{artist_name}**. It may not be tracked yet.",
                ephemeral=True
            )
            return

        gain = info['gain_loss_percent']
        if gain > 0.5:
            emoji = "📈"
        elif gain < -0.5:
            emoji = "📉"
        else:
            emoji = "➡️"

        embed = discord.Embed(
            title=f"{info['artist_name']} {emoji}",
            color=discord.Color.blue()
        )
        embed.add_field(name="Value", value=f"{info['current_share_value']:.2f}€", inline=True)
        embed.add_field(name="Shares", value=str(info['total_shares']), inline=True)
        embed.add_field(name="Change", value=f"{gain:+.2f}%", inline=True)

        await interaction.response.send_message(embed=embed)


    @app_commands.command(name="help", description="Show all commands")
    async def slash_help(self, interaction: discord.Interaction):
        help_message = (
            "**Available commands:**\n"
            "/claim - Claim your daily portfolio value\n"
            "/leaderboard - View the leaderboard\n"
            "/setlastfm <username> - Link your Last.fm account\n"
            "/check - Recalculate your portfolio value (1h cooldown)\n"
            "/portfolio - View your portfolio breakdown\n"
            "/artist <name> - Look up an artist's stock info\n"
            "/help - Show all commands"
        )
        embed = discord.Embed(
            title="Help",
            description=help_message,
            color=discord.Color.blue()
        )
        await interaction.response.send_message(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(MusicCommands(bot))



