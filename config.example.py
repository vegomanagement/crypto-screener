# ─────────────────────────────────────────────────────────────────────────────
# ШАБЛОН — скопируй в config.py и заполни своими данными
# cp config.example.py config.py
# ─────────────────────────────────────────────────────────────────────────────
import os

TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN",   "YOUR_BOT_TOKEN")    # @BotFather
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "YOUR_CHAT_ID")      # @userinfobot
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "YOUR_ANTHROPIC_KEY")

LLM_MODEL_FAST  = os.environ.get("LLM_MODEL_FAST",  "claude-haiku-4-5-20251001")
LLM_MODEL_SMART = os.environ.get("LLM_MODEL_SMART", "claude-sonnet-4-6")

PORT        = int(os.environ.get("PORT", 5001))
SYMBOLS     = [s.strip() for s in os.environ.get("SYMBOLS", "BTCUSDT,ETHUSDT").split(",")]
DIGEST_TIME = os.environ.get("DIGEST_TIME", "08:00")
DB_PATH     = os.environ.get("DB_PATH", "signals.db")
MIN_QUALITY = int(os.environ.get("MIN_QUALITY", 0))
