#!/usr/bin/env python3
"""
Crypto Screener Pro v2
TradingView Webhooks → CVD + VP + MTF + Macro → Claude LLM → Telegram
"""

import base64
import json
import logging
import os
import math
import re
import sqlite3
import threading
import time
import xml.etree.ElementTree as ET
import schedule
import requests
import anthropic
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
from flask import Flask, request, jsonify

from decision import make_decision, format_decision_header
from llm_agents import (
    explain_signal, debate_and_judge, market_brief,
    summarize_day, analyze_user_chart,
)
from chart import render_signal_chart
import tracking
import signal_gate
import patterns
import order_blocks
import block_patterns
import minor_patterns
import backtest as bt_backtest
import bt_data
import bt_compare
import bt_equity_chart
import bt_hyperopt
from webhook_utils import parse_alert_ts

try:
    from config import (
        TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, ANTHROPIC_API_KEY,
        LLM_MODEL_FAST, LLM_MODEL_SMART,
        PORT, SYMBOLS, DIGEST_TIME, DB_PATH, MIN_QUALITY,
    )
except ImportError:
    import os as _os
    TELEGRAM_TOKEN    = _os.environ.get("TELEGRAM_TOKEN",   "YOUR_BOT_TOKEN")
    TELEGRAM_CHAT_ID  = _os.environ.get("TELEGRAM_CHAT_ID", "YOUR_CHAT_ID")
    ANTHROPIC_API_KEY = _os.environ.get("ANTHROPIC_API_KEY", "YOUR_ANTHROPIC_KEY")
    LLM_MODEL_FAST    = _os.environ.get("LLM_MODEL_FAST",  "claude-haiku-4-5-20251001")
    LLM_MODEL_SMART   = _os.environ.get("LLM_MODEL_SMART", "claude-sonnet-4-6")
    PORT              = int(_os.environ.get("PORT", 5001))
    SYMBOLS           = [s.strip() for s in _os.environ.get("SYMBOLS", "BTCUSDT,ETHUSDT").split(",")]
    DIGEST_TIME       = _os.environ.get("DIGEST_TIME", "08:00")
    DB_PATH           = _os.environ.get("DB_PATH", "signals.db")
    MIN_QUALITY       = int(_os.environ.get("MIN_QUALITY", 0))

# ─── LOGGING ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

app = Flask(__name__)
ai  = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

# ─── SIGNAL LABELS ────────────────────────────────────────────────────────────
SIGNAL_META = {
    "BOS_BULL":          ("🔼", "Bullish BOS",            "🟢 BULLISH"),
    "BOS_BEAR":          ("🔽", "Bearish BOS",            "🔴 BEARISH"),
    "CHOCH_BULL":        ("🔄", "CHoCH Bullish",          "🟢 BULLISH"),
    "CHOCH_BEAR":        ("🔄", "CHoCH Bearish",          "🔴 BEARISH"),
    "OB_BULL":           ("📦", "Bullish Order Block",    "🟢 BULLISH"),
    "OB_BEAR":           ("📦", "Bearish Order Block",    "🔴 BEARISH"),
    "FVG_BULL":          ("⬜", "Bullish FVG",            "🟢 BULLISH"),
    "FVG_BEAR":          ("⬜", "Bearish FVG",            "🔴 BEARISH"),
    "FVG_FILLED":        ("✅", "FVG заполнен",           "🟡 НЕЙТРАЛ"),
    "LIQ_SWEEP_H":       ("💧", "Sweep хаёв (BSL)",       "⚡ РАЗВОРОТ?"),
    "LIQ_SWEEP_L":       ("💧", "Sweep лоёв (SSL)",       "⚡ РАЗВОРОТ?"),
    "SWEEP_RECLAIM_BULL": ("🪝", "Sweep low + reclaim",    "🟢 BULLISH"),
    "SWEEP_RECLAIM_BEAR": ("🪝", "Sweep high + reclaim",   "🔴 BEARISH"),
    "MB_BULL":            ("🧱", "Mitigation Block",       "🟢 BULLISH"),
    "MB_BEAR":            ("🧱", "Mitigation Block",       "🔴 BEARISH"),
    "BB_BULL":            ("🔨", "Breaker Block",          "🟢 BULLISH"),
    "BB_BEAR":            ("🔨", "Breaker Block",          "🔴 BEARISH"),
    "INSIDE_BREAK_BULL":  ("💥", "Inside candle breakout", "🟢 BULLISH"),
    "INSIDE_BREAK_BEAR":  ("💥", "Inside candle breakout", "🔴 BEARISH"),
    "RB_BULL":            ("🛡️", "Rejection Block",         "🟢 BULLISH"),
    "RB_BEAR":            ("🛡️", "Rejection Block",         "🔴 BEARISH"),
    "EQH":               ("📊", "Equal Highs (BSL)",      "⚡ ВНИМАНИЕ"),
    "EQL":               ("📊", "Equal Lows (SSL)",       "⚡ ВНИМАНИЕ"),
    "TURTLE_LONG":       ("🐢", "Turtle Long",            "🟢 BULLISH"),
    "TURTLE_SHORT":      ("🐢", "Turtle Short",           "🔴 BEARISH"),
    "TURTLE_FUND_BULL":  ("💰", "Turtle Funding Bull",    "🟢 BULLISH"),
    "TURTLE_FUND_BEAR":  ("💰", "Turtle Funding Bear",    "🔴 BEARISH"),
    "ICT_NY_OPEN":       ("🗽", "NY Open",                "⚡ KILLZONE"),
    "ICT_LONDON":        ("🏦", "London Open",            "⚡ KILLZONE"),
    "ICT_KILLZONE":      ("🎯", "ICT KillZone",           "⚡ KILLZONE"),
    "DAILY_OPEN":        ("📅", "Daily Open тест",        "⚡ УРОВЕНЬ"),
    "WEEKLY_OPEN":       ("📅", "Weekly Open тест",       "⚡ УРОВЕНЬ"),
    "MONTHLY_OPEN":      ("📅", "Monthly Open тест",      "⚡ УРОВЕНЬ"),
    "ALERT":             ("📢", "TV Алерт",               "⚡ ВНИМАНИЕ"),
    "RSI_DIV_BULL":      ("📐", "RSI Бычья дивергенция",  "🟢 BULLISH"),
    "RSI_DIV_BEAR":      ("📐", "RSI Медвежья дивергенция","🔴 BEARISH"),
    "EMA_CROSS_BULL":    ("✨", "EMA 9/21 Golden Cross",   "🟢 BULLISH"),
    "EMA_CROSS_BEAR":    ("💀", "EMA 9/21 Death Cross",    "🔴 BEARISH"),
    "VOL_SPIKE":         ("🔊", "Volume Spike",            "⚡ ВНИМАНИЕ"),
}

TF_LABEL = {
    "1":"1M","3":"3M","5":"5M","15":"15M","30":"30M",
    "60":"1H","120":"2H","240":"4H","D":"1D","W":"1W","M":"1MO",
}

# ─── DATABASE ─────────────────────────────────────────────────────────────────
_db_lock = threading.Lock()

def db_conn():
    return sqlite3.connect(DB_PATH, check_same_thread=False)

