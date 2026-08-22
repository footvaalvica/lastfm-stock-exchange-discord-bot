import logging
import datetime
import os
import tempfile
import time
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import discord
from discord import app_commands
from discord.ext import commands
from services.database import get_user, insert_user, update_last_preview, update_user_money, set_guild_config, get_transactions, add_user_to_guild
from services.portfolio import process_user_claim, calculate_portfolio_value, get_portfolio_breakdown, BASE_SHARE_VALUE, get_artist_info, get_artist_price_history, get_market_overview, get_balance_stats, get_claim_multiplier, LastFMPrivacyError, get_stock_rankings
from services.lastfm import validate_lastfm_user

logger = logging.getLogger('lastfm_bot')

PAGE_SIZE = 5
TXN_PAGE_SIZE = 10
LASTFM_COOLDOWN_SECONDS = 2
_lastfm_cooldowns: dict[int, float] = {}


def get_user_in_guild(discord_id: int, guild_id: int):
    user_row = get_user(discord_id)
    if user_row:
        add_user_to_guild(discord_id, guild_id)
    return user_row


def check_lastfm_cooldown(user_id: int) -> tuple[bool, int]:
    now = time.time()
    last = _lastfm_cooldowns.get(user_id, 0)
    remaining = LASTFM_COOLDOWN_SECONDS - (now - last)
    if remaining > 0:
        return False, int(remaining)
    _lastfm_cooldowns[user_id] = now
    if len(_lastfm_cooldowns) > 5000:
        cutoff = now - 600
        stale = [uid for uid, ts in _lastfm_cooldowns.items() if ts <= cutoff]
        for uid in stale:
            del _lastfm_cooldowns[uid]
    return True, 0


def format_daily_total(count: int) -> str:
    if count >= 1_000_000:
        return f"{count / 1_000_000:.1f}M"
    if count >= 1_000:
        return f"{count / 1_000:.1f}k"
    return str(count)


def generate_allocation_chart(breakdown: list[dict], total_value: float) -> str | None:
    if len(breakdown) <= 1:
        return None

    sorted_items = sorted(breakdown, key=lambda x: x['current_value'], reverse=True)
    top = sorted_items[:10]
    other_value = sum(item['current_value'] for item in sorted_items[10:])

    values = [item['current_value'] for item in top]
    labels = [item['artist_name'] for item in top]
    percentages = [v / total_value * 100 for v in values]

    if other_value > 0:
        values.append(other_value)
        labels.append("Other")
        percentages.append(other_value / total_value * 100)

    colors = plt.cm.tab20(range(len(labels)))

    fig, ax = plt.subplots(figsize=(10, 8))
    wedges, _ = ax.pie(
        values,
        colors=colors,
        startangle=140,
        textprops={'fontsize': 11}
    )
    legend_labels = [f"{l} ({p:.1f}%)" for l, p in zip(labels, percentages)]
    ax.legend(wedges, legend_labels, loc='center left', bbox_to_anchor=(1, 0, 0.5, 1), fontsize=9)
    ax.set_title("Portfolio Allocation", fontsize=16, fontweight='bold')

    with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as tmp:
        plt.savefig(tmp.name, bbox_inches='tight', pad_inches=0.5, dpi=100)
        plt.close(fig)
        return tmp.name


