#!/usr/bin/env python3
"""
Crypto Screener Pro v2
TradingView Webhooks → CVD + VP + MTF + Macro → Claude LLM → Telegram
"""

import base64
import json
import logging
import re
import sqlite3
import threading
import time
import schedule
import requests
import anthropic
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
from flask import Flask, request, jsonify

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
        c.commit()
    log.info("DB инициализирована")

def db_save(symbol, tf, sig_type, price, raw, llm_text, quality=0):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    with _db_lock, db_conn() as c:
        c.execute(
            "INSERT INTO signals(ts,symbol,tf,signal_type,price,raw_json,llm_text,quality)"
            " VALUES(?,?,?,?,?,?,?,?)",
            (ts, symbol, tf, sig_type, price, json.dumps(raw), llm_text, quality),
        )
        c.commit()

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

# ─── MARKET DATA — BYBIT ─────────────────────────────────────────────────────

BYBIT = "https://api.bybit.com"
HL    = "https://api.hyperliquid.xyz/info"

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

    return out


def _klines(symbol: str, interval: str, limit: int = 100) -> list:
    """Candles oldest→newest: [{o,h,l,c,v}, ...]"""
    try:
        r = requests.get(
            f"{BYBIT}/v5/market/kline",
            params={"symbol": symbol, "interval": interval,
                    "limit": limit, "category": "linear"}, timeout=8,
        )
        rows = r.json()["result"]["list"]   # newest first
        rows.reverse()
        return [{"o": float(x[1]), "h": float(x[2]), "l": float(x[3]),
                 "c": float(x[4]), "v": float(x[5])} for x in rows]
    except Exception as e:
        log.warning(f"Klines {symbol} {interval}: {e}")
        return []


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
        d = requests.get("https://api.coingecko.com/api/v3/global", timeout=8).json()["data"]["market_cap_percentage"]
        out["btc_dom"] = round(d.get("btc", 0), 2)
        out["eth_dom"] = round(d.get("eth", 0), 2)
    except Exception as e:
        log.warning(f"Dominance: {e}")

    with _macro_lock:
        _macro_cache["ts"]   = time.time()
        _macro_cache["data"] = out

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
            score -= 10; factors.append(f"MTF ❌ все ТФ против")
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
                try: t += float(lvl["px"]) * float(lvl["sz"])
                except: pass
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
            except: pass
        out["large_trades"] = large[:5]
    except Exception as e:
        log.warning(f"HL trades {coin}: {e}")
        out["large_trades"] = []

    return out


# ─── COMBINED FETCH (parallel) ────────────────────────────────────────────────