def db_init():
    with _db_lock, db_conn() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS signals (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                ts          TEXT    NOT NULL,
                symbol      TEXT    NOT NULL,
                tf          TEXT,
                signal_type TEXT    NOT NULL,
                price       REAL,
                raw_json    TEXT,
                llm_text    TEXT,
                quality     INTEGER DEFAULT 0
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS price_alerts (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id      TEXT    NOT NULL,
                symbol       TEXT    NOT NULL,
                direction    TEXT    NOT NULL,
                target_price REAL    NOT NULL,
                created_at   TEXT    NOT NULL,
                triggered    INTEGER DEFAULT 0
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS signal_outcomes (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                signal_id   INTEGER NOT NULL,
                symbol      TEXT    NOT NULL,
                signal_type TEXT    NOT NULL,
                direction   TEXT    NOT NULL,
                entry_price REAL    NOT NULL,
                entry_ts    TEXT    NOT NULL,
                price_1h    REAL,
                price_4h    REAL,
                price_24h   REAL,
                pct_1h      REAL,
                pct_4h      REAL,
                pct_24h     REAL,
                done        INTEGER DEFAULT 0
            )
        """)
        c.commit()
        # Engine-tracking колонки (Этап 4) — идемпотентная миграция
        tracking.init_schema(c)
        # Signal dispatch / cooldown gate (Этап 5)
        signal_gate.init_schema(c)
    log.info("DB инициализирована")

def db_save(symbol, tf, sig_type, price, raw, llm_text, quality=0,
            decision: dict | None = None, gate_status: str | None = None):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    with _db_lock, db_conn() as c:
        cur = c.execute(
            "INSERT INTO signals(ts,symbol,tf,signal_type,price,raw_json,llm_text,quality)"
            " VALUES(?,?,?,?,?,?,?,?)",
            (ts, symbol, tf, sig_type, price, json.dumps(raw), llm_text, quality),
        )
        signal_id = cur.lastrowid
        c.commit()

    # Engine-tracking (Этап 4): сохраняем decision-snapshot для TP/SL tracking.
    # WAIT/SKIP записываем с status='skipped' — для статистики гейтинга.
    # gate_status='suppressed' — сигнал подавлен cooldown gate (Этап 5),
    # не трекается и не учитывается в win-rate.
    if decision:
        try:
            with _db_lock, db_conn() as c:
                tracking.open_trade(c, signal_id, decision, symbol, sig_type,
                                    force_status=gate_status)
        except Exception as e:
            log.warning(f"tracking.open_trade: {e}")
    else:
        # Fallback на старую логику для обратной совместимости
        # (если сигнал пришёл откуда-то без decision)
        sig_up = any(x in sig_type for x in ("BULL", "LONG", "SWEEP_L", "CHOCH_BULL"))
        sig_dn = any(x in sig_type for x in ("BEAR", "SHORT", "SWEEP_H", "CHOCH_BEAR"))
        if sig_up or sig_dn:
            direction = "bull" if sig_up else "bear"
            try:
                db_outcome_add(signal_id, symbol, sig_type, direction, float(price or 0))
            except Exception as e:
                log.warning(f"db_outcome_add: {e}")

def db_recent(hours=4, limit=8):
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M")
    with _db_lock, db_conn() as c:
        rows = c.execute(
            "SELECT ts,symbol,tf,signal_type,price FROM signals"
            " WHERE ts >= ? ORDER BY ts DESC LIMIT ?",
            (since, limit),
        ).fetchall()
    return rows

def db_today():
    since = datetime.now(timezone.utc).strftime("%Y-%m-%d 00:00")
    with _db_lock, db_conn() as c:
        rows = c.execute(
            "SELECT ts,symbol,tf,signal_type,price,llm_text,quality"
            " FROM signals WHERE ts >= ? ORDER BY ts ASC",
            (since,),
        ).fetchall()
    return rows

def db_last_n(n=10):
    with _db_lock, db_conn() as c:
        rows = c.execute(
            "SELECT ts,symbol,tf,signal_type,price,quality FROM signals"
            " ORDER BY ts DESC LIMIT ?", (n,)
        ).fetchall()
    return rows


# ─── PRICE ALERTS DB ──────────────────────────────────────────────────────────

def db_alert_add(chat_id: str, symbol: str, direction: str, price: float) -> int:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    with _db_lock, db_conn() as c:
        cur = c.execute(
            "INSERT INTO price_alerts(chat_id,symbol,direction,target_price,created_at)"
            " VALUES(?,?,?,?,?)",
            (str(chat_id), symbol, direction, price, ts),
        )
        c.commit()
        return cur.lastrowid


def db_alert_list(chat_id: str) -> list:
    with _db_lock, db_conn() as c:
        return c.execute(
            "SELECT id,symbol,direction,target_price,created_at FROM price_alerts"
            " WHERE chat_id=? AND triggered=0 ORDER BY id",
            (str(chat_id),),
        ).fetchall()


def db_alert_delete(alert_id: int, chat_id: str) -> bool:
    with _db_lock, db_conn() as c:
        cur = c.execute(
            "DELETE FROM price_alerts WHERE id=? AND chat_id=?",
            (alert_id, str(chat_id)),
        )
        c.commit()
        return cur.rowcount > 0


def db_alert_trigger(alert_id: int):
    with _db_lock, db_conn() as c:
        c.execute("UPDATE price_alerts SET triggered=1 WHERE id=?", (alert_id,))
        c.commit()


# ─── SIGNAL OUTCOMES DB ───────────────────────────────────────────────────────

def db_outcome_add(signal_id: int, symbol: str, signal_type: str,
                   direction: str, entry_price: float):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    with _db_lock, db_conn() as c:
        c.execute(
            "INSERT INTO signal_outcomes"
            "(signal_id,symbol,signal_type,direction,entry_price,entry_ts)"
            " VALUES(?,?,?,?,?,?)",
            (signal_id, symbol, signal_type, direction, entry_price, ts),
        )
        c.commit()


def db_outcomes_pending() -> list:
    """Return outcomes where 1H/4H/24H checks are not yet done."""
    with _db_lock, db_conn() as c:
        return c.execute(
            "SELECT id,symbol,signal_type,direction,entry_price,entry_ts,"
            "price_1h,price_4h,price_24h,done FROM signal_outcomes WHERE done=0"
        ).fetchall()


def db_outcome_update(outcome_id: int, field: str, price: float, pct: float):
    done_check = ""
    if field == "price_24h":
        done_check = ", done=1"
    with _db_lock, db_conn() as c:
        c.execute(
            f"UPDATE signal_outcomes SET {field}=?, pct_{field[6:]}=?{done_check}"
            " WHERE id=?",
            (price, pct, outcome_id),
        )
        c.commit()


def db_stats(days: int = 30) -> list:
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d %H:%M")
    with _db_lock, db_conn() as c:
        return c.execute(
            "SELECT signal_type,direction,pct_4h FROM signal_outcomes"
            " WHERE entry_ts>=? AND pct_4h IS NOT NULL",
            (since,),
        ).fetchall()


def db_alerts_active() -> list:
    with _db_lock, db_conn() as c:
        return c.execute(
            "SELECT id,chat_id,symbol,direction,target_price FROM price_alerts"
            " WHERE triggered=0"
        ).fetchall()

# ─── MARKET DATA — BYBIT ─────────────────────────────────────────────────────

BYBIT       = "https://api.bybit.com"
HL          = "https://api.hyperliquid.xyz/info"
BINANCE_FAPI = "https://fapi.binance.com"

def _bybit_data(symbol: str) -> dict:
    out = {"source": "bybit"}
    try:
        tk = requests.get(f"{BYBIT}/v5/market/tickers",
                          params={"symbol": symbol, "category": "linear"},
                          timeout=6).json()["result"]["list"][0]
        out["price"]      = float(tk.get("lastPrice", 0))
        out["change_24h"] = float(tk.get("price24hPcnt", 0)) * 100
        out["funding"]    = float(tk.get("fundingRate", 0))
        out["vol_24h"]    = float(tk.get("volume24h", 0))
        out["mark_px"]    = float(tk.get("markPrice", 0))
    except Exception as e:
        log.warning(f"Bybit ticker {symbol}: {e}")

    # Fallback: Binance Futures ticker if Bybit price is 0
    if not out.get("price"):
        try:
            tk = requests.get(
                f"{BINANCE_FAPI}/fapi/v1/ticker/24hr",
                params={"symbol": symbol}, timeout=6,
            ).json()
            out["price"]      = float(tk.get("lastPrice", 0))
            out["change_24h"] = float(tk.get("priceChangePercent", 0))
            out["vol_24h"]    = float(tk.get("volume", 0))
            out["source"]     = "binance"
            log.info(f"Bybit ticker fallback → Binance for {symbol}")
        except Exception as e:
            log.warning(f"Binance ticker fallback {symbol}: {e}")

    try:
        items = requests.get(
            f"{BYBIT}/v5/market/open-interest",
            params={"symbol": symbol, "intervalTime": "15min",
                    "limit": 3, "category": "linear"}, timeout=6
        ).json()["result"]["list"]
        if len(items) >= 2:
            n, p = float(items[0]["openInterest"]), float(items[1]["openInterest"])
            out["oi"]     = n
            out["oi_chg"] = (n - p) / p * 100 if p else 0.0
        else:
            out["oi_chg"] = 0.0
    except Exception as e:
        log.warning(f"Bybit OI {symbol}: {e}")
        out.setdefault("oi_chg", 0.0)

    # Fallback: Binance Futures funding rate if Bybit funding is missing
    if not out.get("funding"):
        try:
            fr = requests.get(
                f"{BINANCE_FAPI}/fapi/v1/premiumIndex",
                params={"symbol": symbol}, timeout=6,
            ).json()
            out["funding"] = float(fr.get("lastFundingRate", 0))
        except Exception:
            out.setdefault("funding", 0.0)

    return out


# Bybit interval → Binance Futures interval mapping
_BNB_INTERVAL = {
    "1": "1m", "3": "3m", "5": "5m", "15": "15m", "30": "30m",
    "60": "1h", "120": "2h", "240": "4h", "D": "1d", "W": "1w",
}


def _klines_binance(symbol: str, interval: str, limit: int = 100) -> list:
    """Binance Futures klines as fallback. Oldest→newest."""
    bnb_interval = _BNB_INTERVAL.get(interval, interval)
    try:
        r = requests.get(
            f"{BINANCE_FAPI}/fapi/v1/klines",
            params={"symbol": symbol, "interval": bnb_interval, "limit": limit},
            timeout=8,
        )
        data = r.json()
        if not isinstance(data, list) or not data:
            return []
        return [{"o": float(x[1]), "h": float(x[2]), "l": float(x[3]),
                 "c": float(x[4]), "v": float(x[5])} for x in data]
    except Exception as e:
        log.warning(f"Binance klines {symbol} {interval}: {e}")
        return []


_HL_INTERVAL = {
    "1": "1m", "5": "5m", "15": "15m", "30": "30m",
    "60": "1h", "120": "2h", "240": "4h", "D": "1d", "W": "1w",
}
_HL_INTERVAL_MS = {
    "1m": 60_000, "5m": 300_000, "15m": 900_000, "30m": 1_800_000,
    "1h": 3_600_000, "2h": 7_200_000, "4h": 14_400_000,
    "1d": 86_400_000, "1w": 604_800_000,
}


def _klines_hl(symbol: str, interval: str, limit: int = 100) -> list:
    """Hyperliquid candle snapshot as last-resort fallback. Oldest→newest."""
    coin = symbol.replace("USDT", "")
    hl_iv = _HL_INTERVAL.get(interval, "1h")
    ms    = _HL_INTERVAL_MS.get(hl_iv, 3_600_000)
    end   = int(time.time() * 1000)
    start = end - limit * ms
    try:
        r = requests.post(
            HL,
            json={"type": "candleSnapshot",
                  "req": {"coin": coin, "interval": hl_iv,
                          "startTime": start, "endTime": end}},
            timeout=10,
        )
        candles = r.json()
        if not isinstance(candles, list) or not candles:
            return []
        return [{"o": float(c["o"]), "h": float(c["h"]), "l": float(c["l"]),
                 "c": float(c["c"]), "v": float(c["v"])} for c in candles]
    except Exception as e:
        log.warning(f"HL candles {symbol} {interval}: {e}")
        return []


def _klines(symbol: str, interval: str, limit: int = 100) -> list:
    """Candles oldest→newest. Chain: Bybit → Binance Futures → Hyperliquid."""
    # 1. Bybit
    try:
        r = requests.get(
            f"{BYBIT}/v5/market/kline",
            params={"symbol": symbol, "interval": interval,
                    "limit": limit, "category": "linear"}, timeout=8,
        )
        rows = r.json()["result"]["list"]   # newest first
        rows.reverse()
        result = [{"o": float(x[1]), "h": float(x[2]), "l": float(x[3]),
                   "c": float(x[4]), "v": float(x[5])} for x in rows]
        if result:
            return result
    except Exception as e:
        log.warning(f"Bybit klines {symbol} {interval}: {e}")

    # 2. Binance Futures
    result = _klines_binance(symbol, interval, limit)
    if result:
        log.info(f"Klines {symbol} {interval}: using Binance")
        return result

    # 3. Hyperliquid
    result = _klines_hl(symbol, interval, limit)
    if result:
        log.info(f"Klines {symbol} {interval}: using HL")
    return result


# ─── CVD ─────────────────────────────────────────────────────────────────────

def compute_cvd(candles: list) -> dict:
    if len(candles) < 10:
        return {"trend": "unknown", "divergence": False, "delta_5": 0}

    deltas = []
    for c in candles:
        hl = c["h"] - c["l"]
        ratio = (c["c"] - c["l"]) / hl if hl > 0 else 0.5
        deltas.append(c["v"] * ratio - c["v"] * (1 - ratio))

    cvd_vals, cum = [], 0.0
    for d in deltas:
        cum += d
        cvd_vals.append(cum)

    n           = min(20, len(cvd_vals))
    cvd_trend   = "up" if cvd_vals[-1] > cvd_vals[-n] else "down"
    price_trend = "up" if candles[-1]["c"] > candles[-n]["c"] else "down"

    return {
        "trend":       cvd_trend,
        "price_trend": price_trend,
        "divergence":  cvd_trend != price_trend,
        "delta_5":     sum(deltas[-5:]),
    }


# ─── VOLUME PROFILE ───────────────────────────────────────────────────────────

def compute_volume_profile(candles: list, bins: int = 60) -> dict:
    if len(candles) < 5:
        return {}

    lo = min(c["l"] for c in candles)
    hi = max(c["h"] for c in candles)
    if hi <= lo:
        return {"poc": candles[-1]["c"], "vah": hi, "val": lo}

    step     = (hi - lo) / bins
    vol_bins = [0.0] * bins

    for c in candles:
        b0 = max(0, min(int((c["l"] - lo) / step), bins - 1))
        b1 = max(0, min(int((c["h"] - lo) / step), bins - 1))
        each = c["v"] / (b1 - b0 + 1)
        for b in range(b0, b1 + 1):
            vol_bins[b] += each

    poc_bin = vol_bins.index(max(vol_bins))
    poc     = lo + (poc_bin + 0.5) * step

    total  = sum(vol_bins)
    target = total * 0.70
    lo_b, hi_b, va = poc_bin, poc_bin, vol_bins[poc_bin]

    while va < target:
        add_lo = vol_bins[lo_b - 1] if lo_b > 0 else 0.0
        add_hi = vol_bins[hi_b + 1] if hi_b < bins - 1 else 0.0
        if not add_lo and not add_hi:
            break
        if add_hi >= add_lo:
            hi_b += 1; va += add_hi
        else:
            lo_b -= 1; va += add_lo

    return {
        "poc": round(poc, 2),
        "vah": round(lo + (hi_b + 1) * step, 2),
        "val": round(lo + lo_b * step, 2),
    }


# ─── EMA & MTF CONFLUENCE ─────────────────────────────────────────────────────

def _ema(prices: list, span: int) -> list:
    if not prices:
        return []
    k, out = 2 / (span + 1), [prices[0]]
    for p in prices[1:]:
        out.append(p * k + out[-1] * (1 - k))
    return out


def get_ema_biases(k1h: list, k4h: list, k1d: list) -> dict:
    def bias(candles, span=20):
        if len(candles) < span + 2:
            return "unknown"
        prices = [c["c"] for c in candles]
        ema = _ema(prices, span)
        return "bull" if prices[-1] > ema[-1] else "bear"
    return {"1H": bias(k1h), "4H": bias(k4h), "1D": bias(k1d)}


# ─── TURTLE ZONE (Ehlers 2-Pole Log Envelope) ────────────────────────────────

def _ehlers_2pole(values: list, length: int) -> list:
    """Ehlers 2-pole Super Smoother filter — low-lag low-pass filter."""
    a1 = math.exp(-math.sqrt(2) * math.pi / length)
    c2 = 2 * a1 * math.cos(math.sqrt(2) * math.pi / length)
    c3 = -(a1 ** 2)
    c1 = 1 - c2 - c3
    out = [values[0], values[0]]
    for i in range(2, len(values)):
        out.append(c1 * (values[i] + values[i - 1]) / 2 + c2 * out[-1] + c3 * out[-2])
    return out


def compute_turtle_zone(candles: list, length: int = 200,
                         inner_amp: float = 5.6, outer_amp: float = 9.6) -> dict:
    """
    Turtle Zone: Ehlers-smoothed log-price envelope.
    Zones tell whether price is cheap (lower) or expensive (upper) vs history.
    Needs at least `length` candles; returns {} if insufficient data.
    """
    if len(candles) < length:
        return {}

    # Log-transform source: hlc3
    log_src = [math.log((c["h"] + c["l"] + c["c"]) / 3) for c in candles]

    # Log true range (percentage-based ATR)
    log_tr = []
    for i, c in enumerate(candles):
        tr = math.log(c["h"]) - math.log(c["l"])
        if i > 0:
            pc = candles[i - 1]["c"]
            tr = max(tr,
                     abs(math.log(c["h"]) - math.log(pc)),
                     abs(math.log(c["l"]) - math.log(pc)))
        log_tr.append(tr)

    mean_log  = _ehlers_2pole(log_src, length)[-1]
    tr_smooth = _ehlers_2pole(log_tr,  length)[-1]

    price        = candles[-1]["c"]
    price_mean   = math.exp(mean_log)
    upper_inner  = math.exp(mean_log + inner_amp * tr_smooth)
    upper_outer  = math.exp(mean_log + outer_amp * tr_smooth)
    lower_inner  = math.exp(mean_log - inner_amp * tr_smooth)
    lower_outer  = math.exp(mean_log - outer_amp * tr_smooth)
    pct_from_mean = (price / price_mean - 1) * 100

    if price >= upper_outer:
        zone, icon, label = "extreme_upper", "🚨", "Экстремальная перекупленность"
    elif price >= upper_inner:
        zone, icon, label = "upper", "🔴", "Верхняя зона (перекупленность)"
    elif price <= lower_outer:
        zone, icon, label = "extreme_lower", "🚨", "Экстремальная перепроданность"
    elif price <= lower_inner:
        zone, icon, label = "lower", "🟢", "Нижняя зона (перепроданность)"
    else:
        zone, icon, label = "neutral", "⚪", "Нейтральная зона"

    return {
        "zone":          zone,
        "icon":          icon,
        "label":         label,
        "mean":          round(price_mean, 2),
        "upper_inner":   round(upper_inner, 2),
        "upper_outer":   round(upper_outer, 2),
        "lower_inner":   round(lower_inner, 2),
        "lower_outer":   round(lower_outer, 2),
        "pct_from_mean": round(pct_from_mean, 2),
    }


# ─── TECHNICAL INDICATORS ─────────────────────────────────────────────────────

def compute_rsi(closes: list, period: int = 14) -> float:
    """RSI with Wilder's smoothing."""
    if len(closes) < period + 1:
        return 50.0
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains  = [max(d, 0.0) for d in deltas]
    losses = [max(-d, 0.0) for d in deltas]
    avg_g  = sum(gains[:period]) / period
    avg_l  = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_g = (avg_g * (period - 1) + gains[i])  / period
        avg_l = (avg_l * (period - 1) + losses[i]) / period
    if avg_l == 0:
        return 100.0
    return round(100 - 100 / (1 + avg_g / avg_l), 2)


def compute_macd(closes: list, fast: int = 12, slow: int = 26,
                 signal: int = 9) -> dict:
    """MACD line, signal line, histogram, trend, and cross detection."""
    if len(closes) < slow + signal:
        return {}
    ema_f    = _ema(closes, fast)
    ema_s    = _ema(closes, slow)
    macd_l   = [f - s for f, s in zip(ema_f, ema_s)]
    signal_l = _ema(macd_l, signal)
    hist     = macd_l[-1] - signal_l[-1]
    prev_h   = macd_l[-2] - signal_l[-2]
    if   hist > 0 and prev_h <= 0: cross = "golden"
    elif hist < 0 and prev_h >= 0: cross = "death"
    else:                           cross = "none"
    return {
        "macd":      round(macd_l[-1],   6),
        "signal":    round(signal_l[-1], 6),
        "histogram": round(hist,         6),
        "trend":     "bull" if macd_l[-1] > signal_l[-1] else "bear",
        "cross":     cross,
    }


def compute_bollinger(closes: list, period: int = 20,
                      num_std: float = 2.0) -> dict:
    """Bollinger Bands: upper/middle/lower + price position + %B."""
    if len(closes) < period:
        return {}
    recent  = closes[-period:]
    sma     = sum(recent) / period
    std     = (sum((x - sma) ** 2 for x in recent) / period) ** 0.5
    upper   = sma + num_std * std
    lower   = sma - num_std * std
    price   = closes[-1]
    pct_b   = (price - lower) / (upper - lower) if upper != lower else 0.5
    width   = (upper - lower) / sma * 100
    if   price >= upper: pos, icon = "above_upper", "🔴"
    elif price <= lower: pos, icon = "below_lower", "🟢"
    elif price > sma:    pos, icon = "upper_half",  "⚪"
    else:                pos, icon = "lower_half",  "⚪"
    return {
        "upper":   round(upper, 2),
        "middle":  round(sma,   2),
        "lower":   round(lower, 2),
        "position": pos,
        "icon":    icon,
        "pct_b":   round(pct_b, 3),
        "width":   round(width, 2),
    }


def compute_stochastic(candles: list, k_period: int = 14,
                        d_period: int = 3) -> dict:
    """Fast Stochastic %K and smoothed %D."""
    if len(candles) < k_period + d_period:
        return {}
    raw_k = []
    for i in range(d_period):
        end    = len(candles) - (d_period - 1 - i)
        window = candles[end - k_period : end]
        hi = max(c["h"] for c in window)
        lo = min(c["l"] for c in window)
        raw_k.append(50.0 if hi == lo
                     else (window[-1]["c"] - lo) / (hi - lo) * 100)
    k = raw_k[-1]
    d = sum(raw_k) / len(raw_k)
    if   k > 80: signal, icon = "overbought", "🔴"
    elif k < 20: signal, icon = "oversold",   "🟢"
    else:        signal, icon = "neutral",     "⚪"
    return {"k": round(k, 1), "d": round(d, 1), "signal": signal, "icon": icon}


def compute_atr(candles: list, period: int = 14) -> float:
    """Average True Range (Wilder's smoothing)."""
    if len(candles) < period + 1:
        return 0.0
    trs = [max(candles[i]["h"] - candles[i]["l"],
               abs(candles[i]["h"] - candles[i-1]["c"]),
               abs(candles[i]["l"] - candles[i-1]["c"]))
           for i in range(1, len(candles))]
    atr = sum(trs[:period]) / period
    for tr in trs[period:]:
        atr = (atr * (period - 1) + tr) / period
    return round(atr, 4)


def detect_rsi_divergence(candles: list, rsi_period: int = 14,
                          lookback: int = 30) -> str:
    """
    Detect regular RSI divergence over recent candles.
    Returns: 'bullish', 'bearish', or 'none'
    """
    if len(candles) < rsi_period + lookback + 2:
        return "none"

    window   = candles[-lookback:]
    closes   = [c["c"] for c in window]
    rsi_vals = [compute_rsi([c["c"] for c in candles[:-(lookback - i - 1) or None]])
                for i in range(lookback)]

    # Find two most recent swing highs and lows (simple: just compare last vs earlier peak)
    def find_swing_high(prices, rsi, n=5):
        best_i = max(range(n, len(prices) - 1),
                     key=lambda i: prices[i], default=None)
        prev_i = max(range(1, best_i) if best_i else range(0),
                     key=lambda i: prices[i], default=None)
        return (prev_i, best_i) if prev_i is not None and best_i is not None else (None, None)

    def find_swing_low(prices, rsi, n=5):
        best_i = min(range(n, len(prices) - 1),
                     key=lambda i: prices[i], default=None)
        prev_i = min(range(1, best_i) if best_i else range(0),
                     key=lambda i: prices[i], default=None)
        return (prev_i, best_i) if prev_i is not None and best_i is not None else (None, None)

    # Bearish: price makes higher high, RSI makes lower high
    i1, i2 = find_swing_high(closes, rsi_vals)
    if (i1 is not None and i2 is not None
            and closes[i2] > closes[i1]
            and rsi_vals[i2] < rsi_vals[i1] - 3):
        return "bearish"

    # Bullish: price makes lower low, RSI makes higher low
    i1, i2 = find_swing_low(closes, rsi_vals)
    if (i1 is not None and i2 is not None
            and closes[i2] < closes[i1]
            and rsi_vals[i2] > rsi_vals[i1] + 3):
        return "bullish"

    return "none"


def check_ema_cross(candles: list, fast: int = 9, slow: int = 21) -> str:
    """Detect EMA cross on the last two closed candles. Returns 'golden'/'death'/'none'."""
    closes = [c["c"] for c in candles]
    if len(closes) < slow + 2:
        return "none"
    ef = _ema(closes, fast)
    es = _ema(closes, slow)
    if ef[-2] < es[-2] and ef[-1] > es[-1]:
        return "golden"
    if ef[-2] > es[-2] and ef[-1] < es[-1]:
        return "death"
    return "none"


def detect_volume_spike(candles: list, threshold: float = 2.5,
                        avg_period: int = 20) -> bool:
    """True if last candle volume > threshold × average of prior avg_period candles."""
    if len(candles) < avg_period + 1:
        return False
    avg = sum(c["v"] for c in candles[-(avg_period + 1):-1]) / avg_period
    return avg > 0 and candles[-1]["v"] > avg * threshold


# ─── BTC CORRELATION ─────────────────────────────────────────────────────────

def _pearson(xs: list, ys: list) -> float:
    """Pearson correlation coefficient for two equal-length sequences."""
    n = len(xs)
    if n < 3:
        return 0.0
    mx, my = sum(xs) / n, sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx  = sum((x - mx) ** 2 for x in xs) ** 0.5
    dy  = sum((y - my) ** 2 for y in ys) ** 0.5
    return round(num / (dx * dy), 3) if dx * dy else 0.0


def compute_btc_correlation(sym_candles: list, btc_candles: list) -> dict:
    """Pearson r between symbol and BTC on last 24H / 7D of 1H closes."""
    sym_c = [c["c"] for c in sym_candles]
    btc_c = [c["c"] for c in btc_candles]

    def _corr(n):
        if min(len(sym_c), len(btc_c)) < max(n, 5):
            return None
        return _pearson(sym_c[-n:], btc_c[-n:])

    def _label(r):
        if r is None:    return "n/a"
        if r >= 0.85:    return "🔗 очень высокая"
        if r >= 0.65:    return "↑ высокая"
        if r >= 0.40:    return "~ средняя"
        if r >= 0.10:    return "↓ низкая"
        if r >= -0.10:   return "➡️ нет"
        return           "↙ обратная"

    r24 = _corr(24)
    r7d = _corr(168)
    return {
        "r24h": r24, "label24h": _label(r24),
        "r7d":  r7d, "label7d":  _label(r7d),
    }


def compute_indicators(candles: list) -> dict:
    """Bundle all technical indicators from OHLCV candles."""
    if len(candles) < 35:
        return {}
    closes = [c["c"] for c in candles]
    price  = closes[-1]
    atr    = compute_atr(candles)
    return {
        "rsi":          compute_rsi(closes),
        "macd":         compute_macd(closes),
        "bb":           compute_bollinger(closes),
        "stoch":        compute_stochastic(candles),
        "atr":          atr,
        "atr_pct":      round(atr / price * 100, 3) if price else 0,
        "rsi_div":      detect_rsi_divergence(candles),
        "ema_cross":    check_ema_cross(candles),
        "vol_spike":    detect_volume_spike(candles),
    }


def compute_vwap(candles: list) -> dict:
    """
    Daily and Weekly VWAP with ±1σ / ±2σ bands from 1H candles (oldest→newest).
    Uses current UTC time to slice the correct window — no timestamps needed.
    """
    if len(candles) < 2:
        return {}

    now   = datetime.now(timezone.utc)
    price = candles[-1]["c"]

    def _bands(window: list) -> dict:
        if not window:
            return {}
        tp_vol = sum((c["h"] + c["l"] + c["c"]) / 3 * c["v"] for c in window)
        vol    = sum(c["v"] for c in window)
        if vol == 0:
            return {}
        vwap = tp_vol / vol
        var  = sum(((c["h"] + c["l"] + c["c"]) / 3 - vwap) ** 2 * c["v"]
                   for c in window) / vol
        std  = var ** 0.5
        return {
            "vwap":   round(vwap, 2),
            "upper2": round(vwap + 2 * std, 2),
            "upper1": round(vwap + std, 2),
            "lower1": round(vwap - std, 2),
            "lower2": round(vwap - 2 * std, 2),
        }

    def _position(p: float, b: dict) -> str:
        if p > b["upper2"]: return "extreme_upper"
        if p > b["upper1"]: return "upper"
        if p > b["vwap"]:   return "premium"
        if p < b["lower2"]: return "extreme_lower"
        if p < b["lower1"]: return "lower"
        return "discount"

    hours_today = max(1, now.hour + 1)
    hours_week  = max(1, now.weekday() * 24 + now.hour + 1)

    out = {}

    d = _bands(candles[-min(hours_today, len(candles)):])
    if d:
        pos = _position(price, d)
        out["daily"]     = d
        out["daily_pos"] = pos
        out["daily_pct"] = round((price - d["vwap"]) / d["vwap"] * 100, 2)

    w = _bands(candles[-min(hours_week, len(candles)):])
    if w:
        pos = _position(price, w)
        out["weekly"]     = w
        out["weekly_pos"] = pos
        out["weekly_pct"] = round((price - w["vwap"]) / w["vwap"] * 100, 2)

    return out


def check_mtf_confluence(biases: dict, direction: str) -> dict:
    want    = "bull" if direction == "long" else "bear"
    aligned = sum(1 for b in biases.values() if b == want)
    details = [
        f"{tf}: {'✅' if b == want else ('❌' if b != 'unknown' else '❓')} {b.upper()}"
        for tf, b in biases.items()
    ]
    return {"aligned": aligned, "total": len(biases), "details": details}


# ─── SESSION ─────────────────────────────────────────────────────────────────

def get_session() -> dict:
    h = datetime.now(timezone.utc).hour
    if 13 <= h < 16:
        return {"name": "NY+London Overlap", "quality": 5, "icon": "🎯"}
    if 13 <= h < 22:
        return {"name": "NY Session",        "quality": 4, "icon": "🗽"}
    if 7 <= h < 16:
        return {"name": "London Session",    "quality": 4, "icon": "🏦"}
    if 7 <= h < 13:
        return {"name": "Pre-NY London",     "quality": 3, "icon": "🌍"}
    return     {"name": "Asian Session",     "quality": 2, "icon": "🌏"}


# ─── MACRO (Fear & Greed + Dominance) ────────────────────────────────────────

_macro_cache: dict = {"ts": 0.0, "data": {}}
_macro_lock  = threading.Lock()
MACRO_TTL    = 900   # 15 min


def get_macro() -> dict:
    with _macro_lock:
        if time.time() - _macro_cache["ts"] < MACRO_TTL:
            return dict(_macro_cache["data"])

    out: dict = {}

    try:
        d   = requests.get("https://api.alternative.me/fng/?limit=1", timeout=8).json()["data"][0]
        val = int(d["value"])
        out["fg_value"] = val
        out["fg_label"] = d["value_classification"]
        out["fg_icon"]  = "😱" if val < 25 else ("😨" if val < 45 else ("😐" if val < 55 else ("🤑" if val < 75 else "🚀")))
    except Exception as e:
        log.warning(f"Fear&Greed: {e}")

    try:
        cg = requests.get("https://api.coingecko.com/api/v3/global", timeout=8).json()["data"]
        dom = cg.get("market_cap_percentage", {})
        total = cg.get("total_market_cap", {}).get("usd", 0)

        btc_dom  = dom.get("btc", 0)
        eth_dom  = dom.get("eth", 0)
        usdt_dom = dom.get("usdt", 0)
        usdc_dom = dom.get("usdc", 0)

        # TOTAL2 = excl BTC; TOTAL3 = excl BTC+ETH
        total2 = total * (1 - btc_dom / 100) if total else 0
        total3 = total * (1 - btc_dom / 100 - eth_dom / 100) if total else 0

        # OTHERS ≈ TOTAL3 minus visible large alts (BNB, XRP, SOL, etc.)
        stables = {"usdt", "usdc", "busd", "dai", "tusd"}
        big_alts_dom = sum(v for k, v in dom.items()
                           if k not in {"btc", "eth"} and k not in stables)
        others = total * big_alts_dom / 100 if total else 0

        out["btc_dom"]    = round(btc_dom, 2)
        out["eth_dom"]    = round(eth_dom, 2)
        out["usdt_dom"]   = round(usdt_dom + usdc_dom, 2)  # combined stablecoin %
        out["total_mcap"] = total
        out["total2"]     = total2
        out["total3"]     = total3
        out["others"]     = others
        out["mcap_chg24"] = round(cg.get("market_cap_change_percentage_24h_usd", 0), 2)
    except Exception as e:
        log.warning(f"Dominance: {e}")

    with _macro_lock:
        _macro_cache["ts"]   = time.time()
        _macro_cache["data"] = out

    return out


# ─── DERIBIT OPTIONS ──────────────────────────────────────────────────────────

DERIBIT = "https://www.deribit.com/api/v2/public"

_deribit_cache: dict = {"ts": 0.0, "BTC": {}, "ETH": {}}
_deribit_lock  = threading.Lock()
DERIBIT_TTL    = 900  # 15 min


def _deribit_options(currency: str = "BTC") -> dict:
    currency = currency.upper()
    with _deribit_lock:
        if time.time() - _deribit_cache["ts"] < DERIBIT_TTL and _deribit_cache.get(currency):
            return dict(_deribit_cache[currency])

    out: dict = {}
    try:
        r = requests.get(
            f"{DERIBIT}/get_book_summary_by_currency",
            params={"currency": currency, "kind": "option"},
            timeout=8,
        )
        books = r.json().get("result", [])
        if not books:
            return out

        # Put/Call ratio by open interest
        call_oi = sum(float(b.get("open_interest", 0)) for b in books if b["instrument_name"].endswith("-C"))
        put_oi  = sum(float(b.get("open_interest", 0)) for b in books if b["instrument_name"].endswith("-P"))
        out["pc_ratio"] = round(put_oi / call_oi, 3) if call_oi else 0.0
        out["call_oi"]  = call_oi
        out["put_oi"]   = put_oi

        # Max Pain: strike that minimises total payout to option buyers
        now_dt = datetime.now(timezone.utc)
        expiries: dict = {}  # expiry_str → {"C": {strike: oi}, "P": {strike: oi}, "dt": datetime}

        for b in books:
            parts = b["instrument_name"].split("-")
            if len(parts) != 4:
                continue
            _, exp_str, strike_str, opt_type = parts
            if opt_type not in ("C", "P"):
                continue
            try:
                strike = float(strike_str)
                oi     = float(b.get("open_interest", 0))
                exp_dt = datetime.strptime(exp_str, "%d%b%y").replace(tzinfo=timezone.utc)
            except ValueError:
                continue
            if exp_dt < now_dt:
                continue
            if exp_str not in expiries:
                expiries[exp_str] = {"C": {}, "P": {}, "dt": exp_dt}
            expiries[exp_str][opt_type][strike] = expiries[exp_str][opt_type].get(strike, 0.0) + oi

        if expiries:
            nearest = min(expiries, key=lambda e: expiries[e]["dt"])
            ed       = expiries[nearest]
            strikes  = sorted(set(list(ed["C"]) + list(ed["P"])))

            if strikes:
                min_pain, mp_strike = float("inf"), strikes[0]
                for S in strikes:
                    pain = (sum(max(0.0, S - K) * v for K, v in ed["C"].items())
                            + sum(max(0.0, K - S) * v for K, v in ed["P"].items()))
                    if pain < min_pain:
                        min_pain, mp_strike = pain, S
                out["max_pain"]       = mp_strike
                out["nearest_expiry"] = nearest

    except Exception as e:
        log.warning(f"Deribit {currency}: {e}")

    with _deribit_lock:
        _deribit_cache["ts"]     = time.time()
        _deribit_cache[currency] = out

    return out


# ─── CONFLUENCE SCORE ─────────────────────────────────────────────────────────

def compute_confluence_score(signal_type: str, market: dict, mtf: dict) -> tuple:
    """Returns (score 0-100, [factor strings])"""
    sig    = signal_type.upper()
    sig_up = any(x in sig for x in ("BULL", "LONG", "SWEEP_L", "EQL"))
    sig_dn = any(x in sig for x in ("BEAR", "SHORT", "SWEEP_H", "EQH"))
    direction = "long" if sig_up else ("short" if sig_dn else "neutral")

    b, hl   = market.get("bybit", {}), market.get("hl", {})
    cvd     = market.get("cvd", {})
    vp      = market.get("vp", {})
    macro   = market.get("macro", {})
    session = market.get("session", {})
    price   = market.get("price", 0)
    tz_1h   = market.get("turtle_1h", {})
    tz_4h   = market.get("turtle_4h", {})
    indic   = market.get("indicators", {})

    score, factors = 50, []

    # CVD (+15 / -10)
    if cvd.get("trend") and cvd["trend"] != "unknown":
        cvd_bull = cvd["trend"] == "up"
        if (sig_up and cvd_bull) or (sig_dn and not cvd_bull):
            score += 15
            factors.append("CVD ✅ подтверждает направление")
        elif cvd.get("divergence"):
            score -= 10
            factors.append("CVD ⚠️ дивергенция: цена и поток расходятся")
        else:
            factors.append("CVD ⚪ нейтрально")

    # Volume Profile (+10 near key level, -5 if wrong side)
    if vp.get("poc") and price:
        poc, vah, val = vp["poc"], vp["vah"], vp["val"]
        tol = poc * 0.003
        near_vah = abs(price - vah) < tol
        near_val = abs(price - val) < tol
        near_poc = abs(price - poc) < poc * 0.002
        if near_poc:
            score += 8
            factors.append(f"VP ✅ у POC ${poc:,.0f}")
        elif (sig_dn and near_vah) or (sig_up and near_val):
            score += 10
            factors.append(f"VP ✅ у {'VAH' if near_vah else 'VAL'} ${vah if near_vah else val:,.0f}")
        elif (sig_up and near_vah) or (sig_dn and near_val):
            score -= 5
            factors.append(f"VP ❌ у {'VAH' if near_vah else 'VAL'} — против сигнала")
        else:
            in_va = val <= price <= vah
            factors.append(f"VP ⚪ {'внутри VA' if in_va else 'вне VA'} | POC ${poc:,.0f}")

    # MTF Confluence (+20/+10/0/-10)
    if mtf and "aligned" in mtf:
        al, tot = mtf["aligned"], mtf.get("total", 3)
        if al == tot:
            score += 20; factors.append(f"MTF ✅ все {tot} ТФ в направлении")
        elif al >= tot - 1:
            score += 10; factors.append(f"MTF 🟡 {al}/{tot} ТФ")
        elif al == 0:
            score -= 10; factors.append("MTF ❌ все ТФ против")
        else:
            factors.append(f"MTF ⚪ {al}/{tot} ТФ")

    # Session (+0..+8)
    sq = session.get("quality", 2)
    score += (sq - 1) * 2
    factors.append(f"Session {session.get('icon','⏰')} {session.get('name','?')} [{sq}/5]")

    # Funding (+8 / -8)
    if direction != "neutral":
        fr = b.get("funding", 0)
        if sig_up and fr < -0.0001:
            score += 8; factors.append("FR ✅ шорты переплачивают")
        elif sig_dn and fr > 0.0001:
            score += 8; factors.append("FR ✅ лонги переплачивают")
        elif sig_up and fr > 0.0001:
            score -= 8; factors.append("FR ❌ лонги перегреты")
        elif sig_dn and fr < -0.0001:
            score -= 8; factors.append("FR ❌ шорты перегреты")

    # Book ratio (+5/-5)
    ratio = hl.get("book_ratio", 1.0)
    if (sig_up and ratio > 1.1) or (sig_dn and ratio < 0.9):
        score += 5; factors.append(f"Book ✅ {'bid>ask' if sig_up else 'ask>bid'}")
    elif (sig_up and ratio < 0.9) or (sig_dn and ratio > 1.1):
        score -= 5; factors.append("Book ❌ стакан против сигнала")

    # Fear & Greed (+5 contrarian at extremes)
    fg = macro.get("fg_value")
    if fg is not None:
        icon, label = macro.get("fg_icon", "📊"), macro.get("fg_label", "?")
        if sig_up and fg < 25:
            score += 5; factors.append(f"F&G {icon} Extreme Fear {fg} — контрарный лонг")
        elif sig_dn and fg > 75:
            score += 5; factors.append(f"F&G {icon} Extreme Greed {fg} — контрарный шорт")
        else:
            factors.append(f"F&G {icon} {label} [{fg}]")

    # OI Change (+5/-5)
    oi_chg = b.get("oi_chg", 0)
    if (sig_up and oi_chg > 0.5) or (sig_dn and oi_chg < -0.5):
        score += 5; factors.append(f"OI ✅ {oi_chg:+.2f}% позиции открываются")
    elif (sig_up and oi_chg < -0.5) or (sig_dn and oi_chg > 0.5):
        score -= 5; factors.append(f"OI ❌ {oi_chg:+.2f}% позиции закрываются")

    # Turtle Zone (+15 aligned / +8 extreme / -10 opposite / -15 extreme opposite)
    for tz, tf_name in [(tz_1h, "1H"), (tz_4h, "4H")]:
        z = tz.get("zone", "")
        if not z:
            continue
        pct = tz.get("pct_from_mean", 0)
        if sig_up and z in ("lower", "extreme_lower"):
            pts = 15 if z == "extreme_lower" else 10
            score += pts
            factors.append(f"TZ {tf_name} ✅ {tz['icon']} {tz['label']} [{pct:+.1f}%]")
        elif sig_dn and z in ("upper", "extreme_upper"):
            pts = 15 if z == "extreme_upper" else 10
            score += pts
            factors.append(f"TZ {tf_name} ✅ {tz['icon']} {tz['label']} [{pct:+.1f}%]")
        elif sig_up and z in ("upper", "extreme_upper"):
            pts = -15 if z == "extreme_upper" else -10
            score += pts
            factors.append(f"TZ {tf_name} ❌ {tz['icon']} Цена перегрета [{pct:+.1f}%]")
        elif sig_dn and z in ("lower", "extreme_lower"):
            pts = -15 if z == "extreme_lower" else -10
            score += pts
            factors.append(f"TZ {tf_name} ❌ {tz['icon']} Цена перепродана [{pct:+.1f}%]")
        else:
            factors.append(f"TZ {tf_name} ⚪ Нейтральная [{pct:+.1f}% от mean]")

    # RSI (+10 aligned extreme / -10 opposite extreme)
    rsi = indic.get("rsi")
    if rsi is not None:
        if sig_up and rsi < 30:
            score += 10; factors.append(f"RSI ✅ перепродан [{rsi:.0f}] — хороший лонг")
        elif sig_dn and rsi > 70:
            score += 10; factors.append(f"RSI ✅ перекуплен [{rsi:.0f}] — хороший шорт")
        elif sig_up and rsi > 70:
            score -= 10; factors.append(f"RSI ❌ перекуплен [{rsi:.0f}] — рискованный лонг")
        elif sig_dn and rsi < 30:
            score -= 10; factors.append(f"RSI ❌ перепродан [{rsi:.0f}] — рискованный шорт")
        else:
            factors.append(f"RSI ⚪ [{rsi:.0f}]")

    # MACD (+10 cross aligned / +5 trend aligned / -5 trend opposite)
    macd = indic.get("macd", {})
    if macd:
        cross = macd.get("cross", "none")
        trend = macd.get("trend", "")
        if   cross == "golden" and sig_up: score += 10; factors.append("MACD ✅ золотой крест")
        elif cross == "death"  and sig_dn: score += 10; factors.append("MACD ✅ мёртвый крест")
        elif trend == "bull"   and sig_up: score +=  5; factors.append("MACD 🟡 бычий тренд")
        elif trend == "bear"   and sig_dn: score +=  5; factors.append("MACD 🟡 медвежий тренд")
        elif trend == "bear"   and sig_up: score -=  5; factors.append("MACD ❌ медвежий при лонге")
        elif trend == "bull"   and sig_dn: score -=  5; factors.append("MACD ❌ бычий при шорте")

    # Bollinger Bands (+8 at extreme / -8 at wrong extreme)
    bb = indic.get("bb", {})
    bb_pos = bb.get("position", "")
    if bb_pos:
        if   sig_up and bb_pos == "below_lower": score += 8; factors.append(f"BB ✅ ниже нижней полосы [%B:{bb['pct_b']:.2f}]")
        elif sig_dn and bb_pos == "above_upper": score += 8; factors.append(f"BB ✅ выше верхней полосы [%B:{bb['pct_b']:.2f}]")
        elif sig_up and bb_pos == "above_upper": score -= 8; factors.append("BB ❌ выше верхней при лонге")
        elif sig_dn and bb_pos == "below_lower": score -= 8; factors.append("BB ❌ ниже нижней при шорте")
        else: factors.append(f"BB ⚪ {bb.get('icon','⚪')} [%B:{bb.get('pct_b',0.5):.2f}]")

    # Pivot Points (+8 near key level)
    piv = market.get("pivots", {})
    if piv and piv.get("price"):
        cur_price = piv["price"]
        ns = piv.get("nearest_sup")
        nr = piv.get("nearest_res")
        if ns and sig_up:
            dist_pct = abs(cur_price - ns[1]) / cur_price * 100
            if dist_pct < 1.5:
                score += 8; factors.append(f"Pivot ✅ цена у поддержки {ns[0]}:${ns[1]:,.0f} ({dist_pct:.1f}%)")
        if nr and sig_dn:
            dist_pct = abs(nr[1] - cur_price) / cur_price * 100
            if dist_pct < 1.5:
                score += 8; factors.append(f"Pivot ✅ цена у сопротивления {nr[0]}:${nr[1]:,.0f} ({dist_pct:.1f}%)")

    # Funding rate trend (+5 aligned / -5 opposite)
    fr_hist = market.get("fr_history", {})
    if fr_hist.get("trend"):
        fr_trend = fr_hist["trend"]
        if   sig_up and fr_trend == "falling": score += 5;  factors.append("FR Trend ✅ funding падает → шорты закрываются")
        elif sig_dn and fr_trend == "rising":  score += 5;  factors.append("FR Trend ✅ funding растёт → лонги перегреты")
        elif sig_up and fr_trend == "rising":  score -= 5;  factors.append("FR Trend ⚠️ funding растёт → лонги перегреты")
        elif sig_dn and fr_trend == "falling": score -= 5;  factors.append("FR Trend ⚠️ funding падает при шорте")

    # RSI Divergence (+12 / -12) — strong reversal signal
    rsi_div = indic.get("rsi_div", "none")
    if rsi_div == "bullish" and sig_up:
        score += 12; factors.append("RSI Div ✅ бычья дивергенция — разворот вверх")
    elif rsi_div == "bearish" and sig_dn:
        score += 12; factors.append("RSI Div ✅ медвежья дивергенция — разворот вниз")
    elif rsi_div == "bearish" and sig_up:
        score -= 12; factors.append("RSI Div ❌ медвежья дивергенция против лонга")
    elif rsi_div == "bullish" and sig_dn:
        score -= 12; factors.append("RSI Div ❌ бычья дивергенция против шорта")

    # EMA 9/21 Cross (+8 aligned / -8 opposite)
    ema_cross = indic.get("ema_cross", "none")
    if ema_cross == "golden" and sig_up:
        score += 8;  factors.append("EMA ✨ Golden Cross 9/21 подтверждает лонг")
    elif ema_cross == "death" and sig_dn:
        score += 8;  factors.append("EMA 💀 Death Cross 9/21 подтверждает шорт")
    elif ema_cross == "golden" and sig_dn:
        score -= 8;  factors.append("EMA ✨ Golden Cross против шорта")
    elif ema_cross == "death" and sig_up:
        score -= 8;  factors.append("EMA 💀 Death Cross против лонга")

    # Volume Spike (+5 as attention signal)
    if indic.get("vol_spike"):
        score += 5; factors.append("🔊 Volume Spike — повышенный интерес")

    # VWAP (+10 in discount for long / premium for short, -8 opposite, +5 extreme band)
    vwap = market.get("vwap", {})
    if vwap.get("daily_pos"):
        pos = vwap["daily_pos"]
        pct = vwap.get("daily_pct", 0)
        in_discount = pos in ("discount", "lower", "extreme_lower")
        in_premium  = pos in ("premium", "upper", "extreme_upper")
        is_extreme  = pos in ("extreme_lower", "extreme_upper")
        if sig_up and in_discount:
            score += 10 + (5 if is_extreme else 0)
            factors.append(f"VWAP ✅ цена в дисконте [{pct:+.1f}%] — хорошая точка лонга")
        elif sig_dn and in_premium:
            score += 10 + (5 if is_extreme else 0)
            factors.append(f"VWAP ✅ цена в премиуме [{pct:+.1f}%] — хорошая точка шорта")
        elif sig_up and in_premium:
            score -= 8
            factors.append(f"VWAP ❌ покупка в премиуме [{pct:+.1f}%] — переплата")
        elif sig_dn and in_discount:
            score -= 8
            factors.append(f"VWAP ❌ шорт в дисконте [{pct:+.1f}%] — контртренд")
        else:
            factors.append(f"VWAP ⚪ у уровня [{pct:+.1f}%]")

    # Long/Short ratio (+8 / -8)
    ls = market.get("ls_ratio", {})
    taker = ls.get("taker_ratio")
    if taker is not None:
        if   sig_up and taker > 1.1: score += 8;  factors.append(f"Taker ✅ buyers доминируют [{taker:.2f}]")
        elif sig_dn and taker < 0.9: score += 8;  factors.append(f"Taker ✅ sellers доминируют [{taker:.2f}]")
        elif sig_up and taker < 0.9: score -= 8;  factors.append(f"Taker ❌ sellers при лонге [{taker:.2f}]")
        elif sig_dn and taker > 1.1: score -= 8;  factors.append(f"Taker ❌ buyers при шорте [{taker:.2f}]")

    # Liquidations domination (+5 / -5)
    liqs = market.get("liquidations", {})
    if liqs.get("liq_total_usd", 0) > 100_000:
        dom = liqs.get("liq_dom", "")
        if   sig_up and dom == "long":  score += 5;  factors.append("Liqs ✅ лонги ликвидированы — контрариан лонг")
        elif sig_dn and dom == "short": score += 5;  factors.append("Liqs ✅ шорты ликвидированы — контрариан шорт")

    return max(0, min(100, score)), factors


# ─── HYPERLIQUID ──────────────────────────────────────────────────────────────

def _hl_coin(symbol: str) -> str:
    return symbol.replace("USDT.P", "").replace("USDT", "").replace(".P", "")


def _hl_meta() -> tuple:
    r    = requests.post(HL, json={"type": "metaAndAssetCtxs"}, timeout=8)
    data = r.json()
    return data[0]["universe"], data[1]


def _hl_data(symbol: str) -> dict:
    coin = _hl_coin(symbol)
    out  = {"source": "hyperliquid", "coin": coin}
    try:
        universe, ctxs = _hl_meta()
        idx = next((i for i, u in enumerate(universe) if u["name"] == coin), None)
        if idx is None:
            return {"error": f"{coin} not found on HL"}

        ctx    = ctxs[idx]
        mark   = float(ctx.get("markPx") or ctx.get("midPx") or 0)
        prev   = float(ctx.get("prevDayPx") or 0)
        oi_raw = float(ctx.get("openInterest") or 0)
        fr_hr  = float(ctx.get("funding") or 0)

        out["price"]      = mark
        out["funding"]    = fr_hr * 8
        out["funding_hr"] = fr_hr
        out["oi_usd"]     = oi_raw * mark
        out["oi_coins"]   = oi_raw
        out["change_24h"] = (mark - prev) / prev * 100 if prev else 0.0
        out["vol_24h"]    = float(ctx.get("dayNtlVlm") or 0)
        out["premium"]    = float(ctx.get("premium") or 0)
    except Exception as e:
        log.warning(f"HL meta {symbol}: {e}")

    try:
        book   = requests.post(HL, json={"type": "l2Book", "coin": coin}, timeout=6).json()
        levels = book.get("levels", [[], []])
        bids, asks = levels[0], levels[1]

        def depth(side, n=8):
            t = 0.0
            for lvl in side[:n]:
                try:
                    t += float(lvl["px"]) * float(lvl["sz"])
                except (KeyError, ValueError, TypeError):
                    pass
            return t

        bd = depth(bids); ad = depth(asks)
        out["bid_depth"]  = bd
        out["ask_depth"]  = ad
        out["book_ratio"] = bd / ad if ad else 1.0
        out["best_bid"]   = float(bids[0]["px"]) if bids else 0
        out["best_ask"]   = float(asks[0]["px"]) if asks else 0
        out["spread_pct"] = (out["best_ask"] - out["best_bid"]) / out["best_bid"] * 100 if out.get("best_bid") else 0
    except Exception as e:
        log.warning(f"HL orderbook {coin}: {e}")

    try:
        trades = requests.post(HL, json={"type": "recentTrades", "coin": coin}, timeout=6).json()
        large = []
        for t in trades:
            try:
                sz_usd = float(t["px"]) * float(t["sz"])
                if sz_usd >= 300_000:
                    large.append({"side": "BUY" if t.get("side") == "B" else "SELL",
                                  "usd": sz_usd, "price": float(t["px"])})
            except (KeyError, ValueError, TypeError):
                pass
        out["large_trades"] = large[:5]
    except Exception as e:
        log.warning(f"HL trades {coin}: {e}")
        out["large_trades"] = []

    return out


# ─── BINANCE LIQUIDITY ────────────────────────────────────────────────────────

def _binance_book(symbol: str, depth: int = 500) -> dict:
    """
    Binance Futures orderbook: find liquidity walls + bid/ask imbalance.
    Wall threshold: $500K USD at a single price level.
    """
    try:
        r = requests.get(
            f"{BINANCE_FAPI}/fapi/v1/depth",
            params={"symbol": symbol, "limit": depth}, timeout=8,
        )
        data = r.json()
        if "bids" not in data:
            return {}

        bids = [[float(p), float(q)] for p, q in data["bids"]]
        asks = [[float(p), float(q)] for p, q in data["asks"]]

        WALL_USD = 500_000  # $500K+

        def find_walls(levels):
            walls = []
            for price, qty in levels:
                usd = price * qty
                if usd >= WALL_USD:
                    walls.append({"price": price, "usd_m": round(usd / 1e6, 2)})
            return sorted(walls, key=lambda x: x["usd_m"], reverse=True)[:4]

        bid_walls = find_walls(bids)
        ask_walls = find_walls(asks)
        bid_depth = sum(p * q for p, q in bids[:50])
        ask_depth = sum(p * q for p, q in asks[:50])
        ratio     = round(bid_depth / ask_depth, 3) if ask_depth > 0 else 1.0

        return {
            "bid_walls": bid_walls,
            "ask_walls": ask_walls,
            "bid_depth": bid_depth,
            "ask_depth": ask_depth,
            "ratio":     ratio,
        }
    except Exception as e:
        log.warning(f"Binance book {symbol}: {e}")
        return {}


# ─── PIVOT POINTS ────────────────────────────────────────────────────────────

def compute_pivot_points(daily_candles: list) -> dict:
    """
    Classic Pivot Points from previous day's H/L/C.
    Returns P, R1-R3, S1-S3 and nearby levels relative to current price.
    """
    if len(daily_candles) < 2:
        return {}
    prev  = daily_candles[-2]   # previous completed day
    cur   = daily_candles[-1]
    H, L, C = prev["h"], prev["l"], prev["c"]
    price = cur["c"]

    P  = (H + L + C) / 3
    R1 = 2 * P - L
    R2 = P + (H - L)
    R3 = H + 2 * (P - L)
    S1 = 2 * P - H
    S2 = P - (H - L)
    S3 = L - 2 * (H - P)

    levels = {"P": P, "R1": R1, "R2": R2, "R3": R3,
              "S1": S1, "S2": S2, "S3": S3}

    # Find nearest support (below price) and resistance (above price)
    supports    = {k: v for k, v in levels.items() if v < price}
    resistances = {k: v for k, v in levels.items() if v > price}
    nearest_sup = max(supports.items(),    key=lambda x: x[1]) if supports    else None
    nearest_res = min(resistances.items(), key=lambda x: x[1]) if resistances else None

    return {
        "P": round(P, 2), "R1": round(R1, 2), "R2": round(R2, 2), "R3": round(R3, 2),
        "S1": round(S1, 2), "S2": round(S2, 2), "S3": round(S3, 2),
        "nearest_sup": (nearest_sup[0], round(nearest_sup[1], 2)) if nearest_sup else None,
        "nearest_res": (nearest_res[0], round(nearest_res[1], 2)) if nearest_res else None,
        "price": price,
    }


# ─── FUNDING RATE TREND ───────────────────────────────────────────────────────

def _funding_history(symbol: str, limit: int = 8) -> dict:
    """
    Fetch last `limit` funding rate snapshots from Bybit (every 8H).
    Returns trend: 'rising', 'falling', 'neutral' + last value.
    """
    out = {}
    try:
        r = requests.get(
            f"{BYBIT}/v5/market/funding/history",
            params={"symbol": symbol, "category": "linear", "limit": limit},
            timeout=6,
        )
        items = r.json()["result"]["list"]   # newest first
        if len(items) < 4:
            return out
        rates = [float(x["fundingRate"]) * 100 for x in reversed(items)]  # oldest→newest
        avg_old = sum(rates[:len(rates)//2]) / (len(rates)//2)
        avg_new = sum(rates[len(rates)//2:]) / (len(rates)//2)
        diff    = avg_new - avg_old
        trend   = "rising" if diff > 0.001 else ("falling" if diff < -0.001 else "neutral")
        out = {
            "rates":   [round(r, 4) for r in rates],
            "current": round(rates[-1], 4),
            "trend":   trend,
            "diff":    round(diff, 4),
            "icon":    "📈" if trend == "rising" else ("📉" if trend == "falling" else "➡️"),
        }
    except Exception as e:
        log.warning(f"Funding history {symbol}: {e}")
    return out


# ─── LONG/SHORT RATIO + LIQUIDATIONS ─────────────────────────────────────────

def _ls_ratio(symbol: str) -> dict:
    """Long/Short account ratio from Bybit + Binance (1H latest)."""
    out = {}
    # Bybit
    try:
        r = requests.get(
            f"{BYBIT}/v5/market/account-ratio",
            params={"symbol": symbol, "category": "linear",
                    "period": "1h", "limit": 1}, timeout=6,
        )
        item = r.json()["result"]["list"][0]
        out["bybit_long"]  = round(float(item["buyRatio"])  * 100, 1)
        out["bybit_short"] = round(float(item["sellRatio"]) * 100, 1)
    except Exception as e:
        log.warning(f"Bybit L/S ratio {symbol}: {e}")

    # Binance global L/S account ratio
    try:
        r = requests.get(
            f"{BINANCE_FAPI}/futures/data/globalLongShortAccountRatio",
            params={"symbol": symbol, "period": "1h", "limit": 1}, timeout=6,
        )
        item = r.json()[0]
        out["bnb_long"]  = round(float(item["longAccount"])  * 100, 1)
        out["bnb_short"] = round(float(item["shortAccount"]) * 100, 1)
        out["bnb_ratio"] = round(float(item["longShortRatio"]), 3)
    except Exception as e:
        log.warning(f"Binance L/S ratio {symbol}: {e}")

    # Binance taker buy/sell volume ratio (aggression indicator)
    try:
        r = requests.get(
            f"{BINANCE_FAPI}/futures/data/takerlongshortRatio",
            params={"symbol": symbol, "period": "1h", "limit": 1}, timeout=6,
        )
        item = r.json()[0]
        out["taker_ratio"] = round(float(item["buySellRatio"]), 3)
        out["taker_buy"]   = round(float(item["buyVol"]), 1)
        out["taker_sell"]  = round(float(item["sellVol"]), 1)
    except Exception as e:
        log.warning(f"Binance taker ratio {symbol}: {e}")

    return out


def _liq_stats(symbol: str) -> dict:
    """Recent liquidations from Binance public force orders (last ~100)."""
    out = {}
    try:
        r = requests.get(
            f"{BINANCE_FAPI}/fapi/v1/forceOrders",
            params={"symbol": symbol, "limit": 100}, timeout=6,
        )
        orders = r.json()
        if not isinstance(orders, list):
            return out

        cutoff = (time.time() - 3600) * 1000  # last 1 hour
        liq_long_usd  = 0.0   # long liquidated (SELL orders)
        liq_short_usd = 0.0   # short liquidated (BUY orders)

        for o in orders:
            if float(o.get("time", 0)) < cutoff:
                continue
            usd = float(o.get("origQty", 0)) * float(o.get("price", 0))
            if o.get("side") == "SELL":
                liq_long_usd  += usd   # long position liquidated
            else:
                liq_short_usd += usd   # short position liquidated

        out["liq_long_usd"]  = liq_long_usd
        out["liq_short_usd"] = liq_short_usd
        out["liq_total_usd"] = liq_long_usd + liq_short_usd
        out["liq_dom"]       = "long" if liq_long_usd > liq_short_usd else "short"
    except Exception as e:
        log.warning(f"Binance force orders {symbol}: {e}")

    return out


# ─── COMBINED FETCH (parallel) ────────────────────────────────────────────────

def fetch_market(symbol: str) -> dict:
    base = symbol.replace(".P", "")
    if not base.endswith("USDT"):
        base += "USDT"

    # Fetch Deribit options for BTC/ETH only (not every altcoin)
    coin = base.replace("USDT", "")
    deribit_currency = coin if coin in ("BTC", "ETH") else None

    need_btc_corr = base != "BTCUSDT"
    with ThreadPoolExecutor(max_workers=13) as ex:
        f_bybit   = ex.submit(_bybit_data, base)
        f_k1h     = ex.submit(_klines, base, "60",  250)
        f_k4h     = ex.submit(_klines, base, "240", 250)
        f_k1d     = ex.submit(_klines, base, "D",    50)
        f_hl      = ex.submit(_hl_data, base)
        f_bnb     = ex.submit(_binance_book, base)
        f_macro   = ex.submit(get_macro)
        f_ls      = ex.submit(_ls_ratio, base)
        f_liq     = ex.submit(_liq_stats, base)
        f_fr      = ex.submit(_funding_history, base)
        f_options = ex.submit(_deribit_options, deribit_currency) if deribit_currency else None
        f_btc_k1h = ex.submit(_klines, "BTCUSDT", "60", 250) if need_btc_corr else None

    bybit    = f_bybit.result()
    k1h      = f_k1h.result()
    k4h      = f_k4h.result()
    k1d      = f_k1d.result()
    hl       = f_hl.result()
    liq_bnb  = f_bnb.result()
    macro    = f_macro.result()
    ls       = f_ls.result()
    liqs     = f_liq.result()
    fr_hist  = f_fr.result()
    options  = f_options.result() if f_options else {}
    btc_k1h  = f_btc_k1h.result() if f_btc_k1h else []
    session  = get_session()

    cvd        = compute_cvd(k1h)
    btc_corr   = compute_btc_correlation(k1h, btc_k1h) if need_btc_corr and btc_k1h else None
    vp         = compute_volume_profile(k1h)
    ema_biases = get_ema_biases(k1h, k4h, k1d)
    tz_1h      = compute_turtle_zone(k1h)
    tz_4h      = compute_turtle_zone(k4h)
    indicators = compute_indicators(k1h)
    vwap       = compute_vwap(k1h)
    pivots     = compute_pivot_points(k1d)

    fr_div    = abs(bybit.get("funding", 0) - hl.get("funding", 0)) * 100
    fr_signal = fr_div > 0.005

    return {
        "bybit":                bybit,
        "hl":                   hl,
        "price":                bybit.get("price") or hl.get("price", 0),
        "change_24h":           bybit.get("change_24h", 0),
        "fr_divergence":        fr_div,
        "fr_divergence_signal": fr_signal,
        "cvd":                  cvd,
        "vp":                   vp,
        "ema_biases":           ema_biases,
        "turtle_1h":            tz_1h,
        "turtle_4h":            tz_4h,
        "indicators":           indicators,
        "vwap":                 vwap,
        "pivots":               pivots,
        "fr_history":           fr_hist,
        "liquidity":            liq_bnb,
        "ls_ratio":             ls,
        "liquidations":         liqs,
        "options":              options,
        "macro":                macro,
        "session":              session,
        "btc_corr":             btc_corr,
        # raw klines reused by cmd_analyze_symbol (avoids duplicate API calls)
        "_klines": {"60": k1h, "240": k4h, "D": k1d},
    }


# ─── MARKET SUMMARY TEXT (for LLM context) ───────────────────────────────────

def market_summary_text(symbol: str, m: dict) -> str:
    b, hl  = m.get("bybit", {}), m.get("hl", {})
    price  = m.get("price", 0)
    chg    = m.get("change_24h", 0)
    fr_b   = b.get("funding", 0) * 100
    fr_hl  = hl.get("funding", 0) * 100
    oi_chg = b.get("oi_chg", 0)
    oi_usd = hl.get("oi_usd", 0)
    cvd    = m.get("cvd", {})
    vp     = m.get("vp", {})
    macro  = m.get("macro", {})
    sess   = m.get("session", {})
    biases = m.get("ema_biases", {})

    def fr_tag(fr):
        if fr > 0.01:  return "🔴 лонги переплачивают"
        if fr < -0.01: return "🟢 шорты переплачивают"
        return "⚪ нейтральный"

    ratio    = hl.get("book_ratio", 1.0)
    book_str = f"{ratio:.2f} ({'🟢 bid>ask' if ratio > 1.1 else ('🔴 ask>bid' if ratio < 0.9 else '⚪ баланс')})"

    lt = hl.get("large_trades", [])
    lt_str = ""
    if lt:
        parts = [f"{'🟢' if t['side']=='BUY' else '🔴'} ${t['usd']/1e6:.2f}M {t['side']}@${t['price']:,.0f}" for t in lt[:3]]
        lt_str = "\n• Крупные сделки HL: " + " | ".join(parts)

    div_str = ""
    if m.get("fr_divergence_signal"):
        div_str = f"\n⚠️ Расхождение FR Bybit↔HL: {m['fr_divergence']:.4f}%"

    prem_str = f"\n• HL Premium: {hl.get('premium',0)*100:+.4f}%" if hl.get("premium") else ""

    cvd_str = ""
    if cvd.get("trend") and cvd["trend"] != "unknown":
        cvd_str = (f"\n• CVD (1H): {'📈' if cvd['trend']=='up' else '📉'} {cvd['trend'].upper()}"
                   + (" ⚠️ ДИВЕРГЕНЦИЯ" if cvd.get("divergence") else ""))

    vp_str = ""
    if vp.get("poc"):
        vp_str = f"\n• VP: POC ${vp['poc']:,.0f} | VAH ${vp['vah']:,.0f} | VAL ${vp['val']:,.0f}"

    mtf_str = ""
    if biases:
        parts = [f"{tf}:{'🟢' if b=='bull' else ('🔴' if b=='bear' else '❓')}" for tf, b in biases.items()]
        mtf_str = "\n• MTF EMA20: " + " | ".join(parts)

    macro_str = ""
    if macro.get("fg_value") is not None:
        macro_str = (f"\n• F&G: {macro['fg_icon']} {macro['fg_label']} [{macro['fg_value']}]"
                     + (f" | BTC Dom: {macro.get('btc_dom')}%" if macro.get("btc_dom") else ""))

    def _t(v):
        """Format market cap: $2.31T / $890B"""
        if v >= 1e12: return f"${v/1e12:.2f}T"
        if v >= 1e9:  return f"${v/1e9:.1f}B"
        return f"${v/1e6:.0f}M"

    mstruct_str = ""
    if macro.get("total_mcap"):
        chg  = macro.get("mcap_chg24", 0)
        chgi = "📈" if chg >= 0 else "📉"
        usdt_d = macro.get("usdt_dom", 0)
        usdt_i = "🔴" if usdt_d > 7 else ("🟡" if usdt_d > 5 else "🟢")  # high = money on sidelines
        mstruct_str = (
            f"\n• Market Structure:"
            f"\n  TOTAL:  {_t(macro['total_mcap'])} ({chgi}{chg:+.1f}% 24h)"
            f"\n  TOTAL2: {_t(macro['total2'])}  TOTAL3: {_t(macro['total3'])}"
            f"\n  OTHERS: {_t(macro['others'])}"
            f"\n  BTC Dom: {macro['btc_dom']}%  ETH: {macro.get('eth_dom',0)}%  "
            f"Stables: {usdt_i}{usdt_d}%"
        )

    sess_str = f"\n• Session: {sess.get('icon','')} {sess.get('name','')} [{sess.get('quality','?')}/5]"

    tz_str = ""
    for tf_key, tf_name in [("turtle_1h", "1H"), ("turtle_4h", "4H")]:
        tz = m.get(tf_key, {})
        if tz:
            tz_str += (f"\n• Turtle Zone {tf_name}: {tz['icon']} {tz['label']}"
                       f" [{tz['pct_from_mean']:+.1f}% от mean ${tz['mean']:,.0f}]")

    ind = m.get("indicators", {})
    ind_str = ""
    if ind:
        rsi   = ind.get("rsi", 0)
        macd  = ind.get("macd", {})
        bb    = ind.get("bb", {})
        stoch = ind.get("stoch", {})
        rsi_icon = "🔴" if rsi > 70 else ("🟢" if rsi < 30 else "⚪")
        macd_icon = "📈" if macd.get("trend") == "bull" else "📉"
        cross_str = f" [{macd.get('cross','')}]" if macd.get("cross") != "none" else ""
        atr     = ind.get("atr", 0)
        atr_pct = ind.get("atr_pct", 0)
        div     = ind.get("rsi_div", "none")
        ecross  = ind.get("ema_cross", "none")
        vspike  = ind.get("vol_spike", False)
        extras  = []
        if div != "none":    extras.append(f"RSI Div:{'🟢' if div=='bullish' else '🔴'}{div}")
        if ecross != "none": extras.append(f"EMA9/21:{'✨golden' if ecross=='golden' else '💀death'}")
        if vspike:           extras.append("🔊VolSpike")
        extras_str = " | " + " | ".join(extras) if extras else ""
        ind_str = (
            f"\n• Индикаторы (1H): RSI14:{rsi_icon}{rsi:.0f}"
            f" | MACD:{macd_icon}{cross_str}"
            f" | BB:{bb.get('icon','⚪')}[%B:{bb.get('pct_b',0.5):.2f}]"
            f" | Stoch:{stoch.get('icon','⚪')}K:{stoch.get('k',50):.0f}"
            f" | ATR:{atr:,.2f}({atr_pct:.2f}%){extras_str}"
        )

    piv = m.get("pivots", {})
    piv_str = ""
    if piv and piv.get("P") is not None:
        ns = piv.get("nearest_sup")
        nr = piv.get("nearest_res")
        sup_s = f"{ns[0]}:${ns[1]:,.0f}" if ns else "—"
        res_s = f"{nr[0]}:${nr[1]:,.0f}" if nr else "—"
        piv_str = (f"\n• Pivot Points (Daily): P=${piv['P']:,.0f}"
                   f" | Sup:{sup_s} | Res:{res_s}")

    fr_hist = m.get("fr_history", {})
    fr_hist_str = ""
    if fr_hist:
        rates_s = " → ".join(f"{r:+.4f}%" for r in fr_hist.get("rates", [])[-4:])
        fr_hist_str = (f"\n• Funding Trend: {fr_hist['icon']} {fr_hist['trend'].upper()}"
                       f" [{rates_s}]")

    vwap = m.get("vwap", {})
    vwap_str = ""
    if vwap:
        _pos_icon = {
            "extreme_upper": "🔴🔴", "upper": "🔴", "premium": "🟡",
            "discount": "🟡", "lower": "🟢", "extreme_lower": "🟢🟢",
        }
        _pos_label = {
            "extreme_upper": "Extreme Premium +2σ",
            "upper":         "Premium +1σ",
            "premium":       "Premium",
            "discount":      "Discount",
            "lower":         "Discount -1σ",
            "extreme_lower": "Extreme Discount -2σ",
        }
        parts = []
        if vwap.get("daily"):
            d   = vwap["daily"]
            pos = vwap.get("daily_pos", "")
            pct = vwap.get("daily_pct", 0)
            parts.append(f"D-VWAP ${d['vwap']:,.0f} {_pos_icon.get(pos,'⚪')} {pct:+.1f}% [{_pos_label.get(pos,'')}]")
        if vwap.get("weekly"):
            w   = vwap["weekly"]
            pos = vwap.get("weekly_pos", "")
            pct = vwap.get("weekly_pct", 0)
            parts.append(f"W-VWAP ${w['vwap']:,.0f} {pct:+.1f}%")
        vwap_str = "\n• VWAP: " + " | ".join(parts) if parts else ""

    liq = m.get("liquidity", {})
    liq_str = ""
    if liq:
        ratio = liq.get("ratio", 1.0)
        r_icon = "🟢" if ratio > 1.1 else ("🔴" if ratio < 0.9 else "⚪")
        bw = liq.get("bid_walls", [])
        aw = liq.get("ask_walls", [])
        bw_str = " · ".join(f"${w['price']:,.0f}(${w['usd_m']:.1f}M)" for w in bw[:2]) or "—"
        aw_str = " · ".join(f"${w['price']:,.0f}(${w['usd_m']:.1f}M)" for w in aw[:2]) or "—"
        liq_str = (
            f"\n• Ликвидность Binance: bid/ask {r_icon}{ratio:.2f}"
            f"\n  Bid стены: 🟢 {bw_str}"
            f"\n  Ask стены: 🔴 {aw_str}"
        )

    opt = m.get("options", {})
    opt_str = ""
    if opt:
        pcr      = opt.get("pc_ratio", 0)
        pcr_icon = "🔴" if pcr > 1.2 else ("🟢" if pcr < 0.8 else "⚪")
        mp       = opt.get("max_pain")
        exp      = opt.get("nearest_expiry", "")
        mp_str   = f" | Max Pain ${mp:,.0f} ({exp})" if mp else ""
        opt_str  = f"\n• Options (Deribit): P/C {pcr_icon}{pcr:.2f}{mp_str}"

    ls = m.get("ls_ratio", {})
    ls_str = ""
    if ls:
        bybit_l = ls.get("bybit_long")
        bnb_l   = ls.get("bnb_long")
        taker   = ls.get("taker_ratio")
        parts   = []
        if bybit_l is not None:
            icon = "🟢" if bybit_l > 55 else ("🔴" if bybit_l < 45 else "⚪")
            parts.append(f"Bybit {icon}L:{bybit_l:.0f}%/S:{ls['bybit_short']:.0f}%")
        if bnb_l is not None:
            icon = "🟢" if bnb_l > 55 else ("🔴" if bnb_l < 45 else "⚪")
            parts.append(f"BNB {icon}L:{bnb_l:.0f}%/S:{ls['bnb_short']:.0f}%")
        if taker is not None:
            icon = "🟢" if taker > 1.1 else ("🔴" if taker < 0.9 else "⚪")
            parts.append(f"Taker {icon}{taker:.2f}")
        ls_str = "\n• L/S Ratio: " + " | ".join(parts) if parts else ""

    liqs = m.get("liquidations", {})
    liq_str2 = ""
    if liqs.get("liq_total_usd", 0) > 0:
        total = liqs["liq_total_usd"]
        ll    = liqs.get("liq_long_usd", 0)
        ls_   = liqs.get("liq_short_usd", 0)
        dom   = liqs.get("liq_dom", "")
        dom_icon = "🔴 лонги" if dom == "long" else "🟢 шорты"
        liq_str2 = (f"\n• Ликвидации 1H: ${total/1e6:.2f}M"
                    f" (🔴L:${ll/1e6:.2f}M · 🟢S:${ls_/1e6:.2f}M) домин:{dom_icon}")

    return (
        f"• Цена: ${price:,.2f} ({chg:+.2f}% 24h)\n"
        f"• Funding Bybit (8h): {fr_b:+.4f}% — {fr_tag(fr_b)}\n"
        f"• Funding HL (8h):    {fr_hl:+.4f}% — {fr_tag(fr_hl)}"
        f"{div_str}\n"
        f"• OI изм. 15м (Bybit): {oi_chg:+.2f}%\n"
        f"• OI USD (HL): ${oi_usd/1e9:.2f}B\n"
        f"• Book imbalance (HL): {book_str}"
        f"{lt_str}"
        f"{prem_str}"
        f"{cvd_str}"
        f"{vp_str}"
        f"{mtf_str}"
        f"{tz_str}"
        f"{ind_str}"
        f"{piv_str}"
        f"{fr_hist_str}"
        f"{vwap_str}"
        f"{liq_str}"
        f"{opt_str}"
        f"{ls_str}"
        f"{liq_str2}"
        f"{mstruct_str}"
        f"{macro_str}"
        f"{sess_str}"
    )


# ─── LLM ──────────────────────────────────────────────────────────────────────

SYSTEM_SIGNAL = """\
Ты — профессиональный крипто-аналитик уровня prop firm (SMC / ICT / Order Flow / CVD).
Анализируй торговые сигналы кратко, точно и по существу.
Только русский язык. Строго 4–5 предложений, не больше.

Структура ответа:
1. Качество сигнала [X/10] и почему
2. Confluence Score и ключевые подтверждения/противоречия
3. Конкретная рекомендация (вход / ждать / избегать)
4. Главный риск

Без приветствий, без общих слов."""

SYSTEM_CHART = """\
Ты — профессиональный крипто-аналитик уровня prop firm (SMC, ICT, Wyckoff, Price Action).
Трейдер прислал скриншот графика — проанализируй его и сравни со своими данными.

Структура ответа (строго, только русский язык):
1. 📊 Что видишь на графике: структура, ключевые уровни, паттерны, тренд
2. 🔍 Сравнение с объективными данными (CVD, funding, OI, book, MTF)
3. ✅ Где вы согласны / ❌ где расходитесь во мнениях
4. 🎯 Итоговая рекомендация: вход, стоп, цель или "ждать"

Максимум 6–8 предложений. Без воды, без приветствий."""


# ─── TICKER HELPERS ───────────────────────────────────────────────────────────

# Step 1: catch explicit pair formats — BTCUSDT, ETH/USDT, SOLUSDT.P, BTC-USDT
_PAIR_RE = re.compile(r'\b([A-Z0-9]{2,12})[-/]?USDT(?:\.P)?\b', re.IGNORECASE)

# Step 2: fallback — known standalone coin names (expanded list)
_COIN_RE = re.compile(
    r'\b(BTC|ETH|SOL|BNB|XRP|ADA|AVAX|DOT|MATIC|LINK|DOGE|LTC|UNI|ATOM|'
    r'NEAR|FTM|ARB|OP|APT|SUI|SEI|TIA|INJ|PEPE|WIF|TON|HBAR|RENDER|BONK|'
    r'FLOKI|TRUMP|EIGEN|GOAT|PNUT|MEME|TURBO|ACT|NEIRO|POPCAT|DOGS|CATI|'
    r'DRIFT|ZETA|MEW|MOG|BOME|NOT|SAGA|AEVO|BLUR|GMX|DYDX|SNX|CRV|AAVE|'
    r'COMP|MKR|LDO|RPL|FXS|CVX|BAL|YFI|SUSHI|UNI|1INCH|ENS|IMX|GODS|'
    r'SAND|MANA|AXS|GALA|ILV|ALICE|FLOW|CHZ|ENJ|AUDIO|ROSE|KAVA|BAND|'
    r'ZRX|STORJ|ANKR|CELR|SKL|NKN|CTSI|LRC|OMG|REN|KNC|OCEAN|FET|AGIX|'
    r'RNDR|GRT|API3|MASK|BADGER|ALPHA|PERP|DODO|MDX|RAY|SRM|MNGO|STEP)\b',
    re.IGNORECASE,
)

_TF_RE  = re.compile(
    r'\b(1m|3m|5m|15m|30m|1h|2h|4h|1d|1w|m5|m15|m30|h1|h4|d1)\b',
    re.IGNORECASE,
)
_TF_MAP = {
    # Standard
    "1m":"1","3m":"3","5m":"5","15m":"15","30m":"30",
    "1h":"60","2h":"120","4h":"240","1d":"D","1w":"W",
    # Alternative formats: M15, H4, D1
    "m5":"5","m15":"15","m30":"30",
    "h1":"60","h4":"240","d1":"D",
}
# Canonical sort order for display
_TF_ORDER = {"1":0,"3":1,"5":2,"15":3,"30":4,"60":5,"120":6,"240":7,"D":8,"W":9}
DEFAULT_ANALYSIS_TFS = ["15", "60", "240", "D"]   # M15 · 1H · 4H · D1


def _normalize_symbol(raw: str) -> str:
    """Convert any ticker format to XXXUSDT for Bybit API."""
    s = raw.upper().strip()
    s = s.replace(".P", "").replace("/", "").replace("-", "")
    if s.endswith("PERP"):
        s = s[:-4]
    if s.endswith("USDT"):
        return s
    if s.endswith("USD"):       # BTCUSD → BTCUSDT
        return s[:-3] + "USDT"
    return s + "USDT"


def _extract_ticker(text: str) -> str | None:
    """
    Extract first ticker from any text format.
    Handles: BTCUSDT · ETHUSDT.P · BTC/USDT · ETH-USDT.P · standalone BTC
    Returns normalized 'BTCUSDT' or None.
    """
    # Priority 1: explicit pair (BTCUSDT, ETH/USDT.P, SOL-USDT …)
    m = _PAIR_RE.search(text)
    if m:
        return _normalize_symbol(m.group(0))
    # Priority 2: known standalone coin name
    m = _COIN_RE.search(text)
    if m:
        return _normalize_symbol(m.group(1))
    return None


# ─── FREE-FORM CHAT ───────────────────────────────────────────────────────────

_ANALYSIS_KW = {
    "анализируй", "analyze", "analyse", "разбери", "разбор", "анализ",
    "посмотри", "покажи", "check", "смотри", "входить", "шортить",
    "лонговать", "покупать", "продавать", "что думаешь", "что скажешь",
}


def cmd_chat(chat_id: int, text: str):
    """Handle any free-form message: detect intent and route accordingly."""
    text_low = text.lower()

    ticker     = _extract_ticker(text)
    tfs        = [_TF_MAP[m.lower()] for m in _TF_RE.findall(text_low)]
    has_intent = any(kw in text_low for kw in _ANALYSIS_KW)

    if has_intent and ticker:
        cmd_analyze_symbol(chat_id, ticker, tfs or None)
        return

    # Regular chat — provide market context for detected ticker
    tg_send("💬 Думаю...", chat_id=chat_id)
    sym    = ticker or SYMBOLS[0]
    m      = fetch_market(sym)
    answer = llm_ask(text, m, db_last_n(8))
    tg_send(f"🧠 {answer}", chat_id=chat_id)


def _tg_download_photo(file_id: str) -> tuple:
    """Download Telegram photo, return (base64_str, media_type)."""
    try:
        # Get file path from Telegram
        r = requests.get(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getFile",
            params={"file_id": file_id}, timeout=10,
        )
        file_path = r.json()["result"]["file_path"]

        # Download the file
        file_url = f"https://api.telegram.org/file/bot{TELEGRAM_TOKEN}/{file_path}"
        img_bytes = requests.get(file_url, timeout=20).content

        media_type = "image/jpeg" if file_path.endswith(".jpg") else "image/png"
        return base64.standard_b64encode(img_bytes).decode("utf-8"), media_type
    except Exception as e:
        log.error(f"Photo download error: {e}")
        return None, None


def llm_analyze_chart(img_b64: str, media_type: str, caption: str,
                       market: dict, symbol: str,
                       decision: dict | None = None) -> str:
    """
    Анализ присланного юзером скриншота через llm_agents.analyze_user_chart.
    Если decision передан — LLM сравнивает trader's view с engine-verdict
    и подсвечивает расхождения.
    """
    return analyze_user_chart(
        image_b64=img_b64,
        media_type=media_type,
        user_caption=caption or "",
        market=market,
        client=ai,
        model=LLM_MODEL_SMART,
        decision=decision,
    )


def llm_analyze_signal(sig_data: dict, market: dict, recent: list,
                        decision: dict = None,
                        model=LLM_MODEL_FAST) -> tuple:
    """
    Per-signal LLM: single-shot explainer над engine verdict.

    Quality score теперь детерминистский — от decision.confidence,
    а не regex-парсинг из вывода LLM. Это убирает ещё один источник
    рассогласования между engine и LLM.
    """
    if not decision:
        return "⚠️ Нет verdict от engine — анализ пропущен.", 0

    text = explain_signal(
        decision=decision,
        market=market,
        sig_data=sig_data,
        client=ai,
        model=model,
    )

    # Quality 1–10 из confidence 0–100, минимум 1 чтобы фильтры по
    # MIN_QUALITY не отрезали валидные WAIT-сигналы.
    quality = max(1, min(10, int(round(decision.get("confidence", 0) / 10))))
    return text, quality


def llm_ask(question: str, market: dict, recent: list,
            fast_model=LLM_MODEL_FAST, smart_model=LLM_MODEL_SMART) -> str:
    """
    Multi-agent debate: Bull / Bear / Risk параллельно → Sonnet judge.
    """
    try:
        return debate_and_judge(
            question=question,
            market=market,
            recent=recent,
            client=ai,
            fast_model=fast_model,
            smart_model=smart_model,
        )
    except Exception as e:
        return f"⚠️ Ошибка: {e}"


SYSTEM_ANALYZE = """\
Ты — профессиональный крипто-аналитик уровня prop firm (SMC, ICT, Wyckoff, Order Flow, CVD).
Дай полный технический анализ и конкретную торговую идею на основе реальных данных рынка.

Строгий формат ответа (только русский язык):

📊 АНАЛИЗ:
[2-3 предложения: структура рынка, тренд, ключевые уровни]

⚡ КЛЮЧЕВЫЕ ФАКТОРЫ:
• [фактор из CVD / VP / MTF / Funding / OI / Book]
• [ещё фактор]
• [ещё фактор]
• [ещё фактор]

📍 ТОРГОВАЯ ИДЕЯ: [LONG / SHORT / НЕЙТРАЛЬНО — ЖДЁМ]
Зона входа:    $X,XXX – $X,XXX
Стоп-лосс:     $X,XXX  (-X.X%)
Тейк-профит 1: $X,XXX  (+X.X%)
Тейк-профит 2: $X,XXX  (+X.X%)
R:R ratio:     1 : X.X
Уверенность:   X/10

⚠️ ГЛАВНЫЙ РИСК:
[одно конкретное предложение]

Используй точные цифры из предоставленных данных рынка. Никакой воды."""

SYSTEM_ANALYZE_MULTI = """\
Ты — профессиональный крипто-аналитик prop firm (SMC, ICT, Wyckoff, MTF confluence).
Дай мультитаймфреймный анализ и ОДНУ итоговую торговую идею.

Строгий формат (только русский язык):

📊 ПО ТАЙМФРЕЙМАМ:
[M15] [bias + ключевое наблюдение — 1 предложение]
[1H]  [bias + ключевое наблюдение]
[4H]  [bias + ключевое наблюдение]
[D1]  [bias + ключевое наблюдение]
(пиши только те ТФ, которые есть в данных)

🧭 ОБЩИЙ BIAS: [BULLISH / BEARISH / НЕЙТРАЛЬНЫЙ]
[1-2 предложения почему — согласованность ТФ, ключевые уровни]

📍 ТОРГОВАЯ ИДЕЯ: [LONG / SHORT / НЕЙТРАЛЬНО — ЖДЁМ]
Зона входа:    $X,XXX – $X,XXX
Стоп-лосс:     $X,XXX  (-X.X%)
Тейк-профит 1: $X,XXX  (+X.X%)
Тейк-профит 2: $X,XXX  (+X.X%)
R:R ratio:     1 : X.X
Уверенность:   X/10

⚠️ ГЛАВНЫЙ РИСК: [одно конкретное предложение]

Используй точные цифры. Никакой воды."""


def _parse_symbol_tf(args: str) -> tuple:
    """
    Parse ticker + zero/one/many TFs from user input.
    Returns (symbol, tfs_list).
    tfs_list == [] means "use all default TFs".

    Examples:
      "ETH"          → ("ETHUSDT", [])         → all default TFs
      "ETH 4H"       → ("ETHUSDT", ["240"])     → single TF
      "ETH 15m 4H"   → ("ETHUSDT", ["15","240"])→ two TFs
      "SOLUSDT.P 1H" → ("SOLUSDT", ["60"])
    """
    # Extended map including M5/H4/D1 style
    tf_map = {
        "1M":"1","3M":"3","5M":"5","15M":"15","30M":"30",
        "1H":"60","2H":"120","4H":"240","1D":"D","1W":"W",
        "M5":"5","M15":"15","M30":"30",
        "H1":"60","H4":"240","D1":"D",
    }
    # Collect ALL TF tokens found
    tfs = []
    for p in args.upper().split():
        if p in tf_map:
            tfs.append(tf_map[p])

    # If none found via uppercase, try lowercase regex
    if not tfs:
        tfs = [_TF_MAP[m.lower()] for m in _TF_RE.findall(args)]

    symbol = _extract_ticker(args)
    if not symbol:
        symbol = SYMBOLS[0] if SYMBOLS else "BTCUSDT"

    # Deduplicate while preserving order
    seen, tfs_unique = set(), []
    for t in tfs:
        if t not in seen:
            seen.add(t); tfs_unique.append(t)

    return symbol, tfs_unique


def _tf_snapshot(candles: list, tf: str) -> dict:
    """Compute key indicators for a single TF from raw OHLCV candles."""
    if len(candles) < 22:
        return {"tf": tf, "tf_label": TF_LABEL.get(tf, tf), "error": True}
    prices = [c["c"] for c in candles]
    ema20  = _ema(prices, 20)
    bias   = "bull" if prices[-1] > ema20[-1] else "bear"
    rsi    = compute_rsi(prices) if len(prices) >= 15 else None
    macd   = compute_macd(prices) if len(prices) >= 35 else {}
    return {
        "tf":       tf,
        "tf_label": TF_LABEL.get(tf, tf),
        "bias":     bias,
        "rsi":      rsi,
        "macd":     macd,
        "cvd":      compute_cvd(candles),
        "vp":       compute_volume_profile(candles),
        "turtle":   compute_turtle_zone(candles) if len(candles) >= 200 else {},
        "signals":  detect_signals(candles),
        "close":    candles[-1]["c"],
    }


def llm_multi_tf_analysis(market: dict, tf_snapshots: list, symbol: str) -> str:
    mkt_text = market_summary_text(symbol, market)

    tf_text = ""
    for snap in tf_snapshots:
        if snap.get("error"):
            tf_text += f"\n[{snap['tf_label']}] — недостаточно данных"
            continue
        bias_str = "🟢 BULL" if snap["bias"] == "bull" else "🔴 BEAR"
        cvd      = snap.get("cvd", {})
        cvd_str  = f"CVD:{'📈' if cvd.get('trend')=='up' else '📉'}" if cvd.get("trend") else "CVD:❓"
        vp       = snap.get("vp", {})
        vp_str   = f"POC:${vp['poc']:,.0f}" if vp.get("poc") else ""
        tz       = snap.get("turtle", {})
        tz_str   = f"TZ:{tz['icon']}[{tz['pct_from_mean']:+.1f}%]" if tz.get("zone") else ""
        sigs     = snap.get("signals", [])
        sig_str  = f"⚡{','.join(sigs)}" if sigs else "no signals"
        tf_text += (f"\n[{snap['tf_label']}] {bias_str} | {cvd_str}"
                    + (f" | {vp_str}" if vp_str else "")
                    + (f" | {tz_str}" if tz_str else "")
                    + f" | {sig_str}")

    prompt = (
        f"Пара: {symbol.replace('USDT','')}/USDT.P — Мультитаймфреймный анализ\n\n"
        f"Глобальные данные рынка:\n{mkt_text}\n\n"
        f"Данные по таймфреймам:{tf_text}\n\n"
        "Проведи мультитаймфреймный анализ и дай единую торговую идею."
    )
    try:
        resp = ai.messages.create(
            model=LLM_MODEL_SMART, max_tokens=900, system=SYSTEM_ANALYZE_MULTI,
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.content[0].text.strip()
    except Exception as e:
        log.error(f"LLM multi-TF: {e}")
        return f"⚠️ Ошибка: {e}"


def llm_full_analysis(market: dict, symbol: str, tf: str = "60") -> str:
    tf_label   = TF_LABEL.get(tf, tf)
    mkt_text   = market_summary_text(symbol, market)
    biases     = market.get("ema_biases", {})
    cvd        = market.get("cvd", {})

    # Derive likely direction from available signals for confluence
    bull_pts = sum([
        cvd.get("trend") == "up",
        biases.get("4H") == "bull",
        biases.get("1D") == "bull",
    ])
    direction = "long" if bull_pts >= 2 else "short"
    mtf_check = check_mtf_confluence(biases, direction)
    sig_key   = "BOS_BULL" if direction == "long" else "BOS_BEAR"
    conf_score, conf_factors = compute_confluence_score(sig_key, market, mtf_check)

    conf_text = (
        f"\nConfluence Score: {conf_score}/100\n"
        + "\n".join(f"  {f}" for f in conf_factors)
    )
    prompt = (
        f"Пара: {symbol.replace('USDT','')}/USDT.P | Таймфрейм: {tf_label}\n\n"
        f"Данные рынка:\n{mkt_text}\n"
        f"{conf_text}\n\n"
        "Проведи полный анализ и дай торговую идею."
    )
    try:
        resp = ai.messages.create(
            model=LLM_MODEL_SMART, max_tokens=750, system=SYSTEM_ANALYZE,
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.content[0].text.strip()
    except Exception as e:
        log.error(f"LLM full analysis: {e}")
        return f"⚠️ Ошибка анализа: {e}"


def cmd_analyze_symbol(chat_id: int, symbol: str, tfs: list = None):
    """
    tfs=None or []  → multi-TF: DEFAULT_ANALYSIS_TFS (M15·1H·4H·D1)
    tfs=["240"]     → single TF: detailed analysis for 4H only
    tfs=["15","240"]→ multi-TF: only the specified TFs
    """
    requested = tfs if tfs else DEFAULT_ANALYSIS_TFS
    sym_short = symbol.replace("USDT", "")

    if len(requested) == 1:
        tf_label = TF_LABEL.get(requested[0], requested[0])
        tg_send(f"🔍 Анализирую {sym_short}/USDT.P [{tf_label}]...", chat_id=chat_id)
    else:
        labels = " · ".join(TF_LABEL.get(t, t) for t in requested)
        tg_send(f"🔍 Мульти-ТФ анализ {sym_short}/USDT.P\n[{labels}]...", chat_id=chat_id)

    try:
        market = fetch_market(symbol)
    except Exception as e:
        tg_send(f"❌ Не могу получить данные по {sym_short}: {e}", chat_id=chat_id)
        return

    price  = market.get("price", 0)
    b      = market.get("bybit", {})
    fr_b   = b.get("funding", 0) * 100
    oi_chg = b.get("oi_chg", 0)
    now_str = datetime.now(timezone.utc).strftime("%H:%M UTC")

    if len(requested) == 1:
        # ── Single TF: detailed analysis ─────────────────────────────────────
        tf       = requested[0]
        tf_label = TF_LABEL.get(tf, tf)
        biases   = market.get("ema_biases", {})
        cvd      = market.get("cvd", {})
        cvd_icon = "📈" if cvd.get("trend") == "up" else ("📉" if cvd.get("trend") == "down" else "➡️")
        mtf_str  = " | ".join(
            f"{t}:{'🟢' if bv=='bull' else ('🔴' if bv=='bear' else '❓')}"
            for t, bv in biases.items()
        )
        tz_parts = [
            f"{tname}:{market[tk]['icon']}[{market[tk]['pct_from_mean']:+.1f}%]"
            for tk, tname in [("turtle_1h","1H"),("turtle_4h","4H")]
            if market.get(tk)
        ]
        tz_line = "  TZ: " + " | ".join(tz_parts) + "\n" if tz_parts else ""
        analysis = llm_full_analysis(market, symbol, tf)
        tg_send(
            f"🎯 <b>Анализ {sym_short}/USDT.P</b> [{tf_label}]\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"💰 ${price:,.2f}  ({market.get('change_24h',0):+.2f}% 24h)\n"
            f"📊 MTF: {mtf_str}\n"
            f"{tz_line}"
            f"CVD: {cvd_icon}  |  FR: {fr_b:+.4f}%  |  OI: {oi_chg:+.2f}%\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"{analysis}\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"<i>Bybit + HL · {now_str}</i>",
            chat_id=chat_id,
        )

    else:
        # ── Multi-TF: reuse klines already in market, fetch only missing TFs ──
        cached = market.get("_klines", {})   # {"60": [...], "240": [...], "D": [...]}
        missing_tfs = [tf for tf in requested if tf not in cached]

        if missing_tfs:
            with ThreadPoolExecutor(max_workers=len(missing_tfs)) as ex:
                new_futs = {ex.submit(_klines, symbol, tf, 250): tf for tf in missing_tfs}
            for fut, tf in new_futs.items():
                cached[tf] = fut.result()

        snapshots = []
        for tf in requested:
            candles = cached.get(tf, [])
            snapshots.append(_tf_snapshot(candles, tf))
        snapshots.sort(key=lambda s: _TF_ORDER.get(s["tf"], 99))

        # Build compact per-TF summary for the message header
        tf_lines = []
        for snap in snapshots:
            if snap.get("error"):
                tf_lines.append(f"  {snap['tf_label']:<4} ❓ нет данных")
                continue
            b_icon  = "🟢" if snap["bias"] == "bull" else "🔴"
            cvd_i   = "📈" if snap.get("cvd",{}).get("trend")=="up" else "📉"
            tz      = snap.get("turtle", {})
            tz_s    = f" TZ:{tz['icon']}[{tz['pct_from_mean']:+.1f}%]" if tz.get("zone") else ""
            sigs    = snap.get("signals", [])
            sig_s   = f" ⚡{'|'.join(sigs[:2])}" if sigs else ""
            tf_lines.append(f"  {snap['tf_label']:<4} {b_icon} EMA | CVD:{cvd_i}{tz_s}{sig_s}")

        # BTC Correlation line
        btc_corr = market.get("btc_corr")
        if btc_corr:
            r24 = btc_corr.get("r24h")
            r7d = btc_corr.get("r7d")
            corr_line = (
                f"🔗 BTC corr: "
                f"24H={r24:+.2f} ({btc_corr['label24h']})  "
                f"7D={r7d:+.2f} ({btc_corr['label7d']})\n"
                if r24 is not None else ""
            )
        else:
            corr_line = ""

        analysis = llm_multi_tf_analysis(market, snapshots, symbol)
        labels   = " · ".join(TF_LABEL.get(t, t) for t in requested)
        tg_send(
            f"🎯 <b>Мульти-ТФ {sym_short}/USDT.P</b>\n"
            f"<i>{labels}</i>\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"💰 ${price:,.2f}  ({market.get('change_24h',0):+.2f}% 24h)\n"
            f"FR: {fr_b:+.4f}%  |  OI: {oi_chg:+.2f}%\n"
            f"{corr_line}"
            f"━━ По таймфреймам ━\n"
            + "\n".join(tf_lines)
            + f"\n━━━━━━━━━━━━━━━━━━\n"
            f"{analysis}\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"<i>Bybit + HL · {now_str}</i>",
            chat_id=chat_id,
        )

        # ── Inline buttons: drill down to a specific TF ───────────────────────
        cb_sym = sym_short  # e.g. "BTC", "ETH"
        tg_send(
            "⏱ Детальный анализ по таймфрейму:",
            chat_id=chat_id,
            reply_markup={"inline_keyboard": [[
                {"text": "📊 15M", "callback_data": f"analyze:{cb_sym}:15"},
                {"text": "📊 1H",  "callback_data": f"analyze:{cb_sym}:60"},
                {"text": "📊 4H",  "callback_data": f"analyze:{cb_sym}:240"},
                {"text": "📊 D1",  "callback_data": f"analyze:{cb_sym}:D"},
            ]]},
        )


def llm_digest(signals: list, market: dict,
               tracking_stats: dict | None = None) -> str:
    """
    Дневной debrief через llm_agents.summarize_day — учитывает реальную
    engine-performance (win-rate, TP/SL hits) и не противоречит verdict'ам.
    """
    try:
        return summarize_day(
            signals=signals,
            market=market,
            client=ai,
            model=LLM_MODEL_SMART,
            tracking_stats=tracking_stats,
        )
    except Exception as e:
        return f"⚠️ Ошибка дайджеста: {e}"


# ─── TELEGRAM ─────────────────────────────────────────────────────────────────

def tg_send(text: str, chat_id=None, reply_markup: dict = None) -> bool:
    cid = chat_id or TELEGRAM_CHAT_ID
    payload = {
        "chat_id": cid, "text": text,
        "parse_mode": "HTML", "disable_web_page_preview": True,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json=payload, timeout=10,
        )
        r.raise_for_status()
        return True
    except Exception as e:
        log.error(f"Telegram send: {e}")
        return False


TELEGRAM_CAPTION_LIMIT = 1024


def tg_send_photo(photo_bytes: bytes, caption: str, chat_id=None,
                  filename: str = "chart.png") -> bool:
    """
    Отправляет PNG в Telegram. Если caption > 1024 (лимит API), фото
    шлётся с обрезанной шапкой, а полный текст приходит ОТДЕЛЬНЫМ
    сообщением сразу следом (вместо silent truncation).
    """
    cid = chat_id or TELEGRAM_CHAT_ID
    full_text = caption
    overflowed = len(caption) > TELEGRAM_CAPTION_LIMIT
    if overflowed:
        # Шапка под фото: первые 1000 символов + явный маркер
        caption = caption[:1000].rstrip() + "\n\n<i>↓ полный текст ниже</i>"

    files = {"photo": (filename, photo_bytes, "image/png")}
    data  = {"chat_id": cid, "caption": caption, "parse_mode": "HTML"}
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto",
            data=data, files=files, timeout=15,
        )
        r.raise_for_status()
    except Exception as e:
        log.error(f"Telegram sendPhoto: {e}")
        return False

    if overflowed:
        tg_send(full_text, chat_id=cid)

    return True


def tg_send_document(doc_bytes: bytes, filename: str,
                     caption: str = "", chat_id=None) -> bool:
    """
    Отправляет файл (JSON dump, CSV, лог) как Telegram-attachment.
    """
    cid = chat_id or TELEGRAM_CHAT_ID
    files = {"document": (filename, doc_bytes, "application/octet-stream")}
    data  = {"chat_id": cid, "caption": caption[:1024], "parse_mode": "HTML"}
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendDocument",
            data=data, files=files, timeout=30,
        )
        r.raise_for_status()
        return True
    except Exception as e:
        log.error(f"Telegram sendDocument: {e}")
        return False


def tg_send_chunked(text: str, chat_id=None, chunk: int = 3800) -> None:
    """
    Шлёт длинный текст пачками по ≤chunk символов (лимит Telegram 4096).
    Сохраняет `<pre>...</pre>` обёртку если она была.
    """
    cid = chat_id or TELEGRAM_CHAT_ID
    wrap_pre = text.startswith("<pre>") and text.endswith("</pre>")
    inner = text[5:-6] if wrap_pre else text
    if len(text) <= chunk:
        tg_send(text, chat_id=cid)
        return
    lines = inner.split("\n")
    buf, parts = [], []
    cur = 0
    for ln in lines:
        if cur + len(ln) + 1 > chunk and buf:
            parts.append("\n".join(buf))
            buf, cur = [], 0
        buf.append(ln)
        cur += len(ln) + 1
    if buf:
        parts.append("\n".join(buf))
    for i, p in enumerate(parts, 1):
        body = f"<pre>{p}</pre>" if wrap_pre else p
        prefix = f"({i}/{len(parts)})\n" if len(parts) > 1 else ""
        tg_send(prefix + body, chat_id=cid)


def _register_bot_commands() -> None:
    """Register slash commands so Telegram shows them in the / menu."""
    commands = [
        {"command": "analyze",  "description": "Анализ монеты: /analyze BTC или /analyze ETH 4H"},
        {"command": "status",   "description": "Рынок: CVD, VP, MTF EMA, Fear&Greed"},
        {"command": "market",   "description": "TOTAL / TOTAL2 / доминации / альткоины"},
        {"command": "risk",     "description": "Калькулятор позиции: /risk BTC 76000 74000"},
        {"command": "news",     "description": "Последние новости: /news или /news ETH"},
        {"command": "top",      "description": "Топ гейнеры / лузеры / объём (24H)"},
        {"command": "movers",   "description": "Движения 1H по watchlist"},
        {"command": "ask",      "description": "Вопрос о рынке: /ask что думаешь о BTC?"},
        {"command": "alert",    "description": "Ценовой алерт: /alert BTC 105000"},
        {"command": "alerts",   "description": "Список активных алертов"},
        {"command": "delalert", "description": "Удалить алерт: /delalert 3"},
        {"command": "stats",    "description": "Win-rate по типам сигналов (30 дней)"},
        {"command": "trades",   "description": "Последние закрытые сделки с R-исходом"},
        {"command": "backtest", "description": "Прогон стратегии на истории: /backtest BTC 30"},
        {"command": "btdiag",   "description": "Диагностика: funnel + breakdown сигналов: /btdiag BTC 30"},
        {"command": "scanbt",   "description": "Backtest на нескольких монетах: /scanbt BTC,ETH,SOL 30"},
        {"command": "hyperopt", "description": "Optuna-тюнинг параметров: /hyperopt BTC 60 30"},
        {"command": "strategy", "description": "Сводка по стратегии и команды для экспериментов"},
        {"command": "history",  "description": "Последние 10 сигналов из БД"},
        {"command": "digest",   "description": "Дневной дайджест с LLM-анализом"},
        {"command": "scan",     "description": "Ручной запуск автосканера"},
        {"command": "help",     "description": "Список всех команд"},
    ]
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/setMyCommands",
            json={"commands": commands},
            timeout=10,
        )
        if r.json().get("ok"):
            log.info("✅ Telegram bot commands registered")
        else:
            log.warning(f"setMyCommands: {r.text}")
    except Exception as e:
        log.warning(f"setMyCommands error: {e}")


def _tg_answer_callback(callback_id: str) -> None:
    """Dismiss the inline button loading spinner."""
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/answerCallbackQuery",
            json={"callback_query_id": callback_id},
            timeout=5,
        )
    except Exception:
        pass


# ─── MESSAGE BUILDER ─────────────────────────────────────────────────────────

def build_signal_message(data: dict, market: dict, llm_text: str, quality: int,
                          confluence: int = 0, conf_factors: list = None,
                          decision: dict = None) -> str:
    """
    Компактный per-signal формат:
      title · pair · TF · time
      price · 24h change · bias
      ━ Verdict (Entry/SL/TP/RR из engine, или WAIT/SKIP с причиной)
      ━ Анализ (2-3 предложения от LLM)
      ━ Контекст (5-7 строк market_brief)

    Полный дамп индикаторов остался в /status — не дублируем.
    """
    sig    = data.get("signal", "ALERT").upper()
    symbol = (data.get("symbol", data.get("ticker", "?"))
              .replace("USDT.P", "").replace("USDT", ""))
    price  = data.get("price", data.get("close", market.get("price", 0)))
    tf     = TF_LABEL.get(str(data.get("tf", data.get("interval", "?"))),
                          str(data.get("tf", "?")))
    now    = datetime.now(timezone.utc).strftime("%H:%M UTC")

    emoji, title, bias = SIGNAL_META.get(sig, SIGNAL_META["ALERT"])

    try:
        price_f = f"${float(price):,.2f}"
    except (TypeError, ValueError):
        price_f = str(price)

    # Optional TradingView-supplied levels (OB/FVG/target/stop)
    extras = []
    for k, lbl in [("ob_top", "OB↑"), ("ob_bot", "OB↓"),
                   ("fvg_top", "FVG↑"), ("fvg_bot", "FVG↓"),
                   ("target", "Цель"), ("stop", "Стоп")]:
        if data.get(k):
            try:
                extras.append(f"{lbl} ${float(data[k]):,.0f}")
            except (TypeError, ValueError):
                pass
    tv_levels = ("\n📐 От TV: " + " · ".join(extras)) if extras else ""

    verdict_block = ""
    if decision:
        verdict_block = ("━━ Verdict ━━━━━━━━\n"
                         f"{format_decision_header(decision)}\n")

    return (
        f"{emoji} <b>{title}</b>  ·  <b>{symbol}/USDT.P</b>  ·  {tf}  ·  {now}\n"
        f"💰 <b>{price_f}</b>  ({market.get('change_24h', 0):+.2f}% 24h)  "
        f"·  Bias: <b>{bias}</b>"
        f"{tv_levels}\n"
        f"{verdict_block}"
        f"━━ Анализ ━━━━━━━━\n"
        f"{llm_text}\n"
        f"━━ Контекст ━━━━━━\n"
        f"{market_brief(market)}\n"
        f"ℹ️ Полный дамп: /status {symbol}"
    )


# ─── WEBHOOK ──────────────────────────────────────────────────────────────────

def _process_winner(winner: "signal_gate.BufferedSignal",
                    suppressed: list) -> None:
    """
    Aggregator callback. Запускает полный pipeline для winner-сигнала:
    fetch_market → make_decision → cooldown_check → llm → chart → send.
    `suppressed` — сигналы, проигравшие в окне (для пометки в сообщении).
    """
    data     = winner.payload
    sig_type = winner.sig_type
    symbol   = data.get("symbol", data.get("ticker", "BTCUSDT"))
    tf       = str(data.get("tf", data.get("interval", "?")))
    price    = float(data.get("price", data.get("close", 0)) or 0)

    base_sym = symbol.replace(".P", "").replace("/", "")
    market   = fetch_market(base_sym if base_sym.endswith("USDT") else base_sym + "USDT")

    # Timestamp алерта (если TV прислал time/ts/timestamp) — пробрасываем в
    # market, чтобы killzone-гейт (Этап 10 фаза 3) работал по времени
    # сигнала, а не now(). На прод-нагрузке разница в секунды, но aggregator
    # буферизирует до 30s, плюс scheduler может задержать обработку.
    alert_ts = parse_alert_ts(data)
    if alert_ts is not None:
        market["ts"] = alert_ts

    sig_up    = any(x in sig_type for x in ("BULL","LONG","SWEEP_L","EQL"))
    sig_dn    = any(x in sig_type for x in ("BEAR","SHORT","SWEEP_H","EQH"))
    direction = "long" if sig_up else ("short" if sig_dn else "neutral")
    biases    = market.get("ema_biases", {})
    mtf       = check_mtf_confluence(biases, direction) if direction != "neutral" else {}

    conf_score, conf_factors = compute_confluence_score(sig_type, market, mtf)

    decision = make_decision(
        signal_type=sig_type,
        price=price or market.get("price", 0),
        market=market,
        mtf=mtf,
        confluence_score=conf_score,
        confluence_factors=conf_factors,
    )
    log.info(f"  Decision: {decision['verdict']} "
             f"conf={decision['confidence']}/100 "
             f"vetoes={len(decision['veto_reasons'])} "
             f"reason='{decision['reason']}'")

    recent            = db_recent(hours=4, limit=6)
    llm_text, quality = llm_analyze_signal(data, market, recent, decision)

    # Cooldown gate ДО db_save: подавленные сигналы метим status='suppressed',
    # чтобы они не трекались как сделки и не портили win-rate.
    gate = None
    if decision["verdict"] in ("LONG", "SHORT"):
        with _db_lock, db_conn() as c:
            gate = signal_gate.cooldown_check(
                c, symbol, decision["verdict"],
                decision.get("confidence", 0), tf,
            )

    # Bug-11 fix: LONG/SHORT с quality < MIN_QUALITY юзеру не отправляются,
    # но раньше всё равно трекались как 'open' и портили win-rate. Метим
    # их как 'suppressed' — параллельно с cooldown-suppressed (Этап 6).
    quality_blocked = (decision["verdict"] in ("LONG", "SHORT")
                       and quality < MIN_QUALITY)
    gate_status = "suppressed" if (
        (gate and gate.action == "suppress") or quality_blocked
    ) else None
    db_save(symbol, tf, sig_type, price, data, llm_text, quality,
            decision=decision, gate_status=gate_status)

    if decision["verdict"] == "SKIP":
        log.info(f"  Verdict=SKIP — не отправляем ({decision['reason']})")
        return

    if quality < MIN_QUALITY:
        log.info(f"  Качество {quality} < {MIN_QUALITY} — не отправляем")
        return

    if gate and gate.action == "suppress":
        log.info(f"  [gate] SUPPRESS {symbol} {decision['verdict']}: "
                 f"{gate.reason}")
        return
    if gate and gate.action == "reversal":
        log.info(f"  [gate] REVERSAL {symbol} {decision['verdict']}: "
                 f"{gate.reason}")

    msg = build_signal_message(data, market, llm_text, quality,
                               conf_score, conf_factors, decision)
    if gate and gate.action == "reversal" and gate.active is not None:
        msg += signal_gate.format_reversal_note(gate.active, decision["verdict"])
    if suppressed:
        msg += signal_gate.format_suppressed_note(suppressed)

    klines_1h = (market.get("_klines") or {}).get("60") or []
    photo     = render_signal_chart(symbol, klines_1h, decision, market)

    if photo:
        ok = tg_send_photo(photo, msg)
    else:
        ok = tg_send(msg)

    log.info(f"  {sig_type} {symbol} Q:{quality}/10 Conf:{conf_score}/100 "
             f"Verdict:{decision['verdict']} chart:{'yes' if photo else 'no'} "
             f"→ {'OK' if ok else 'FAIL'}")

    if ok and decision["verdict"] in ("LONG", "SHORT"):
        try:
            with _db_lock, db_conn() as c:
                signal_gate.record_dispatch(
                    c, symbol, decision["verdict"], tf, sig_type,
                    decision.get("confidence", 0),
                    note=("reversal" if gate and gate.action == "reversal" else None),
                )
        except Exception as e:
            log.warning(f"signal_gate.record_dispatch: {e}")


# Singleton aggregator — буферизирует входящие webhook-сигналы по символу.
_aggregator = signal_gate.SignalAggregator(callback=_process_winner)


@app.route("/webhook", methods=["POST"])
def webhook():
    raw = request.get_data(as_text=True)
    log.info(f"← Webhook: {raw[:150]}")

    data = {}
    try:
        data = json.loads(raw)
    except Exception:
        data = {"signal": "ALERT", "msg": raw[:300]}

    symbol = data.get("symbol", data.get("ticker", "BTCUSDT"))
    _aggregator.submit(symbol, data)
    return jsonify({"status": "queued", "symbol": symbol}), 200


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "running", "time": datetime.now(timezone.utc).isoformat()}), 200


# ─── UI ROUTES (chart viewer) ─────────────────────────────────────────────
#
# Простой read-only UI для просмотра charts и indicators в браузере.
# Не требует auth (личное использование). Использует Lightweight Charts
# от TradingView через CDN — без build-step.
#
# Routes:
#   GET /ui                    — главная страница с chart viewer
#   GET /api/klines            — JSON-ответ klines для символа/интервала
#   GET /api/symbols           — список доступных символов из watchlist
#
# Безопасность: webhook() / pipeline НЕ тронуты, эти endpoints read-only.

UI_CHART_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Crypto Screener — Chart Viewer</title>
<style>
  :root {
    --bg: #0d1117;
    --panel: #161b22;
    --border: #21262d;
    --text: #c9d1d9;
    --text-dim: #8b949e;
    --green: #3fb950;
    --red: #f85149;
    --blue: #58a6ff;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; background: var(--bg); color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    font-size: 14px;
  }
  header {
    display: flex; gap: 12px; align-items: center;
    padding: 10px 16px; background: var(--panel);
    border-bottom: 1px solid var(--border);
  }
  header h1 { margin: 0; font-size: 16px; font-weight: 600; }
  header select, header input {
    background: var(--bg); color: var(--text);
    border: 1px solid var(--border); border-radius: 6px;
    padding: 6px 10px; font-size: 13px;
  }
  header button {
    background: var(--blue); color: white; border: 0;
    padding: 6px 14px; border-radius: 6px; cursor: pointer;
    font-size: 13px; font-weight: 500;
  }
  header button:hover { opacity: 0.85; }
  .toolbar-sep {
    width: 1px; height: 22px; background: var(--border);
    margin: 0 4px;
  }
  .tool-btn, .tool-btn-secondary {
    background: var(--bg) !important; color: var(--text) !important;
    border: 1px solid var(--border) !important;
    padding: 6px 12px !important;
    font-weight: 500 !important;
  }
  .tool-btn:hover, .tool-btn-secondary:hover {
    background: var(--panel) !important; opacity: 1 !important;
  }
  .tool-btn.active {
    background: var(--blue) !important; color: white !important;
    border-color: var(--blue) !important;
  }
  #chart.drawing-mode { cursor: crosshair; }
  .layout {
    display: grid;
    grid-template-columns: 1fr 280px;
    gap: 0; height: calc(100vh - 51px);
  }
  #chart { background: var(--panel); }
  .sidebar {
    background: var(--panel); border-left: 1px solid var(--border);
    padding: 16px; overflow-y: auto;
  }
  .sidebar h2 {
    margin: 0 0 12px; font-size: 13px; font-weight: 600;
    text-transform: uppercase; color: var(--text-dim);
    letter-spacing: 0.5px;
  }
  .indicator {
    background: var(--bg); border: 1px solid var(--border);
    border-radius: 6px; padding: 10px 12px; margin-bottom: 8px;
    display: flex; justify-content: space-between; align-items: center;
  }
  .indicator-name { font-size: 12px; color: var(--text-dim); }
  .indicator-value { font-weight: 600; font-family: "SF Mono", Menlo, monospace; }
  .pos { color: var(--green); }
  .neg { color: var(--red); }
  .status { font-size: 12px; color: var(--text-dim); margin-top: 16px; }
  .watchlist-row {
    display: grid; grid-template-columns: 20px 1fr auto auto;
    gap: 6px; align-items: center;
    padding: 8px 10px; cursor: pointer;
    border-radius: 6px; transition: background 0.1s;
    font-family: "SF Mono", Menlo, monospace; font-size: 12px;
  }
  .watchlist-row:hover { background: var(--bg); }
  .watchlist-row.active { background: var(--bg); outline: 1px solid var(--blue); }
  .watchlist-star {
    cursor: pointer; font-size: 13px; line-height: 1;
    color: var(--text-dim); user-select: none;
    width: 18px; text-align: center;
  }
  .watchlist-star.fav { color: #d29922; }
  .watchlist-star:hover { transform: scale(1.2); }
  .watchlist-symbol { font-weight: 600; }
  .watchlist-price { color: var(--text-dim); text-align: right; }
  .watchlist-change { text-align: right; font-weight: 600; min-width: 56px; }
  .sidebar-section { margin-bottom: 20px; }
  .bottom-analysis {
    background: var(--panel);
    border-top: 1px solid var(--border);
    padding: 12px 16px;
    max-height: 200px; overflow-y: auto;
  }
  .ba-header {
    display: flex; align-items: center; gap: 12px;
    margin-bottom: 8px;
  }
  .ba-title {
    font-size: 13px; font-weight: 600;
    text-transform: uppercase; color: var(--text-dim);
    letter-spacing: 0.5px;
  }
  .ba-status {
    margin-left: auto; font-size: 11px; color: var(--text-dim);
  }
  .ba-refresh {
    background: var(--blue); color: white; border: 0;
    padding: 4px 10px; border-radius: 4px; cursor: pointer;
    font-size: 11px; font-weight: 500;
  }
  .ba-refresh:hover { opacity: 0.85; }
  .ba-confluence {
    font-size: 13px; font-weight: 600; margin-bottom: 6px;
    padding: 6px 10px; background: var(--bg);
    border-radius: 4px; display: inline-block;
  }
  .ba-brief {
    font-size: 12px; color: var(--text);
    line-height: 1.6; margin-bottom: 6px;
    font-family: "SF Mono", Menlo, monospace;
  }
  .ba-factors {
    display: flex; gap: 6px; flex-wrap: wrap;
  }
  .ba-factor {
    background: var(--bg); border: 1px solid var(--border);
    border-radius: 3px; padding: 2px 8px;
    font-size: 11px; color: var(--text-dim);
  }
  .ba-factor.pos { color: var(--green); border-color: var(--green); }
  .ba-factor.neg { color: var(--red); border-color: var(--red); }
  @media (max-width: 800px) {
    .layout { grid-template-columns: 1fr; height: auto; }
    #chart { height: 60vh; }
    .sidebar { border-left: 0; border-top: 1px solid var(--border); }
  }
</style>
</head>
<body>
<header>
  <h1>📊 Chart Viewer</h1>
  <select id="symbol">
    <option value="BTCUSDT">BTC</option>
    <option value="ETHUSDT">ETH</option>
    <option value="SOLUSDT">SOL</option>
    <option value="BNBUSDT">BNB</option>
    <option value="XRPUSDT">XRP</option>
    <option value="ADAUSDT">ADA</option>
    <option value="DOGEUSDT">DOGE</option>
    <option value="AVAXUSDT">AVAX</option>
    <option value="LINKUSDT">LINK</option>
    <option value="MATICUSDT">MATIC</option>
  </select>
  <select id="interval">
    <option value="5">5m</option>
    <option value="15">15m</option>
    <option value="60" selected>1H</option>
    <option value="240">4H</option>
    <option value="D">1D</option>
  </select>
  <button onclick="loadChart()">Reload</button>
  <span class="toolbar-sep"></span>
  <button id="btnHLine" class="tool-btn" onclick="toggleTool('hline')"
          title="Click toolbar then click chart to place horizontal line">📏 H-Line</button>
  <button id="btnClearDrawings" class="tool-btn-secondary"
          onclick="clearDrawings()" title="Clear all drawings for this symbol">🧹 Clear</button>
  <span id="status" style="margin-left: auto; color: var(--text-dim); font-size: 12px;"></span>
</header>
<div class="layout">
  <div id="chart"></div>
  <aside class="sidebar">
    <div class="sidebar-section">
      <h2>Watchlist</h2>
      <div id="watchlist"></div>
    </div>
    <h2 style="margin-top: 0;">Engine Market</h2>
    <div class="indicator">
      <span class="indicator-name">CVD trend</span>
      <span class="indicator-value" id="cvd">—</span>
    </div>
    <div class="indicator">
      <span class="indicator-name">MTF EMA bias</span>
      <span class="indicator-value" id="mtfbias">—</span>
    </div>
    <div class="indicator">
      <span class="indicator-name">Funding (Bybit)</span>
      <span class="indicator-value" id="funding">—</span>
    </div>
    <div class="indicator">
      <span class="indicator-name">Open Interest</span>
      <span class="indicator-value" id="oi">—</span>
    </div>
    <div class="indicator">
      <span class="indicator-name">VWAP vs price</span>
      <span class="indicator-value" id="vwapdelta">—</span>
    </div>
    <div class="indicator">
      <span class="indicator-name">VP POC</span>
      <span class="indicator-value" id="poc">—</span>
    </div>
    <div class="indicator">
      <span class="indicator-name">Turtle 1H / 4H</span>
      <span class="indicator-value" id="turtle">—</span>
    </div>
    <div class="indicator">
      <span class="indicator-name">Liq long / short</span>
      <span class="indicator-value" id="liqs">—</span>
    </div>
    <h2 style="margin-top: 20px;">Technical</h2>
    <div class="indicator">
      <span class="indicator-name">Price</span>
      <span class="indicator-value" id="price">—</span>
    </div>
    <div class="indicator">
      <span class="indicator-name">24h Change</span>
      <span class="indicator-value" id="chg24">—</span>
    </div>
    <div class="indicator">
      <span class="indicator-name">EMA 20</span>
      <span class="indicator-value" id="ema20">—</span>
    </div>
    <div class="indicator">
      <span class="indicator-name">EMA 50</span>
      <span class="indicator-value" id="ema50">—</span>
    </div>
    <div class="indicator">
      <span class="indicator-name">RSI 14</span>
      <span class="indicator-value" id="rsi14">—</span>
    </div>
    <div class="indicator">
      <span class="indicator-name">ATR 14</span>
      <span class="indicator-value" id="atr14">—</span>
    </div>
    <div class="indicator">
      <span class="indicator-name">High</span>
      <span class="indicator-value" id="high">—</span>
    </div>
    <div class="indicator">
      <span class="indicator-name">Low</span>
      <span class="indicator-value" id="low">—</span>
    </div>
    <h2 style="margin-top: 20px;">Signals (30d)</h2>
    <label style="display: flex; align-items: center; gap: 8px;
                  margin: 8px 0; cursor: pointer; font-size: 12px;">
      <input type="checkbox" id="toggleSignals" checked>
      Show entries / TP / SL markers
    </label>
    <div id="signalsCount"
         style="font-size: 11px; color: var(--text-dim);
                margin-top: 4px;">—</div>
    <h2 style="margin-top: 20px;">SMC Zones</h2>
    <label style="display: flex; align-items: center; gap: 8px;
                  margin: 8px 0; cursor: pointer; font-size: 12px;">
      <input type="checkbox" id="toggleZones" checked>
      Show OB / FVG / MB / BB zones
    </label>
    <div id="zonesCount"
         style="font-size: 11px; color: var(--text-dim);
                margin-top: 4px;">—</div>
    <div class="status">
      Read-only viewer. Webhook signals → Telegram bot.
      <br>See <code>/strategy</code> for research commands.
    </div>
  </aside>
</div>

<div class="bottom-analysis" id="bottomAnalysis">
  <div class="ba-header">
    <span class="ba-title">📋 Engine Analysis</span>
    <span class="ba-status" id="analysisStatus">—</span>
    <button class="ba-refresh" onclick="loadAnalysis(true)">Refresh</button>
  </div>
  <div class="ba-content">
    <div class="ba-confluence" id="confluencePanel">—</div>
    <div class="ba-brief" id="briefPanel">—</div>
    <div class="ba-factors" id="factorsPanel"></div>
  </div>
</div>

<script src="https://unpkg.com/lightweight-charts@4.1.3/dist/lightweight-charts.standalone.production.js"></script>
<script>
let chart, candleSeries, ema20Series, ema50Series;

function ema(values, period) {
  const k = 2 / (period + 1);
  const out = [];
  let prev = null;
  for (let i = 0; i < values.length; i++) {
    const v = values[i];
    prev = prev === null ? v : v * k + prev * (1 - k);
    out.push(prev);
  }
  return out;
}

function rsi(closes, period = 14) {
  if (closes.length <= period) return null;
  let gains = 0, losses = 0;
  for (let i = 1; i <= period; i++) {
    const d = closes[i] - closes[i - 1];
    if (d > 0) gains += d; else losses -= d;
  }
  let avgG = gains / period, avgL = losses / period;
  for (let i = period + 1; i < closes.length; i++) {
    const d = closes[i] - closes[i - 1];
    avgG = (avgG * (period - 1) + (d > 0 ? d : 0)) / period;
    avgL = (avgL * (period - 1) + (d < 0 ? -d : 0)) / period;
  }
  if (avgL === 0) return 100;
  const rs = avgG / avgL;
  return 100 - 100 / (1 + rs);
}

function atr(klines, period = 14) {
  if (klines.length < period + 1) return null;
  let sum = 0;
  for (let i = klines.length - period; i < klines.length; i++) {
    const c = klines[i], p = klines[i - 1];
    const tr = Math.max(
      c.h - c.l,
      Math.abs(c.h - p.c),
      Math.abs(c.l - p.c),
    );
    sum += tr;
  }
  return sum / period;
}

function fmtNum(v, digits) {
  if (v == null) return "—";
  return Number(v).toLocaleString("en-US", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

function digits(v) {
  if (v >= 1000) return 2;
  if (v >= 1) return 4;
  if (v >= 0.01) return 6;
  return 8;
}

function initChart() {
  const el = document.getElementById("chart");
  chart = LightweightCharts.createChart(el, {
    width: el.clientWidth, height: el.clientHeight,
    layout: { background: { color: "#161b22" }, textColor: "#c9d1d9" },
    grid: {
      vertLines: { color: "#21262d" },
      horzLines: { color: "#21262d" },
    },
    timeScale: { timeVisible: true, secondsVisible: false },
    crosshair: { mode: 1 },
  });
  candleSeries = chart.addCandlestickSeries({
    upColor: "#3fb950", downColor: "#f85149",
    borderVisible: false,
    wickUpColor: "#3fb950", wickDownColor: "#f85149",
  });
  ema20Series = chart.addLineSeries({
    color: "#58a6ff", lineWidth: 1, priceLineVisible: false,
  });
  ema50Series = chart.addLineSeries({
    color: "#d29922", lineWidth: 1, priceLineVisible: false,
  });
  new ResizeObserver(() => {
    chart.applyOptions({
      width: el.clientWidth, height: el.clientHeight,
    });
  }).observe(el);

  // Click handler для drawing tools (H-Line)
  chart.subscribeClick(param => {
    if (!activeTool || !param.point) return;
    const price = candleSeries.coordinateToPrice(param.point.y);
    if (price == null || !isFinite(price)) return;
    if (activeTool === "hline") {
      addHLine(price);
      // Toggle off после одной линии
      toggleTool("hline");
    }
  });
}

async function loadChart() {
  const sym = document.getElementById("symbol").value;
  const iv = document.getElementById("interval").value;
  document.getElementById("status").textContent =
    `Loading ${sym} ${iv}...`;
  try {
    const r = await fetch(
      `/api/klines?symbol=${sym}&interval=${iv}&limit=300`);
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const data = await r.json();
    if (!data.klines || !data.klines.length) {
      throw new Error("No klines");
    }
    const candles = data.klines.map(k => ({
      time: Math.floor(k.ts / 1000),
      open: k.o, high: k.h, low: k.l, close: k.c,
    }));
    candleSeries.setData(candles);

    const closes = data.klines.map(k => k.c);
    const e20 = ema(closes, 20);
    const e50 = ema(closes, 50);
    ema20Series.setData(candles.map((c, i) => ({
      time: c.time, value: e20[i],
    })));
    ema50Series.setData(candles.map((c, i) => ({
      time: c.time, value: e50[i],
    })));

    const last = data.klines[data.klines.length - 1];
    const first = data.klines[0];
    const d = digits(last.c);
    document.getElementById("price").textContent = fmtNum(last.c, d);
    const ch = ((last.c - first.c) / first.c) * 100;
    const chgEl = document.getElementById("chg24");
    chgEl.textContent = (ch >= 0 ? "+" : "") + ch.toFixed(2) + "%";
    chgEl.className = "indicator-value " + (ch >= 0 ? "pos" : "neg");
    document.getElementById("ema20").textContent =
      fmtNum(e20[e20.length - 1], d);
    document.getElementById("ema50").textContent =
      fmtNum(e50[e50.length - 1], d);
    const rsiVal = rsi(closes, 14);
    const rsiEl = document.getElementById("rsi14");
    rsiEl.textContent = rsiVal ? rsiVal.toFixed(1) : "—";
    rsiEl.className = "indicator-value " + (
      rsiVal > 70 ? "neg" : rsiVal < 30 ? "pos" : "");
    const atrVal = atr(data.klines, 14);
    document.getElementById("atr14").textContent =
      atrVal ? fmtNum(atrVal, d) : "—";
    document.getElementById("high").textContent =
      fmtNum(Math.max(...data.klines.map(k => k.h)), d);
    document.getElementById("low").textContent =
      fmtNum(Math.min(...data.klines.map(k => k.l)), d);

    chart.timeScale().fitContent();
    document.getElementById("status").textContent =
      `${sym} ${iv} · ${data.klines.length} bars · ${data.source}`;

    // После klines — signals + SMC zones + engine market + analysis +
    // пользовательские drawing'и из localStorage
    await loadSignals(sym);
    loadZones(sym);    // async, не блокирует chart
    loadMarket(sym);   // async
    loadAnalysis();    // engine analysis bottom panel
    loadDrawingsFromStorage();   // восстанавливаем H-Lines
  } catch (e) {
    document.getElementById("status").textContent = `Error: ${e.message}`;
  }
}

async function loadMarket(symbol) {
  try {
    const r = await fetch(`/api/market?symbol=${symbol}`);
    if (!r.ok) {
      ["cvd","mtfbias","funding","oi","vwapdelta","poc","turtle","liqs"]
        .forEach(id => document.getElementById(id).textContent = "—");
      return;
    }
    const m = await r.json();
    if (m.error) {
      ["cvd","mtfbias","funding","oi","vwapdelta","poc","turtle","liqs"]
        .forEach(id => document.getElementById(id).textContent = "—");
      return;
    }
    const setEl = (id, text, cls) => {
      const el = document.getElementById(id);
      if (!el) return;
      el.textContent = text;
      el.className = "indicator-value" + (cls ? " " + cls : "");
    };
    // CVD
    const trend = (m.cvd && m.cvd.trend) || "—";
    const cvdCls = trend === "up" ? "pos" : trend === "down" ? "neg" : "";
    setEl("cvd", trend.toUpperCase(), cvdCls);
    // MTF EMA bias
    const eb = m.ema_bias || {};
    const arrow = b => b === "bull" ? "▲" : b === "bear" ? "▼" : "▬";
    setEl("mtfbias",
      `${arrow(eb["1h"])}1H ${arrow(eb["4h"])}4H ${arrow(eb["1d"])}1D`);
    // Funding
    const fr = m.funding && m.funding.bybit;
    if (fr != null) {
      const pct = (fr * 100).toFixed(4);
      const cls = fr > 0.0005 ? "neg" : fr < -0.0005 ? "pos" : "";
      setEl("funding", pct + "%", cls);
    } else {
      setEl("funding", "—");
    }
    // OI (raw, в коинах)
    if (m.open_int != null) {
      const oi = m.open_int;
      const fmt = oi > 1e6 ? (oi/1e6).toFixed(2) + "M"
                : oi > 1e3 ? (oi/1e3).toFixed(2) + "K"
                : oi.toFixed(0);
      setEl("oi", fmt);
    } else { setEl("oi", "—"); }
    // VWAP delta
    if (m.vwap != null && m.price != null) {
      const d = ((m.price - m.vwap) / m.vwap) * 100;
      setEl("vwapdelta", (d >= 0 ? "+" : "") + d.toFixed(2) + "%",
            d >= 0 ? "pos" : "neg");
    } else { setEl("vwapdelta", "—"); }
    // VP POC
    if (m.vp && m.vp.poc != null) {
      const dig = digits(m.vp.poc);
      setEl("poc", fmtNum(m.vp.poc, dig));
    } else { setEl("poc", "—"); }
    // Turtle zones
    const t1 = m.turtle_1h || "—";
    const t4 = m.turtle_4h || "—";
    setEl("turtle", `${t1} / ${t4}`);
    // Liquidations
    const liqs = m.liquidations || {};
    if (liqs.long_24h != null || liqs.short_24h != null) {
      const fmtL = v => v == null ? "—"
        : v > 1e6 ? (v/1e6).toFixed(1) + "M"
        : v > 1e3 ? (v/1e3).toFixed(1) + "K" : v.toFixed(0);
      setEl("liqs", `${fmtL(liqs.long_24h)} / ${fmtL(liqs.short_24h)}`);
    } else { setEl("liqs", "—"); }
  } catch (e) {
    console.warn("market load failed:", e);
  }
}

// ─── Favorites (localStorage persistence) ────────────────────────────────
const FAVORITES_KEY = "screener_favorites";

function getFavorites() {
  try {
    const raw = localStorage.getItem(FAVORITES_KEY);
    if (!raw) return [];
    const arr = JSON.parse(raw);
    return Array.isArray(arr) ? arr : [];
  } catch (e) { return []; }
}

function saveFavorites(favs) {
  try {
    localStorage.setItem(FAVORITES_KEY, JSON.stringify(favs));
  } catch (e) {}
}

function isFavorite(symbol) {
  return getFavorites().includes(symbol);
}

function toggleFavorite(symbol) {
  const favs = getFavorites();
  const idx = favs.indexOf(symbol);
  if (idx >= 0) {
    favs.splice(idx, 1);
  } else {
    favs.push(symbol);
  }
  saveFavorites(favs);
  return idx < 0;   // true если добавили
}

async function loadWatchlist() {
  try {
    const r = await fetch("/api/prices");
    if (!r.ok) return;
    const data = await r.json();
    const cur = document.getElementById("symbol").value;
    const favs = new Set(getFavorites());

    // Sort: favorites first (sorted alphabetically внутри),
    // потом не-favorites (by abs change descending — как было)
    const items = (data.prices || []).slice();
    items.sort((a, b) => {
      const af = favs.has(a.symbol) ? 1 : 0;
      const bf = favs.has(b.symbol) ? 1 : 0;
      if (af !== bf) return bf - af;   // favorites first
      if (af && bf) return a.symbol.localeCompare(b.symbol);
      return Math.abs(b.change_24h || 0) - Math.abs(a.change_24h || 0);
    });

    const html = items.map(p => {
      const ch = p.change_24h || 0;
      const chCls = ch >= 0 ? "pos" : "neg";
      const chStr = (ch >= 0 ? "+" : "") + ch.toFixed(2) + "%";
      const sym = p.symbol.replace("USDT", "");
      const active = p.symbol === cur ? " active" : "";
      const fav = favs.has(p.symbol);
      const star = fav ? "⭐" : "☆";
      const starCls = fav ? "watchlist-star fav" : "watchlist-star";
      const d = digits(p.price);
      return `<div class="watchlist-row${active}" data-symbol="${p.symbol}">
        <div class="${starCls}" data-fav-symbol="${p.symbol}">${star}</div>
        <div class="watchlist-symbol">${sym}</div>
        <div class="watchlist-price">${fmtNum(p.price, d)}</div>
        <div class="watchlist-change ${chCls}">${chStr}</div>
      </div>`;
    }).join("");
    document.getElementById("watchlist").innerHTML = html;

    // Star click → toggle favorite (stopPropagation чтобы не switch symbol)
    document.querySelectorAll(".watchlist-star").forEach(el => {
      el.addEventListener("click", (ev) => {
        ev.stopPropagation();
        const s = el.getAttribute("data-fav-symbol");
        toggleFavorite(s);
        loadWatchlist();   // перерисовать с новой сортировкой
      });
    });

    // Row click — переключает символ (остаётся как было)
    document.querySelectorAll(".watchlist-row").forEach(row => {
      row.addEventListener("click", () => {
        const sym = row.getAttribute("data-symbol");
        const select = document.getElementById("symbol");
        if (![...select.options].some(o => o.value === sym)) {
          const opt = document.createElement("option");
          opt.value = sym;
          opt.textContent = sym.replace("USDT", "");
          select.appendChild(opt);
        }
        select.value = sym;
        loadChart();
        loadWatchlist();
        if (typeof updateHash === "function") updateHash();
      });
    });
  } catch (e) {
    console.warn("watchlist load failed:", e);
  }
}

let zonePriceLines = [];
let userDrawingLines = [];   // {price, title, color, lineObj}
let activeTool = null;       // null | "hline"

function _drawingsKey() {
  const sym = document.getElementById("symbol").value;
  return `screener_drawings_${sym}`;
}

function loadDrawingsFromStorage() {
  // Remove existing drawn lines from chart
  for (const d of userDrawingLines) {
    try { candleSeries.removePriceLine(d.lineObj); } catch (e) {}
  }
  userDrawingLines = [];

  const raw = localStorage.getItem(_drawingsKey());
  if (!raw) return;
  try {
    const saved = JSON.parse(raw);
    for (const d of saved) {
      const lineObj = candleSeries.createPriceLine({
        price: d.price, color: d.color || "#bc8cff",
        lineWidth: 2, lineStyle: 0,
        axisLabelVisible: true,
        title: d.title || "",
      });
      userDrawingLines.push({ ...d, lineObj });
    }
  } catch (e) {
    console.warn("loadDrawings failed:", e);
  }
}

function saveDrawingsToStorage() {
  const serializable = userDrawingLines.map(d => ({
    price: d.price, color: d.color, title: d.title,
  }));
  localStorage.setItem(_drawingsKey(),
                       JSON.stringify(serializable));
}

function toggleTool(tool) {
  const wasActive = activeTool === tool;
  activeTool = wasActive ? null : tool;
  // Update button states
  for (const id of ["btnHLine"]) {
    document.getElementById(id).classList.remove("active");
  }
  if (!wasActive) {
    const btnId = tool === "hline" ? "btnHLine" : null;
    if (btnId) document.getElementById(btnId).classList.add("active");
  }
  // Cursor
  const chartEl = document.getElementById("chart");
  chartEl.classList.toggle("drawing-mode", activeTool !== null);
}

function addHLine(price) {
  const color = "#bc8cff";
  const title = `${price.toFixed(2)}`;
  const lineObj = candleSeries.createPriceLine({
    price: price, color: color,
    lineWidth: 2, lineStyle: 0,
    axisLabelVisible: true, title: title,
  });
  userDrawingLines.push({ price, color, title, lineObj });
  saveDrawingsToStorage();
}

function clearDrawings() {
  for (const d of userDrawingLines) {
    try { candleSeries.removePriceLine(d.lineObj); } catch (e) {}
  }
  userDrawingLines = [];
  saveDrawingsToStorage();
}


function clearZones() {
  for (const pl of zonePriceLines) {
    try { candleSeries.removePriceLine(pl); } catch (e) {}
  }
  zonePriceLines = [];
}

async function loadZones(symbol) {
  clearZones();
  const showZones = document.getElementById("toggleZones");
  if (showZones && !showZones.checked) return;
  try {
    const iv = document.getElementById("interval").value;
    const r = await fetch(`/api/zones?symbol=${symbol}&interval=${iv}&limit=5`);
    if (!r.ok) return;
    const data = await r.json();
    const z = data.zones || {};
    const addLine = (price, color, title) => {
      try {
        const pl = candleSeries.createPriceLine({
          price: price, color: color, lineWidth: 1,
          lineStyle: 2, axisLabelVisible: true, title: title,
        });
        zonePriceLines.push(pl);
      } catch (e) {}
    };
    // OB — top + bottom (bull green, bear red)
    for (const o of (z.ob || [])) {
      const col = o.direction === "bull" ? "#3fb950" : "#f85149";
      addLine(o.top, col, "OB↑");
      addLine(o.bottom, col, "OB↓");
    }
    // FVG — top + bottom
    for (const f of (z.fvg || [])) {
      const col = f.direction === "bull" ? "#39c5cf" : "#d29922";
      addLine(f.top, col, "FVG↑");
      addLine(f.bottom, col, "FVG↓");
    }
    // MB/BB — single level
    for (const m of (z.mb || [])) {
      const col = m.direction === "bull" ? "#bc8cff" : "#e9a8ff";
      addLine(m.level, col, "MB");
    }
    for (const b of (z.bb || [])) {
      const col = b.direction === "bull" ? "#58a6ff" : "#f85149";
      addLine(b.level, col, "BB");
    }
    // Update zones count
    const cnt = document.getElementById("zonesCount");
    if (cnt) {
      const total = (z.ob || []).length + (z.fvg || []).length +
                    (z.mb || []).length + (z.bb || []).length;
      cnt.textContent =
        `${total} zones: ${(z.ob || []).length} OB · ` +
        `${(z.fvg || []).length} FVG · ${(z.mb || []).length} MB · ` +
        `${(z.bb || []).length} BB`;
    }
  } catch (e) {
    console.warn("zones load failed:", e);
  }
}

async function loadAnalysis(force = false) {
  const sym = document.getElementById("symbol").value;
  const status = document.getElementById("analysisStatus");
  if (status) status.textContent = "Loading...";
  try {
    const url = `/api/analysis?symbol=${sym}` + (force ? "&force=true" : "");
    const r = await fetch(url);
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const data = await r.json();
    if (data.error) throw new Error(data.error);

    const cp = document.getElementById("confluencePanel");
    if (data.confluence != null) {
      let cls = "";
      if (data.confluence >= 65) cls = "pos";
      else if (data.confluence < 45) cls = "neg";
      cp.textContent = `Confluence: ${data.confluence}/100`;
      cp.className = "ba-confluence " + cls;
    } else {
      cp.textContent = "Confluence: —";
    }

    const bp = document.getElementById("briefPanel");
    bp.textContent = data.brief_raw || "—";

    const fp = document.getElementById("factorsPanel");
    fp.innerHTML = "";
    for (const f of (data.confluence_factors || [])) {
      const span = document.createElement("span");
      let cls = "ba-factor";
      const fl = f.toLowerCase();
      if (fl.includes("✅") || fl.includes("+") || fl.includes("bull")) {
        cls += " pos";
      } else if (fl.includes("❌") || fl.includes("против") ||
                 fl.includes("-") || fl.includes("bear")) {
        cls += " neg";
      }
      span.className = cls;
      span.textContent = f.slice(0, 60);
      fp.appendChild(span);
    }

    if (status) {
      const cacheStr = data.cached
        ? ` (cached ${data.age_sec}s ago)` : " (fresh)";
      status.textContent = `${sym}${cacheStr}`;
    }
  } catch (e) {
    if (status) status.textContent = `Error: ${e.message}`;
  }
}

async function loadSignals(symbol) {
  const showSignals = document.getElementById("toggleSignals");
  if (showSignals && !showSignals.checked) {
    candleSeries.setMarkers([]);
    return;
  }
  try {
    const r = await fetch(`/api/signals?symbol=${symbol}&days=30&limit=80`);
    if (!r.ok) return;
    const data = await r.json();
    if (!data.signals || !data.signals.length) {
      candleSeries.setMarkers([]);
      return;
    }
    const markers = [];
    for (const s of data.signals) {
      // Entry marker
      const isLong = s.direction === "long";
      markers.push({
        time: s.ts,
        position: isLong ? "belowBar" : "aboveBar",
        color: isLong ? "#3fb950" : "#f85149",
        shape: isLong ? "arrowUp" : "arrowDown",
        text: (isLong ? "L" : "S") +
              (s.confidence ? "·" + s.confidence : ""),
      });
      // Exit marker (если уже закрылась)
      if (s.hit_ts && s.hit_level) {
        const hit = s.hit_level.toUpperCase();
        let color, text;
        if (hit === "SL") {
          color = "#f85149"; text = "SL";
        } else if (hit.startsWith("TP")) {
          color = "#3fb950"; text = hit;
        } else {
          color = "#8b949e"; text = hit.slice(0, 4);
        }
        markers.push({
          time: s.hit_ts,
          position: "inBar",
          color: color,
          shape: "circle",
          text: text,
        });
      }
    }
    candleSeries.setMarkers(markers);
    const stat = document.getElementById("signalsCount");
    if (stat) {
      const wins = data.signals.filter(s =>
        s.hit_level && s.hit_level.toUpperCase().startsWith("TP")).length;
      const losses = data.signals.filter(s =>
        s.hit_level && s.hit_level.toUpperCase() === "SL").length;
      const closed = wins + losses;
      const wr = closed > 0 ? (wins / closed * 100).toFixed(1) : "—";
      stat.textContent =
        `${data.signals.length} signals · ${closed} closed · ` +
        `WR ${wr}% (${wins}W/${losses}L)`;
    }
  } catch (e) {
    console.warn("signals load failed:", e);
  }
}

// ─── URL routing via hash (#BTCUSDT/1H) ──────────────────────────────────
// Allow shareable links: /ui#BTCUSDT/60, /ui#ETH/4H, etc.
const TF_ALIASES = {
  "1H": "60", "2H": "120", "4H": "240",
  "5M": "5", "15M": "15", "30M": "30",
  "1D": "D",
};
const TF_REVERSE = {
  "5": "5m", "15": "15m", "60": "1H", "240": "4H", "D": "1D",
};

function _normalizeSymbol(s) {
  if (!s) return null;
  let v = s.toUpperCase().replace(".P", "");
  if (!v.endsWith("USDT")) v += "USDT";
  return v;
}
function _normalizeInterval(s) {
  if (!s) return null;
  const v = s.toUpperCase();
  return TF_ALIASES[v] || v;
}

function parseHash() {
  const h = window.location.hash.replace(/^#/, "");
  if (!h) return { symbol: null, interval: null };
  const [rawSym, rawIv] = h.split("/");
  return {
    symbol:   _normalizeSymbol(rawSym),
    interval: _normalizeInterval(rawIv),
  };
}

function updateHash() {
  const sym = document.getElementById("symbol").value;
  const iv = document.getElementById("interval").value;
  const symShort = sym.replace("USDT", "");
  const ivDisp = TF_REVERSE[iv] || iv;
  // skip update if hash matches (avoid hashchange loop)
  const desired = `#${symShort}/${ivDisp}`;
  if (window.location.hash !== desired) {
    history.replaceState(null, "", desired);
  }
}

function applyHashOnLoad() {
  const { symbol, interval } = parseHash();
  const symSelect = document.getElementById("symbol");
  const ivSelect = document.getElementById("interval");
  if (symbol) {
    // Add to dropdown если ещё не там
    if (![...symSelect.options].some(o => o.value === symbol)) {
      const opt = document.createElement("option");
      opt.value = symbol;
      opt.textContent = symbol.replace("USDT", "");
      symSelect.appendChild(opt);
    }
    symSelect.value = symbol;
  }
  if (interval && [...ivSelect.options].some(o => o.value === interval)) {
    ivSelect.value = interval;
  }
}

window.addEventListener("hashchange", () => {
  const { symbol, interval } = parseHash();
  const symSelect = document.getElementById("symbol");
  const ivSelect = document.getElementById("interval");
  if (symbol && symSelect.value !== symbol) {
    if (![...symSelect.options].some(o => o.value === symbol)) {
      const opt = document.createElement("option");
      opt.value = symbol;
      opt.textContent = symbol.replace("USDT", "");
      symSelect.appendChild(opt);
    }
    symSelect.value = symbol;
  }
  if (interval && ivSelect.value !== interval) {
    ivSelect.value = interval;
  }
  loadChart();
});

initChart();
applyHashOnLoad();
loadChart();
loadWatchlist();
updateHash();
setInterval(loadWatchlist, 30000);   // auto-refresh каждые 30s
document.getElementById("symbol").addEventListener("change", () => {
  loadChart();
  loadWatchlist();
  updateHash();
});
document.getElementById("interval").addEventListener("change", () => {
  loadChart();
  updateHash();
});
document.getElementById("toggleSignals").addEventListener("change", () => {
  loadSignals(document.getElementById("symbol").value);
});
document.getElementById("toggleZones").addEventListener("change", () => {
  loadZones(document.getElementById("symbol").value);
});
</script>
</body>
</html>"""


@app.route("/ui", methods=["GET"])
def ui_chart():
    """Главная UI-страница: chart viewer с indicators."""
    return UI_CHART_HTML, 200, {"Content-Type": "text/html; charset=utf-8"}


@app.route("/api/symbols", methods=["GET"])
def api_symbols():
    """Список символов из watchlist."""
    return jsonify({"symbols": list(SYMBOLS)}), 200


# Дефолтный watchlist для UI (если SYMBOLS env-var задаёт только основные)
UI_DEFAULT_WATCHLIST = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT",
    "ADAUSDT", "DOGEUSDT", "AVAXUSDT", "LINKUSDT", "MATICUSDT",
    "DOTUSDT", "ARBUSDT", "OPUSDT", "SUIUSDT", "TONUSDT",
]


def _fetch_bulk_prices_bybit(symbols: list) -> dict:
    """Возвращает {symbol: {price, change_24h, vol_24h}} из Bybit."""
    out = {}
    try:
        r = requests.get(
            f"{BYBIT}/v5/market/tickers",
            params={"category": "linear"}, timeout=8,
        )
        r.raise_for_status()
        data = r.json()
        tickers = (data.get("result") or {}).get("list") or []
        wanted = set(symbols)
        for t in tickers:
            sym = t.get("symbol")
            if sym not in wanted:
                continue
            try:
                out[sym] = {
                    "price":      float(t.get("lastPrice", 0)),
                    "change_24h": float(t.get("price24hPcnt", 0)) * 100,
                    "vol_24h":    float(t.get("volume24h", 0)),
                }
            except (ValueError, TypeError):
                continue
    except Exception as e:
        log.warning(f"Bybit bulk tickers: {e}")
    return out


def _fetch_bulk_prices_binance(symbols: list) -> dict:
    """Fallback: Binance Futures bulk tickers."""
    out = {}
    try:
        r = requests.get(
            f"{BINANCE_FAPI}/fapi/v1/ticker/24hr", timeout=8,
        )
        r.raise_for_status()
        tickers = r.json()
        wanted = set(symbols)
        for t in tickers:
            sym = t.get("symbol")
            if sym not in wanted:
                continue
            try:
                out[sym] = {
                    "price":      float(t.get("lastPrice", 0)),
                    "change_24h": float(t.get("priceChangePercent", 0)),
                    "vol_24h":    float(t.get("volume", 0)),
                }
            except (ValueError, TypeError):
                continue
    except Exception as e:
        log.warning(f"Binance bulk tickers: {e}")
    return out


@app.route("/api/prices", methods=["GET"])
def api_prices():
    """
    Bulk цены для нескольких символов одним запросом. Параметры:
      symbols — comma-separated список (e.g. BTCUSDT,ETHUSDT)
      или omit → берём UI_DEFAULT_WATCHLIST

    Использует Bybit bulk tickers с fallback на Binance Futures.
    Сортирует ответ по abs(change_24h) убывая (movers топ).
    """
    raw = request.args.get("symbols")
    if raw:
        symbols = [s.strip().upper() for s in raw.split(",") if s.strip()]
        symbols = [s for s in symbols if s.endswith("USDT")]
    else:
        symbols = list(UI_DEFAULT_WATCHLIST)
    if not symbols:
        return jsonify({"prices": []}), 200

    # Bybit first
    prices_map = _fetch_bulk_prices_bybit(symbols)

    # Binance fallback для отсутствующих
    missing = [s for s in symbols if s not in prices_map]
    if missing:
        fallback = _fetch_bulk_prices_binance(missing)
        prices_map.update(fallback)

    items = []
    for sym in symbols:
        p = prices_map.get(sym)
        if not p:
            continue
        items.append({"symbol": sym, **p})

    # Сортировка: movers (top abs change) первыми
    items.sort(key=lambda x: -abs(x.get("change_24h", 0)))

    return jsonify({"prices": items}), 200


@app.route("/api/signals", methods=["GET"])
def api_signals():
    """
    Recent signals из signal_outcomes для overlay на chart'е.
    Параметры:
      symbol — BTCUSDT (default: BTCUSDT)
      days — сколько дней назад (default: 30, max: 90)
      limit — макс кол-во сигналов (default: 100, max: 200)

    Возвращает {signals: [{ts, direction, entry, sl, tp1, tp2, tp3,
                           status, r_multiple, hit_ts, hit_level,
                           signal_type, confidence, verdict}]}

    ts (entry_ts) — unix seconds для совместимости с Lightweight Charts.
    """
    symbol = (request.args.get("symbol") or "BTCUSDT").upper().strip()
    try:
        days = int(request.args.get("days") or "30")
    except ValueError:
        days = 30
    days = max(1, min(90, days))
    try:
        limit = int(request.args.get("limit") or "100")
    except ValueError:
        limit = 100
    limit = max(1, min(200, limit))

    try:
        from datetime import datetime as _dt, timedelta as _td, timezone as _tz
        cutoff = _dt.now(_tz.utc) - _td(days=days)
        cutoff_iso = cutoff.isoformat()

        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                """
                SELECT entry_ts, entry_price, signal_type, direction,
                       verdict, sl, tp1, tp2, tp3, status,
                       hit_level, hit_at, r_multiple, confidence
                FROM signal_outcomes
                WHERE symbol = ?
                  AND verdict IN ('LONG', 'SHORT')
                  AND entry_ts >= ?
                ORDER BY entry_ts DESC
                LIMIT ?
                """,
                (symbol, cutoff_iso, limit),
            ).fetchall()
        finally:
            conn.close()

        signals = []
        for r in rows:
            ts_raw = r["entry_ts"]
            try:
                ts_sec = int(_dt.fromisoformat(
                    ts_raw.replace("Z", "+00:00")).timestamp())
            except (ValueError, AttributeError):
                continue
            hit_ts = None
            if r["hit_at"]:
                try:
                    hit_ts = int(_dt.fromisoformat(
                        r["hit_at"].replace("Z", "+00:00")).timestamp())
                except (ValueError, AttributeError):
                    pass
            signals.append({
                "ts":          ts_sec,
                "entry":       r["entry_price"],
                "signal_type": r["signal_type"],
                "direction":   r["direction"],
                "verdict":     r["verdict"],
                "sl":          r["sl"], "tp1": r["tp1"],
                "tp2":         r["tp2"], "tp3": r["tp3"],
                "status":      r["status"],
                "hit_level":   r["hit_level"],
                "hit_ts":      hit_ts,
                "r_multiple":  r["r_multiple"],
                "confidence":  r["confidence"],
            })

        # Reverse → oldest first для chart'а
        signals.reverse()
        return jsonify({"symbol": symbol, "days": days,
                        "signals": signals}), 200
    except sqlite3.OperationalError as e:
        log.warning(f"/api/signals SQL: {e}")
        return jsonify({"symbol": symbol, "signals": [],
                        "error": "DB not initialized"}), 200
    except Exception as e:
        log.warning(f"/api/signals failed: {e}")
        return jsonify({"error": str(e)[:200]}), 500


@app.route("/api/market", methods=["GET"])
def api_market():
    """
    Compact market snapshot для UI: CVD, MTF EMA bias, funding, OI,
    VWAP, VP, indicators (RSI/MACD/ATR%), regime hints.
    """
    symbol = (request.args.get("symbol") or "BTCUSDT").upper().strip()
    if not symbol.endswith("USDT"):
        return jsonify({"error": "invalid symbol"}), 400

    try:
        m = fetch_market(symbol)
        if not m:
            return jsonify({"error": "no market data"}), 502

        b = m.get("bybit") or {}
        hl = m.get("hl") or {}
        cvd = m.get("cvd") or {}
        vp = m.get("vp") or {}
        ind = m.get("indicators") or {}
        ema_biases = m.get("ema_biases") or {}
        vwap = m.get("vwap")
        liqs = m.get("liquidations") or {}

        def _safe_float(v):
            try:
                return float(v) if v is not None else None
            except (ValueError, TypeError):
                return None

        out = {
            "symbol":     symbol,
            "price":      _safe_float(m.get("price")),
            "change_24h": _safe_float(m.get("change_24h")),
            "cvd": {
                "trend":      cvd.get("trend"),
                "divergence": cvd.get("divergence"),
                "delta_5":    _safe_float(cvd.get("delta_5")),
            },
            "ema_bias": {
                "1h": ema_biases.get("1h"),
                "4h": ema_biases.get("4h"),
                "1d": ema_biases.get("1d"),
            },
            "funding": {
                "bybit": _safe_float(b.get("funding")),
                "hl":    _safe_float(hl.get("funding")),
            },
            "vol_24h":   _safe_float(b.get("vol_24h")),
            "open_int":  _safe_float(b.get("open_interest")),
            "vp": {
                "poc": _safe_float(vp.get("poc")),
                "vah": _safe_float(vp.get("vah")),
                "val": _safe_float(vp.get("val")),
            },
            "vwap":       _safe_float(vwap),
            "indicators": {
                "rsi":    _safe_float(ind.get("rsi")),
                "macd":   ind.get("macd"),
                "atr":    _safe_float(ind.get("atr")),
                "atr_pct": _safe_float(ind.get("atr_pct")),
            },
            "turtle_1h":   m.get("turtle_1h"),
            "turtle_4h":   m.get("turtle_4h"),
            "liquidations": {
                "long_24h":  _safe_float(liqs.get("long_24h")),
                "short_24h": _safe_float(liqs.get("short_24h")),
            },
            "btc_corr": _safe_float(m.get("btc_corr")),
        }
        return jsonify(out), 200
    except Exception as e:
        log.warning(f"/api/market failed for {symbol}: {e}")
        return jsonify({"error": str(e)[:200]}), 500


@app.route("/api/zones", methods=["GET"])
def api_zones():
    """
    Smart-money zones для overlay на chart'е: Order Blocks, Mitigation
    Blocks, Breaker Blocks, FVGs.
    """
    symbol = (request.args.get("symbol") or "BTCUSDT").upper().strip()
    interval = (request.args.get("interval") or "60").strip()
    try:
        limit = int(request.args.get("limit") or "8")
    except ValueError:
        limit = 8
    limit = max(1, min(20, limit))

    if not symbol.endswith("USDT"):
        return jsonify({"error": "invalid symbol"}), 400

    try:
        rows = _klines(symbol, interval, 300)
        if not rows or len(rows) < 30:
            return jsonify({"symbol": symbol, "interval": interval,
                            "zones": {"ob": [], "mb": [], "bb": [],
                                      "fvg": []}}), 200

        ob_zones = []
        try:
            obs = order_blocks.find_order_blocks(rows)
            obs = [o for o in obs if o.validated and not o.mitigated]
            for o in obs[-limit:]:
                ob_zones.append({
                    "direction": o.direction,
                    "top":       float(o.body_high),
                    "bottom":    float(o.body_low),
                    "idx":       int(o.candle_idx),
                })
        except Exception as e:
            log.debug(f"OB detect failed: {e}")

        mb_zones = []
        bb_zones = []
        try:
            from structure import find_swing_points
            swings = find_swing_points(rows)
            mbs = block_patterns.find_mitigation_blocks(rows, swings)
            for mb in mbs[-limit:]:
                mb_zones.append({
                    "direction": mb.direction,
                    "level":     float(mb.level),
                    "idx":       int(mb.test_at),
                })
            bbs = block_patterns.find_breaker_blocks(rows, swings)
            for bb in bbs[-limit:]:
                bb_zones.append({
                    "direction": bb.direction,
                    "level":     float(bb.level),
                    "idx":       int(bb.test_at),
                })
        except Exception as e:
            log.debug(f"MB/BB detect failed: {e}")

        # Простой FVG-detect (3-candle gap), recent внутри последних 100 баров
        fvg_zones = []
        try:
            scan_start = max(2, len(rows) - 100)
            for i in range(scan_start, len(rows)):
                c0, c2 = rows[i], rows[i - 2]
                if c0["l"] > c2["h"]:
                    fvg_zones.append({
                        "direction": "bull",
                        "top":       float(c0["l"]),
                        "bottom":    float(c2["h"]),
                        "idx":       i,
                    })
                elif c0["h"] < c2["l"]:
                    fvg_zones.append({
                        "direction": "bear",
                        "top":       float(c2["l"]),
                        "bottom":    float(c0["h"]),
                        "idx":       i,
                    })
            fvg_zones = fvg_zones[-limit:]
        except Exception as e:
            log.debug(f"FVG detect failed: {e}")

        return jsonify({
            "symbol": symbol, "interval": interval,
            "zones": {"ob": ob_zones, "mb": mb_zones,
                      "bb": bb_zones, "fvg": fvg_zones},
        }), 200
    except Exception as e:
        log.warning(f"/api/zones failed: {e}")
        return jsonify({"error": str(e)[:200]}), 500


# Кеш для market briefs (TTL 5 минут) чтобы не качать API на каждый ui-load
_ANALYSIS_CACHE: dict = {}
_ANALYSIS_TTL_SEC = 300


@app.route("/api/analysis", methods=["GET"])
def api_analysis():
    """
    Deterministic market analysis для UI: market_brief из llm_agents,
    confluence score.

    Параметры:
      symbol — BTCUSDT
      force — пересчитать без кеша (true/false), default false

    Возвращает {symbol, brief: [строки], confluence, factors, cached, ts}.

    LLM в этой версии НЕ вызывается (стоимость + latency). Только
    deterministic engine output. Будущий PR может добавить опциональный
    LLM-comment.
    """
    import time as _time
    symbol = (request.args.get("symbol") or "BTCUSDT").upper().strip()
    force = (request.args.get("force") or "").lower() == "true"
    if not symbol.endswith("USDT"):
        return jsonify({"error": "invalid symbol"}), 400

    now = _time.time()
    if not force and symbol in _ANALYSIS_CACHE:
        cached = _ANALYSIS_CACHE[symbol]
        if now - cached["ts"] < _ANALYSIS_TTL_SEC:
            data = dict(cached["data"])
            data["cached"] = True
            data["age_sec"] = round(now - cached["ts"], 1)
            return jsonify(data), 200

    try:
        market = fetch_market(symbol)
        if not market:
            return jsonify({"error": "no market data"}), 502

        from llm_agents import market_brief
        brief_text = market_brief(market)
        brief_lines = [
            line.strip() for line in brief_text.split("·")
            if line.strip()
        ]

        confluence_score = None
        confluence_factors: list = []
        try:
            score, factors = compute_confluence_score(
                "GENERIC", market, {})
            confluence_score = score
            confluence_factors = factors[:8]
        except Exception as e:
            log.debug(f"confluence calc failed: {e}")

        out = {
            "symbol":    symbol,
            "brief":     brief_lines[:8],
            "brief_raw": brief_text,
            "confluence": confluence_score,
            "confluence_factors": confluence_factors,
            "cached":    False,
            "ts":        now,
        }
        _ANALYSIS_CACHE[symbol] = {"ts": now, "data": dict(out)}
        return jsonify(out), 200
    except Exception as e:
        log.warning(f"/api/analysis failed for {symbol}: {e}")
        return jsonify({"error": str(e)[:200]}), 500


@app.route("/api/klines", methods=["GET"])
def api_klines():
    """
    JSON klines для UI. Параметры:
      symbol — BTCUSDT, ETHUSDT и т.д.
      interval — 5/15/60/240/D
      limit — кол-во баров (1-500)

    Используется уже существующий _klines() с fallback chain
    Bybit → Binance → Hyperliquid.
    """
    symbol = (request.args.get("symbol") or "").upper().strip()
    interval = (request.args.get("interval") or "60").strip()
    try:
        limit = int(request.args.get("limit") or "300")
    except ValueError:
        limit = 300
    limit = max(1, min(500, limit))

    if not symbol or not symbol.endswith("USDT"):
        return jsonify({"error": "invalid symbol"}), 400

    try:
        rows = _klines(symbol, interval, limit)
        # _klines() возвращает list[{o,h,l,c,v}] но без ts. Поскольку UI
        # нужен ts, считаем его обратно от now() с шагом interval.
        from datetime import datetime as _dt, timezone as _tz
        mins = {"5": 5, "15": 15, "30": 30, "60": 60, "120": 120,
                "240": 240, "D": 1440, "W": 10080}.get(interval, 60)
        now_ms = int(_dt.now(_tz.utc).timestamp() * 1000)
        step_ms = mins * 60_000
        klines = []
        for i, r in enumerate(rows):
            ts = now_ms - (len(rows) - 1 - i) * step_ms
            klines.append({
                "ts": ts, "o": r["o"], "h": r["h"],
                "l": r["l"], "c": r["c"], "v": r.get("v", 0),
            })
        return jsonify({
            "symbol": symbol, "interval": interval,
            "klines": klines, "source": "auto-fallback",
        }), 200
    except Exception as e:
        log.warning(f"/api/klines failed: {e}")
        return jsonify({"error": str(e)[:200]}), 500


# ─── TELEGRAM COMMANDS ────────────────────────────────────────────────────────

def cmd_status(chat_id: int):
    tg_send("📡 Получаю данные с бирж...", chat_id=chat_id)
    lines = []
    for sym in SYMBOLS:
        m = fetch_market(sym)
        lines.append(
            f"<b>{sym.replace('USDT','')}/USDT.P</b>\n"
            + market_summary_text(sym, m)
        )

    recent  = db_recent(hours=2, limit=5)
    rec_str = "\n".join(f"  • {r[0]}: {r[3]} {r[1]} {r[2]}" for r in recent) or "  Нет сигналов за 2ч"

    tg_send(
        "📡 <b>Статус рынка</b>  [Bybit + HL + CVD + MTF]\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        + "\n\n".join(lines)
        + "\n━━━━━━━━━━━━━━━━━━━━\n"
        "🕐 <b>Последние сигналы (2ч):</b>\n" + rec_str,
        chat_id=chat_id,
    )


def cmd_history(chat_id: int):
    rows = db_last_n(10)
    if not rows:
        tg_send("📭 История пуста.", chat_id=chat_id)
        return
    lines = "\n".join(
        f"  {r[0]}  {r[3]} {r[1]} {r[2]}  [Q:{r[5]}]"
        for r in rows
    )
    tg_send(f"📜 <b>Последние 10 сигналов:</b>\n<pre>{lines}</pre>", chat_id=chat_id)


def cmd_ask(chat_id: int, question: str):
    if not question:
        tg_send("❓ Пример: /ask стоит ли сейчас лонговать BTC?", chat_id=chat_id)
        return
    tg_send("🧠 Bull, Bear и Risk обсуждают…", chat_id=chat_id)

    # Multi-agent работает по одному символу за раз — выбираем по
    # упоминанию в вопросе или дефолтный.
    sym    = _extract_ticker(question) or SYMBOLS[0]
    market = fetch_market(sym)
    recent = db_last_n(12)
    answer = llm_ask(question, market, recent)
    tg_send(f"🧠 <b>Анализ по {sym}:</b>\n\n{answer}", chat_id=chat_id)


def cmd_digest(chat_id: int):
    tg_send("📊 Генерирую дайджест...", chat_id=chat_id)
    signals = db_today()

    # Один основной символ для market_brief (если несколько SYMBOLS —
    # берём первый, остальные попадут в общую engine-stats picture)
    primary_market = fetch_market(SYMBOLS[0])

    # Engine-performance за сегодня для контекста LLM
    tracking_stats = None
    try:
        with _db_lock, db_conn() as c:
            tracking_stats = tracking.compute_stats(c, days=1)
    except Exception as e:
        log.warning(f"digest tracking_stats: {e}")

    digest = llm_digest(signals, primary_market, tracking_stats)
    tg_send(
        f"📊 <b>Дайджест за сегодня</b> ({len(signals)} сигналов)\n"
        f"━━━━━━━━━━━━━━━━━\n{digest}",
        chat_id=chat_id,
    )


def cmd_analyze(chat_id: int, args: str):
    if not args:
        tg_send(
            "❓ Примеры:\n"
            "/analyze ETH          — все ТФ: M15 · 1H · 4H · D1\n"
            "/analyze SOL 4H       — только 4H\n"
            "/analyze BTC 15m 4H   — M15 + 4H\n\n"
            "Или просто напиши:\n"
            "<i>анализируй ETH</i>  или  <i>analyze SOL 4H</i>",
            chat_id=chat_id,
        )
        return
    symbol, tfs = _parse_symbol_tf(args)
    cmd_analyze_symbol(chat_id, symbol, tfs or None)


def cmd_alert_add(chat_id: int, args: str):
    """
    /alert BTC 105000      → above (auto-detect direction)
    /alert ETH < 3200      → below
    /alert SOL > 200       → above
    """
    # Parse: [TICKER] [</>/nothing] [PRICE]
    # e.g. "BTC 105000", "ETHUSDT.P < 3200", "SOL > 200"
    m = re.search(r'([<>])\s*([\d,\.]+)|([\d,\.]+)', args)
    if not m:
        tg_send(
            "❓ Формат:\n"
            "/alert BTC 105000      — уведомит при $105,000\n"
            "/alert ETH &lt; 3200      — когда ETH упадёт ниже\n"
            "/alert SOLUSDT &gt; 200   — когда SOL поднимется выше",
            chat_id=chat_id,
        )
        return

    ticker = _extract_ticker(args)
    if not ticker:
        tg_send("❓ Не могу распознать тикер. Пример: /alert BTC 105000", chat_id=chat_id)
        return
    symbol = ticker

    op        = m.group(1) or ""
    price_str = (m.group(2) or m.group(3) or "0").replace(",", "")
    if not price_str or float(price_str) == 0:
        tg_send("❓ Укажи цену. Пример: /alert BTC 105000", chat_id=chat_id)
        return
    target = float(price_str)

    # Get current price to auto-detect direction when operator omitted
    try:
        tk = requests.get(
            f"{BYBIT}/v5/market/tickers",
            params={"symbol": symbol, "category": "linear"}, timeout=5,
        ).json()["result"]["list"][0]
        current = float(tk["lastPrice"])
    except Exception:
        current = 0.0

    if op == "<":
        direction = "below"
    elif op == ">":
        direction = "above"
    else:
        direction = "above" if target > current else "below"

    alert_id  = db_alert_add(chat_id, symbol, direction, target)
    sym_short = symbol.replace("USDT", "")
    arrow     = "📈" if direction == "above" else "📉"
    cur_str   = f"\nТекущая цена: ${current:,.2f}" if current else ""
    tg_send(
        f"✅ <b>Алерт #{alert_id} создан</b>\n"
        f"{arrow} {sym_short}/USDT.P "
        f"{'выше' if direction == 'above' else 'ниже'} <b>${target:,.0f}</b>"
        f"{cur_str}",
        chat_id=chat_id,
    )


def cmd_alert_list(chat_id: int):
    alerts = db_alert_list(str(chat_id))
    if not alerts:
        tg_send("📭 Активных алертов нет.\n\nСоздать: /alert BTC 105000", chat_id=chat_id)
        return
    lines = []
    for aid, sym, direction, target, ts in alerts:
        arrow = "📈" if direction == "above" else "📉"
        sym_s = sym.replace("USDT", "")
        lines.append(
            f"#{aid}  {arrow} {sym_s} "
            f"{'>' if direction=='above' else '<'} ${target:,.0f}"
            f"  <i>{ts}</i>"
        )
    tg_send(
        f"🔔 <b>Активные алерты ({len(alerts)}):</b>\n"
        + "\n".join(lines)
        + "\n\nУдалить: /delalert [ID]",
        chat_id=chat_id,
    )


def cmd_alert_delete(chat_id: int, args: str):
    try:
        alert_id = int(args.strip())
    except ValueError:
        tg_send("❓ Пример: /delalert 3", chat_id=chat_id)
        return
    if db_alert_delete(alert_id, str(chat_id)):
        tg_send(f"🗑 Алерт #{alert_id} удалён.", chat_id=chat_id)
    else:
        tg_send(f"❌ Алерт #{alert_id} не найден.", chat_id=chat_id)


def cmd_scan(chat_id: int):
    tg_send("🔍 Запускаю ручное сканирование...", chat_id=chat_id)
    threading.Thread(target=run_auto_scan, daemon=True).start()


def cmd_risk(chat_id: int, args: str):
    """
    Risk calculator. Usage:
      /risk BTC 76000 74000
      /risk BTC 76000 74000 80000
      /risk BTC 76000 sl=74000 tp=80000 account=10000 risk=2
    """
    import re as _re
    text = args.strip()

    # Extract symbol
    sym_match = _re.match(r'([A-Za-z]+)', text)
    if not sym_match:
        tg_send("❌ Укажи тикер. Пример: /risk BTC 76000 74000", chat_id=chat_id)
        return
    coin = sym_match.group(1).upper()
    rest = text[sym_match.end():].strip()

    # Parse named params
    def _get(key, default=None):
        m = _re.search(rf'{key}=([0-9.]+)', rest, _re.IGNORECASE)
        return float(m.group(1)) if m else default

    account = _get("account", 10_000.0)
    risk_pct = _get("risk",   1.0)
    entry    = _get("entry")
    sl       = _get("sl")
    tp       = _get("tp")

    # Positional fallback (numbers without key=)
    nums = [float(x) for x in _re.findall(r'\b(\d+(?:\.\d+)?)\b', rest)
            if float(x) > 10]  # filter out e.g. risk=1
    if entry is None and nums:      entry = nums[0]
    if sl    is None and len(nums) > 1: sl = nums[1]
    if tp    is None and len(nums) > 2: tp = nums[2]

    if entry is None or sl is None:
        tg_send(
            "❌ Нужны entry и stop loss.\n"
            "Пример: <code>/risk BTC 76000 74000</code>\n"
            "Или: <code>/risk BTC entry=76000 sl=74000 tp=80000 account=10000 risk=1</code>",
            chat_id=chat_id,
        )
        return

    sl_dist  = abs(entry - sl)
    sl_pct   = sl_dist / entry * 100
    risk_usd = account * risk_pct / 100
    qty_usd  = risk_usd / (sl_pct / 100) if sl_pct > 0 else 0
    qty_coin = qty_usd / entry if entry > 0 else 0
    leverage = qty_usd / account if account > 0 else 0
    direction = "🟢 LONG" if entry > sl else "🔴 SHORT"

    rr_str = ""
    if tp:
        tp_dist = abs(tp - entry)
        tp_pct  = tp_dist / entry * 100
        rr      = tp_dist / sl_dist if sl_dist > 0 else 0
        profit  = risk_usd * rr
        rr_str  = (f"\nTake Profit:  ${tp:,.2f}  ({tp_pct:+.2f}%)"
                   f"\nR:R Ratio:    1 : {rr:.1f}"
                   f"\nПотенциал:    +${profit:,.0f}")

    # Fetch ATR for context
    atr_str = ""
    try:
        symbol  = coin + "USDT"
        candles = _klines(symbol, "60", 50)
        if candles:
            atr = compute_atr(candles)
            atr_pct_val = atr / entry * 100
            sl_in_atr   = sl_dist / atr if atr > 0 else 0
            atr_str = (f"\n━━━━━━━━━━━━━━━━━━\n"
                       f"📏 ATR(14) 1H: ${atr:,.2f} ({atr_pct_val:.2f}%)\n"
                       f"Стоп = {sl_in_atr:.1f}× ATR "
                       f"{'✅ норма' if 0.5 <= sl_in_atr <= 2.5 else ('⚠️ тесный' if sl_in_atr < 0.5 else '⚠️ широкий')}")
    except Exception:
        pass

    tg_send(
        f"💰 <b>Risk Calculator — {coin}/USDT.P</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"Направление:  {direction}\n"
        f"Entry:        ${entry:,.2f}\n"
        f"Stop Loss:    ${sl:,.2f}  ({sl_pct:.2f}%)"
        f"{rr_str}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📊 <b>Позиция (риск {risk_pct}% от ${account:,.0f})</b>\n"
        f"Риск $:       ${risk_usd:,.0f}\n"
        f"Размер позиции: ${qty_usd:,.0f}  ({qty_coin:.4f} {coin})\n"
        f"Плечо:        ~{leverage:.1f}x"
        f"{atr_str}",
        chat_id=chat_id,
    )


def cmd_market(chat_id: int):
    """Show TOTAL / TOTAL2 / TOTAL3 / OTHERS / dominance snapshot."""
    macro = get_macro()

    def _t(v):
        if v >= 1e12: return f"${v/1e12:.2f}T"
        if v >= 1e9:  return f"${v/1e9:.1f}B"
        return f"${v/1e6:.0f}M"

    total = macro.get("total_mcap", 0)
    if not total:
        tg_send("❌ Нет данных CoinGecko.", chat_id=chat_id)
        return

    chg    = macro.get("mcap_chg24", 0)
    chgi   = "📈" if chg >= 0 else "📉"
    btcd   = macro.get("btc_dom", 0)
    ethd   = macro.get("eth_dom", 0)
    usdt_d = macro.get("usdt_dom", 0)
    t2     = macro.get("total2", 0)
    t3     = macro.get("total3", 0)
    others = macro.get("others", 0)
    ts     = datetime.now(timezone.utc).strftime("%H:%M UTC")

    # Alt season proxy: if BTC dom < 50% → alt season
    alt_signal = "🟢 Alt Season" if btcd < 50 else ("🔴 BTC Season" if btcd > 58 else "⚪ Transition")
    # Stablecoin signal: high = money on sidelines = potential inflow
    stable_sig = "🟢 Много денег на сайдлайне" if usdt_d > 7 else ("⚪ Нейтрально" if usdt_d > 5 else "🔴 Деньги в работе")

    tg_send(
        f"🌍 <b>Market Structure</b>  <i>{ts}</i>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 <b>TOTAL</b>:  {_t(total)}  {chgi}{chg:+.1f}% 24h\n"
        f"📊 <b>TOTAL2</b>: {_t(t2)}  <i>(excl BTC)</i>\n"
        f"📊 <b>TOTAL3</b>: {_t(t3)}  <i>(excl BTC+ETH)</i>\n"
        f"📊 <b>OTHERS</b>: {_t(others)}  <i>(alt caps)</i>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🟠 BTC Dom:    <b>{btcd}%</b>\n"
        f"⚪ ETH Dom:    <b>{ethd}%</b>\n"
        f"💵 Stables Dom: <b>{usdt_d}%</b>  {stable_sig}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"{alt_signal}\n"
        f"<i>Fear&Greed: {macro.get('fg_icon','')} {macro.get('fg_label','')} [{macro.get('fg_value','?')}]</i>",
        chat_id=chat_id,
    )


def cmd_debug(chat_id: int):
    """Test all API endpoints and report which ones are reachable."""
    tg_send("🔧 Проверяю API endpoints...", chat_id=chat_id)
    lines = ["🔧 <b>API Debug</b>", "━━━━━━━━━━━━━━━━━━━━"]

    tests = [
        ("Bybit ticker",  lambda: requests.get(f"{BYBIT}/v5/market/tickers",
            params={"symbol":"BTCUSDT","category":"linear"}, timeout=6
            ).json()["result"]["list"][0]["lastPrice"]),
        ("Bybit klines",  lambda: len(requests.get(f"{BYBIT}/v5/market/kline",
            params={"symbol":"BTCUSDT","interval":"60","limit":"5","category":"linear"},
            timeout=6).json()["result"]["list"])),
        ("Binance ticker", lambda: requests.get(f"{BINANCE_FAPI}/fapi/v1/ticker/24hr",
            params={"symbol":"BTCUSDT"}, timeout=6).json()["lastPrice"]),
        ("Binance klines", lambda: len(requests.get(f"{BINANCE_FAPI}/fapi/v1/klines",
            params={"symbol":"BTCUSDT","interval":"1h","limit":"5"}, timeout=6).json())),
        ("HL candles",     lambda: len(requests.post(HL, timeout=8,
            json={"type":"candleSnapshot","req":{"coin":"BTC","interval":"1h",
            "startTime": int(time.time()*1000)-5*3_600_000,
            "endTime": int(time.time()*1000)}}).json())),
        ("CoinGecko",      lambda: requests.get("https://api.coingecko.com/api/v3/global",
            timeout=6).json()["data"]["market_cap_percentage"]["btc"]),
        ("Fear&Greed",     lambda: requests.get("https://api.alternative.me/fng/?limit=1",
            timeout=6).json()["data"][0]["value"]),
    ]

    for name, fn in tests:
        try:
            val = fn()
            lines.append(f"✅ {name}: {val}")
        except Exception as e:
            lines.append(f"❌ {name}: {str(e)[:60]}")

    tg_send("\n".join(lines), chat_id=chat_id)


def cmd_help(chat_id: int):
    tg_send(
        "🤖 <b>Crypto Screener Pro v3 — команды</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "📊 <b>Анализ</b>\n"
        "/analyze BTC         — полный анализ + торговая идея\n"
        "/analyze SOL 4H      — анализ на конкретном ТФ\n"
        "/status              — рынок: CVD, VP, MTF, F&G\n"
        "/ask [вопрос]        — вопрос о рынке\n\n"
        "📈 <b>Рынок</b>\n"
        "/market              — TOTAL/TOTAL2/TOTAL3/OTHERS + доминации\n"
        "/risk BTC 76000 74000       — калькулятор позиции\n"
        "/risk BTC 76000 74000 80000 — с тейк-профитом\n"
        "/risk BTC entry=76000 sl=74000 account=5000 risk=2\n\n"
        "/top                 — топ гейнеры/лузеры + объём (24H)\n"
        "/movers              — движения 1H по watchlist\n\n"
        "🔔 <b>Price Alerts</b>\n"
        "/alert BTC 105000    — уведомить при $105K\n"
        "/alert ETH &lt; 3200    — уведомить при падении\n"
        "/alerts              — список активных алертов\n"
        "/delalert 3          — удалить алерт #3\n\n"
        "⚙️ <b>Прочее</b>\n"
        "/news                — последние новости (CoinDesk · CT · CryptoSlate)\n"
        "/news ETH            — новости по монете\n"
        "/scan                — ручной запуск автосканера\n"
        "/history             — последние 10 сигналов\n"
        "/stats               — win-rate + risk-adjusted метрики (30д)\n"
        "/trades [days]       — последние закрытые сделки с R-исходом\n"
        "/backtest [sym days] — прогон стратегии на истории (до 365 дней)\n"
        "                       /backtest BTC 30  · /backtest BTC 180 compare\n"
        "                       /backtest BTC 30 tf=15  — другой primary TF\n"
        "/btdiag [sym days [preset=NAME] [KEY=VAL ...]] — диагностика: funnel + breakdown\n"
        "                       /btdiag BTC 30  — куда уходят сигналы\n"
        "                       /btdiag BTC 30 preset=no_gates  — все P3/P4 OFF\n"
        "                       /btdiag BTC 30 preset=wide_tp  — TP 3/5/8 ATR\n"
        "/scanbt [SYM1,SYM2... days [preset=NAME] [KEY=VAL ...]] — backtest нескольких монет\n"
        "                       /scanbt BTC,ETH,SOL 30 preset=no_p3\n"
        "/hyperopt [sym days trials [preset=NAME] [KEY=VAL ...]] — Optuna-тюнинг\n"
        "                       /hyperopt BTC 60 30\n"
        "                       /hyperopt BTC 60 50 walkforward\n"
        "                       /hyperopt BTC 60 50 HTF_BIAS_GATE_ENABLED=false\n"
        "/digest              — дневной дайджест\n\n"
        "💬 <b>Свободный чат — пиши без команд!</b>\n"
        "<i>анализируй BTC 4H</i>     → полный разбор\n"
        "<i>что думаешь об ETH?</i>   → LLM ответит с данными\n\n"
        "📸 <b>Фото графика:</b> пришли скриншот + подпись\n"
        "<i>BTC 4H — думаю шорт отсюда</i>",
        chat_id=chat_id,
    )


def cmd_analyze_chart(chat_id: int, photos: list, caption: str):
    """Handle photo message: download → LLM vision analysis."""
    tg_send("🔍 Анализирую график...", chat_id=chat_id)

    # Pick highest resolution photo
    best = max(photos, key=lambda p: p.get("file_size", 0))
    img_b64, media_type = _tg_download_photo(best["file_id"])
    if not img_b64:
        tg_send("❌ Не удалось загрузить фото. Попробуй ещё раз.", chat_id=chat_id)
        return

    # Detect symbol from caption using universal extractor
    symbol = _extract_ticker(caption) if caption else None
    if not symbol:
        symbol = SYMBOLS[0]

    market = fetch_market(symbol)
    result = llm_analyze_chart(img_b64, media_type, caption, market, symbol)

    sym_short = symbol.replace("USDT", "")
    tg_send(
        f"📊 <b>Анализ графика {sym_short}/USDT.P</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"{result}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"<i>Данные: Bybit + HL · {datetime.now(timezone.utc).strftime('%H:%M UTC')}</i>",
        chat_id=chat_id,
    )


# ─── NEWS (free RSS — no API key needed) ─────────────────────────────────────

_NEWS_FEEDS = [
    ("CoinDesk",       "https://www.coindesk.com/arc/outboundfeeds/rss/"),
    ("Cointelegraph",  "https://cointelegraph.com/rss"),
    ("CryptoSlate",    "https://cryptoslate.com/feed/"),
]
_news_cache: dict = {}   # {filter_key: (timestamp, [items])}
_NEWS_TTL = 300          # 5 min cache


def _fetch_rss(url: str) -> list:
    """Parse RSS feed, return list of {title, url, source} dicts."""
    try:
        r = requests.get(url, timeout=8, headers={"User-Agent": "Mozilla/5.0"})
        root = ET.fromstring(r.content)
        ns   = {"atom": "http://www.w3.org/2005/Atom"}
        items = root.findall(".//item") or root.findall(".//atom:entry", ns)
        out = []
        for item in items[:10]:
            title = (item.findtext("title") or item.findtext("atom:title", namespaces=ns) or "").strip()
            link  = (item.findtext("link")  or item.findtext("atom:link",  namespaces=ns) or "").strip()
            # <atom:link> can be an element with href attribute
            if not link:
                el = item.find("atom:link", ns)
                link = (el.get("href", "") if el is not None else "")
            if title and link:
                out.append({"title": title, "url": link})
        return out
    except Exception as e:
        log.debug(f"RSS {url}: {e}")
        return []


def _news_rss(keyword: str = "", limit: int = 6) -> list:
    """Merge RSS feeds, optionally filter by keyword, deduplicate, return top N."""
    cache_key = keyword.upper() or "ALL"
    ts, cached = _news_cache.get(cache_key, (0, []))
    if time.time() - ts < _NEWS_TTL:
        return cached[:limit]

    all_items = []
    with ThreadPoolExecutor(max_workers=len(_NEWS_FEEDS)) as ex:
        futures = {ex.submit(_fetch_rss, url): name for name, url in _NEWS_FEEDS}
    for fut, name in futures.items():
        for item in fut.result():
            item["source"] = name
            all_items.append(item)

    # Filter by keyword if given (case-insensitive title match)
    if keyword:
        kw = keyword.upper()
        all_items = [i for i in all_items if kw in i["title"].upper()]

    # Deduplicate by title prefix
    seen, deduped = set(), []
    for item in all_items:
        key = item["title"][:40].lower()
        if key not in seen:
            seen.add(key)
            deduped.append(item)

    _news_cache[cache_key] = (time.time(), deduped)
    return deduped[:limit]


def cmd_news(chat_id: int, args: str):
    """Show latest crypto news from free RSS feeds."""
    ticker  = _extract_ticker(args) if args else None
    coin    = ticker.replace("USDT", "") if ticker else ""
    label   = coin if coin else "крипто"

    tg_send(f"📰 Загружаю новости по {label}...", chat_id=chat_id)
    news = _news_rss(keyword=coin, limit=6)

    if not news:
        if coin:
            # Fallback: general news if no coin-specific results
            news = _news_rss(keyword="", limit=6)
            if news:
                tg_send(
                    f"ℹ️ Статей именно по {coin} не нашлось — показываю общие новости.",
                    chat_id=chat_id,
                )
        if not news:
            tg_send("📰 Нет свежих новостей. Попробуй позже.", chat_id=chat_id)
            return

    sources = ", ".join(sorted({n["source"] for n in news}))
    lines   = [f"📰 <b>Новости: {label.upper() or 'крипто'}</b>  <i>({sources})</i>"]
    for i, n in enumerate(news, 1):
        lines.append(f"\n{i}. <a href=\"{n['url']}\">{n['title']}</a>")
        lines.append(f"   <i>— {n['source']}</i>")
    lines.append(f"\n<i>{datetime.now(timezone.utc).strftime('%H:%M UTC')}</i>")
    tg_send("\n".join(lines), chat_id=chat_id)


# ─── INLINE CALLBACK HANDLER ──────────────────────────────────────────────────

def _handle_callback(cb: dict):
    """Dispatch inline keyboard button presses."""
    chat_id = cb.get("message", {}).get("chat", {}).get("id")
    data    = cb.get("data", "")
    cb_id   = cb.get("id", "")

    _tg_answer_callback(cb_id)   # dismiss spinner immediately

    if not chat_id:
        return

    # format: "analyze:BTC:60"
    if data.startswith("analyze:"):
        parts = data.split(":")
        if len(parts) == 3:
            _, sym_short, tf = parts
            symbol = sym_short + "USDT"
            threading.Thread(
                target=cmd_analyze_symbol,
                args=(chat_id, symbol, [tf]),
                daemon=True,
            ).start()


def handle_update(update: dict):
    # ── Inline button press ────────────────────────────────────────────────────
    if "callback_query" in update:
        try:    _handle_callback(update["callback_query"])
        except Exception as e: log.error(f"callback_query: {e}")
        return

    msg     = update.get("message", {})
    chat_id = msg.get("chat", {}).get("id")
    if not chat_id:
        return

    # ── Photo message ──────────────────────────────────────────────────────────
    photos = msg.get("photo")
    if photos:
        caption = (msg.get("caption") or "").strip()
        log.info(f"← Фото от {chat_id}, caption={caption!r}")
        cmd_analyze_chart(chat_id, photos, caption)
        return

    # ── Text commands ──────────────────────────────────────────────────────────
    text = (msg.get("text") or "").strip()
    if not text:
        return

    # Free-form message (not a command) → chat handler
    if not text.startswith("/"):
        log.info(f"← Сообщение от {chat_id}: {text[:80]!r}")
        threading.Thread(target=cmd_chat, args=(chat_id, text), daemon=True).start()
        return

    parts = text.split(None, 1)
    cmd   = parts[0].lower().split("@")[0]
    args  = parts[1].strip() if len(parts) > 1 else ""

    log.info(f"← Команда: {cmd} args={args!r}")

    if cmd == "/status":               cmd_status(chat_id)
    elif cmd == "/history":            cmd_history(chat_id)
    elif cmd == "/ask":                cmd_ask(chat_id, args)
    elif cmd == "/analyze":            cmd_analyze(chat_id, args)
    elif cmd == "/digest":             cmd_digest(chat_id)
    elif cmd == "/scan":               cmd_scan(chat_id)
    elif cmd == "/alert":              cmd_alert_add(chat_id, args)
    elif cmd == "/alerts":             cmd_alert_list(chat_id)
    elif cmd == "/delalert":           cmd_alert_delete(chat_id, args)
    elif cmd == "/stats":              cmd_stats(chat_id, args)
    elif cmd == "/trades":             cmd_trades(chat_id, args)
    elif cmd == "/backtest":           cmd_backtest(chat_id, args)
    elif cmd == "/btdiag":             cmd_btdiag(chat_id, args)
    elif cmd == "/scanbt":             cmd_scanbt(chat_id, args)
    elif cmd == "/hyperopt":           cmd_hyperopt(chat_id, args)
    elif cmd == "/strategy":           cmd_strategy(chat_id)
    elif cmd == "/top":                cmd_top(chat_id)
    elif cmd == "/movers":             cmd_movers(chat_id)
    elif cmd == "/risk":               cmd_risk(chat_id, args)
    elif cmd == "/market":             cmd_market(chat_id)
    elif cmd == "/debug":              cmd_debug(chat_id)
    elif cmd == "/news":               cmd_news(chat_id, args)
    elif cmd in ("/help", "/start"):   cmd_help(chat_id)


def telegram_polling():
    offset = 0
    log.info("▶ Telegram polling запущен")
    while True:
        try:
            r = requests.get(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates",
                params={"offset": offset, "timeout": 25, "allowed_updates": ["message", "callback_query"]},
                timeout=30,
            )
            for upd in r.json().get("result", []):
                offset = upd["update_id"] + 1
                try:    handle_update(upd)
                except Exception as e: log.error(f"handle_update: {e}")
        except Exception as e:
            log.warning(f"Polling error: {e}")
            time.sleep(5)


# ─── AUTO SCANNER ─────────────────────────────────────────────────────────────

SCAN_COOLDOWN_MIN = 120                       # minutes between same signal on same symbol+tf
SCAN_INTERVALS    = ["15", "60", "240", "D"]  # M15 · 1H · 4H · D1  (M5 removed — too noisy)
SCAN_MIN_CONF     = 55                        # skip signals below this confluence score

_scan_cooldown: dict = {}
_scan_lock = threading.Lock()


def detect_signals(candles: list) -> list:
    """
    Detect SMC signals from OHLCV candles (oldest→newest).
    Returns list of signal type strings found on the last closed candle.
    """
    if len(candles) < 22:
        return []

    lookback = 20
    prev     = candles[-lookback - 1 : -1]   # 20 completed candles before last
    last     = candles[-1]
    signals  = []

    prev_high = max(x["h"] for x in prev)
    prev_low  = min(x["l"] for x in prev)
    close_now = last["c"]

    # ── BOS / CHoCH ─────────────────────────────────────────────────────────
    # Trend direction: compare close at start vs end of lookback window
    trend_up = candles[-lookback - 1]["c"] < candles[-2]["c"]

    if close_now > prev_high:
        signals.append("BOS_BULL" if trend_up else "CHOCH_BULL")
    elif close_now < prev_low:
        signals.append("BOS_BEAR" if not trend_up else "CHOCH_BEAR")

    # ── FVG (3-candle gap, средняя свеча игнорируется по определению) ────
    c2, c0 = candles[-3], candles[-1]
    if c0["l"] > c2["h"]:
        signals.append("FVG_BULL")
    elif c0["h"] < c2["l"]:
        signals.append("FVG_BEAR")

    # ── Liquidity Sweep ──────────────────────────────────────────────────────
    if last["h"] > prev_high and last["c"] < prev_high:
        signals.append("LIQ_SWEEP_H")
    if last["l"] < prev_low and last["c"] > prev_low:
        signals.append("LIQ_SWEEP_L")

    # ── EMA 9/21 Cross ───────────────────────────────────────────────────────
    if len(candles) >= 23:
        cross = check_ema_cross(candles)
        if cross == "golden": signals.append("EMA_CROSS_BULL")
        elif cross == "death": signals.append("EMA_CROSS_BEAR")

    # ── RSI Divergence ───────────────────────────────────────────────────────
    if len(candles) >= 50:
        div = detect_rsi_divergence(candles)
        if div == "bullish":  signals.append("RSI_DIV_BULL")
        elif div == "bearish": signals.append("RSI_DIV_BEAR")

    # ── Volume Spike ─────────────────────────────────────────────────────────
    if detect_volume_spike(candles):
        signals.append("VOL_SPIKE")

    return signals


def run_auto_scan():
    log.info("🔍 Автосканер: начинаю сканирование...")
    now = time.time()

    for symbol in SYMBOLS:
        base   = symbol if symbol.endswith("USDT") else symbol + "USDT"
        market = None   # lazy-fetch once per symbol

        for interval in SCAN_INTERVALS:
            candles = _klines(base, interval, 250)
            if not candles:
                continue

            detected = detect_signals(candles)

            # Sweep+reclaim паттерн (Этап 11 фаза 1) — детект по тем же
            # candles, добавляем синтетический тип если событие свежее.
            sr = patterns.latest_sweep_reclaim(candles)
            if sr is not None:
                detected.append(
                    "SWEEP_RECLAIM_BULL" if sr.direction == "bull"
                    else "SWEEP_RECLAIM_BEAR"
                )

            # Order Block test (Этап 12 фаза 1) — текущий бар тестирует
            # unmitigated OB → entry trigger по канону ICT.
            ob = order_blocks.latest_ob_test(candles)
            if ob is not None:
                detected.append(
                    "OB_BULL" if ob.direction == "bull" else "OB_BEAR"
                )

            # Mitigation Block test (Этап 12 фаза 4) — extreme нарушен,
            # opposite intact, return to level.
            mb = block_patterns.latest_mb_test(candles)
            if mb is not None:
                detected.append(
                    "MB_BULL" if mb.direction == "bull" else "MB_BEAR"
                )

            # Breaker Block test — extreme нарушен + opposite тоже (reversal),
            # return to original extreme.
            bb = block_patterns.latest_bb_test(candles)
            if bb is not None:
                detected.append(
                    "BB_BULL" if bb.direction == "bull" else "BB_BEAR"
                )

            # Inside Candle Breakout (Этап 12 фаза 5) — взрывное движение
            # после inside-свечи в направлении breakout'а.
            ib = minor_patterns.latest_inside_breakout(candles)
            if ib is not None:
                detected.append(
                    "INSIDE_BREAK_BULL" if ib.direction == "bull"
                    else "INSIDE_BREAK_BEAR"
                )

            # Rejection Block test — длинный wick + retest body level.
            rb = minor_patterns.latest_rejection_test(candles)
            if rb is not None:
                detected.append(
                    "RB_BULL" if rb.direction == "bull" else "RB_BEAR"
                )

            if not detected:
                continue

            # Fetch market data once per symbol (shared across TFs)
            if market is None:
                try:
                    market = fetch_market(base)
                except Exception as e:
                    log.warning(f"Auto-scan fetch_market {base}: {e}")
                    break

            for sig_type in detected:
                key = f"{base}_{interval}_{sig_type}"
                with _scan_lock:
                    if now - _scan_cooldown.get(key, 0) < SCAN_COOLDOWN_MIN * 60:
                        log.info(f"  Cooldown: {sig_type} {base} {interval}")
                        continue
                    _scan_cooldown[key] = now

                price = market.get("price") or candles[-1]["c"]

                # Confluence
                sig_up    = any(x in sig_type for x in ("BULL", "LONG", "SWEEP_L"))
                sig_dn    = any(x in sig_type for x in ("BEAR", "SHORT", "SWEEP_H"))
                direction = "long" if sig_up else ("short" if sig_dn else "neutral")
                biases    = market.get("ema_biases", {})
                mtf       = check_mtf_confluence(biases, direction) if direction != "neutral" else {}
                conf_score, conf_factors = compute_confluence_score(sig_type, market, mtf)

                if conf_score < SCAN_MIN_CONF:
                    log.info(f"  Low conf {conf_score}/100: {sig_type} {base} {interval}")
                    continue

                sig_data = {"signal": sig_type, "symbol": base,
                            "tf": interval, "price": price}
                recent           = db_recent(hours=4, limit=6)

                # Engine verdict для auto-scanned сигналов (был баг: старая
                # сигнатура передавала conf_score/conf_factors как
                # decision/model и крашила llm_analyze_signal)
                mtf_dir = "long" if any(x in sig_type for x in ("BULL","LONG","SWEEP_L","EQL")) \
                          else ("short" if any(x in sig_type for x in ("BEAR","SHORT","SWEEP_H","EQH")) else "neutral")
                mtf_data = check_mtf_confluence(market.get("ema_biases", {}), mtf_dir) \
                           if mtf_dir != "neutral" else {}
                decision = make_decision(
                    signal_type=sig_type, price=price, market=market,
                    mtf=mtf_data,
                    confluence_score=conf_score, confluence_factors=conf_factors,
                )

                llm_text, quality = llm_analyze_signal(
                    sig_data, market, recent, decision
                )

                # Bug-16 fix: автосканер должен уважать cooldown gate, как
                # _process_winner. Раньше он шёл напрямую к send и не писал
                # в signal_dispatch — позже webhook-gate его не видел, и сигналы
                # могли противоречить друг другу.
                gate = None
                if decision["verdict"] in ("LONG", "SHORT"):
                    with _db_lock, db_conn() as c:
                        gate = signal_gate.cooldown_check(
                            c, base, decision["verdict"],
                            decision.get("confidence", 0), interval,
                        )

                # Bug-11 fix: LONG/SHORT с quality<MIN_QUALITY не отправляются
                # юзеру → метим 'suppressed' (как для cooldown-suppress).
                quality_blocked = (decision["verdict"] in ("LONG", "SHORT")
                                   and quality < MIN_QUALITY)
                gate_status = "suppressed" if (
                    (gate and gate.action == "suppress") or quality_blocked
                ) else None
                db_save(base, interval, sig_type, price, sig_data,
                        llm_text, quality, decision=decision,
                        gate_status=gate_status)

                if decision["verdict"] == "SKIP":
                    log.info(f"  Skip {sig_type} {base} {interval}: {decision['reason']}")
                    continue
                if quality < MIN_QUALITY:
                    continue

                if gate and gate.action == "suppress":
                    log.info(f"  [gate] SUPPRESS auto-scan {base} "
                             f"{decision['verdict']}: {gate.reason}")
                    continue
                if gate and gate.action == "reversal":
                    log.info(f"  [gate] REVERSAL auto-scan {base} "
                             f"{decision['verdict']}: {gate.reason}")

                msg = "🤖 <b>[АВТОСКАНЕР]</b>\n" + build_signal_message(
                    sig_data, market, llm_text, quality, conf_score, conf_factors,
                    decision,
                )
                if gate and gate.action == "reversal" and gate.active is not None:
                    msg += signal_gate.format_reversal_note(
                        gate.active, decision["verdict"])

                # Чарт для auto-scanned LONG/SHORT (как в /webhook)
                klines_1h = (market.get("_klines") or {}).get("60") or []
                photo = render_signal_chart(base, klines_1h, decision, market) \
                        if klines_1h else None
                if photo:
                    ok = tg_send_photo(photo, msg)
                else:
                    ok = tg_send(msg)

                log.info(f"  ✅ {sig_type} {base} {interval} "
                         f"Q:{quality}/10 Conf:{conf_score}/100 "
                         f"Verdict:{decision['verdict']}")

                # Записываем dispatch для будущих cooldown-проверок
                if ok and decision["verdict"] in ("LONG", "SHORT"):
                    try:
                        with _db_lock, db_conn() as c:
                            signal_gate.record_dispatch(
                                c, base, decision["verdict"], interval, sig_type,
                                decision.get("confidence", 0),
                                note=("auto-scan reversal"
                                      if gate and gate.action == "reversal"
                                      else "auto-scan"),
                            )
                    except Exception as e:
                        log.warning(
                            f"signal_gate.record_dispatch auto-scan: {e}")

    log.info("🔍 Автосканер: завершено")


# ─── PRICE ALERT CHECKER ──────────────────────────────────────────────────────

def check_price_alerts():
    alerts = db_alerts_active()
    if not alerts:
        return

    # Fetch prices for all unique symbols in one pass
    symbols = list({row[2] for row in alerts})
    prices: dict = {}
    for sym in symbols:
        try:
            tk = requests.get(
                f"{BYBIT}/v5/market/tickers",
                params={"symbol": sym, "category": "linear"}, timeout=5,
            ).json()["result"]["list"][0]
            prices[sym] = float(tk["lastPrice"])
        except Exception:
            pass

    for alert_id, chat_id, symbol, direction, target in alerts:
        price = prices.get(symbol)
        if price is None:
            continue
        triggered = (
            (direction == "above" and price >= target)
            or (direction == "below" and price <= target)
        )
        if triggered:
            db_alert_trigger(alert_id)
            sym_short = symbol.replace("USDT", "")
            arrow     = "📈" if direction == "above" else "📉"
            tg_send(
                f"🔔 <b>Price Alert!</b>\n"
                f"{arrow} <b>{sym_short}/USDT.P</b> = <b>${price:,.2f}</b>\n"
                f"Твой алерт: {'выше' if direction=='above' else 'ниже'} ${target:,.0f}",
                chat_id=int(chat_id),
            )
            log.info(f"Alert fired: {symbol} {direction} ${target} (now ${price})")


# ─── TOP COINS ────────────────────────────────────────────────────────────────

def cmd_top(chat_id: int):
    """Show top gainers/losers and volume leaders from Bybit (24H)."""
    tg_send("📊 Загружаю топ монет...", chat_id=chat_id)
    try:
        r = requests.get(
            f"{BYBIT}/v5/market/tickers",
            params={"category": "linear"},
            timeout=8,
        )
        tickers = r.json()["result"]["list"]
        # Only USDT perps with meaningful volume
        usdt = [t for t in tickers
                if t["symbol"].endswith("USDT") and float(t.get("volume24h", 0)) > 500_000]

        def pct(t): return float(t.get("price24hPcnt", 0)) * 100
        def fmt(t, show_vol=False):
            sym   = t["symbol"].replace("USDT", "")
            p     = pct(t)
            price = float(t.get("lastPrice", 0))
            icon  = "🚀" if p >= 5 else ("📈" if p > 0 else ("💥" if p <= -5 else "📉"))
            vol   = float(t.get("volume24h", 0))
            vol_s = f"  vol ${vol/1e6:.0f}M" if show_vol else ""
            return f"{icon} <b>{sym}</b>  ${price:,.4g}  {p:+.1f}%{vol_s}"

        gainers = sorted(usdt, key=pct, reverse=True)[:7]
        losers  = sorted(usdt, key=pct)[:7]
        by_vol  = sorted(usdt, key=lambda t: float(t.get("volume24h", 0)), reverse=True)[:7]

        ts = datetime.now(timezone.utc).strftime("%H:%M UTC")
        lines = [
            f"📊 <b>Топ Bybit Perpetuals · 24H</b>  <i>{ts}</i>",
            "━━━━━━━━━━━━━━━━━━━━",
            "🚀 <b>Топ гейнеры</b>",
        ] + [fmt(t) for t in gainers] + [
            "",
            "💀 <b>Топ лузеры</b>",
        ] + [fmt(t) for t in losers] + [
            "",
            "💰 <b>Топ по объёму (24H)</b>",
        ] + [fmt(t, show_vol=True) for t in by_vol]

        tg_send("\n".join(lines), chat_id=chat_id)
    except Exception as e:
        log.error(f"cmd_top: {e}")
        tg_send(f"❌ Ошибка: {e}", chat_id=chat_id)


# ─── MOVERS ───────────────────────────────────────────────────────────────────

MOVERS_THRESHOLD = float(os.environ.get("MOVERS_THRESHOLD", "3.0"))
MOVERS_WATCHLIST = list(dict.fromkeys(
    SYMBOLS + ["SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT",
               "AVAXUSDT", "LINKUSDT", "DOTUSDT", "NEARUSDT", "APTUSDT"]
))


def cmd_movers(chat_id: int):
    """Show 1H price changes for the watchlist."""
    tg_send("⚡ Проверяю движения (1H)...", chat_id=chat_id)
    results = []
    for symbol in MOVERS_WATCHLIST:
        try:
            candles = _klines(symbol, "60", limit=2)
            if len(candles) < 2:
                continue
            prev, cur = candles[-2]["c"], candles[-1]["c"]
            if prev <= 0:
                continue
            pct = (cur - prev) / prev * 100
            results.append((symbol.replace("USDT", ""), cur, pct))
        except Exception:
            pass

    if not results:
        tg_send("❌ Нет данных", chat_id=chat_id)
        return

    results.sort(key=lambda x: -abs(x[2]))
    ts = datetime.now(timezone.utc).strftime("%H:%M UTC")
    lines = [f"⚡ <b>Движения 1H · Watchlist</b>  <i>{ts}</i>",
             "━━━━━━━━━━━━━━━━━━━━"]
    for sym, price, p in results:
        icon = "🚀" if p >= 3 else ("📈" if p > 0 else ("💥" if p <= -3 else "📉"))
        lines.append(f"{icon} <b>{sym}</b>  ${price:,.4g}  {p:+.1f}%")
    tg_send("\n".join(lines), chat_id=chat_id)


def check_movers():
    """Auto-alert when a watchlist coin moves ≥ MOVERS_THRESHOLD% in 1H."""
    alerts = []
    for symbol in MOVERS_WATCHLIST:
        try:
            candles = _klines(symbol, "60", limit=2)
            if len(candles) < 2:
                continue
            prev, cur = candles[-2]["c"], candles[-1]["c"]
            if prev <= 0:
                continue
            pct = (cur - prev) / prev * 100
            if abs(pct) >= MOVERS_THRESHOLD:
                alerts.append((symbol.replace("USDT", ""), cur, pct))
        except Exception:
            pass

    if not alerts:
        return

    alerts.sort(key=lambda x: -abs(x[2]))
    ts    = datetime.now(timezone.utc).strftime("%H:%M UTC")
    lines = [f"⚡ <b>Mover Alert · {ts}</b>", "━━━━━━━━━━━━━━━━━━━━"]
    for sym, price, p in alerts:
        icon = "🚀" if p > 0 else "💥"
        lines.append(f"{icon} <b>{sym}</b>  ${price:,.4g}  <b>{p:+.1f}%</b> за 1H")
    tg_send("\n".join(lines))
    log.info(f"Movers alert: {len(alerts)} монет  threshold={MOVERS_THRESHOLD}%")


# ─── SIGNAL OUTCOME CHECKER ───────────────────────────────────────────────────

def check_trade_outcomes():
    """
    Engine-based outcome tracking (Этап 4): walk 5m klines с момента entry,
    проверяем первый touch SL / TP для каждой open LONG/SHORT сделки.
    Обновляет signal_outcomes.status / hit_level / r_multiple.
    """
    try:
        with _db_lock, db_conn() as c:
            stats = tracking.check_open_trades(
                c,
                fetch_klines=lambda sym, interval, limit: _klines(sym, interval, limit),
            )
        if stats["closed"] > 0:
            log.info(f"Trade outcomes: проверено {stats['checked']}, "
                     f"закрыто {stats['closed']}, "
                     f"остаётся open {stats['still_open']}")
    except Exception as e:
        log.warning(f"check_trade_outcomes: {e}")


def check_signal_outcomes():
    """Check pending signal outcomes at 1H / 4H / 24H intervals."""
    pending = db_outcomes_pending()
    if not pending:
        return

    now = datetime.now(timezone.utc)
    updated = 0

    for row in pending:
        oid, symbol, sig_type, direction, entry_price, entry_ts, \
            p1h, p4h, p24h, done = row

        try:
            entry_dt = datetime.strptime(entry_ts, "%Y-%m-%d %H:%M").replace(
                tzinfo=timezone.utc
            )
        except ValueError:
            continue

        elapsed = (now - entry_dt).total_seconds() / 3600  # hours

        # Fetch current price once
        try:
            tk = requests.get(
                f"{BYBIT}/v5/market/tickers",
                params={"symbol": symbol, "category": "linear"},
                timeout=5,
            ).json()["result"]["list"][0]
            cur_price = float(tk["lastPrice"])
        except Exception as e:
            log.warning(f"Outcome price fetch {symbol}: {e}")
            continue

        pct = ((cur_price - entry_price) / entry_price * 100) if entry_price else 0
        if direction == "bear":
            pct = -pct  # positive = winner for short

        if elapsed >= 1 and p1h is None:
            db_outcome_update(oid, "price_1h", cur_price, pct)
            updated += 1

        if elapsed >= 4 and p4h is None:
            db_outcome_update(oid, "price_4h", cur_price, pct)
            updated += 1

        if elapsed >= 24 and p24h is None:
            db_outcome_update(oid, "price_24h", cur_price, pct)
            updated += 1

    if updated:
        log.info(f"Signal outcomes updated: {updated} records")


def cmd_stats(chat_id: int, args: str = ""):
    """
    Engine-based win-rate статистика (Этап 4): TP/SL hits, R-multiple,
    разбивка по signal_type / символам / confidence-bucket.

    /stats          — 30 дней (по умолчанию)
    /stats 7        — последние 7 дней
    """
    try:
        days = int(args.strip()) if args.strip().isdigit() else 30
    except Exception:
        days = 30
    days = max(1, min(365, days))

    try:
        with _db_lock, db_conn() as c:
            stats = tracking.compute_stats(c, days=days)
    except Exception as e:
        log.error(f"compute_stats: {e}")
        tg_send(f"⚠️ Ошибка stats: {e}", chat_id=chat_id)
        return

    tg_send(tracking.format_stats_message(stats), chat_id=chat_id)


def cmd_trades(chat_id: int, args: str = ""):
    """
    Список последних закрытых сделок с R-исходом.

    /trades         — за 7 дней по умолчанию
    /trades 30      — за последние 30 дней
    """
    try:
        days = int(args.strip()) if args.strip().isdigit() else 7
    except Exception:
        days = 7
    days = max(1, min(60, days))

    try:
        with _db_lock, db_conn() as c:
            trades = tracking.recent_trades(c, days=days, limit=30)
    except Exception as e:
        log.error(f"recent_trades: {e}")
        tg_send(f"⚠️ Ошибка trades: {e}", chat_id=chat_id)
        return

    tg_send(tracking.format_trades_message(trades, days), chat_id=chat_id)


def cmd_backtest(chat_id: int, args: str = ""):
    """
    Прогон стратегии на исторических данных (Этап 13 backtest harness).
    Запускается в background thread.

      /backtest                  — BTC 7 дней baseline (primary TF 5m)
      /backtest ETH              — ETH 7 дней baseline
      /backtest BTC 30           — BTC за 30 дней
      /backtest BTC 180 compare  — compare-режим (4 конфига) на полугоду
      /backtest BTC 365          — год данных (может занять 3-7 мин)
      /backtest BTC 30 tf=15     — primary TF 15m (вместо дефолтного 5m)
      /backtest BTC 30 tf=1H     — алиасы: 1H/4H/1D тоже распознаются
      /backtest BTC 30 tf=15 compare — комбо: 15m + compare-режим

    tf= — primary TF (5/15/60/240/D). Без него backtest идёт на 5m
    (ICT-канон). При выборе другого TF expiry/cooldown масштабируются
    автоматически (7d expiry / 1h cooldown в реальном времени).

    Максимум 365 дней. Compare-режим 4× медленнее baseline.
    Сообщение «🔄 Запускаю...» отправляется сразу, итог — когда готов.
    """
    parts = [p for p in args.strip().split() if p]
    symbol = "BTC"
    days = 7
    compare_mode = False
    tf_primary = "5"   # ICT-канон по умолчанию

    for p in parts:
        pl = p.lower()
        if p.isdigit():
            days = int(p)
        elif pl in ("compare", "cmp", "--compare"):
            compare_mode = True
        elif pl.startswith("tf="):
            tf_val = p.split("=", 1)[1].upper()
            # Нормализация: 1H→60, 4H→240 и т.д.
            tf_aliases = {"1H": "60", "2H": "120", "4H": "240",
                          "5M": "5", "15M": "15", "30M": "30",
                          "1D": "D"}
            tf_primary = tf_aliases.get(tf_val, tf_val)
        else:
            symbol = p.upper().replace("USDT", "").replace(".P", "")

    symbol_full = symbol if symbol.endswith("USDT") else symbol + "USDT"
    days = max(1, min(365, days))

    # Оценка длительности: baseline ~30-60 сек/30d, compare 4×, longer 1.5-2× линейно.
    if days <= 30:
        eta = "30-90 сек" if not compare_mode else "2-4 мин"
    elif days <= 90:
        eta = "1-3 мин" if not compare_mode else "5-10 мин"
    else:
        eta = "3-7 мин" if not compare_mode else "10-20 мин"

    tf_label = f"tf={tf_primary}" if tf_primary != "5" else ""
    tg_send(
        f"🔄 <b>Backtest {symbol_full} {days}d</b> "
        f"({'compare' if compare_mode else 'baseline'}{', ' + tf_label if tf_label else ''})\n"
        f"Запускаю — может занять {eta}...",
        chat_id=chat_id,
    )

    def _run():
        import time as _t
        t0 = _t.time()
        try:
            # ── Шаг 1: загрузка klines ──
            tg_send("⏳ <b>Шаг 1/3:</b> Загружаю историю с Bybit...",
                    chat_id=chat_id)
            tfs_to_fetch = sorted(set(["5", "15", "60", "240", "D",
                                       tf_primary]))
            data = bt_data.fetch_all(
                symbol_full, days,
                tfs=tfs_to_fetch,
                fetch_funding_data=False,
                fetch_oi_data=False,
            )
            n_bars = len(data["klines"].get(tf_primary) or [])
            fetch_time = _t.time() - t0
            log.info(f"[/backtest] fetched {n_bars} {tf_primary}m bars "
                     f"for {symbol_full} in {fetch_time:.1f}s")
            tg_send(
                f"✅ <b>Шаг 1/3:</b> Загружено {n_bars} {tf_primary}m баров "
                f"({fetch_time:.0f}с).\n"
                f"⏳ <b>Шаг 2/3:</b> Replay стратегии...",
                chat_id=chat_id,
            )

            # ── Шаг 2: replay ──
            t1 = _t.time()
            # Progress callback пишет в лог (Telegram прогресс отдельно)
            def _bg_progress(msg):
                log.info(f"[/backtest] {msg}")

            # Опциональный inline-прогресс через тред-таймер для long runs
            progress_state = {"sent_5min": False, "sent_10min": False}

            def _periodic_check():
                elapsed = _t.time() - t1
                if elapsed > 5 * 60 and not progress_state["sent_5min"]:
                    tg_send(f"⏳ Replay идёт, прошло {elapsed/60:.1f} мин...",
                            chat_id=chat_id)
                    progress_state["sent_5min"] = True
                if elapsed > 10 * 60 and not progress_state["sent_10min"]:
                    tg_send(f"⏳ Замечу: уже {elapsed/60:.1f} мин. "
                            f"Большие периоды реально долгие на 5m primary "
                            f"(O(N²) slicing). Жди...", chat_id=chat_id)
                    progress_state["sent_10min"] = True

            # Запускаем таймер для периодических обновлений
            _periodic_t = threading.Timer(5 * 60, _periodic_check)
            _periodic_t.daemon = True
            _periodic_t.start()

            try:
                if compare_mode:
                    cmp = bt_compare.compare(data, [
                        bt_compare.Config(name="baseline"),
                        bt_compare.Config(
                            name="no_p3",
                            overrides={"KILLZONE_GATE_ENABLED": False,
                                       "STRUCTURE_GATE_ENABLED": False},
                        ),
                        bt_compare.Config(
                            name="no_p4",
                            overrides={"HTF_BIAS_GATE_ENABLED": False},
                        ),
                        bt_compare.Config(
                            name="all_gates_off",
                            overrides={"KILLZONE_GATE_ENABLED": False,
                                       "STRUCTURE_GATE_ENABLED": False,
                                       "HTF_BIAS_GATE_ENABLED": False},
                        ),
                    ], tf_primary=tf_primary, warmup_bars=200)
                    body = bt_compare.format_comparison(cmp, max_name_len=15)
                else:
                    result = bt_backtest.run_backtest(
                        data, tf_primary=tf_primary, warmup_bars=200,
                        progress_each=1000,
                    )
                    body = bt_backtest.format_result(result)
            finally:
                _periodic_t.cancel()

            replay_time = _t.time() - t1
            total_time = _t.time() - t0
            log.info(f"[/backtest] replay completed in {replay_time:.1f}s "
                     f"(total {total_time:.1f}s)")

            # ── Шаг 3: ответ ──
            footer = (f"\n\n⏱ Fetch: {fetch_time:.0f}с · "
                      f"Replay: {replay_time:.0f}с · "
                      f"Total: {total_time:.0f}с")
            tg_send(f"<pre>{body}</pre>{footer}", chat_id=chat_id)

            # Equity chart — single для baseline, multi для compare
            try:
                sym_short = symbol_full.replace("USDT", "")
                if compare_mode:
                    # 4 кривые на одном графике
                    curves = []
                    for cfg, res in zip(cmp.configs, cmp.results):
                        equity = (res.stats or {}).get("equity") or []
                        curves.append((cfg.name, equity, res.stats))
                    png = bt_equity_chart.render_multi_equity_curves(
                        curves, sym_short, days,
                    )
                    if png:
                        tg_send_photo(
                            png, "📊 Equity comparison: 4 configs",
                            chat_id=chat_id,
                            filename=f"compare_{symbol_full}_{days}d.png",
                        )
                else:
                    equity = (result.stats or {}).get("equity") or []
                    png = bt_equity_chart.render_equity_curve(
                        equity, sym_short, days, stats=result.stats,
                    )
                    if png:
                        final_r = equity[-1] if equity else 0.0
                        emoji = ("🟢" if final_r > 0
                                 else "🔴" if final_r < 0 else "⚪")
                        tg_send_photo(
                            png,
                            f"{emoji} Equity curve: итог {final_r:+.2f}R",
                            chat_id=chat_id,
                            filename=f"equity_{symbol_full}_{days}d.png",
                        )
            except Exception as e:
                log.warning(f"[/backtest] equity chart failed: {e}")
        except Exception as e:
            log.exception(f"[/backtest] failed: {e}")
            tg_send(f"❌ Backtest error: {type(e).__name__}: {e}",
                    chat_id=chat_id)

    threading.Thread(target=_run, daemon=True).start()


# ─── STRATEGY OVERVIEW (/strategy) ──────────────────────────────────────────


def cmd_strategy(chat_id: int):
    """
    Печатает компактную сводку по стратегии и доступным командам для
    экспериментов. Дублирует ключевые выводы из STRATEGY.md в чате,
    чтобы не нужно было читать GitHub.
    """
    text = (
        "📋 <b>Strategy overview</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"

        "🔍 <b>Где мы сейчас</b>\n"
        "Last hyperopt BTC 60d (5m):\n"
        "  • in-sample best PF: <b>1.21</b> (52 trades, WR 21%)\n"
        "  • OOS mean PF: <b>0.62</b> — стратегия теряет на новых данных\n"
        "  • avg_r_net = <b>-0.98</b> — комиссии съедают весь edge\n\n"

        "Прод-бот /stats 30d: WinR <b>18.4%</b>, AvgR <b>-0.41</b>, PF <b>0.42</b>\n\n"

        "🎯 <b>Что найдено</b>\n"
        "  ✅ Killzone gate РАБОТАЕТ — НЕ убирать "
        "(без него MaxDD <b>-129R</b>)\n"
        "  ❌ HTF gate НЕ работает (Optuna его отключает)\n"
        "  💡 Edge есть (один trial дал +10.95R) — нужно его выделить\n"
        "  💸 Комиссии 0.06%×2 убивают узкие R-units\n\n"

        "🧪 <b>Топ-3 эксперимента сейчас</b>\n\n"

        "<b>1. (CRITICAL) Wider TPs:</b>\n"
        "<code>/hyperopt BTC 60 50 walkforward preset=wide_tp</code>\n"
        "→ Цель: avgR > 4R компенсирует WR 19%\n\n"

        "<b>2. Multi-symbol edge discovery:</b>\n"
        "<code>/scanbt BTC,ETH,SOL,ARB,LINK 30 sort=avg_r_net</code>\n"
        "→ Ищем монету с positive net post-fee\n\n"

        "<b>3. Higher TF (меньше шума):</b>\n"
        "<code>/hyperopt BTC 90 30 tf=1H walkforward</code>\n"
        "→ Тестируем гипотезу что 1h даёт больше edge\n\n"

        "📚 <b>Все команды для research</b>\n\n"

        "  /btdiag SYMBOL DAYS [tf=N] [preset=NAME]\n"
        "    Диагностика с funnel + breakdown + JSON dump\n\n"

        "  /hyperopt SYMBOL DAYS TRIALS [tf=N] [walkforward]\n"
        "    Optuna-тюнинг с walk-forward OOS валидацией\n\n"

        "  /scanbt SYMS DAYS [tf=N] [sort=COL]\n"
        "    Multi-symbol comparison таблицей\n\n"

        "  /backtest SYMBOL DAYS [compare]\n"
        "    Базовый replay (3 P3/P4 конфигурации)\n\n"

        "🎛 <b>Presets:</b>\n"
        "  <code>no_p3</code> · <code>no_p4</code> · <code>no_gates</code>\n"
        "  <code>wide_tp</code> · <code>narrow_tp</code>\n"
        "  <code>tight_sl</code> · <code>loose_sl</code>\n"
        "  <code>aggressive</code> · <code>conservative</code>\n\n"

        "🛡 <b>Anti-patterns</b>\n"
        "  ✗ НЕ убирать killzone gate (MaxDD взрывается)\n"
        "  ✗ НЕ доверять in-sample PF без walkforward\n"
        "  ✗ НЕ менять prod-defaults до OOS PF > 1.3\n\n"

        "🖥 <b>Web UI</b> доступен на /ui:\n"
        "  • Chart + indicators + watchlist\n"
        "  • Signals overlay (прошлые entries/TP/SL)\n"
        "  • SMC zones (OB/FVG/MB/BB)\n"
        "  • Engine market panel (CVD/MTF/funding/...)\n"
        "  • Drawing tools (H-Line)\n"
        "  • Shareable links: /ui#BTC/1H\n"
        "  • Keyboard: ? для shortcuts\n\n"

        "📖 Полная документация: <a href=\"https://github.com/"
        "vegomanagement/crypto-screener/blob/main/STRATEGY.md\">STRATEGY.md</a>"
    )
    tg_send(text, chat_id=chat_id)


# ─── BACKTEST DIAGNOSTIC (/btdiag) ───────────────────────────────────────────


# Named config presets — shorthand для частых combinations.
# Используются через токен preset=NAME в /btdiag, /hyperopt, /scanbt.
# Explicit KEY=VAL после preset перекрывают значения из preset'а.
CONFIG_PRESETS: dict[str, dict] = {
    "no_p3": {
        "KILLZONE_GATE_ENABLED": False,
        "STRUCTURE_GATE_ENABLED": False,
    },
    "no_p4": {
        "HTF_BIAS_GATE_ENABLED": False,
    },
    "no_gates": {
        "KILLZONE_GATE_ENABLED": False,
        "STRUCTURE_GATE_ENABLED": False,
        "HTF_BIAS_GATE_ENABLED": False,
    },
    "wide_tp": {
        "ATR_TP1_DIST": 3.0,
        "ATR_TP2_DIST": 5.0,
        "ATR_TP3_DIST": 8.0,
    },
    "narrow_tp": {
        "ATR_TP1_DIST": 1.0,
        "ATR_TP2_DIST": 1.8,
        "ATR_TP3_DIST": 3.0,
    },
    "tight_sl": {
        "ATR_SL_DIST": 0.7,
    },
    "loose_sl": {
        "ATR_SL_DIST": 1.5,
    },
    "aggressive": {
        "MIN_CONFIDENCE_FOR_TRADE": 55,
        "ATR_TP1_DIST": 3.0,
        "ATR_TP2_DIST": 5.0,
        "ATR_TP3_DIST": 8.0,
    },
    "conservative": {
        "MIN_CONFIDENCE_FOR_TRADE": 72,
        "STRUCTURE_GATE_ENABLED": True,
        "KILLZONE_GATE_ENABLED": True,
    },
}


def _extract_preset_tokens(parts: list[str]) -> tuple[list[str], dict]:
    """
    Из списка token'ов вытаскивает все `preset=NAME`, мерджит их в один dict
    overrides (сохраняя порядок), возвращает (parts без preset-токенов,
    preset_overrides). Несуществующие presets молча пропускаются.
    """
    other: list[str] = []
    preset_ovr: dict = {}
    for p in parts:
        if p.lower().startswith("preset="):
            name = p.split("=", 1)[1].strip().lower()
            if name in CONFIG_PRESETS:
                preset_ovr.update(CONFIG_PRESETS[name])
        else:
            other.append(p)
    return other, preset_ovr


_TF_ALIASES = {
    "1H": "60", "2H": "120", "4H": "240",
    "5M": "5", "15M": "15", "30M": "30",
    "1D": "D",
}


def _normalize_tf(raw: str) -> str:
    """'1H'→'60', '5'→'5', '4H'→'240'. Unknown → as-is (передастся as-is)."""
    s = raw.upper()
    return _TF_ALIASES.get(s, s)


def _parse_btdiag_args(args: str) -> tuple[str, int, dict | None, str]:
    """
    Парсит '/btdiag BTC 30 tf=15 preset=no_gates KEY=VAL' → (
        'BTCUSDT', 30, overrides_dict, '15').

    Дефолты: BTC 30d, tf=5 (ICT-канон), без overrides.

    preset=NAME — shorthand из CONFIG_PRESETS. Explicit KEY=VAL применяется
    ПОСЛЕ preset'а, перебивая его значения.
    tf= — primary TF: 5|15|60|240|D (или alias 1H/4H/1D).
    """
    raw_parts = [p for p in args.strip().split() if p]
    raw_parts, preset_ovr = _extract_preset_tokens(raw_parts)
    symbol, days = "BTC", 30
    tf_primary = "5"
    override_parts: list[str] = []
    for p in raw_parts:
        pl = p.lower()
        if pl.startswith("tf="):
            tf_primary = _normalize_tf(p.split("=", 1)[1])
        elif "=" in p and not p.startswith("="):
            override_parts.append(p)
        elif p.isdigit():
            days = int(p)
        else:
            symbol = p.upper().replace("USDT", "").replace(".P", "")
    symbol_full = symbol if symbol.endswith("USDT") else symbol + "USDT"
    days = max(1, min(365, days))
    explicit_ovr = (bt_backtest._parse_overrides(",".join(override_parts))
                    if override_parts else None)
    # Merge: preset сначала, explicit перебивает
    merged: dict = dict(preset_ovr)
    if explicit_ovr:
        merged.update(explicit_ovr)
    return symbol_full, days, (merged or None), tf_primary


def cmd_btdiag(chat_id: int, args: str = ""):
    """
    Диагностический backtest: funnel + breakdown + комиссии + JSON dump.

      /btdiag                            — BTCUSDT 30d
      /btdiag ETH 60                     — ETHUSDT 60d
      /btdiag BTC 90                     — BTCUSDT 90d (~2-3 мин)
      /btdiag BTC 30 KILLZONE_GATE_ENABLED=false
                                         — отключить killzone gate для этого прогона
      /btdiag BTC 30 KILLZONE_GATE_ENABLED=false STRUCTURE_GATE_ENABLED=false
                                         — отключить оба P3 гейта

    KEY=VALUE — override любой константы из decision.py для эксперимента
    (не меняет prod-настройки). Полезно сравнивать «с гейтом vs без» без PR.

    Шлёт текстовый отчёт и JSON-файл со всеми сигналами/трейдами.
    """
    symbol_full, days, overrides, tf_primary = _parse_btdiag_args(args)

    # На больших TF меньше баров → быстрее. Корректировка ETA.
    tf_speedup = {"5": 1.0, "15": 0.5, "60": 0.3, "240": 0.2, "D": 0.15}
    speedup = tf_speedup.get(tf_primary, 1.0)
    base_eta = "30-90 сек" if days <= 30 else (
        "1-3 мин" if days <= 90 else "3-7 мин")
    eta = base_eta if speedup >= 0.9 else f"~{int(60 * speedup)}-{int(180 * speedup)} сек"
    ovr_label = (f"\n<i>overrides: {overrides}</i>" if overrides else "")
    tf_label = f" tf={tf_primary}" if tf_primary != "5" else ""
    tg_send(
        f"🔬 <b>Btdiag {symbol_full} {days}d{tf_label}</b>{ovr_label}\n"
        f"Запускаю — может занять {eta}...",
        chat_id=chat_id,
    )

    def _run():
        import time as _t
        t0 = _t.time()
        try:
            tg_send("⏳ <b>Шаг 1/2:</b> Загружаю историю...", chat_id=chat_id)
            tfs_to_fetch = sorted(set(["5", "15", "60", "240", "D",
                                       tf_primary]))
            data = bt_data.fetch_all(
                symbol_full, days,
                tfs=tfs_to_fetch,
                fetch_funding_data=False, fetch_oi_data=False,
            )
            n_bars = len(data["klines"].get(tf_primary) or [])
            fetch_time = _t.time() - t0
            tg_send(
                f"✅ <b>Шаг 1/2:</b> {n_bars} {tf_primary}m баров "
                f"({fetch_time:.0f}с).\n"
                f"⏳ <b>Шаг 2/2:</b> Replay + breakdown...",
                chat_id=chat_id,
            )

            t1 = _t.time()
            result = bt_backtest.run_backtest(
                data, tf_primary=tf_primary,
                warmup_bars=200, progress_each=1000,
                collect_signals=True,
                config_overrides=overrides,
            )
            replay_time = _t.time() - t1
            total_time = _t.time() - t0

            body = bt_backtest.format_result(result)
            footer = (f"\n\n⏱ Fetch: {fetch_time:.0f}с · "
                      f"Replay: {replay_time:.0f}с · "
                      f"Total: {total_time:.0f}с")

            tg_send_chunked(f"<pre>{body}</pre>{footer}", chat_id=chat_id)

            # JSON dump через sendDocument
            import tempfile as _tempfile
            with _tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
                tmp_path = f.name
            try:
                bt_backtest.dump_result_json(result, tmp_path)
                with open(tmp_path, "rb") as f:
                    payload = f.read()
                ovr_tag = "_ovr" if overrides else ""
                tf_tag = f"_tf{tf_primary}" if tf_primary != "5" else ""
                tg_send_document(
                    payload,
                    f"btdiag_{symbol_full}_{days}d{tf_tag}{ovr_tag}.json",
                    caption=f"Полный dump: {len(result.signals)} сигналов, "
                            f"{len(result.trades)} трейдов",
                    chat_id=chat_id,
                )
            finally:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

            # Equity curve PNG — визуальная сводка
            try:
                equity = (result.stats or {}).get("equity") or []
                png = bt_equity_chart.render_equity_curve(
                    equity, symbol_full.replace("USDT", ""),
                    days, stats=result.stats,
                )
                if png:
                    final_r = equity[-1] if equity else 0.0
                    emoji = "🟢" if final_r > 0 else ("🔴" if final_r < 0 else "⚪")
                    tg_send_photo(
                        png,
                        f"{emoji} Equity curve: итог {final_r:+.2f}R",
                        chat_id=chat_id,
                        filename=f"equity_{symbol_full}_{days}d.png",
                    )
            except Exception as e:
                log.warning(f"[/btdiag] equity chart failed: {e}")

        except Exception as e:
            log.exception(f"[/btdiag] failed: {e}")
            tg_send(f"❌ Btdiag error: {type(e).__name__}: {e}",
                    chat_id=chat_id)

    threading.Thread(target=_run, daemon=True).start()


# ─── SCAN BACKTEST (/scanbt) ─────────────────────────────────────────────────


SCANBT_VALID_SORT = ("pf", "wr", "avg_r", "avg_r_net", "max_dd", "closed")


def _parse_scanbt_args(
    args: str,
) -> tuple[list[str], int, dict | None, str, str]:
    """
    Парсит '/scanbt BTC,ETH,SOL 30 tf=15 preset=no_p3 sort=avg_r_net KEY=VAL' → (
        ['BTCUSDT','ETHUSDT','SOLUSDT'], 30, merged_overrides, 'avg_r_net', '15').

    Дефолты: ['BTC','ETH','SOL'], 30d, sort='pf', tf='5'.
    preset=NAME применяется первым, explicit KEY=VAL перебивает.
    sort= одно из: pf | wr | avg_r | avg_r_net | max_dd | closed.
    tf= — primary TF: 5|15|60|240|D (или alias 1H/4H/1D).
    """
    raw_parts = [p for p in args.strip().split() if p]
    raw_parts, preset_ovr = _extract_preset_tokens(raw_parts)
    symbols_raw = "BTC,ETH,SOL"
    days = 30
    sort_by = "pf"
    tf_primary = "5"
    override_parts: list[str] = []
    for p in raw_parts:
        pl = p.lower()
        if pl.startswith("sort="):
            val = p.split("=", 1)[1].lower()
            if val in SCANBT_VALID_SORT:
                sort_by = val
        elif pl.startswith("tf="):
            tf_primary = _normalize_tf(p.split("=", 1)[1])
        elif "=" in p and not p.startswith("="):
            override_parts.append(p)
        elif p.isdigit():
            days = int(p)
        elif "," in p:
            symbols_raw = p
        else:
            # одиночный символ — добавим
            symbols_raw = p
    syms = []
    for s in symbols_raw.split(","):
        s = s.strip().upper().replace(".P", "").replace("USDT", "")
        if not s:
            continue
        full = s if s.endswith("USDT") else s + "USDT"
        if full not in syms:
            syms.append(full)
    days = max(1, min(365, days))
    explicit_ovr = (bt_backtest._parse_overrides(",".join(override_parts))
                    if override_parts else None)
    merged: dict = dict(preset_ovr)
    if explicit_ovr:
        merged.update(explicit_ovr)
    return syms, days, (merged or None), sort_by, tf_primary


_SCANBT_SORT_KEY_MAP = {
    "pf":         "pf",
    "wr":         "win_rate",
    "avg_r":      "avg_r",
    "avg_r_net":  "avg_r_net",
    "max_dd":     "max_dd",      # ascending (closer to 0 = better)
    "closed":     "closed",
}


def _scanbt_sort_key(row: dict, sort_by: str):
    """Возвращает sort-key с поддержкой строковых ∞/-∞ PF."""
    field_name = _SCANBT_SORT_KEY_MAP.get(sort_by, "pf")
    v = row.get(field_name, 0)
    if isinstance(v, str):
        if v == "∞":
            return float("inf")
        return -float("inf")
    return v


def _format_scanbt_table(rows: list[dict]) -> str:
    """Pretty-print таблица результатов scanbt."""
    if not rows:
        return "(no symbols)"
    header = (f"{'Symbol':<10} {'Trades':>7} {'WR%':>6} {'AvgR':>7} "
              f"{'netR':>7} {'PF':>6} {'MaxDD':>8}")
    lines = [header, "-" * len(header)]
    for r in rows:
        lines.append(
            f"{r['symbol']:<10} "
            f"{r['closed']:>7} "
            f"{r['win_rate']:>5.1f}% "
            f"{r['avg_r']:>+7.2f} "
            f"{r['avg_r_net']:>+7.2f} "
            f"{r['pf']:>6} "
            f"{r['max_dd']:>+8.2f}"
        )
    return "\n".join(lines)


def cmd_scanbt(chat_id: int, args: str = ""):
    """
    Multi-symbol backtest: сравнить стратегию на нескольких монетах
    с одинаковыми параметрами.

      /scanbt                                — BTC,ETH,SOL 30d baseline
      /scanbt BTC,ETH 60                     — две монеты 60d
      /scanbt BTC,ETH,SOL,ARB,DOGE 30        — 5 монет
      /scanbt BTC,ETH 30 KILLZONE_GATE_ENABLED=false STRUCTURE_GATE_ENABLED=false
                                             — те же overrides для всех

    Выводит compare-таблицу. Для каждого символа: closed, WR, avgR, netR,
    PF, MaxDD. Помогает найти где у стратегии есть edge.
    """
    symbols, days, overrides, sort_by, tf_primary = _parse_scanbt_args(args)
    if not symbols:
        tg_send("❌ Не указаны символы. Пример: /scanbt BTC,ETH,SOL 30",
                chat_id=chat_id)
        return

    n_sym = len(symbols)
    eta = (f"{n_sym}-{n_sym * 2} мин" if days <= 30
           else f"{n_sym * 2}-{n_sym * 4} мин")
    ovr_label = (f"\n<i>overrides: {overrides}</i>" if overrides else "")
    sort_label = f" · sort={sort_by}" if sort_by != "pf" else ""
    tf_label = f" tf={tf_primary}" if tf_primary != "5" else ""
    tg_send(
        f"📊 <b>Scanbt {n_sym} symbols ({days}d){tf_label}</b>"
        f"{sort_label}{ovr_label}\n"
        f"{', '.join(s.replace('USDT','') for s in symbols)}\n"
        f"ETA ~{eta}...",
        chat_id=chat_id,
    )

    def _run():
        import time as _t
        t0 = _t.time()
        rows: list[dict] = []
        failed: list[tuple[str, str]] = []
        tfs_to_fetch = sorted(set(["5", "15", "60", "240", "D",
                                   tf_primary]))

        for i, sym in enumerate(symbols, start=1):
            try:
                tg_send(
                    f"⏳ <b>{i}/{n_sym}</b> {sym}: fetch + backtest...",
                    chat_id=chat_id,
                )
                data = bt_data.fetch_all(
                    sym, days,
                    tfs=tfs_to_fetch,
                    fetch_funding_data=False, fetch_oi_data=False,
                )
                result = bt_backtest.run_backtest(
                    data, tf_primary=tf_primary,
                    warmup_bars=200, progress_each=2000,
                    collect_signals=False,
                    config_overrides=overrides,
                )
                s = result.stats or {}
                risk = (s.get("risk") or {})
                pf = risk.get("profit_factor", 0)
                rows.append({
                    "symbol":    sym.replace("USDT", ""),
                    "closed":    s.get("closed", 0),
                    "win_rate":  s.get("win_rate", 0),
                    "avg_r":     s.get("avg_r", 0),
                    "avg_r_net": s.get("avg_r_net", 0),
                    "pf":        pf if isinstance(pf, (int, float)) else str(pf),
                    "max_dd":    risk.get("max_drawdown_r", 0),
                })
            except Exception as e:
                log.exception(f"[/scanbt] {sym} failed: {e}")
                failed.append((sym, f"{type(e).__name__}: {str(e)[:120]}"))

        # Сортировка по выбранной колонке (default 'pf').
        rows.sort(key=lambda r: _scanbt_sort_key(r, sort_by),
                  reverse=(sort_by != "max_dd"))

        total_time = _t.time() - t0
        body = _format_scanbt_table(rows)
        footer = f"\n\n⏱ Total: {total_time:.0f}с"
        if failed:
            footer += "\n\n❌ Failed:\n" + "\n".join(
                f"  {sym}: {err}" for sym, err in failed)
        tg_send_chunked(f"<pre>{body}</pre>{footer}", chat_id=chat_id)

    threading.Thread(target=_run, daemon=True).start()


# ─── HYPEROPT (/hyperopt) ────────────────────────────────────────────────────


def _parse_hyperopt_args(
    args: str,
) -> tuple[str, int, int, bool, str, dict | None, str]:
    """
    Парсит '/hyperopt BTC 60 50 walkforward tf=15 metric=sharpe_r preset=no_p4 KEY=VAL'.
    Дефолты: BTC 60d 30 trials, без walkforward, metric=profit_factor, tf='5'.

    preset=NAME → fixed_params из CONFIG_PRESETS.
    KEY=VAL (кроме metric=, preset=, tf=) → fixed_params для всех trials.
    Explicit KEY=VAL перебивает preset values.
    tf= — primary TF: 5|15|60|240|D (или alias 1H/4H/1D).
    """
    raw_parts = [p for p in args.strip().split() if p]
    raw_parts, preset_ovr = _extract_preset_tokens(raw_parts)
    symbol, days, trials = "BTC", 60, 30
    walkforward = False
    metric = "profit_factor"
    tf_primary = "5"
    int_seen = 0
    fixed_parts: list[str] = []
    for p in raw_parts:
        pl = p.lower()
        if p.isdigit():
            v = int(p)
            if int_seen == 0:
                days = v
            else:
                trials = v
            int_seen += 1
        elif pl in ("walkforward", "wf", "--walkforward"):
            walkforward = True
        elif pl.startswith("metric="):
            val = p.split("=", 1)[1].lower()
            if val in bt_hyperopt.VALID_METRICS:
                metric = val
        elif pl.startswith("tf="):
            tf_primary = _normalize_tf(p.split("=", 1)[1])
        elif "=" in p and not p.startswith("="):
            fixed_parts.append(p)
        else:
            symbol = p.upper().replace("USDT", "").replace(".P", "")
    symbol_full = symbol if symbol.endswith("USDT") else symbol + "USDT"
    days = max(7, min(365, days))
    trials = max(5, min(200, trials))
    explicit_fixed = (bt_backtest._parse_overrides(",".join(fixed_parts))
                      if fixed_parts else None)
    merged: dict = dict(preset_ovr)
    if explicit_fixed:
        merged.update(explicit_fixed)
    return (symbol_full, days, trials, walkforward, metric,
            (merged or None), tf_primary)


def cmd_hyperopt(chat_id: int, args: str = ""):
    """
    Optuna-based тюнинг decision-параметров.

      /hyperopt                       — BTC 60d, 30 trials, PF
      /hyperopt ETH 90 50             — ETH 90d, 50 trials
      /hyperopt BTC 60 30 walkforward — out-of-sample валидация на 3 окнах
      /hyperopt BTC 60 50 metric=sharpe_r
      /hyperopt BTC 60 50 KILLZONE_GATE_ENABLED=false
                                     — зафиксировать flag, остальное искать
      /hyperopt BTC 60 50 HTF_BIAS_GATE_ENABLED=false MIN_CONFIDENCE_FOR_TRADE=60

    Защита: trial с closed<10 трейдов отбрасывается (anti-overfit).
    Walk-forward optimize на 70% train → backtest на 30% test, mean OOS.

    Один trial ≈ один backtest. 30 trials × 60d ≈ 5-10 мин.
    """
    (symbol_full, days, trials, walkforward, metric, fixed,
     tf_primary) = _parse_hyperopt_args(args)

    if walkforward:
        eta = f"{int(trials * 3 * 0.7 / 15)}-{int(trials * 3 / 10)} мин"
    else:
        eta = f"{int(trials / 15)}-{int(trials / 8)} мин"

    fixed_label = (f"\n<i>fixed: {fixed}</i>" if fixed else "")
    tf_label = f" tf={tf_primary}" if tf_primary != "5" else ""
    tg_send(
        f"🧪 <b>Hyperopt {symbol_full} {days}d{tf_label}</b>{fixed_label}\n"
        f"Trials: {trials} · metric: {metric} · "
        f"{'walk-forward (3 windows)' if walkforward else 'single-shot'}\n"
        f"ETA ~{eta}...",
        chat_id=chat_id,
    )

    def _run():
        import time as _t
        t0 = _t.time()
        try:
            tg_send("⏳ <b>Шаг 1/2:</b> Загружаю историю...", chat_id=chat_id)
            tfs_to_fetch = sorted(set(["5", "15", "60", "240", "D",
                                       tf_primary]))
            data = bt_data.fetch_all(
                symbol_full, days,
                tfs=tfs_to_fetch,
                fetch_funding_data=False, fetch_oi_data=False,
            )
            n_bars = len(data["klines"].get(tf_primary) or [])
            fetch_time = _t.time() - t0
            tg_send(
                f"✅ <b>Шаг 1/2:</b> {n_bars} {tf_primary}m баров "
                f"({fetch_time:.0f}с).\n"
                f"⏳ <b>Шаг 2/2:</b> Optuna {trials} trials...",
                chat_id=chat_id,
            )

            t1 = _t.time()
            last_log = [_t.time()]

            def _progress(msg: str):
                # Throttle: send TG update раз в ~30 сек чтобы не флудить
                now = _t.time()
                if now - last_log[0] >= 30:
                    elapsed = now - t1
                    tg_send(f"⏳ {msg} · прошло {elapsed/60:.1f} мин",
                            chat_id=chat_id)
                    last_log[0] = now
                log.info(f"[/hyperopt] {msg}")

            result = bt_hyperopt.hyperopt(
                data, n_trials=trials, metric=metric,
                fixed_params=fixed,
                tf_primary=tf_primary,
                seed=42, min_trades=10, warmup_bars=200,
                progress=_progress,
            )

            if walkforward:
                tg_send("⏳ Walk-forward валидация (3 окна)...",
                        chat_id=chat_id)
                wf = bt_hyperopt.hyperopt_walkforward(
                    data, n_windows=3, train_test_ratio=0.7,
                    n_trials=trials, metric=metric,
                    fixed_params=fixed,
                    tf_primary=tf_primary,
                    seed=42, min_trades=10, warmup_bars=200,
                    progress=_progress,
                )
                result.walkforward = wf

            opt_time = _t.time() - t1
            total_time = _t.time() - t0

            body = bt_hyperopt.format_hyperopt(result, top_n=10)
            footer = (f"\n\n⏱ Fetch: {fetch_time:.0f}с · "
                      f"Optimize: {opt_time:.0f}с · "
                      f"Total: {total_time:.0f}с")

            tg_send_chunked(f"<pre>{body}</pre>{footer}", chat_id=chat_id)

            # JSON dump
            import tempfile as _tempfile
            with _tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
                tmp_path = f.name
            try:
                bt_hyperopt.dump_result_json(result, tmp_path)
                with open(tmp_path, "rb") as f:
                    payload = f.read()
                tf_tag = f"_tf{tf_primary}" if tf_primary != "5" else ""
                tg_send_document(
                    payload,
                    f"hyperopt_{symbol_full}_{days}d{tf_tag}_{trials}t.json",
                    caption=(f"Trials dump: {trials}t · "
                            f"best {metric}="
                            f"{result.best_value if result.best_value is not None else '—'}"),
                    chat_id=chat_id,
                )
            finally:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

        except Exception as e:
            log.exception(f"[/hyperopt] failed: {e}")
            tg_send(f"❌ Hyperopt error: {type(e).__name__}: {e}",
                    chat_id=chat_id)

    threading.Thread(target=_run, daemon=True).start()


# ─── DAILY DIGEST ─────────────────────────────────────────────────────────────

def run_daily_digest():
    log.info("📊 Отправляю дайджест...")
    cmd_digest(int(TELEGRAM_CHAT_ID))


def start_scheduler():
    schedule.every().day.at(DIGEST_TIME).do(run_daily_digest)
    schedule.every(15).minutes.do(run_auto_scan)
    schedule.every(1).minutes.do(check_price_alerts)
    schedule.every(30).minutes.do(check_signal_outcomes)
    schedule.every(10).minutes.do(check_trade_outcomes)
    schedule.every(5).minutes.do(check_movers)
    time.sleep(15)          # short delay so Flask is fully up first
    run_auto_scan()         # run once immediately on startup
    while True:
        schedule.run_pending()
        time.sleep(30)


# ─── ENTRY POINT ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    errors = []
    if "YOUR_BOT_TOKEN"     in TELEGRAM_TOKEN:    errors.append("TELEGRAM_TOKEN")
    if "YOUR_CHAT_ID"       in TELEGRAM_CHAT_ID:  errors.append("TELEGRAM_CHAT_ID")
    if "YOUR_ANTHROPIC_KEY" in ANTHROPIC_API_KEY: errors.append("ANTHROPIC_API_KEY")
    if errors:
        print(f"❌  Заполни в config.py: {', '.join(errors)}")
        exit(1)

    db_init()
    _register_bot_commands()

    threading.Thread(target=telegram_polling, daemon=True).start()
    threading.Thread(target=start_scheduler,  daemon=True).start()

    tg_send(
        "🤖 <b>Crypto Screener Pro v3 запущен</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "📡 TradingView webhook: активен\n"
        "🔍 Автосканер: каждые 15 мин (BOS · CHoCH · FVG · Sweep)\n"
        "🔔 Price Alerts: проверка каждую минуту\n"
        "🧠 LLM: Claude активен (чат без команд!)\n"
        "📊 CVD · VP · MTF · Торговые идеи с R:R\n"
        f"⏰ Дайджест: каждый день в {DIGEST_TIME} UTC\n\n"
        "Новое: /analyze BTC · /alert · свободный чат\n"
        "Справка: /help"
    )
    log.info(f"🚀 Запуск v2 | порт {PORT} | дайджест {DIGEST_TIME} UTC")

    app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False)
