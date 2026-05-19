#!/usr/bin/env python3
"""
Crypto Screener — TradingView Webhook → Telegram
Принимает алерты от TradingView (SMC, ICT, Turtle) и шлёт в Telegram
"""

import json
import logging
import requests
from datetime import datetime, timezone
from flask import Flask, request, jsonify

# ─── КОНФИГУРАЦИЯ ─────────────────────────────────────────────────────────────
TELEGRAM_TOKEN   = "YOUR_BOT_TOKEN"    # от @BotFather
TELEGRAM_CHAT_ID = "YOUR_CHAT_ID"      # твой chat_id

PORT = 5001   # локальный порт

# ─── LOGGING ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

app = Flask(__name__)

# ─── SIGNAL DEFINITIONS ───────────────────────────────────────────────────────
# Каждый тип сигнала → эмодзи, заголовок, bias, рекомендация

SIGNAL_MAP = {
    # ── SMC: Структура ─────────────────────────────────────────────────────
    "BOS_BULL":     ("🔼", "BOS — Bullish Break of Structure",     "🟢 BULLISH",  "LONG"),
    "BOS_BEAR":     ("🔽", "BOS — Bearish Break of Structure",     "🔴 BEARISH",  "SHORT"),
    "CHOCH_BULL":   ("🔄", "CHoCH — Смена характера (бычий)",      "🟢 BULLISH",  "LONG"),
    "CHOCH_BEAR":   ("🔄", "CHoCH — Смена характера (медвежий)",   "🔴 BEARISH",  "SHORT"),

    # ── SMC: Order Blocks ───────────────────────────────────────────────────
    "OB_BULL":      ("📦", "Bullish Order Block — тест снизу",     "🟢 BULLISH",  "LONG"),
    "OB_BEAR":      ("📦", "Bearish Order Block — тест сверху",    "🔴 BEARISH",  "SHORT"),

    # ── SMC: Fair Value Gaps ────────────────────────────────────────────────
    "FVG_BULL":     ("⬜", "Bullish FVG — цена в имбалансе",       "🟢 BULLISH",  "LONG"),
    "FVG_BEAR":     ("⬜", "Bearish FVG — цена в имбалансе",       "🔴 BEARISH",  "SHORT"),
    "FVG_FILLED":   ("✅", "FVG заполнен — имбаланс закрыт",       "🟡 НЕЙТРАЛ",  "ЖДАТЬ"),

    # ── SMC: Liquidity ──────────────────────────────────────────────────────
    "LIQ_SWEEP_H":  ("💧", "Liquidity Sweep — выбило хаи (BSL)",   "🔴 BEARISH",  "SHORT / разворот"),
    "LIQ_SWEEP_L":  ("💧", "Liquidity Sweep — выбило лои (SSL)",   "🟢 BULLISH",  "LONG / разворот"),
    "EQH":          ("📊", "Equal Highs — BSL над уровнем",        "⚡ ВНИМАНИЕ", "Следи за sweep"),
    "EQL":          ("📊", "Equal Lows — SSL под уровнем",         "⚡ ВНИМАНИЕ", "Следи за sweep"),

    # ── Turtle ──────────────────────────────────────────────────────────────
    "TURTLE_LONG":  ("🐢", "Turtle — сигнал на покупку",           "🟢 BULLISH",  "LONG"),
    "TURTLE_SHORT": ("🐢", "Turtle — сигнал на продажу",           "🔴 BEARISH",  "SHORT"),
    "TURTLE_FUND_BULL": ("💰", "Turtle Funding — бычий сигнал",    "🟢 BULLISH",  "LONG"),
    "TURTLE_FUND_BEAR": ("💰", "Turtle Funding — медвежий сигнал", "🔴 BEARISH",  "SHORT"),

    # ── ICT Sessions ────────────────────────────────────────────────────────
    "ICT_NY_OPEN":  ("🗽", "ICT — открытие NY сессии",             "⚡ ВНИМАНИЕ", "Ждать Judas swing"),
    "ICT_LONDON":   ("🏦", "ICT — открытие London сессии",         "⚡ ВНИМАНИЕ", "Следи за displacement"),
    "ICT_KILLZONE": ("🎯", "ICT — KillZone активна",               "⚡ ВНИМАНИЕ", "Высокая вероятность мува"),

    # ── Key Levels ──────────────────────────────────────────────────────────
    "DAILY_OPEN":   ("📅", "Тест Daily Open",                      "⚡ ВНИМАНИЕ", "Ключевой магнит"),
    "WEEKLY_OPEN":  ("📅", "Тест Weekly Open",                     "⚡ ВНИМАНИЕ", "Ключевой магнит"),
    "MONTHLY_OPEN": ("📅", "Тест Monthly Open",                    "⚡ ВНИМАНИЕ", "Macro уровень"),

    # ── Generic fallback ────────────────────────────────────────────────────
    "ALERT":        ("📢", "TradingView Алерт",                    "⚡ ВНИМАНИЕ", "Смотри график"),
}