def fetch_market(symbol: str) -> dict:
    base = symbol.replace(".P", "")
    if not base.endswith("USDT"):
        base += "USDT"

    with ThreadPoolExecutor(max_workers=7) as ex:
        f_bybit = ex.submit(_bybit_data, base)
        f_k1h   = ex.submit(_klines, base, "60",  100)
        f_k4h   = ex.submit(_klines, base, "240",  50)
        f_k1d   = ex.submit(_klines, base, "D",    50)
        f_hl    = ex.submit(_hl_data, base)
        f_macro = ex.submit(get_macro)

    bybit   = f_bybit.result()
    k1h     = f_k1h.result()
    k4h     = f_k4h.result()
    k1d     = f_k1d.result()
    hl      = f_hl.result()
    macro   = f_macro.result()
    session = get_session()

    cvd        = compute_cvd(k1h)
    vp         = compute_volume_profile(k1h)
    ema_biases = get_ema_biases(k1h, k4h, k1d)

    fr_div    = abs(bybit.get("funding", 0) - hl.get("funding", 0)) * 100
    fr_signal = fr_div > 0.005

    return {
        "bybit":               bybit,
        "hl":                  hl,
        "price":               bybit.get("price") or hl.get("price", 0),
        "change_24h":          bybit.get("change_24h", 0),
        "fr_divergence":       fr_div,
        "fr_divergence_signal": fr_signal,
        "cvd":                 cvd,
        "vp":                  vp,
        "ema_biases":          ema_biases,
        "macro":               macro,
        "session":             session,
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

    sess_str = f"\n• Session: {sess.get('icon','')} {sess.get('name','')} [{sess.get('quality','?')}/5]"

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

SYSTEM_ASK = """\
Ты — профессиональный институциональный крипто-трейдер (SMC, ICT, Wyckoff, Order Flow, CVD).
Отвечаешь на вопросы трейдера на основе текущих рыночных данных и истории сигналов.
Только русский язык. Конкретно, по существу, без воды."""

SYSTEM_CHART = """\
Ты — профессиональный крипто-аналитик уровня prop firm (SMC, ICT, Wyckoff, Price Action).
Трейдер прислал скриншот графика — проанализируй его и сравни со своими данными.

Структура ответа (строго, только русский язык):
1. 📊 Что видишь на графике: структура, ключевые уровни, паттерны, тренд
2. 🔍 Сравнение с объективными данными (CVD, funding, OI, book, MTF)
3. ✅ Где вы согласны / ❌ где расходитесь во мнениях
4. 🎯 Итоговая рекомендация: вход, стоп, цель или "ждать"

Максимум 6–8 предложений. Без воды, без приветствий."""


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
                       market: dict, symbol: str) -> str:
    """Analyze chart screenshot + compare with live market data."""
    mkt_text = market_summary_text(symbol, market)

    prompt = (
        f"Пара: {symbol.replace('USDT','')}/USDT.P\n"
        + (f"Комментарий трейдера: {caption}\n\n" if caption else "\n")
        + f"Текущие данные рынка:\n{mkt_text}\n\n"
        "Проанализируй скриншот и дай сравнительный разбор."
    )

    try:
        resp = ai.messages.create(
            model=LLM_MODEL_SMART,
            max_tokens=700,
            system=SYSTEM_CHART,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": img_b64,
                        },
                    },
                    {"type": "text", "text": prompt},
                ],
            }],
        )
        return resp.content[0].text.strip()
    except Exception as e:
        log.error(f"LLM chart error: {e}")
        return f"⚠️ Ошибка анализа: {e}"


def llm_analyze_signal(sig_data: dict, market: dict, recent: list,
                        confluence: int = 0, conf_factors: list = None,
                        model=LLM_MODEL_FAST) -> tuple:
    symbol = sig_data.get("symbol", "UNKNOWN")
    sig    = sig_data.get("signal", "ALERT")
    price  = sig_data.get("price", market.get("price", 0))
    tf     = TF_LABEL.get(str(sig_data.get("tf", "")), sig_data.get("tf", "?"))

    recent_lines = "\n".join(
        f"  • {r[0]} UTC: {r[3]} {r[1]} {r[2]} @ ${float(r[4]):,.0f}"
        for r in recent
    ) or "  Нет недавних сигналов"

    extras = []
    for k, label in [("ob_top","OB верх"),("ob_bot","OB низ"),
                      ("fvg_top","FVG верх"),("fvg_bot","FVG низ"),
                      ("target","Цель"),("stop","Стоп")]:
        if sig_data.get(k):
            extras.append(f"• {label}: ${float(sig_data[k]):,.0f}")

    conf_text = ""
    if conf_factors:
        conf_text = (f"\nConfluence Score: {confluence}/100\n"
                     + "\n".join(f"  {f}" for f in conf_factors[:6]))

    prompt = f"""Новый сигнал от TradingView:
Тип: {sig}
Пара: {symbol} | ТФ: {tf} | Цена: ${float(price):,.2f}
{chr(10).join(extras) if extras else ''}
{conf_text}

Деривативы прямо сейчас:
{market_summary_text(symbol, market)}

Последние сигналы (4ч):
{recent_lines}

Дай анализ."""

    try:
        resp = ai.messages.create(
            model=model, max_tokens=350, system=SYSTEM_SIGNAL,
            messages=[{"role": "user", "content": prompt}],
        )
        text    = resp.content[0].text.strip()
        quality = 5
        m = re.search(r"\b([1-9]|10)\s*/\s*10", text)
        if m:
            quality = int(m.group(1))
        return text, quality
    except Exception as e:
        log.error(f"LLM error: {e}")
        return f"⚠️ LLM временно недоступен: {e}", 0


