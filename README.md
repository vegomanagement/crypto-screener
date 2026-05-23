# 🤖 Crypto Screener Pro

TradingView alerts → **deterministic engine** → Claude LLM explainer → Telegram + PNG chart

**Features:** Decision Engine (verdict + Entry/SL/TP/RR) · TradingView-style PNG charts · Bull/Bear/Risk multi-agent `/ask` · TP/SL outcome tracking · SMC/ICT signals · CVD · Volume Profile · MTF EMA confluence · Fear & Greed

---

## How it works

```
TradingView alert (JSON)
        ↓
   Flask /webhook
        ↓
   fetch_market()       ← 13 parallel API calls
        ↓
   compute_confluence_score()        (50-factor weighting)
        ↓
   ┌───────────────────────────────┐
   │  make_decision()              │  ← deterministic engine
   │   • parses signal direction   │
   │   • computes ATR-based levels │
   │   • applies veto rules        │
   │   → LONG / SHORT / WAIT / SKIP│
   │   → Entry / SL / TP1-3 / RR   │
   │   → confidence 0-100          │
   └───────────────────────────────┘
        ↓
   explain_signal()       (Haiku, 2-3 предложения, не флипает verdict)
        ↓
   render_signal_chart() → PNG (TradingView-style)
        ↓
   tg_send_photo()   (compact caption + chart)
        ↓
   tracking.open_trade() → signal_outcomes DB
        ↓ (background, every 10 min)
   check_trade_outcomes() → walk 5m klines → mark tp/sl hit
        ↓
   /stats → win-rate · avg R · breakdown by confidence-bucket
```

### Key guarantees
- **Engine = source of truth.** LLM cannot flip the verdict — only explains it. No more wishy-washy contradictory prose.
- **Hard veto gates:** `confluence < 55` → WAIT, `RR(TP1) < 1.5` → SKIP, ≥3 contradictions → WAIT.
- **Deterministic quality score** (was regex from LLM text → source of mismatch).
- **All trades tracked.** Real TP/SL outcomes calibrate the engine over time via `/stats`.

---

## Telegram bot commands

| Command | Description |
|---------|-------------|
| `/status [SYMBOL]` | Live market: price, CVD, Volume Profile, MTF EMA, Fear&Greed, full indicator dump |
| `/ask [question]` | **Multi-agent debate**: Bull / Bear / Risk (parallel Haiku) → Sonnet judge synthesizes answer |
| `/stats [days]` | **Engine-based win-rate**: TP/SL hits, avg R, breakdown by signal_type / symbol / confidence-bucket |
| `/history` | Last 10 signals from DB |
| `/digest` | Daily debrief — references real engine performance, not just prose |
| 📸 **Send a photo** | Chart screenshot analysis — compares user's view with engine verdict & objective data |

**Photo tip:** add a caption like `BTC 4H — думаю шорт отсюда` and the LLM will compare your analysis with engine-recommended levels.

---

## Signal message format

Each TradingView alert produces a compact message + TradingView-style PNG chart:

```
🔼 Bullish BOS · BTC/USDT.P · 1H · 11:22 UTC
💰 $42,500.00 (+1.20% 24h) · Bias: 🟢 BULLISH
📐 От TV: OB↑ $42,500
━━ Verdict ━━━━━━━━
🟢 LONG · RR(TP1): 1.5 · Confidence: 78/100
📍 Entry: 42,440.00 — 42,560.00
🛑 SL:    42,300.00
🎯 TP1:   42,800.00 (RR 1.5)
🎯 TP2:   43,000.00 (RR 2.5)
🎯 TP3:   43,300.00 (RR 4.0)
✅ За: CVD ✅ · MTF ✅ · VP POC
━━ Анализ ━━━━━━━━
Engine за лонг: CVD↑ совпадает с MTF bullish, цена у POC — реактивный
спрос. Цели 42,800/43,000 от ATR-сетки. Отменит сделку: пробой и
закрепление ниже 42,300.
━━ Контекст ━━━━━━
  • Цена $42,500 (+1.20% 24h)
  • CVD UP · MTF 1H:bull 4H:bull 1D:bull
  • Bybit FR +0.010% · OI Δ +1.20%
  • RSI 55 · MACD bull · ATR% 0.47
  • VP POC $42,400 · VAL/VAH $42,100–$42,700
  • F&G 55 · BTC.D 52.3%
ℹ️ Полный дамп: /status BTC
```

**WAIT** (when engine vetoes the trade):
```
━━ Verdict ━━━━━━━━
⚪ WAIT — переждать
💬 Confluence 21/100 < 55 — мало подтверждений
⚠️ Против: RSI 82 перекуплен · MTF: все 3 ТФ против · Funding +0.080% — лонги перегреты
```

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

