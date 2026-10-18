"""Container entry point: the Flask UI/API only — no Telegram bot, scheduler or alert sender.

Mirrors the local launcher (import screener, call screener.app.run). ``screener.py``'s
``__main__`` block (Telegram polling, schedule thread, startup tg_send) is never executed
because screener is imported, not run. Optional workers start inside the import only when
PAPER_JOURNAL=1 / WHALE_RECORDER=1 (each takes a flock on its DB, so one process only).
"""
import os
import sys

APP_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, APP_DIR)
os.chdir(APP_DIR)

# Belt and braces: even an accidental tg_send() from a webhook route cannot reach Telegram.
for _k in ("TELEGRAM_TOKEN", "TELEGRAM_CHAT_ID"):
    os.environ.pop(_k, None)

DATA = os.environ.setdefault("DATA_DIR", "/data")
os.environ.setdefault("DB_PATH", os.path.join(DATA, "signals.db"))
os.environ.setdefault("LIQUIDATIONS_DB_PATH", os.path.join(DATA, "liquidations.db"))
os.environ.setdefault("PAPER_DB_PATH", os.path.join(DATA, "paper_journal.db"))
os.environ.setdefault("WHALES_DB_PATH", os.path.join(DATA, "whales.db"))
os.environ.setdefault("PAPER_JOURNAL", "0")
os.environ.setdefault("WHALE_RECORDER", "0")
os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib")

import screener  # noqa: E402

if __name__ == "__main__":
    # Schema for signals.db (CREATE TABLE IF NOT EXISTS only; no network, no messages).
    screener.db_init()
    port = int(os.environ.get("PORT", "5087"))
    screener.app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)