def llm_ask(question: str, market_ctx: str, recent: list, model=LLM_MODEL_SMART) -> str:
    recent_lines = "\n".join(
        f"  • {r[0]}: {r[3]} {r[1]} {r[2]} @ ${float(r[4]):,.0f} [Q:{r[5]}]"
        for r in recent
    ) or "  Нет недавних сигналов"

    prompt = f"""Текущая рыночная ситуация:
{market_ctx}

Последние сигналы (8ч):
{recent_lines}

Вопрос трейдера: {question}"""

    try:
        resp = ai.messages.create(
            model=model, max_tokens=600, system=SYSTEM_ASK,
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.content[0].text.strip()
    except Exception as e:
        return f"⚠️ Ошибка: {e}"


def llm_digest(signals: list, market_ctx: str) -> str:
    if not signals:
        return "Сигналов за сегодня не было."
    lines = "\n".join(
        f"  {r[0]}: {r[3]} {r[1]} {r[2]} @ ${float(r[4]):,.0f} [Q:{r[6]}]"
        for r in signals
    )
    prompt = f"""Сигналы за сегодня:
{lines}

Текущий рынок:
{market_ctx}

Дай дневной дайджест: ключевые паттерны, что показал рынок, общий bias."""
    try:
        resp = ai.messages.create(
            model=LLM_MODEL_SMART, max_tokens=500, system=SYSTEM_SIGNAL,
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.content[0].text.strip()
    except Exception as e:
        return f"⚠️ Ошибка дайджеста: {e}"


# ─── TELEGRAM ─────────────────────────────────────────────────────────────────

def tg_send(text: str, chat_id=None) -> bool:
    cid = chat_id or TELEGRAM_CHAT_ID
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": cid, "text": text,
                  "parse_mode": "HTML", "disable_web_page_preview": True},
            timeout=10,
        )
        r.raise_for_status()
        return True
    except Exception as e:
        log.error(f"Telegram send: {e}")
        return False


# ─── MESSAGE BUILDER ─────────────────────────────────────────────────────────