# ─── TIMEFRAME LABELS ─────────────────────────────────────────────────────────
TF_LABELS = {
    "1": "1M", "3": "3M", "5": "5M", "15": "15M",
    "30": "30M", "60": "1H", "120": "2H", "240": "4H",
    "D": "1D", "W": "1W", "M": "1MO",
}

# ─── TELEGRAM ─────────────────────────────────────────────────────────────────

def send_telegram(text: str) -> bool:
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        r = requests.post(url, json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }, timeout=10)
        r.raise_for_status()
        return True
    except Exception as e:
        log.error(f"Telegram error: {e}")
        return False

# ─── MESSAGE BUILDER ─────────────────────────────────────────────────────────

def format_signal(data: dict) -> str:
    signal   = data.get("signal", "ALERT").upper()
    symbol   = data.get("symbol", data.get("ticker", "UNKNOWN")).replace("USDT", "/USDT")
    price    = data.get("price", data.get("close", 0))
    tf_raw   = str(data.get("tf", data.get("interval", "?")))
    tf       = TF_LABELS.get(tf_raw, tf_raw)
    msg_text = data.get("msg", "")           # доп. текст из TradingView
    now      = datetime.now(timezone.utc).strftime("%H:%M UTC")

    emoji, title, bias, action = SIGNAL_MAP.get(signal, SIGNAL_MAP["ALERT"])

    # Форматируем цену
    try:
        price_f = f"${float(price):,.2f}"
    except (ValueError, TypeError):
        price_f = str(price)

    # Дополнительные поля (уровни, если переданы)
    extras = []
    if data.get("ob_top") and data.get("ob_bot"):
        extras.append(f"  📦 OB зона: ${float(data['ob_top']):,.0f} – ${float(data['ob_bot']):,.0f}")
    if data.get("fvg_top") and data.get("fvg_bot"):
        extras.append(f"  ⬜ FVG зона: ${float(data['fvg_top']):,.0f} – ${float(data['fvg_bot']):,.0f}")
    if data.get("target"):
        extras.append(f"  🎯 Цель:  ${float(data['target']):,.0f}")
    if data.get("stop"):
        extras.append(f"  🛑 Стоп:  ${float(data['stop']):,.0f}")
    if msg_text:
        extras.append(f"  💬 {msg_text}")

    extras_str = "\n" + "\n".join(extras) if extras else ""

    # Цвет bias → action emoji
    act_emoji = "🟢" if "LONG" in action else ("🔴" if "SHORT" in action else "⚡")

    return (
        f"{emoji} <b>{title}</b>\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"📌 <b>{symbol}</b>  •  {tf}  •  {now}\n"
        f"💰 Цена: <b>{price_f}</b>\n"
        f"📊 Bias: <b>{bias}</b>\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"{act_emoji} Действие: <b>{action}</b>"
        f"{extras_str}\n"
        f"━━━━━━━━━━━━━━━━━"
    )

# ─── WEBHOOK ENDPOINT ─────────────────────────────────────────────────────────

@app.route("/webhook", methods=["POST"])
def webhook():
    raw = request.get_data(as_text=True)
    log.info(f"← Webhook: {raw[:200]}")

    # Парсим тело запроса
    data = {}
    ct = request.content_type or ""
    if "json" in ct:
        try:
            data = request.get_json(force=True) or {}
        except Exception:
            pass
    if not data:
        # TradingView иногда шлёт plain-text — оборачиваем
        try:
            data = json.loads(raw)
        except Exception:
            data = {"signal": "ALERT", "msg": raw[:300]}

    # Строим и шлём сообщение
    msg = format_signal(data)
    ok  = send_telegram(msg)
    log.info(f"→ Telegram: {'OK' if ok else 'FAIL'}")

    return jsonify({"status": "ok" if ok else "telegram_error"}), 200


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "running", "time": datetime.now(timezone.utc).isoformat()}), 200

# ─── ENTRY POINT ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if "YOUR_BOT_TOKEN" in TELEGRAM_TOKEN or "YOUR_CHAT_ID" in TELEGRAM_CHAT_ID:
        print("❌  Заполни TELEGRAM_TOKEN и TELEGRAM_CHAT_ID в webhook_server.py")
        exit(1)

    send_telegram(
        "🤖 <b>Crypto Screener (TradingView mode) запущен</b>\n"
        "📡 Жду алерты от TradingView...\n"
        "Сигналы: SMC · ICT · Turtle · Opens"
    )
    log.info(f"🚀 Webhook сервер запущен на http://localhost:{PORT}")
    log.info(f"   Endpoint: http://localhost:{PORT}/webhook")
    app.run(host="0.0.0.0", port=PORT, debug=False)