For development:
```bash
pip install -r requirements-dev.txt
pytest                            # 99 unit tests
ruff check decision.py llm_agents.py chart.py tracking.py tests/
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

Optional fields: `ob_top`, `ob_bot`, `fvg_top`, `fvg_bot`, `target`, `stop` — engine uses them where applicable.

---

## Decision engine: how levels are computed

ATR-anchored (deterministic, no LLM):

```
entry_zone = price ± 0.3 × ATR     # для лимитной заявки
sl         = price ∓ 1.0 × ATR     # risk = 1.0 × ATR
tp1/2/3    = price ± 1.5 / 2.5 / 4.0 × ATR
→ RR = 1.5 / 2.5 / 4.0
```

Veto rules that force WAIT/SKIP:

| Rule | Result | Why |
|---|---|---|
| `confluence < 55` | **WAIT** | Недостаточно подтверждений |
| `RR(TP1) < 1.5` | **SKIP** | Невыгодное соотношение |
| ≥3 противоречия | **WAIT** | Слишком много vetoes |
| Нет ATR / нет цены | **SKIP** | Невозможно рассчитать риск |

Confidence-penalty per veto: RSI extreme (-20), MTF против (-15), funding overheated (-10), MACD против (-8), RSI divergence (-12), Turtle Zone extreme (-10).

---

## Multi-agent `/ask`

Three parallel Haiku agents → one Sonnet judge:

- **Bull** — best long thesis given current market
- **Bear** — best short thesis
- **Risk** — structural risks (macro, funding, BTC.D, vol events)

**Sonnet judge** synthesizes the final answer. If there's an active engine-verdict for the symbol, the judge **must** respect it (prompt-level veto). No more "diplomatic" answers that contradict the engine.

---

## Outcome tracking & `/stats`

Every LONG/SHORT signal is stored in `signal_outcomes` with full decision snapshot (Entry/SL/TP/RR/confidence). A background worker (every 10 min) walks 5m klines since entry and detects the first touch of SL or TP1/2/3.

`/stats [days]` output:
```
📊 Статистика 30 дней
━━━━━━━━━━━━━━━━━━━━
Всего: 42 · открыто: 5 · закрыто: 37
🟢 Win-rate: 65.0%  ·  🟢 Avg R: +0.83

По уровням:
  🎯 TP1 hit: 18 · TP2: 6 · TP3: 0
  🛑 SL hit:  13
  ⏰ Expired: 0

По confidence (калибровка engine):
  🟢 conf 75+      15 ·  73% · +1.50R
  🟡 conf 55-74    17 ·  53% · +0.40R
```

The confidence-bucket breakdown gives you ground truth for engine calibration over time.

---

## Deploy free on Railway

1. Push this repo to GitHub (`config.py` is gitignored)
2. Go to [railway.app](https://railway.app) → New Project → Deploy from GitHub
3. Add environment variables:

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

## Project layout

| Module | Lines | Purpose |
|---|---|---|
| `decision.py` | 280 | Deterministic verdict engine (ATR levels + veto rules) |
| `llm_agents.py` | 400 | Single explainer + multi-agent debate + digest + chart-analysis prompts |
| `chart.py` | 380 | TradingView-style PNG renderer (Entry/SL/TP zones, EMA, Volume Profile, VWAP, Pivots, CVD) |
| `tracking.py` | 320 | TP/SL outcome tracking + win-rate stats with confidence buckets |
| `screener.py` | 4000 | Webhook, market fetcher, Telegram bot, scheduler (legacy + integration layer) |
| `webhook_server.py` | 195 | Standalone webhook server (optional) |

Tests in `tests/`:
- `test_decision.py` (25) — engine math, veto rules, edge cases
- `test_llm_agents.py` (32) — prompts, multi-agent debate, digest, chart-analysis
- `test_chart.py` (16) — PNG rendering, dimensions, low-cap altcoins
- `test_tracking.py` (26) — schema migration, hit detection, stats math

Total: **99 unit tests**. CI: `pytest` + `ruff` on every push.

---

## Environment variables reference

| Variable | Default | Description |
|----------|---------|-------------|
| `TELEGRAM_TOKEN` | — | Required |
| `TELEGRAM_CHAT_ID` | — | Required |
| `ANTHROPIC_API_KEY` | — | Required |
| `LLM_MODEL_FAST` | `claude-haiku-4-5-20251001` | For per-signal explainer + debate agents |
| `LLM_MODEL_SMART` | `claude-sonnet-4-6` | For `/ask` judge, `/digest`, chart-analysis |
| `PORT` | `5001` | Flask server port |
| `SYMBOLS` | `BTCUSDT,ETHUSDT` | Comma-separated pairs for `/status` and digest |
| `DIGEST_TIME` | `08:00` | Daily digest time (UTC) |
| `DB_PATH` | `signals.db` | SQLite database path |
| `MIN_QUALITY` | `0` | Min quality score to send (1-10, derived from `decision.confidence`) |