def build_signal_message(data: dict, market: dict, llm_text: str, quality: int,
                          confluence: int = 0, conf_factors: list = None) -> str:
    sig    = data.get("signal", "ALERT").upper()
    symbol = data.get("symbol", data.get("ticker", "?")).replace("USDT.P","").replace("USDT","")
    price  = data.get("price", data.get("close", market.get("price", 0)))
    tf     = TF_LABEL.get(str(data.get("tf", data.get("interval","?"))), str(data.get("tf","?")))
    now    = datetime.now(timezone.utc).strftime("%H:%M UTC")

    emoji, title, bias = SIGNAL_META.get(sig, SIGNAL_META["ALERT"])

    try:    price_f = f"${float(price):,.2f}"
    except: price_f = str(price)

    stars      = "⭐" * min(quality, 5) + ("+" if quality > 5 else "")
    conf_bar   = "🔥" * (confluence // 20) + "▫️" * (5 - confluence // 20)
    conf_color = "🔴" if confluence < 35 else ("🟡" if confluence < 55 else ("🟢" if confluence < 75 else "🚀"))

    extras = []
    for k, lbl in [("ob_top","OB ↑"),("ob_bot","OB ↓"),
                    ("fvg_top","FVG ↑"),("fvg_bot","FVG ↓"),
                    ("target","Цель"),("stop","Стоп")]:
        if data.get(k):
            try: extras.append(f"  {lbl}: ${float(data[k]):,.0f}")
            except: pass

    extras_str = ("\n" + "\n".join(extras)) if extras else ""

    b, hl  = market.get("bybit", {}), market.get("hl", {})
    fr_b   = b.get("funding", 0) * 100
    fr_hl  = hl.get("funding", 0) * 100
    oi_chg = b.get("oi_chg", 0)
    oi_usd = hl.get("oi_usd", 0)

    def fr_icon(fr): return "🔴" if fr > 0.01 else ("🟢" if fr < -0.01 else "⚪")

    ratio     = hl.get("book_ratio", 1.0)
    book_icon = "🟢" if ratio > 1.1 else ("🔴" if ratio < 0.9 else "⚪")

    lt = hl.get("large_trades", [])
    lt_line = ""
    if lt:
        parts = [f"{'🟢' if t['side']=='BUY' else '🔴'}${t['usd']/1e6:.1f}M" for t in lt[:3]]
        lt_line = f"\n  🐋 Крупные: {' '.join(parts)}"

    div_line = f"\n  ⚡ FR расхождение: {market['fr_divergence']:.4f}%" if market.get("fr_divergence_signal") else ""

    cvd = market.get("cvd", {})
    cvd_line = ""
    if cvd.get("trend") and cvd["trend"] != "unknown":
        cvd_line = (f"\n  CVD: {'📈' if cvd['trend']=='up' else '📉'} {cvd['trend'].upper()}"
                    + (" ⚠️ DIV" if cvd.get("divergence") else ""))

    vp = market.get("vp", {})
    vp_line = f"\n  VP POC: ${vp['poc']:,.0f} | VA ${vp['val']:,.0f}–${vp['vah']:,.0f}" if vp.get("poc") else ""

    biases = market.get("ema_biases", {})
    mtf_line = ""
    if biases:
        parts = [f"{tf}:{'🟢' if b=='bull' else ('🔴' if b=='bear' else '❓')}" for tf, b in biases.items()]
        mtf_line = "\n  MTF: " + " ".join(parts)

    macro = market.get("macro", {})
    macro_line = ""
    if macro.get("fg_value") is not None:
        macro_line = (f"\n  F&G: {macro['fg_icon']} {macro['fg_label']} [{macro['fg_value']}]"
                      + (f" | Dom: {macro.get('btc_dom')}%" if macro.get("btc_dom") else ""))

    sess = market.get("session", {})
    sess_line = f"\n  {sess.get('icon','')} {sess.get('name','')} [{sess.get('quality',2)}/5]"

    conf_lines = ""
    if conf_factors:
        conf_lines = "\n" + "\n".join(f"  {f}" for f in conf_factors[:5])

    return (
        f"{emoji} <b>{title}</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📌 <b>{symbol}/USDT.P</b> • {tf} • {now}\n"
        f"💰 <b>{price_f}</b>  ({market.get('change_24h', 0):+.2f}% 24h)\n"
        f"📊 Bias: <b>{bias}</b>\n"
        f"⭐ LLM: {stars} [{quality}/10]\n"
        f"━━ Confluence ━━━━\n"
        f"{conf_color} Score: <b>{confluence}/100</b>  {conf_bar}"
        f"{conf_lines}"
        f"{extras_str}\n"
        f"━━ Деривативы ━━━━\n"
        f"  Bybit FR:  {fr_icon(fr_b)} {fr_b:+.4f}%\n"
        f"  HL FR:     {fr_icon(fr_hl)} {fr_hl:+.4f}%{div_line}\n"
        f"  OI Bybit:  {oi_chg:+.2f}% (15м)\n"
        f"  OI HL:     ${oi_usd/1e9:.2f}B\n"
        f"  Book HL:   {book_icon} {ratio:.2f}{lt_line}"
        f"{cvd_line}"
        f"{vp_line}"
        f"{mtf_line}"
        f"{macro_line}"
        f"{sess_line}\n"
        f"━━ 🧠 Анализ LLM ━━\n"
        f"{llm_text}\n"
        f"━━━━━━━━━━━━━━━━━━"
    )


# ─── WEBHOOK ──────────────────────────────────────────────────────────────────

@app.route("/webhook", methods=["POST"])
def webhook():
    raw = request.get_data(as_text=True)
    log.info(f"← Webhook: {raw[:150]}")

    data = {}
    try:
        data = json.loads(raw)
    except Exception:
        data = {"signal": "ALERT", "msg": raw[:300]}

    sig_type = data.get("signal", "ALERT").upper()
    symbol   = data.get("symbol", data.get("ticker", "BTCUSDT"))
    tf       = str(data.get("tf", data.get("interval", "?")))
    price    = float(data.get("price", data.get("close", 0)) or 0)

    base_sym = symbol.replace(".P", "").replace("/", "")
    market   = fetch_market(base_sym if base_sym.endswith("USDT") else base_sym + "USDT")

    # MTF confluence with signal direction
    sig_up    = any(x in sig_type for x in ("BULL","LONG","SWEEP_L","EQL"))
    sig_dn    = any(x in sig_type for x in ("BEAR","SHORT","SWEEP_H","EQH"))
    direction = "long" if sig_up else ("short" if sig_dn else "neutral")
    biases    = market.get("ema_biases", {})
    mtf       = check_mtf_confluence(biases, direction) if direction != "neutral" else {}

    conf_score, conf_factors = compute_confluence_score(sig_type, market, mtf)

    recent               = db_recent(hours=4, limit=6)
    llm_text, quality    = llm_analyze_signal(data, market, recent, conf_score, conf_factors)

    db_save(symbol, tf, sig_type, price, data, llm_text, quality)

    if quality < MIN_QUALITY:
        log.info(f"  Качество {quality} < {MIN_QUALITY} — не отправляем")
        return jsonify({"status": "filtered", "quality": quality}), 200

    msg = build_signal_message(data, market, llm_text, quality, conf_score, conf_factors)
    ok  = tg_send(msg)
    log.info(f"  {sig_type} {symbol} Q:{quality}/10 Conf:{conf_score}/100 → {'OK' if ok else 'FAIL'}")

    return jsonify({"status": "ok", "quality": quality, "confluence": conf_score}), 200


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "running", "time": datetime.now(timezone.utc).isoformat()}), 200


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
    tg_send("🧠 Думаю...", chat_id=chat_id)

    ctx_parts = []
    for sym in SYMBOLS:
        m = fetch_market(sym)
        ctx_parts.append(f"{sym}:\n{market_summary_text(sym, m)}")

    recent = db_last_n(12)
    answer = llm_ask(question, "\n\n".join(ctx_parts), recent)
    tg_send(f"🧠 <b>Анализ:</b>\n\n{answer}", chat_id=chat_id)


