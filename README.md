# Last.fm Stock Exchange Discord Bot

A Discord bot that turns Last.fm listening history into a scrobble-velocity market. Every scrobbled artist is a share purchase, priced by community daily scrobble totals.

## How It Works

- **Share = 1 scrobble**. Each time you claim, new scrobbles become shares in that artist's stock.
- **Price = scrobble velocity**. Share value is based on the artist's daily total scrobbles across all users — momentum and volatility drive price changes.
- **Base value = 10.00€**. Every share starts at a base value of 10.00€, scaled by scrobble velocity change.
- **24h cooldown**. Claim once per day to update your portfolio.

### Share Valuation

```python
current_share_value = 10.00€ * (current_velocity / purchase_velocity)
portfolio_value = sum of all current_share_values
gain_loss_percent = ((current_velocity / purchase_velocity) - 1) * 100
```

## Commands

| Command                  | Description                                              |
| ------------------------ | -------------------------------------------------------- |
| `/claim`                 | Claim your portfolio value (24h cooldown)                |
| `/check`                 | Recalculate without claiming (1h cooldown, admin bypass) |
| `/portfolio`             | View your portfolio breakdown                            |
| `/allocation`            | View your portfolio as a pie chart                       |
| `/artist <name>`         | Look up an artist's stock info                           |
| `/history <name>`        | View an artist's velocity history                  |
| `/transactions [artist]` | View your transaction history                            |
| `/market`                | Market overview: top gainers, losers, most held          |
| `/marketconfig`          | Configure daily market summary channel and time (admin)  |
| `/lastfm <username>`     | Link your Last.fm account                                |
| `/leaderboard`           | Top portfolios by value                                  |
| `/rules`                 | How to play                                              |
| `/help`                  | Show all commands                                        |

## Setup

### Prerequisites

- Python 3.9+
- Discord bot token ([Discord Developer Portal](https://discord.com/developers/applications))
- Last.fm API key ([Last.fm API](https://www.last.fm/api/account/create))

### Installation

```bash
git clone <repo-url>
cd lastfm-stock-exchange-discord-bot
pip install -r requirements.txt
```

### Configuration

```bash
cp .env.example .env
```

Edit `.env` with your credentials:

```env
LASTFM_API_KEY=your_key
LASTFM_API_SECRET=your_secret
DISCORD_TOKEN=your_token
```

### Run

```bash
python bot.py
```

## Database

Uses SQLite (`db.sqlite3`). Schema:

- **`users`** — Discord user, linked Last.fm, money, last claim timestamp, guild
- **`scrobbles`** — Artist shares purchased (artist, purchase price, date, play count)
- **`artist_scrobbles`** — Daily scrobble totals per artist (artist, daily_total, date)
- **`guild_config`** — Per-server market summary channel, hour, and timezone

## Behavior

- Artist scrobble snapshots are computed on-demand when users claim or view portfolio data.
- Snapshots are cached per day, so the first user computes the price; subsequent users reuse cached data.
- Daily database backup runs automatically while the bot is online.
- Daily market summary is posted to configured channels at the scheduled time.

## Testing

```bash
python -m pytest tests/ -v
```
