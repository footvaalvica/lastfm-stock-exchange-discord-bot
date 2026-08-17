import logging
import datetime
import discord
from discord import app_commands
from discord.ext import commands
import pylast
from services.database import get_user, insert_user, update_last_preview, update_user_money
from services.portfolio import process_user_claim, calculate_portfolio_value, get_portfolio_breakdown, BASE_SHARE_VALUE, get_artist_info, get_artist_price_history
from services.lastfm import validate_lastfm_user, get_lastfm_user

logger = logging.getLogger('lastfm_bot')

PAGE_SIZE = 10


def format_listeners(count: int) -> str:
    if count >= 1_000_000:
        return f"{count / 1_000_000:.1f}M"
    if count >= 1_000:
        return f"{count / 1_000:.1f}k"
    return str(count)


class PortfolioView(discord.ui.View):
    def __init__(self, author_id: int, breakdown: list[dict], total_value: float, total_shares: int, total_gain_percent: float, sort_by: str):
        super().__init__(timeout=180)
        self.author_id = author_id
        self.breakdown = breakdown
        self.total_value = total_value
        self.total_shares = total_shares
        self.total_gain_percent = total_gain_percent
        self.sort_by = sort_by
        self.page = 0
        self.total_pages = max(1, (len(breakdown) + PAGE_SIZE - 1) // PAGE_SIZE)
        self._update_buttons()

    def _update_buttons(self):
        self.previous_page.disabled = self.page == 0
        self.next_page.disabled = self.page >= self.total_pages - 1

    def _build_embed(self) -> discord.Embed:
        start = self.page * PAGE_SIZE
        end = start + PAGE_SIZE
        page_items = self.breakdown[start:end]

        embed_color = discord.Color.green() if self.total_gain_percent >= 0 else discord.Color.red()

        lines = []
        for item in page_items:
            gain = item['gain_loss_percent']
            gain_str = f"{gain:+.2f}%"
            lines.append(f"**{item['artist_name']}** ×{item['shares']}\n💰 {item['current_value']:.2f}€ | 📈 {gain_str}")

        body = "\n".join(lines)
        embed = discord.Embed(
            title=f"Portfolio (page {self.page + 1}/{self.total_pages})",
            description=body,
            color=embed_color
        )
        embed.set_footer(text=f"Total: {self.total_value:.2f}€ | {self.total_shares} shares | {self.total_gain_percent:+.2f}%")
        return embed

    @discord.ui.button(label="◀️", style=discord.ButtonStyle.secondary)
    async def previous_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.page > 0:
            self.page -= 1
            self._update_buttons()
            await interaction.response.edit_message(embed=self._build_embed(), view=self)
        else:
            await interaction.response.defer()

    @discord.ui.button(label="▶️", style=discord.ButtonStyle.secondary)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.page < self.total_pages - 1:
            self.page += 1
            self._update_buttons()
            await interaction.response.edit_message(embed=self._build_embed(), view=self)
        else:
            await interaction.response.defer()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.author_id


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
        update_user_money(ctx.author.id, total_money)
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
        update_user_money(interaction.user.id, total_money)
        if not is_admin:
            update_last_preview(interaction.user.id, now_ts)
        await interaction.followup.send(f"{interaction.user.mention}, your portfolio is worth **{total_money:.2f}€**{gain_str}")


    @commands.command(name="balance")
    async def prefix_balance(self, ctx):
        user_row = get_user(ctx.author.id)
        if not user_row:
            await ctx.send(f"{ctx.author.name}, set up your account first by setting your Last.fm username.")
            return
        await ctx.send(f"{ctx.author.mention}, your portfolio is worth **{user_row['money']:.2f}€** (+0.00%)")


    @app_commands.command(name="balance", description="View your balance")
    async def slash_balance(self, interaction: discord.Interaction):
        user_row = get_user(interaction.user.id)
        if not user_row:
            await interaction.response.send_message(
                f"{interaction.user.name}, set up your account first by setting your Last.fm username.",
                ephemeral=True
            )
            return
        await interaction.response.send_message(f"{interaction.user.mention}, your portfolio is worth **{user_row['money']:.2f}€** (+0.00%)")


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

        view = PortfolioView(ctx.author.id, breakdown, total_value, total_shares, total_gain_percent, "value")
        await ctx.send(embed=view._build_embed(), view=view)


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

        view = PortfolioView(interaction.user.id, breakdown, total_value, total_shares, total_gain_percent, sort_by)
        await interaction.followup.send(embed=view._build_embed(), view=view)


    @commands.command(name="leaderboard")
    async def prefix_leaderboard(self, ctx):
        from services.database import get_db
        conn = get_db()
        rows = conn.execute('SELECT discord_id, username FROM users').fetchall()
        conn.close()

        if not rows:
            embed = discord.Embed(
                title="Leaderboard",
                description="No users yet.",
                color=discord.Color.gold()
            )
            await ctx.send(embed=embed)
            return

        today_str = datetime.datetime.now(datetime.timezone.utc).date().isoformat().replace('-', '')
        leaderboard = []
        for row in rows:
            value, _ = await calculate_portfolio_value(row['discord_id'], today_str)
            leaderboard.append((row['username'], value))

        leaderboard.sort(key=lambda x: x[1], reverse=True)

        lines = []
        for rank, (username, value) in enumerate(leaderboard, start=1):
            lines.append(f"**{rank}.** {username} — {value:.2f}€")

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
        rows = conn.execute('SELECT discord_id, username FROM users').fetchall()
        conn.close()

        if not rows:
            embed = discord.Embed(
                title="Leaderboard",
                description="No users yet.",
                color=discord.Color.gold()
            )
            await interaction.response.send_message(embed=embed)
            return

        await interaction.response.defer()
        today_str = datetime.datetime.now(datetime.timezone.utc).date().isoformat().replace('-', '')
        leaderboard = []
        for row in rows:
            value, _ = await calculate_portfolio_value(row['discord_id'], today_str)
            leaderboard.append((row['username'], value))

        leaderboard.sort(key=lambda x: x[1], reverse=True)

        lines = []
        for rank, (username, value) in enumerate(leaderboard, start=1):
            lines.append(f"**{rank}.** {username} — {value:.2f}€")

        embed = discord.Embed(
            title="Leaderboard",
            description="\n".join(lines),
            color=discord.Color.gold()
        )
        await interaction.response.send_message(embed=embed)


    @commands.command(name="lastfm")
    async def prefix_lastfm(self, ctx):
        try:
            lastfm_username = ctx.message.content.split(' ')[1]
            await validate_lastfm_user(lastfm_username)
            insert_user(ctx.author.id, ctx.author.name, lastfm_username)
            await ctx.send(f"{ctx.author.mention}, your Last.fm username has been set to {lastfm_username}.")
        except IndexError:
            await ctx.send("Please provide a Last.fm username.")
        except pylast.WSError:
            await ctx.send("Last.fm user not found.")


    @app_commands.command(name="lastfm", description="Set your Last.fm username")
    async def slash_lastfm(self, interaction: discord.Interaction, lastfm_username: str):
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


    @commands.command(name="history")
    async def prefix_history(self, ctx, *, artist_name: str = None):
        if not artist_name:
            await ctx.send("Please provide an artist name. Usage: `!history <name>`")
            return

        history = await get_artist_price_history(artist_name)
        if not history:
            await ctx.send(f"No price history found for **{artist_name}**.")
            return

        lines = []
        prev_listeners = None
        for entry in history:
            date_fmt = datetime.datetime.strptime(entry['date'], '%Y%m%d').strftime('%d/%m/%Y')
            listeners = entry['listeners']
            if prev_listeners is None:
                trend = "➡️"
            elif listeners > prev_listeners:
                trend = "📈"
            elif listeners < prev_listeners:
                trend = "📉"
            else:
                trend = "➡️"
            prev_listeners = listeners
            value = listeners / 100_000
            lines.append(f"{trend} {date_fmt}: {format_listeners(listeners)} listeners ({value:.2f}€)")

        body = "\n".join(lines)
        embed = discord.Embed(
            title=f"Price History: {history[-1].get('artist_name', artist_name)}",
            description=body,
            color=discord.Color.blue()
        )
        await ctx.send(embed=embed)


    @app_commands.command(name="history", description="View an artist's listener history")
    async def slash_history(self, interaction: discord.Interaction, artist_name: str):
        await interaction.response.defer()
        history = await get_artist_price_history(artist_name)
        if not history:
            await interaction.followup.send(f"No price history found for **{artist_name}**.")
            return

        lines = []
        prev_listeners = None
        for entry in history:
            date_fmt = datetime.datetime.strptime(entry['date'], '%Y%m%d').strftime('%d/%m/%Y')
            listeners = entry['listeners']
            if prev_listeners is None:
                trend = "➡️"
            elif listeners > prev_listeners:
                trend = "📈"
            elif listeners < prev_listeners:
                trend = "📉"
            else:
                trend = "➡️"
            prev_listeners = listeners
            value = listeners / 100_000
            lines.append(f"{trend} {date_fmt}: {format_listeners(listeners)} listeners ({value:.2f}€)")

        body = "\n".join(lines)
        embed = discord.Embed(
            title=f"Price History: {history[-1].get('artist_name', artist_name)}",
            description=body,
            color=discord.Color.blue()
        )
        await interaction.followup.send(embed=embed)


    @commands.command(name="rules")
    async def prefix_rules(self, ctx):
        embed = discord.Embed(
            title="📈 How to Play",
            description=(
                "**1.** Set your Last.fm with `/lastfm <username>`\n"
                "**2.** Run `/claim` to buy shares from your recent scrobbles at that day's price\n"
                "**3.** Run `/portfolio` to see your holdings\n"
                "**4.** Run `/check` to update stale data\n\n"
                "**How to profit:**\n"
                "• An artist's share price = their Last.fm listener count\n"
                "• When more people listen to an artist, the price goes up — your shares gain value\n"
                "• The more scrobbles you have for an artist, the more shares you own\n"
                "• Claim daily to keep accumulating shares\n"
                "• Price changes happen once per day, so patience pays off"
            ),
            color=discord.Color.blue()
        )
        await ctx.send(embed=embed)


    @app_commands.command(name="rules", description="How to play")
    async def slash_rules(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="📈 How to Play",
            description=(
                "**1.** Set your Last.fm with `/lastfm <username>`\n"
                "**2.** Run `/claim` to buy shares from your recent scrobbles at that day's price\n"
                "**3.** Run `/portfolio` to see your holdings\n"
                "**4.** Run `/check` to update stale data\n\n"
                "**How to profit:**\n"
                "• An artist's share price = their Last.fm listener count\n"
                "• When more people listen to an artist, the price goes up — your shares gain value\n"
                "• The more scrobbles you have for an artist, the more shares you own\n"
                "• Claim daily to keep accumulating shares\n"
                "• Price changes happen once per day, so patience pays off"
            ),
            color=discord.Color.blue()
        )
        await interaction.response.send_message(embed=embed)


    @app_commands.command(name="help", description="Show all commands")
    async def slash_help(self, interaction: discord.Interaction):
        help_message = (
            "**Available commands:**\n"
            "/claim - Claim your daily portfolio value\n"
            "/leaderboard - View the leaderboard\n"
            "/lastfm <username> - Link your Last.fm account\n"
            "/check - Recalculate your portfolio value (1h cooldown)\n"
            "/balance - View your balance\n"
            "/portfolio - View your portfolio breakdown\n"
            "/artist <name> - Look up an artist's stock info\n"
            "/history <name> - View an artist's price history\n"
            "/rules - How to play\n"
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