class TransactionsView(discord.ui.View):
    def __init__(self, author_id: int, username: str, grouped: dict[str, list[dict]], artist_filter: str | None, page_size: int = 5):
        super().__init__(timeout=180)
        self.author_id = author_id
        self.username = username
        self.grouped = grouped
        self.artist_filter = artist_filter
        self.page_size = page_size
        self.artists = list(grouped.keys())
        self.total_pages = max(1, (len(self.artists) + page_size - 1) // page_size)
        self.page = 0
        if self.total_pages <= 1:
            self.clear_items()
        self._update_buttons()

    def _update_buttons(self):
        self.previous_page.disabled = self.page == 0
        self.next_page.disabled = self.page >= self.total_pages - 1

    def build_embed(self) -> discord.Embed:
        start = self.page * self.page_size
        end = start + self.page_size
        page_artists = self.artists[start:end]

        lines = []
        for artist in page_artists:
            txs = self.grouped[artist]
            lines.append(f"**{artist}** — ×{sum(tx['count'] for tx in txs)}")
            i = 0
            shown = 0
            while i < len(txs) and shown < 3:
                tx = txs[i]
                date_fmt = datetime.datetime.strptime(tx['scrobble_date'], '%Y%m%d').strftime('%d/%m/%Y')
                count = 1
                while i + count < len(txs) and txs[i + count]['purchase_price'] == tx['purchase_price']:
                    count += 1
                if count > 1:
                    lines.append(f"  {date_fmt} x{count}")
                else:
                    lines.append(f"  {date_fmt}")
                shown += count
                i += count
            remaining = len(txs) - i
            if remaining > 0:
                lines.append(f"  ...and {remaining} more")

        body = "\n".join(lines)
        embed = discord.Embed(
            title=f"{self.username}'s Transactions",
            description=body,
            color=discord.Color.blue()
        )
        embed.set_footer(text=f"Page {self.page + 1}/{self.total_pages}")
        return embed

    @discord.ui.button(label="◀️", style=discord.ButtonStyle.secondary)
    async def previous_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.page > 0:
            self.page -= 1
            self._update_buttons()
            await interaction.response.edit_message(embed=self.build_embed(), view=self)
        else:
            await interaction.response.defer()

    @discord.ui.button(label="▶️", style=discord.ButtonStyle.secondary)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.page < self.total_pages - 1:
            self.page += 1
            self._update_buttons()
            await interaction.response.edit_message(embed=self.build_embed(), view=self)
        else:
            await interaction.response.defer()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.author_id


class PortfolioView(discord.ui.View):
    def __init__(self, author_id: int, breakdown: list[dict], total_value: float, total_shares: int, total_gain_percent: float):
        super().__init__(timeout=180)
        self.author_id = author_id
        self.breakdown = breakdown
        self.total_value = total_value
        self.total_shares = total_shares
        self.total_gain_percent = total_gain_percent
        self.page = 0
        self.total_pages = max(1, (len(breakdown) + PAGE_SIZE - 1) // PAGE_SIZE)
        if self.total_pages <= 1:
            self.clear_items()
        self._update_buttons()

    def _update_buttons(self):
        self.previous_page.disabled = self.page == 0
        self.next_page.disabled = self.page >= self.total_pages - 1

    def build_embed(self) -> discord.Embed:
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
            await interaction.response.edit_message(embed=self.build_embed(), view=self)
        else:
            await interaction.response.defer()

    @discord.ui.button(label="▶️", style=discord.ButtonStyle.secondary)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.page < self.total_pages - 1:
            self.page += 1
            self._update_buttons()
            await interaction.response.edit_message(embed=self.build_embed(), view=self)
        else:
            await interaction.response.defer()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.author_id


class ConfirmUnlinkView(discord.ui.View):
    def __init__(self, author_id: int):
        super().__init__(timeout=60)
        self.author_id = author_id

    @discord.ui.button(label="Confirm Delete", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("You cannot confirm this action.", ephemeral=True)
            return
        try:
            from services.database import get_db
            conn = get_db()
            try:
                conn.execute('DELETE FROM scrobbles WHERE discord_id = ?', (self.author_id,))
                conn.execute('DELETE FROM user_guilds WHERE discord_id = ?', (self.author_id,))
                conn.execute('DELETE FROM users WHERE discord_id = ?', (self.author_id,))
                conn.commit()
            finally:
                conn.close()
            await interaction.response.edit_message(content="Your account and all data have been deleted.", view=None)
        except Exception as e:
            logger.error("Failed to unlink user %s: %s", self.author_id, e)
            await interaction.response.edit_message(content="Something went wrong while deleting your account. Try again later.", view=None)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("You cannot cancel this action.", ephemeral=True)
            return
        await interaction.response.edit_message(content="Account deletion cancelled.", view=None)


class TransactionPaginationView(discord.ui.View):
    def __init__(self, author_id: int, username: str, rows: list[dict]):
        super().__init__(timeout=180)
        self.author_id = author_id
        self.username = username
        self.rows = rows
        self.page = 0
        self.total_pages = max(1, (len(rows) + TXN_PAGE_SIZE - 1) // TXN_PAGE_SIZE)
        if self.total_pages <= 1:
            self.clear_items()
        self._update_buttons()

    def _update_buttons(self):
        self.previous_page.disabled = self.page == 0
        self.next_page.disabled = self.page >= self.total_pages - 1

    def build_embed(self) -> discord.Embed:
        start = self.page * TXN_PAGE_SIZE
        end = start + TXN_PAGE_SIZE
        page_rows = self.rows[start:end]

        lines = []
        for row in page_rows:
            date_fmt = datetime.datetime.strptime(row['scrobble_date'], '%Y%m%d').strftime('%d/%m/%Y')
            count = row.get('count', 1)
            count_str = f" x{count}" if count > 1 else ""
            lines.append(f"**{row['artist_name']}**{count_str} — {date_fmt} — {max(row['purchase_price'], 10)} pts")

        body = "\n".join(lines)
        embed = discord.Embed(
            title=f"{self.username}'s Transactions",
            description=body,
            color=discord.Color.blue()
        )
        embed.set_footer(text=f"Page {self.page + 1}/{self.total_pages}")
        return embed

    @discord.ui.button(label="◀️", style=discord.ButtonStyle.secondary)
    async def previous_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.page > 0:
            self.page -= 1
            self._update_buttons()
            await interaction.response.edit_message(embed=self.build_embed(), view=self)
        else:
            await interaction.response.defer()

    @discord.ui.button(label="▶️", style=discord.ButtonStyle.secondary)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.page < self.total_pages - 1:
            self.page += 1
            self._update_buttons()
            await interaction.response.edit_message(embed=self.build_embed(), view=self)
        else:
            await interaction.response.defer()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.author_id


class StockCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot


    @app_commands.command(name="claim", description="Claim your daily portfolio value")
    async def slash_claim(self, interaction: discord.Interaction):
        guild_id = interaction.guild.id if interaction.guild else 0
        user_row = get_user_in_guild(interaction.user.id, guild_id)
        if not user_row:
            await interaction.response.send_message(
                f"{interaction.user.name}, set up your account first by setting your Last.fm username.",
                ephemeral=True
            )
            return

        last_claim = int(user_row['last_claim'] or 0)
        now_ts = int(datetime.datetime.now(datetime.timezone.utc).timestamp())
        if now_ts - last_claim < 86400:
            remaining = 86400 - (now_ts - last_claim)
            hours = remaining // 3600
            minutes = (remaining % 3600) // 60
            await interaction.response.send_message(
                f"{interaction.user.mention}, you can only claim once every 24 hours. Try again in {hours}h {minutes}m."
            )
            return

        allowed, remaining = check_lastfm_cooldown(interaction.user.id)
        if not allowed:
            await interaction.response.send_message(
                f"Please wait {remaining}s before using Last.fm again.",
                ephemeral=True
            )
            return

        await interaction.response.defer()
        logger.info("Fetching money for user: %s", interaction.user.name)
        try:
            user = await validate_lastfm_user(user_row['lastfm_username'])
        except Exception as e:
            logger.error("Failed to fetch Last.fm user %s: %s", user_row['lastfm_username'], e)
            await interaction.followup.send(
                f"{interaction.user.mention}, could not reach Last.fm right now. Try again later."
            )
            return
        try:
            total_money, gain_loss = await process_user_claim(user, interaction.user.id, guild_id)
            multiplier = get_claim_multiplier(interaction.user.id)
            gain_str = f" (+{gain_loss:.2f}%)" if gain_loss >= 0 else f" ({gain_loss:.2f}%)"
            if multiplier > 1.0:
                await interaction.followup.send(
                    f"{interaction.user.mention}, your portfolio is worth **{total_money:.2f}€**{gain_str} (×{multiplier} listening bonus)"
                )
            else:
                await interaction.followup.send(f"{interaction.user.mention}, your portfolio is worth **{total_money:.2f}€**{gain_str}")
        except Exception as e:
            logger.error("Failed to process claim for user %s: %s", interaction.user.id, e)
            if isinstance(e, LastFMPrivacyError):
                await interaction.followup.send(
                    f"{interaction.user.mention}, your Last.fm account has privacy settings enabled that prevent others from seeing your streaming history. Please enable public access to your recent tracks in your Last.fm privacy settings."
                )
            else:
                await interaction.followup.send(
                    f"{interaction.user.mention}, something went wrong while processing your claim. Try again later."
                )


    @app_commands.command(name="check", description="Recalculate your portfolio value (1h cooldown, admin bypass)")
    async def slash_check(self, interaction: discord.Interaction):
        guild_id = interaction.guild.id if interaction.guild else 0
        user_row = get_user_in_guild(interaction.user.id, guild_id)
        if not user_row:
            await interaction.response.send_message(
                f"{interaction.user.name}, set up your account first by setting your Last.fm username.",
                ephemeral=True
            )
            return

        now_ts = int(datetime.datetime.now(datetime.timezone.utc).timestamp())
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

        allowed, remaining = check_lastfm_cooldown(interaction.user.id)
        if not allowed:
            await interaction.response.send_message(
                f"Please wait {remaining}s before using Last.fm again.",
                ephemeral=True
            )
            return

        await interaction.response.defer()
        try:
            today_str = datetime.datetime.now(datetime.timezone.utc).date().isoformat().replace('-', '')
            total_money, gain_loss = calculate_portfolio_value(interaction.user.id, today_str)
            gain_str = f" (+{gain_loss:.2f}%)" if gain_loss >= 0 else f" ({gain_loss:.2f}%)"
            update_user_money(interaction.user.id, total_money)
            if not is_admin:
                update_last_preview(interaction.user.id, now_ts)
            await interaction.followup.send(f"{interaction.user.mention}, your portfolio is worth **{total_money:.2f}€**{gain_str}")
        except Exception as e:
            logger.error("Failed to check portfolio for user %s: %s", interaction.user.id, e)
            if isinstance(e, LastFMPrivacyError):
                await interaction.followup.send(
                    f"{interaction.user.mention}, your Last.fm account has privacy settings enabled that prevent others from seeing your streaming history. Please enable public access to your recent tracks in your Last.fm privacy settings."
                )
            else:
                await interaction.followup.send(
                    f"{interaction.user.mention}, something went wrong while checking your portfolio. Try again later."
                )


    @app_commands.command(name="balance", description="View your balance")
    async def slash_balance(self, interaction: discord.Interaction):
        guild_id = interaction.guild.id if interaction.guild else 0
        user_row = get_user_in_guild(interaction.user.id, guild_id)
        if not user_row:
            await interaction.response.send_message(
                f"{interaction.user.name}, set up your account first by setting your Last.fm username.",
                ephemeral=True
            )
            return

        await interaction.response.defer()
        try:
            today_str = datetime.datetime.now(datetime.timezone.utc).date().isoformat().replace('-', '')
            yesterday_str = (datetime.datetime.now(datetime.timezone.utc).date() - datetime.timedelta(days=1)).isoformat().replace('-', '')

            stats = get_balance_stats(interaction.user.id, today_str, yesterday_str)

            embed = discord.Embed(
                title="Portfolio Balance",
                color=discord.Color.blue()
            )

            embed.add_field(name="💰 Value", value=f"{stats['total_value']:.2f}€", inline=True)
            embed.add_field(name="📊 Shares", value=str(stats['total_shares']), inline=True)
            embed.add_field(name="🎨 Artists", value=str(stats['diversity']), inline=True)

            if stats['overall_gain'] >= 0:
                embed.add_field(name="📈 Overall", value=f"+{stats['overall_gain']:.2f}%", inline=True)
            else:
                embed.add_field(name="📉 Overall", value=f"{stats['overall_gain']:.2f}%", inline=True)

            if stats['today_change'] is not None:
                if stats['today_change'] >= 0:
                    embed.add_field(name="📅 Today", value=f"+{stats['today_change']:.2f}%", inline=True)
                else:
                    embed.add_field(name="📅 Today", value=f"{stats['today_change']:.2f}%", inline=True)
            else:
                embed.add_field(name="📅 Today", value="N/A", inline=True)

            embed.add_field(name="🏆 Top Holding", value=stats['top_holding'], inline=True)

            await interaction.followup.send(embed=embed)
        except Exception as e:
            logger.error("Failed to get balance for user %s: %s", interaction.user.id, e)
            await interaction.followup.send(
                f"{interaction.user.mention}, something went wrong while fetching your balance. Try again later."
            )


    @app_commands.command(name="portfolio", description="View your portfolio breakdown")
    async def slash_portfolio(self, interaction: discord.Interaction):
        guild_id = interaction.guild.id if interaction.guild else 0
        user_row = get_user_in_guild(interaction.user.id, guild_id)
        if not user_row:
            await interaction.response.send_message(
                f"{interaction.user.name}, set up your account first by setting your Last.fm username.",
                ephemeral=True
            )
            return

        await interaction.response.defer()
        try:
            today_str = datetime.datetime.now(datetime.timezone.utc).date().isoformat().replace('-', '')
            breakdown = get_portfolio_breakdown(interaction.user.id, today_str)
            if not breakdown:
                await interaction.followup.send(f"{interaction.user.mention}, you have no shares yet.")
                return

            total_value = sum(item['current_value'] for item in breakdown)
            total_shares = sum(item['shares'] for item in breakdown)
            total_base = BASE_SHARE_VALUE * total_shares
            total_gain_percent = ((total_value - total_base) / total_base * 100) if total_base > 0 else 0.0

            view = PortfolioView(interaction.user.id, breakdown, total_value, total_shares, total_gain_percent)
            await interaction.followup.send(embed=view.build_embed(), view=view)
        except Exception as e:
            logger.error("Failed to get portfolio for user %s: %s", interaction.user.id, e)
            await interaction.followup.send(
                f"{interaction.user.mention}, something went wrong while fetching your portfolio. Try again later."
            )


    @app_commands.command(name="allocation", description="View your portfolio allocation as a pie chart")
    async def slash_allocation(self, interaction: discord.Interaction):
        guild_id = interaction.guild.id if interaction.guild else 0
        user_row = get_user_in_guild(interaction.user.id, guild_id)
        if not user_row:
            await interaction.response.send_message(
                f"{interaction.user.name}, set up your account first by setting your Last.fm username.",
                ephemeral=True
            )
            return

        await interaction.response.defer()
        try:
            today_str = datetime.datetime.now(datetime.timezone.utc).date().isoformat().replace('-', '')
            breakdown = get_portfolio_breakdown(interaction.user.id, today_str)
            if not breakdown:
                await interaction.followup.send(f"{interaction.user.mention}, you have no shares yet.")
                return

            total_value = sum(item['current_value'] for item in breakdown)
            try:
                chart_path = generate_allocation_chart(breakdown, total_value)
            except Exception as e:
                logger.error("Failed to generate allocation chart: %s", e)
                await interaction.followup.send("Could not generate chart right now. Try again later.")
                return
            if chart_path:
                try:
                    embed = discord.Embed(
                        title="Portfolio Allocation",
                        description="Your holdings by artist value",
                        color=discord.Color.blue()
                    )
                    file = discord.File(chart_path, filename='allocation.png')
                    embed.set_image(url='attachment://allocation.png')
                    await interaction.followup.send(embed=embed, file=file)
                finally:
                    os.unlink(chart_path)
            else:
                await interaction.followup.send("Need at least 2 artists to show allocation.")
        except Exception as e:
            logger.error("Failed to get allocation for user %s: %s", interaction.user.id, e)
            await interaction.followup.send(
                f"{interaction.user.mention}, something went wrong while generating your allocation chart. Try again later."
            )


    @app_commands.command(name="leaderboard", description="View the leaderboard")
    async def slash_leaderboard(self, interaction: discord.Interaction):
        guild_id = interaction.guild.id if interaction.guild else 0
        await interaction.response.defer()
        try:
            from services.database import get_db
            conn = get_db()
            try:
                rows = conn.execute(
                    '''SELECT u.username, u.money
                       FROM users u
                       JOIN user_guilds ug ON u.discord_id = ug.discord_id
                       WHERE ug.guild_id = ?
                       ORDER BY u.money DESC
                       LIMIT 10''',
                    (guild_id,)
                ).fetchall()
            finally:
                conn.close()

            if not rows:
                embed = discord.Embed(
                    title="Leaderboard",
                    description="No users yet.",
                    color=discord.Color.gold()
                )
                await interaction.followup.send(embed=embed)
                return

            lines = []
            for rank, row in enumerate(rows, start=1):
                lines.append(f"**{rank}.** {row['username']} — {row['money']:.2f}€")

            embed = discord.Embed(
                title="Leaderboard",
                description="\n".join(lines),
                color=discord.Color.gold()
            )
            await interaction.followup.send(embed=embed)
        except Exception as e:
            logger.error("Failed to get leaderboard for guild %s: %s", guild_id, e)
            await interaction.followup.send(
                "Something went wrong while fetching the leaderboard. Try again later."
            )


    @app_commands.command(name="lastfm", description="Set your Last.fm username")
    async def slash_lastfm(self, interaction: discord.Interaction, lastfm_username: str):
        if not lastfm_username.strip():
            await interaction.response.send_message("Please provide a valid Last.fm username.", ephemeral=True)
            return
        allowed, remaining = check_lastfm_cooldown(interaction.user.id)
        if not allowed:
            await interaction.response.send_message(
                f"Please wait {remaining}s before using Last.fm again.",
                ephemeral=True
            )
            return
        await interaction.response.defer()
        guild_id = interaction.guild.id if interaction.guild else 0
        try:
            await validate_lastfm_user(lastfm_username)
            insert_user(interaction.user.id, interaction.user.name, lastfm_username, guild_id=guild_id)
            await interaction.followup.send(f"{interaction.user.mention}, your Last.fm username has been set to {lastfm_username}.")
        except Exception as e:
            logger.error("Failed to validate Last.fm user %s: %s", lastfm_username, e)
            await interaction.followup.send("Last.fm user not found or could not be reached.", ephemeral=True)


    @app_commands.command(name="unlink", description="Delete your account and all data")
    async def slash_unlink(self, interaction: discord.Interaction):
        guild_id = interaction.guild.id if interaction.guild else 0
        user_row = get_user(interaction.user.id)
        if not user_row:
            await interaction.response.send_message("You don't have an account linked.", ephemeral=True)
            return

        view = ConfirmUnlinkView(interaction.user.id)
        await interaction.response.send_message(
            "⚠️ This will permanently delete your account, all scrobbles, and portfolio data. Are you sure?",
            view=view,
            ephemeral=True
        )


    @app_commands.command(name="artist", description="Look up an artist's stock info")
    async def slash_artist(self, interaction: discord.Interaction, artist_name: str):
        if not artist_name.strip() or len(artist_name) > 100:
            await interaction.response.send_message("Please provide a valid artist name.", ephemeral=True)
            return
        guild_id = interaction.guild.id if interaction.guild else 0
        today_str = datetime.datetime.now(datetime.timezone.utc).date().isoformat().replace('-', '')
        allowed, remaining = check_lastfm_cooldown(interaction.user.id)
        if not allowed:
            await interaction.response.send_message(
                f"Please wait {remaining}s before checking another artist.",
                ephemeral=True
            )
            return
        await interaction.response.defer()
        try:
            info = await get_artist_info(artist_name, today_str)
        except Exception as e:
            logger.error("Failed to fetch artist info for %s: %s", artist_name, e)
            await interaction.followup.send(
                f"Could not look up **{artist_name}** right now. Try again later.",
                ephemeral=True
            )
            return
        if not info:
            await interaction.followup.send(
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

        await interaction.followup.send(embed=embed)


    @app_commands.command(name="history", description="View an artist's velocity history")
    async def slash_history(self, interaction: discord.Interaction, artist_name: str):
        if not artist_name.strip() or len(artist_name) > 100:
            await interaction.response.send_message("Please provide a valid artist name.", ephemeral=True)
            return
        allowed, remaining = check_lastfm_cooldown(interaction.user.id)
        if not allowed:
            await interaction.response.send_message(
                f"Please wait {remaining}s before checking another artist.",
                ephemeral=True
            )
            return
        await interaction.response.defer()
        try:
            history = get_artist_price_history(artist_name)
            if not history:
                await interaction.followup.send(f"No price history found for **{artist_name}**.")
                return

            lines = []
            prev_daily_total = None
            for entry in history:
                date_fmt = datetime.datetime.strptime(entry['date'], '%Y%m%d').strftime('%d/%m/%Y')
                daily_total = entry['daily_total']
                if prev_daily_total is None:
                    trend = "➡️"
                elif daily_total > prev_daily_total:
                    trend = "📈"
                elif daily_total < prev_daily_total:
                    trend = "📉"
                else:
                    trend = "➡️"
                prev_daily_total = daily_total
                value = daily_total / 100_000
                lines.append(f"{trend} {date_fmt}: {format_daily_total(daily_total)} pts ({value:.2f}€)")

            body = "\n".join(lines)
            embed = discord.Embed(
                title=f"Price History: {history[-1].get('artist_name', artist_name)}",
                description=body,
                color=discord.Color.blue()
            )
            await interaction.followup.send(embed=embed)
        except Exception as e:
            logger.error("Failed to get price history for %s: %s", artist_name, e)
            await interaction.followup.send(
                f"Something went wrong while fetching price history for **{artist_name}**. Try again later."
            )


    @app_commands.command(name="transactions", description="View your transaction history")
    async def slash_transactions(self, interaction: discord.Interaction, artist_name: str = None):
        guild_id = interaction.guild.id if interaction.guild else 0
        user_row = get_user_in_guild(interaction.user.id, guild_id)
        if not user_row:
            await interaction.response.send_message(
                f"{interaction.user.name}, set up your account first by setting your Last.fm username.",
                ephemeral=True
            )
            return

        rows = get_transactions(interaction.user.id, artist_name)
        if not rows:
            if artist_name:
                await interaction.response.send_message(f"No transactions found for **{artist_name}**.", ephemeral=True)
            else:
                await interaction.response.send_message("No transactions yet.", ephemeral=True)
            return

        if artist_name:
            view = TransactionPaginationView(interaction.user.id, interaction.user.name, rows)
            await interaction.response.send_message(embed=view.build_embed(), view=view)
        else:
            grouped = {}
            for row in rows:
                name = row['artist_name']
                if name not in grouped:
                    grouped[name] = []
                grouped[name].append(row)

            view = TransactionsView(interaction.user.id, interaction.user.name, grouped, artist_name)
            await interaction.response.send_message(embed=view.build_embed(), view=view)


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
                "• An artist's share price = their scrobble velocity\n"
                "• When more people listen to an artist, the price goes up — your shares gain value\n"
                "• The more scrobbles you have for an artist, the more shares you own\n"
                "• Claim daily to keep accumulating shares\n"
                "• Price changes happen once per day, so patience pays off\n\n"
                "**Listening bonus:**\n"
                "• Your 7-day average daily scrobbles give you a claim multiplier\n"
                "• 100+ avg/day → ×1.2 bonus\n"
                "• 50+ avg/day → ×1.1 bonus\n"
                "• Below 50 → ×1.0 (no bonus)"
            ),
            color=discord.Color.blue()
        )
        await interaction.response.send_message(embed=embed)


    @app_commands.command(name="market", description="View market overview: top gainers, losers, and most held artists")
    @app_commands.choices(period=[
        app_commands.Choice(name="Day", value="day"),
        app_commands.Choice(name="Week", value="week"),
        app_commands.Choice(name="Month", value="month"),
        app_commands.Choice(name="Year", value="year"),
        app_commands.Choice(name="Alltime", value="alltime"),
    ])
    async def slash_market(self, interaction: discord.Interaction, period: str = "day"):
        allowed, remaining = check_lastfm_cooldown(interaction.user.id)
        if not allowed:
            await interaction.response.send_message(
                f"Please wait {remaining}s before checking the market again.",
                ephemeral=True
            )
            return
        await interaction.response.defer()
        try:
            guild_id = interaction.guild.id if interaction.guild else 0
            days_map = {"day": 1, "week": 7, "month": 30, "year": 365, "alltime": "alltime"}
            days = days_map.get(period, 1)
            overview = get_market_overview(guild_id, days=days)

            period_label = {"day": "Daily", "week": "Weekly", "month": "Monthly", "year": "Yearly", "alltime": "All-time"}.get(period, "Market")

            sections = []

            if overview['gainers']:
                lines = []
                for entry in overview['gainers']:
                    if entry.get('change_percent', 0) == 0:
                        continue
                    lines.append(f"📈 **{entry['artist_name']}**: {entry['current_share_value']:.2f}€ ({entry['change_value']:+.2f}€ / {entry['change_percent']:+.2f}%)")
                if lines:
                    sections.append("**Top Gainers**\n" + "\n".join(lines))

            if overview['losers']:
                lines = []
                for entry in overview['losers']:
                    if entry.get('change_percent', 0) == 0:
                        continue
                    lines.append(f"📉 **{entry['artist_name']}**: {entry['current_share_value']:.2f}€ ({entry['change_value']:+.2f}€ / {entry['change_percent']:+.2f}%)")
                if lines:
                    sections.append("**Top Losers**\n" + "\n".join(lines))

            if overview['most_held']:
                lines = []
                for entry in overview['most_held']:
                    lines.append(f"🏦 **{entry['artist_name']}**: {entry['count']} shares")
                sections.append("**Most Held**\n" + "\n".join(lines))

            if not sections:
                rankings = get_stock_rankings(limit=10)
                if rankings['most_valuable']:
                    lines = []
                    for entry in rankings['most_valuable'][:5]:
                        lines.append(f"📈 **{entry['artist_name']}**: {entry['current_share_value']:.2f}€")
                    sections.append("**Most Valuable Stocks**\n" + "\n".join(lines))
                if rankings['least_valuable']:
                    lines = []
                    for entry in rankings['least_valuable'][:5]:
                        lines.append(f"📉 **{entry['artist_name']}**: {entry['current_share_value']:.2f}€")
                    sections.append("**Least Valuable Stocks**\n" + "\n".join(lines))

            if not sections:
                await interaction.followup.send("No market data available yet.")
                return

            body = "\n\n".join(sections)
            embed = discord.Embed(
                title=f"{period_label} Market Overview",
                description=body,
                color=discord.Color.blue()
            )
            await interaction.followup.send(embed=embed)
        except Exception as e:
            logger.error("Failed to get market overview for guild %s: %s", guild_id, e)
            await interaction.followup.send(
                "Something went wrong while fetching the market overview. Try again later."
            )

    @app_commands.command(name="stocks", description="View most and least valuable stocks")
    async def slash_stocks(self, interaction: discord.Interaction):
        allowed, remaining = check_lastfm_cooldown(interaction.user.id)
        if not allowed:
            await interaction.response.send_message(
                f"Please wait {remaining}s before checking the market again.",
                ephemeral=True
            )
            return
        await interaction.response.defer()
        try:
            rankings = get_stock_rankings(limit=10)

            sections = []

            if rankings['most_valuable']:
                lines = []
                for entry in rankings['most_valuable']:
                    lines.append(f"📈 **{entry['artist_name']}**: {entry['current_share_value']:.2f}€")
                sections.append("**Most Valuable Stocks**\n" + "\n".join(lines))

            if rankings['least_valuable']:
                lines = []
                for entry in rankings['least_valuable']:
                    lines.append(f"📉 **{entry['artist_name']}**: {entry['current_share_value']:.2f}€")
                sections.append("**Least Valuable Stocks**\n" + "\n".join(lines))

            if not sections:
                await interaction.followup.send("No stock data available yet.")
                return

            body = "\n\n".join(sections)
            embed = discord.Embed(
                title="Stock Rankings",
                description=body,
                color=discord.Color.blue()
            )
            await interaction.followup.send(embed=embed)
        except Exception as e:
            logger.error("Failed to get stock rankings: %s", e)
            await interaction.followup.send(
                "Something went wrong while fetching the stock rankings. Try again later."
            )


    @app_commands.command(name="help", description="Show all commands")
    async def slash_help(self, interaction: discord.Interaction):
        help_message = (
            "**Available commands:**\n"
            "/claim - Claim your daily portfolio value\n"
            "/leaderboard - View the leaderboard\n"
            "/lastfm <username> - Link your Last.fm account\n"
            "/unlink - Delete your account and all data\n"
            "/check - Recalculate your portfolio value (1h cooldown)\n"
            "/balance - View your balance\n"
            "/portfolio - View your portfolio breakdown\n"
            "/allocation - View your portfolio allocation pie chart\n"
            "/artist <name> - Look up an artist's stock info\n"
            "/history <name> - View an artist's price history\n"
            "/transactions [artist] - View your transaction history\n"
            "/market - View market overview\n"
            "/stocks - View most and least valuable stocks\n"
            "/marketconfig - Configure daily market summary (admin)\n"
            "/rules - How to play\n"
        )
        embed = discord.Embed(
            title="Help",
            description=help_message,
            color=discord.Color.blue()
        )
        await interaction.response.send_message(embed=embed)


    @app_commands.command(name="marketconfig", description="Configure the daily market summary channel and time (admin only)")
    @app_commands.choices(time=[
        app_commands.Choice(name=f"{h:02d}:00", value=f"{h:02d}:00") for h in range(24)
    ])
    @app_commands.choices(timezone=[
        app_commands.Choice(name=f"UTC{offset:+.0f}", value=f"{offset:+d}") for offset in range(-12, 13)
    ])
    async def slash_marketconfig(self, interaction: discord.Interaction, channel: discord.TextChannel, time: str, timezone: str):
        is_admin = interaction.user.guild_permissions.administrator if interaction.guild else False
        if not is_admin:
            await interaction.response.send_message("You need administrator permissions to use this command.", ephemeral=True)
            return

        hour = int(time.split(':')[0])
        guild_id = interaction.guild.id if interaction.guild else 0
        set_guild_config(guild_id, channel.id, hour, timezone)
        from bot import invalidate_guild_config_cache
        invalidate_guild_config_cache()

        tz_str = f"UTC{timezone}"
        await interaction.response.send_message(
            f"Daily market summary configured: channel {channel.mention}, will fire at {time} your local time ({tz_str})",
            ephemeral=True
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(StockCommands(bot))
