# 🤖 Crypto Screener Pro

TradingView alerts → Claude LLM analysis → Telegram bot

**Features:** SMC/ICT signals · CVD · Volume Profile · MTF EMA confluence · Fear&Greed · Confluence Score · Chart screenshot analysis

---

## Quick Start (local)

```bash
git clone https://github.com/YOUR_USERNAME/crypto-screener
cd crypto-screener

python3 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt

cp config.example.py config.py
# Edit config.py — fill in your tokens

python screener.py
# In a second terminal:
ngrok http 5001
```

---

## Setup: get your tokens

| Token | Where to get |
|-------|-------------|
| `TELEGRAM_TOKEN` | [@BotFather](https://t.me/BotFather) → /newbot |
| `TELEGRAM_CHAT_ID` | [@userinfobot](https://t.me/userinfobot) — send any message |
| `ANTHROPIC_API_KEY` | [console.anthropic.com](https://console.anthropic.com) → API Keys |

---

## TradingView alert format (JSON body)

```json
{
  "signal": "BOS_BULL",
  "symbol": "{{ticker}}",
  "price":  "{{close}}",
  "tf":     "{{interval}}"
}
```

Webhook URL: `https://YOUR-NGROK-OR-SERVER.app/webhook`

**Supported signal types:** `BOS_BULL` · `BOS_BEAR` · `CHOCH_BULL` · `CHOCH_BEAR` · `OB_BULL` · `OB_BEAR` · `FVG_BULL` · `FVG_BEAR` · `LIQ_SWEEP_H` · `LIQ_SWEEP_L` · `TURTLE_LONG` · `TURTLE_SHORT` · `ICT_NY_OPEN` · `ICT_LONDON` · `ICT_KILLZONE` · `DAILY_OPEN` · `WEEKLY_OPEN`

---

## Telegram bot commands

| Command | Description |
|---------|-------------|
| `/status` | Live market: price, CVD, Volume Profile, MTF EMA, Fear&Greed, session |
| `/ask [question]` | Ask Claude anything about current market |
| `/history` | Last 10 signals from DB |
| `/digest` | Daily summary with LLM analysis |
| 📸 **Send a photo** | Chart screenshot analysis — Claude compares your view with live data |

**Photo tip:** add a caption like `BTC 4H — thinking short from here` and Claude will compare your analysis with real market data.

---

## Deploy free on Railway

1. Push this repo to GitHub (your `config.py` is gitignored — safe)
2. Go to [railway.app](https://railway.app) → New Project → Deploy from GitHub
3. Add environment variables in Railway dashboard:

```
TELEGRAM_TOKEN     = your_token
TELEGRAM_CHAT_ID   = your_chat_id
ANTHROPIC_API_KEY  = your_key
```

4. (Optional) Add a **Volume** at `/app` for persistent SQLite, set `DB_PATH=/data/signals.db`
5. Deploy — Railway auto-detects Python and runs `Procfile`

**Cost:** $5 free credits/month ≈ enough for a full month of light usage.

---

## Deploy free on Fly.io

```bash
fly auth login
fly launch          # auto-detects Python
fly secrets set TELEGRAM_TOKEN=xxx TELEGRAM_CHAT_ID=xxx ANTHROPIC_API_KEY=xxx
fly deploy
```

---

## Architecture

```
TradingView alert (JSON)
        ↓
  Flask /webhook
        ↓
  fetch_market()  ←── Bybit API (price, OI, klines)
        |         ←── Hyperliquid API (funding, book, trades)
        |         ←── CoinGecko (BTC dominance)  [cached 15m]
        |         ←── alternative.me (Fear&Greed) [cached 15m]
        ↓
  compute: CVD · Volume Profile · EMA biases · Confluence Score
        ↓
  Claude LLM (Haiku) → signal analysis + quality score
        ↓
  Telegram message
        
Telegram polling thread → handles commands + photo analysis (Claude Sonnet)
Scheduler thread        → daily digest at configured UTC time
```

---

## Environment variables reference

| Variable | Default | Description |
|----------|---------|-------------|
| `TELEGRAM_TOKEN` | — | Required |
| `TELEGRAM_CHAT_ID` | — | Required |
| `ANTHROPIC_API_KEY` | — | Required |
| `LLM_MODEL_FAST` | `claude-haiku-4-5-20251001` | For signal analysis |
| `LLM_MODEL_SMART` | `claude-sonnet-4-6` | For /ask and chart analysis |
| `PORT` | `5001` | Flask server port |
| `SYMBOLS` | `BTCUSDT,ETHUSDT` | Comma-separated pairs for /status |
| `DIGEST_TIME` | `08:00` | Daily digest time (UTC) |
| `DB_PATH` | `signals.db` | SQLite database path |
| `MIN_QUALITY` | `0` | Min LLM quality score to send (0 = all) |
