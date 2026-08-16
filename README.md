# Last.fm Stock Exchange Discord Bot

A Discord bot that turns Last.fm listening history into a stock market simulation. Every scrobbled track is a share purchase in that artist's "stock," priced by Last.fm listener count. Famous artists behave like blue-chip stocks; niche artists behave like high-risk penny stocks.

## How It Works

- **Share = 1 scrobble**. Each time you claim, new scrobbles since your last claim are recorded as shares.
- **Normalized share value**. Every share is purchased at a fixed base value of **1.00€**. Share value changes based on the **percentage change** in the artist's Last.fm listener count, not the absolute count. This makes blue-chip artists low-volatility and niche artists high-risk/high-reward.
- **Real portfolio math**. Your total money is the sum of current share values. Positive gain/loss percentage means your portfolio is up; negative means it's down.
- **24h cooldown**. You can only claim once every 24 hours, so the market updates once per day per user.

### Share Valuation Formula

```python
current_share_value = 1.00€ * (current_listeners / purchase_listeners)
portfolio_value = sum of all current_share_values
gain_loss_percent = ((current_listeners / purchase_listeners) - 1) * 100
```

### Why Percentage-Based?

| Scenario | Old Model (absolute) | New Model (percentage) |
|----------|---------------------|------------------------|
| Taylor Swift: 15M → 15.1M | +1.00€ | +0.67€ (+0.67%) |
| Niche artist: 100k → 200k | +1.00€ | +2.00€ (+100%) |
| Breakout artist: 10k → 500k | +4.90€ | +50.00€ (+4900%) |
| Artist crash: 100k → 50k | -0.50€ | -0.50€ (-50%) |

The new model rewards discovering artists before they blow up, and punishes holding crashing artists proportionally. Famous artists have large absolute listener counts but small percentage moves, so they behave like stable blue-chip stocks.

## Commands

All commands are available as both prefix (`!`) and slash (`/`) commands.

| Command                       | Description                                       |
| ----------------------------- | ------------------------------------------------- |
| `!claim` / `/claim`           | Claim your portfolio value (24h cooldown)         |
| `!check` / `/check`           | Recalculate portfolio value without claiming (1h cooldown, admin bypass) |
| `!portfolio` / `/portfolio`   | View portfolio breakdown with sorting             |
| `!artist <name>` / `/artist`  | Look up an artist's stock info                    |
| `!setlastfm <username>` / `/setlastfm` | Link your Last.fm account          |
| `!leaderboard` / `/leaderboard` | Top portfolios by value                          |
| `!help` / `/help`             | Show all commands                                 |

## Setup

### Prerequisites

- Python 3.9+
- A Discord bot token ([Discord Developer Portal](https://discord.com/developers/applications))
- A Last.fm API key ([Last.fm API](https://www.last.fm/api/account/create))

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

### Initialize Database

```bash
python init_db.py
```

### Optional: Seed Sample Data

For local testing without real Last.fm data:

```bash
python seed_db.py
```

### Run

```bash
python bot.py
```

## Architecture

```
lastfm-stock-exchange-discord-bot/
├── bot.py                    # Entry point, bot init, slash sync
├── config.py                 # Env vars, constants, Last.fm network
├── init_db.py                # SQLite schema initialization
├── seed_db.py                # Sample data for local testing
├── verify.py                 # Local portfolio verification
├── requirements.txt
├── LICENSE
├── README.md
├── .gitignore
├── .env
├── .env.example
└── db.sqlite3                # Local database (gitignored)
    ├── services/
    │   ├── __init__.py
    │   ├── database.py       # SQLite queries
    │   ├── lastfm.py         # Async Last.fm API wrappers with retry/backoff
    │   └── portfolio.py      # Portfolio valuation logic
    ├── cogs/
    │   ├── __init__.py
    │   └── commands.py       # All Discord commands + slash commands
    ├── utils/
    │   └── logging.py        # Centralized logging
    └── tests/
        ├── __init__.py
        └── test_services.py  # Unit tests for DB and portfolio logic
```

## Database

Uses SQLite (`db.sqlite3`). Schema:

- **`users`** — Discord username, linked Last.fm, money, last claim timestamp
- **`scrobbles`** — Each share purchased (artist, title, album, purchase price, date)
- **`artist_popularity`** — Daily listener count snapshots (artist, listeners, date)

## Rate Limits & Performance

Last.fm allows ~5 requests per second per API key. This bot is designed to stay well within those limits:

| Operation                        | Frequency            | API Calls                        |
| -------------------------------- | -------------------- | -------------------------------- |
| User claim (fetch new scrobbles) | 1 per user per day   | 1 per user                       |
| Artist snapshot (if not cached)  | 1 per artist per day | 1 per unique artist in portfolio |
| Rate limit delay between calls   | —                    | 0.2s                             |

**No midnight batch.** Artist snapshots are fetched on-demand when users claim, not via a scheduled background task. This means:

- 0 API calls when the bot is idle
- Artists only cost API calls if someone is actually holding them
- Snapshots are cached per day, so the first user pays the cost; subsequent users holding the same artists reuse the cached data

### Last.fm Data Freshness

Last.fm's listener counts are not real-time. Based on community observations, they update approximately **daily**, but can lag by 1-7 days for some artists. This is acceptable for a game/simulation — the portfolio trends correctly over time even with minor delays.

## Testing

```bash
python -m pytest tests/ -v
```

Runs unit tests for database operations, portfolio valuation, and euro conversion.

For local portfolio verification with seed data:

```bash
python verify.py
```

## Beta Scope

This is a beta release. Currently supported:

- Artist-level portfolio tracking with percentage-based valuation
- Normalized share model: every scrobble buys 1 share at 1.00€ base value
- Daily claim with 24h cooldown
- Portfolio check without claiming (1h cooldown, admin bypass)
- Portfolio breakdown with pagination and sorting by value/price/quantity
- Artist stock lookup with price appreciation/depreciation emojis
- On-demand artist snapshot caching
- Slash and prefix commands

Planned for future releases:

- Sell/offload shares
- Portfolio diversification stats
- Artist discovery recommendations

## License

MIT — see [LICENSE](LICENSE).