def cmd_digest(chat_id: int):
    tg_send("📊 Генерирую дайджест...", chat_id=chat_id)
    signals   = db_today()
    ctx_parts = []
    for sym in SYMBOLS:
        m = fetch_market(sym)
        ctx_parts.append(f"{sym}:\n{market_summary_text(sym, m)}")
    digest = llm_digest(signals, "\n\n".join(ctx_parts))
    tg_send(
        f"📊 <b>Дайджест за сегодня</b> ({len(signals)} сигналов)\n"
        f"━━━━━━━━━━━━━━━━━\n{digest}",
        chat_id=chat_id,
    )


def cmd_scan(chat_id: int):
    tg_send("🔍 Запускаю ручное сканирование...", chat_id=chat_id)
    threading.Thread(target=run_auto_scan, daemon=True).start()


def cmd_help(chat_id: int):
    tg_send(
        "🤖 <b>Crypto Screener Pro — команды</b>\n"
        "━━━━━━━━━━━━━━━━━\n"
        "/status   — рынок: цена, CVD, VP, MTF, F&G, сессия\n"
        "/history  — последние 10 сигналов из БД\n"
        "/digest   — дневной дайджест с LLM анализом\n"
        "/scan     — ручной запуск автосканера прямо сейчас\n"
        "/ask [вопрос] — задай вопрос о рынке\n\n"
        "📸 <b>Анализ графика:</b> просто пришли скриншот!\n"
        "Можно добавить подпись: <i>BTC 4H — думаю шорт отсюда</i>\n"
        "LLM сравнит твой взгляд с реальными данными рынка.\n\n"
        "<i>Пример: /ask стоит ли сейчас шортить ETH?</i>",
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

    # Guess symbol from caption (e.g. "BTC", "ETH", "btcusdt")
    cap_upper = caption.upper()
    symbol = next(
        (s for s in SYMBOLS if s.replace("USDT","") in cap_upper or s in cap_upper),
        SYMBOLS[0],
    )

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


def handle_update(update: dict):
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
    if not text or not text.startswith("/"):
        return

    parts = text.split(None, 1)
    cmd   = parts[0].lower().split("@")[0]
    args  = parts[1].strip() if len(parts) > 1 else ""

    log.info(f"← Команда: {cmd} args={args!r}")

    if cmd == "/status":               cmd_status(chat_id)
    elif cmd == "/history":            cmd_history(chat_id)
    elif cmd == "/ask":                cmd_ask(chat_id, args)
    elif cmd == "/digest":             cmd_digest(chat_id)
    elif cmd == "/scan":               cmd_scan(chat_id)
    elif cmd in ("/help", "/start"):   cmd_help(chat_id)


def telegram_polling():
    offset = 0
    log.info("▶ Telegram polling запущен")
    while True:
        try:
            r = requests.get(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates",
                params={"offset": offset, "timeout": 25, "allowed_updates": ["message", "message_with_photo"]},
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

SCAN_COOLDOWN_MIN = 60          # minutes between same signal on same symbol+tf
SCAN_INTERVALS    = ["15", "60", "240"]
SCAN_MIN_CONF     = 40          # skip signals below this confluence score

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

    # ── FVG (3-candle gap) ───────────────────────────────────────────────────
    c2, c1, c0 = candles[-3], candles[-2], candles[-1]
    if c0["l"] > c2["h"]:
        signals.append("FVG_BULL")
    elif c0["h"] < c2["l"]:
        signals.append("FVG_BEAR")

    # ── Liquidity Sweep ──────────────────────────────────────────────────────
    if last["h"] > prev_high and last["c"] < prev_high:
        signals.append("LIQ_SWEEP_H")
    if last["l"] < prev_low and last["c"] > prev_low:
        signals.append("LIQ_SWEEP_L")

    return signals


def run_auto_scan():
    log.info("🔍 Автосканер: начинаю сканирование...")
    now = time.time()

    for symbol in SYMBOLS:
        base   = symbol if symbol.endswith("USDT") else symbol + "USDT"
        market = None   # lazy-fetch once per symbol

        for interval in SCAN_INTERVALS:
            candles = _klines(base, interval, 60)
            if not candles:
                continue

            detected = detect_signals(candles)
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
                llm_text, quality = llm_analyze_signal(
                    sig_data, market, recent, conf_score, conf_factors
                )

                db_save(base, interval, sig_type, price, sig_data, llm_text, quality)

                if quality < MIN_QUALITY:
                    continue

                msg = "🤖 <b>[АВТОСКАНЕР]</b>\n" + build_signal_message(
                    sig_data, market, llm_text, quality, conf_score, conf_factors
                )
                tg_send(msg)
                log.info(f"  ✅ {sig_type} {base} {interval} "
                         f"Q:{quality}/10 Conf:{conf_score}/100")

    log.info("🔍 Автосканер: завершено")


# ─── DAILY DIGEST ─────────────────────────────────────────────────────────────

def run_daily_digest():
    log.info("📊 Отправляю дайджест...")
    cmd_digest(int(TELEGRAM_CHAT_ID))


def start_scheduler():
    schedule.every().day.at(DIGEST_TIME).do(run_daily_digest)
    schedule.every(15).minutes.do(run_auto_scan)
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

    threading.Thread(target=telegram_polling, daemon=True).start()
    threading.Thread(target=start_scheduler,  daemon=True).start()

    tg_send(
        "🤖 <b>Crypto Screener Pro v2 запущен</b>\n"
        "━━━━━━━━━━━━━━━━━\n"
        "📡 TradingView webhook: активен\n"
        "🔍 Автосканер: каждые 15 мин (BOS · CHoCH · FVG · Sweep)\n"
        "🧠 LLM: Claude активен\n"
        "📊 CVD · Volume Profile · MTF EMA · Fear&Greed · Confluence\n"
        f"⏰ Дайджест: каждый день в {DIGEST_TIME} UTC\n\n"
        "Команды: /help  |  /scan — ручной запуск"
    )
    log.info(f"🚀 Запуск v2 | порт {PORT} | дайджест {DIGEST_TIME} UTC")

    app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False)
