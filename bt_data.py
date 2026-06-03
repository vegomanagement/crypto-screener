"""
bt_data.py — historical data fetcher для бектеста.

Тянет klines (multi-TF), funding rate, open interest. Чейн fallback для klines:
Bybit V5 → Binance Futures → Hyperliquid (если первые два геоблочат).
Funding/OI — только Bybit (нет универсального source).

Кеширует в bt_cache/{symbol}_{kind}.jsonl, чтобы не делать повторные запросы.

API endpoints:
  • /v5/market/kline           — klines history (max 1000 per request)
  • /fapi/v1/klines            — Binance Futures klines (max 1500)
  • /info candleSnapshot       — Hyperliquid (max ~5000)
  • /v5/market/funding/history — funding rate history (max 200 per request)
  • /v5/market/open-interest   — OI history (max 200 per request)

CLI:
  python -m bt_data BTCUSDT 30 --tfs 5,15,60,240
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import requests

__all__ = [
    "BYBIT_BASE",
    "CACHE_DIR",
    "fetch_klines",
    "fetch_funding",
    "fetch_open_interest",
    "fetch_all",
    "tf_to_bybit_interval",
    "tf_to_minutes",
]

BYBIT_BASE          = "https://api.bybit.com"
BINANCE_FAPI        = "https://fapi.binance.com"
HL_INFO             = "https://api.hyperliquid.xyz/info"
CACHE_DIR           = Path("bt_cache")
KLINE_MAX_LIMIT     = 1000
BINANCE_KLINE_LIMIT = 1500
HL_KLINE_LIMIT      = 5000
FUNDING_MAX_LIMIT   = 200
OI_MAX_LIMIT        = 200
DEFAULT_CATEGORY    = "linear"  # USDT perpetual futures
REQUEST_TIMEOUT     = 15
RETRY_ATTEMPTS      = 3
RETRY_BACKOFF_SEC   = 2

# Bybit V5 → Binance Futures interval mapping
_BINANCE_INTERVAL_MAP = {
    "1": "1m", "3": "3m", "5": "5m", "15": "15m", "30": "30m",
    "60": "1h", "120": "2h", "240": "4h", "360": "6h", "480": "8h",
    "720": "12h", "D": "1d", "W": "1w", "M": "1M",
}

# Bybit V5 → Hyperliquid interval mapping
_HL_INTERVAL_MAP = {
    "1": "1m", "3": "3m", "5": "5m", "15": "15m", "30": "30m",
    "60": "1h", "120": "2h", "240": "4h", "480": "8h",
    "720": "12h", "D": "1d", "W": "1w", "M": "1M",
}


# ─── Helpers ──────────────────────────────────────────────────────────────


def tf_to_bybit_interval(tf: str) -> str:
    """
    Нормализованный наш TF ('5', '15', '60', '240', 'D') → формат Bybit V5.
    Bybit принимает: 1, 3, 5, 15, 30, 60, 120, 240, 360, 720, D, W, M.
    """
    s = str(tf).strip().upper()
    aliases = {
        "1H": "60", "2H": "120", "4H": "240",
        "1D": "D",  "1W": "W",   "1M": "M",
        "5M": "5",  "15M": "15", "30M": "30",
    }
    return aliases.get(s, s)


def tf_to_minutes(tf: str) -> int:
    """Кол-во минут в одной свече указанного TF."""
    iv = tf_to_bybit_interval(tf)
    if iv == "D":
        return 1440
    if iv == "W":
        return 7 * 1440
    if iv == "M":
        return 30 * 1440
    return int(iv)


def _now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def _ago_ms(days: int) -> int:
    return _now_ms() - days * 86_400_000


def _ensure_cache_dir() -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _cache_path(symbol: str, kind: str, tf: str | None = None) -> Path:
    suffix = f"_{tf}" if tf else ""
    return CACHE_DIR / f"{symbol}_{kind}{suffix}.jsonl"


def _read_cache(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out: list[dict] = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def _write_cache(path: Path, rows: list[dict]) -> None:
    _ensure_cache_dir()
    with path.open("w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def _request_with_retry(
    url: str,
    params: dict,
    session: requests.Session | None = None,
    *,
    source: str = "Bybit",
) -> dict:
    """GET с retry на network errors / 5xx. `source` идёт в ошибку для логов."""
    sess = session or requests
    last_exc: Exception | None = None
    for attempt in range(RETRY_ATTEMPTS):
        try:
            r = sess.get(url, params=params, timeout=REQUEST_TIMEOUT)
            r.raise_for_status()
            return r.json()
        except (requests.exceptions.RequestException,
                requests.exceptions.HTTPError) as e:
            last_exc = e
            if attempt + 1 < RETRY_ATTEMPTS:
                time.sleep(RETRY_BACKOFF_SEC ** attempt)
    raise RuntimeError(f"{source} request failed after {RETRY_ATTEMPTS} "
                       f"retries: {last_exc}")


# ─── Klines ────────────────────────────────────────────────────────────────


def _parse_kline_row(row: list) -> dict:
    """
    Bybit kline row format: [start_ms, open, high, low, close, volume, turnover].
    Все цены — строки.
    """
    return {
        "ts":  int(row[0]),
        "o":   float(row[1]),
        "h":   float(row[2]),
        "l":   float(row[3]),
        "c":   float(row[4]),
        "v":   float(row[5]),
    }


def _fetch_klines_bybit(
    symbol: str, tf: str, start_ms: int, end_ms: int,
    *, session=None, category: str = DEFAULT_CATEGORY,
) -> list[dict]:
    """Bybit V5 paginated kline fetch. Может бросить RuntimeError (403 etc)."""
    iv = tf_to_bybit_interval(tf)
    bars_per_request = KLINE_MAX_LIMIT
    minutes = tf_to_minutes(tf)
    window_ms = bars_per_request * minutes * 60_000

    out: list[dict] = []
    cur_end = end_ms
    while cur_end > start_ms:
        cur_start = max(start_ms, cur_end - window_ms)
        params = {
            "category": category, "symbol": symbol, "interval": iv,
            "start": cur_start, "end": cur_end, "limit": bars_per_request,
        }
        data = _request_with_retry(
            f"{BYBIT_BASE}/v5/market/kline", params, session=session)
        rows = (data.get("result") or {}).get("list") or []
        if not rows:
            break
        # Bybit возвращает newest→oldest, нормализуем
        parsed = [_parse_kline_row(r) for r in rows]
        parsed.sort(key=lambda x: x["ts"])
        parsed = [p for p in parsed if start_ms <= p["ts"] < cur_end]
        if not parsed:
            break
        out = parsed + out
        cur_end = parsed[0]["ts"]
        if len(rows) < bars_per_request:
            break
    return out


def _parse_binance_kline_row(row: list) -> dict:
    """
    Binance Futures kline: [openTime, open, high, low, close, volume,
                            closeTime, quoteAssetVolume, ...]
    """
    return {
        "ts": int(row[0]),
        "o":  float(row[1]),
        "h":  float(row[2]),
        "l":  float(row[3]),
        "c":  float(row[4]),
        "v":  float(row[5]),
    }


def _fetch_klines_binance(
    symbol: str, tf: str, start_ms: int, end_ms: int,
    *, session=None,
) -> list[dict]:
    """
    Binance Futures (USDT-M) paginated kline fetch — fallback когда Bybit 403.
    Endpoint /fapi/v1/klines поддерживает startTime/endTime.
    """
    iv_bybit = tf_to_bybit_interval(tf)
    iv = _BINANCE_INTERVAL_MAP.get(iv_bybit)
    if iv is None:
        raise RuntimeError(f"No Binance interval mapping for tf={tf}")

    minutes = tf_to_minutes(tf)
    window_ms = BINANCE_KLINE_LIMIT * minutes * 60_000

    out: list[dict] = []
    cur_start = start_ms
    while cur_start < end_ms:
        cur_end = min(end_ms, cur_start + window_ms)
        params = {
            "symbol": symbol, "interval": iv,
            "startTime": cur_start, "endTime": cur_end,
            "limit": BINANCE_KLINE_LIMIT,
        }
        data = _request_with_retry(
            f"{BINANCE_FAPI}/fapi/v1/klines", params, session=session,
            source="Binance")
        if not isinstance(data, list) or not data:
            break
        parsed = [_parse_binance_kline_row(r) for r in data]
        parsed = [p for p in parsed if start_ms <= p["ts"] < end_ms]
        if not parsed:
            break
        out.extend(parsed)
        last_ts = parsed[-1]["ts"]
        cur_start = last_ts + minutes * 60_000   # next bar after last
        if len(data) < BINANCE_KLINE_LIMIT:
            break

    return out


def _hl_symbol(symbol: str) -> str:
    """BTCUSDT → BTC. HL использует bare coin tickers, не пары."""
    s = symbol.upper().replace(".P", "")
    for suffix in ("USDT", "USDC", "USD", "PERP"):
        if s.endswith(suffix):
            return s[: -len(suffix)]
    return s


def _parse_hl_kline_row(row: dict) -> dict:
    """
    Hyperliquid candle dict: {t (start ms), T (close ms), o, h, l, c, v, n, s, i}
    Возвращает наш стандартный формат.
    """
    return {
        "ts": int(row["t"]),
        "o":  float(row["o"]),
        "h":  float(row["h"]),
        "l":  float(row["l"]),
        "c":  float(row["c"]),
        "v":  float(row["v"]),
    }


def _post_with_retry(
    url: str,
    payload: dict,
    session: requests.Session | None = None,
):
    """HL POST с retry на network errors / 5xx (HL не использует GET-params)."""
    sess = session or requests
    last_exc: Exception | None = None
    for attempt in range(RETRY_ATTEMPTS):
        try:
            r = sess.post(url, json=payload, timeout=REQUEST_TIMEOUT)
            r.raise_for_status()
            return r.json()
        except (requests.exceptions.RequestException,
                requests.exceptions.HTTPError) as e:
            last_exc = e
            if attempt + 1 < RETRY_ATTEMPTS:
                time.sleep(RETRY_BACKOFF_SEC ** attempt)
    raise RuntimeError(f"Hyperliquid request failed after {RETRY_ATTEMPTS} "
                       f"retries: {last_exc}")


def _fetch_klines_hl(
    symbol: str, tf: str, start_ms: int, end_ms: int,
    *, session=None,
) -> list[dict]:
    """
    Hyperliquid paginated kline fetch — fallback когда Bybit И Binance заблочены.
    Endpoint `candleSnapshot` поддерживает startTime/endTime, max ~5000
    свечей за запрос.

    HL имеет ограниченный retention для коротких TF (~5000 баров):
      5m  ~17d, 15m ~50d, 1h ~7m, 4h ~2y, 1d ~13y
    Поэтому итерируемся НАЗАД от end_ms — получаем сначала свежие данные,
    а недоступная старая часть просто отсутствует (graceful). Это симметрично
    с Bybit, который тоже идёт newest→oldest.

    Если HL вернул 0 свечей за весь запрос — `RuntimeError`, чтобы caller
    знал что HL не покрыл диапазон (vs silent empty).
    """
    iv_bybit = tf_to_bybit_interval(tf)
    iv = _HL_INTERVAL_MAP.get(iv_bybit)
    if iv is None:
        raise RuntimeError(f"No Hyperliquid interval mapping for tf={tf}")

    coin = _hl_symbol(symbol)
    minutes = tf_to_minutes(tf)
    window_ms = HL_KLINE_LIMIT * minutes * 60_000

    out: list[dict] = []
    cur_end = end_ms
    while cur_end > start_ms:
        cur_start = max(start_ms, cur_end - window_ms)
        payload = {
            "type": "candleSnapshot",
            "req": {"coin": coin, "interval": iv,
                    "startTime": cur_start, "endTime": cur_end},
        }
        data = _post_with_retry(HL_INFO, payload, session=session)
        if not isinstance(data, list):
            # HL может вернуть {"error": "..."} вместо списка
            err_msg = (data.get("error") if isinstance(data, dict)
                       else f"unexpected type {type(data).__name__}")
            raise RuntimeError(
                f"Hyperliquid candleSnapshot for {coin} {iv} returned non-list: "
                f"{err_msg}"
            )
        if not data:
            # Этот чанк пустой — HL не имеет данных в [cur_start, cur_end).
            # Это нормально для retention-edge: продолжаем — может в более
            # свежих чанках данные есть. Но если ВЕСЬ диапазон пустой —
            # raise в конце.
            cur_end = cur_start
            continue
        parsed = [_parse_hl_kline_row(r) for r in data
                  if isinstance(r, dict) and "t" in r]
        parsed = [p for p in parsed if start_ms <= p["ts"] < cur_end]
        parsed.sort(key=lambda x: x["ts"])
        if not parsed:
            cur_end = cur_start
            continue
        out = parsed + out
        cur_end = parsed[0]["ts"]
        if len(data) < HL_KLINE_LIMIT:
            # HL вернул меньше лимита → дальше история закончилась
            break

    return out


def fetch_klines(
    symbol: str,
    tf: str,
    days: int,
    *,
    cache: bool = True,
    session: requests.Session | None = None,
    category: str = DEFAULT_CATEGORY,
) -> list[dict]:
    """
    Klines за последние `days` дней. Bybit → Binance Futures fallback.
    Кеш: bt_cache/{symbol}_klines_{tf}.jsonl. Cache учитывает диапазон.
    Возвращает list[dict] oldest→newest.
    """
    path = _cache_path(symbol, "klines", tf)
    start_ms = _ago_ms(days)
    end_ms   = _now_ms()

    if cache and path.exists():
        cached = _read_cache(path)
        if cached and cached[0]["ts"] <= start_ms:
            return [k for k in cached if start_ms <= k["ts"] <= end_ms]

    # 1) Try Bybit (prod-primary)
    out: list[dict] = []
    _bybit_err: Exception | None = None
    try:
        out = _fetch_klines_bybit(symbol, tf, start_ms, end_ms,
                                  session=session, category=category)
    except RuntimeError as e:
        # 403 / network — fall through
        _bybit_err = e

    # 2) Fallback to Binance Futures
    _binance_err: Exception | None = None
    if not out:
        try:
            out = _fetch_klines_binance(symbol, tf, start_ms, end_ms,
                                        session=session)
        except RuntimeError as e:
            _binance_err = e

    # 3) Fallback to Hyperliquid (geo-friendly, не блокируется в US/EU)
    _hl_err: Exception | None = None
    _hl_attempted = False
    if not out:
        _hl_attempted = True
        try:
            out = _fetch_klines_hl(symbol, tf, start_ms, end_ms,
                                   session=session)
        except RuntimeError as e:
            _hl_err = e

    if not out and (_bybit_err or _binance_err or _hl_err):
        if _hl_err is not None:
            hl_msg = str(_hl_err)
        elif _hl_attempted:
            hl_msg = (f"no data in range (retention exceeded for {tf}m? "
                      f"5m=~17d, 15m=~50d)")
        else:
            hl_msg = "not attempted"
        raise RuntimeError(
            f"All exchanges failed for {symbol} tf={tf}. "
            f"Bybit: {_bybit_err}. "
            f"Binance: {_binance_err}. "
            f"Hyperliquid: {hl_msg}."
        )

    # Дедупликация по ts
    seen: set[int] = set()
    deduped: list[dict] = []
    for r in out:
        if r["ts"] in seen:
            continue
        seen.add(r["ts"])
        deduped.append(r)
    deduped.sort(key=lambda x: x["ts"])

    if cache and deduped:
        _write_cache(path, deduped)
    return deduped


# ─── Funding rate ─────────────────────────────────────────────────────────


def _parse_funding_row(row: dict) -> dict:
    """
    Bybit funding row: {symbol, fundingRate, fundingRateTimestamp}.
    """
    return {
        "ts":      int(row["fundingRateTimestamp"]),
        "funding": float(row["fundingRate"]),
    }


def fetch_funding(
    symbol: str,
    days: int,
    *,
    cache: bool = True,
    session: requests.Session | None = None,
    category: str = DEFAULT_CATEGORY,
) -> list[dict]:
    """
    Funding rate history. Bybit обновляет каждые 8 часов → ~3 точки в день.
    """
    path = _cache_path(symbol, "funding")
    start_ms = _ago_ms(days)
    end_ms   = _now_ms()

    if cache and path.exists():
        cached = _read_cache(path)
        if cached and cached[0]["ts"] <= start_ms:
            return [f for f in cached if start_ms <= f["ts"] <= end_ms]

    out: list[dict] = []
    cur_end = end_ms

    while cur_end > start_ms:
        params = {
            "category": category, "symbol": symbol,
            "startTime": start_ms, "endTime": cur_end,
            "limit": FUNDING_MAX_LIMIT,
        }
        data = _request_with_retry(
            f"{BYBIT_BASE}/v5/market/funding/history", params, session=session)
        rows = (data.get("result") or {}).get("list") or []
        if not rows:
            break
        parsed = [_parse_funding_row(r) for r in rows]
        parsed.sort(key=lambda x: x["ts"])
        parsed = [p for p in parsed if start_ms <= p["ts"] < cur_end]
        if not parsed:
            break
        out = parsed + out
        cur_end = parsed[0]["ts"]
        if len(rows) < FUNDING_MAX_LIMIT:
            break

    seen_ts: set[int] = set()
    deduped: list[dict] = []
    for r in out:
        if r["ts"] in seen_ts:
            continue
        seen_ts.add(r["ts"])
        deduped.append(r)
    deduped.sort(key=lambda x: x["ts"])

    if cache:
        _write_cache(path, deduped)
    return deduped


# ─── Open Interest ─────────────────────────────────────────────────────────


def _parse_oi_row(row: dict) -> dict:
    """Bybit OI row: {openInterest (str), timestamp (str-ms)}."""
    return {
        "ts": int(row["timestamp"]),
        "oi": float(row["openInterest"]),
    }


def fetch_open_interest(
    symbol: str,
    days: int,
    *,
    interval: str = "1h",
    cache: bool = True,
    session: requests.Session | None = None,
    category: str = DEFAULT_CATEGORY,
) -> list[dict]:
    """
    Open Interest history. Bybit поддерживает интервалы: 5min, 15min, 30min,
    1h, 4h, 1d. По умолчанию — 1h.
    """
    path = _cache_path(symbol, f"oi_{interval}")
    start_ms = _ago_ms(days)
    end_ms   = _now_ms()

    if cache and path.exists():
        cached = _read_cache(path)
        if cached and cached[0]["ts"] <= start_ms:
            return [o for o in cached if start_ms <= o["ts"] <= end_ms]

    out: list[dict] = []
    cursor: str | None = None
    cur_end = end_ms

    # Bybit OI API не поддерживает start/end совместно с cursor одинаково;
    # пагинируем через startTime + ограниченное окно
    while cur_end > start_ms:
        params = {
            "category": category, "symbol": symbol,
            "intervalTime": interval, "limit": OI_MAX_LIMIT,
            "startTime": start_ms, "endTime": cur_end,
        }
        if cursor:
            params["cursor"] = cursor
        data = _request_with_retry(
            f"{BYBIT_BASE}/v5/market/open-interest", params, session=session)
        result = data.get("result") or {}
        rows = result.get("list") or []
        if not rows:
            break
        parsed = [_parse_oi_row(r) for r in rows]
        parsed.sort(key=lambda x: x["ts"])
        parsed = [p for p in parsed if start_ms <= p["ts"] < cur_end]
        if not parsed:
            break
        out = parsed + out
        cur_end = parsed[0]["ts"]
        cursor = result.get("nextPageCursor")
        if not cursor and len(rows) < OI_MAX_LIMIT:
            break

    seen_ts: set[int] = set()
    deduped: list[dict] = []
    for r in out:
        if r["ts"] in seen_ts:
            continue
        seen_ts.add(r["ts"])
        deduped.append(r)
    deduped.sort(key=lambda x: x["ts"])

    if cache:
        _write_cache(path, deduped)
    return deduped


# ─── All-in-one ───────────────────────────────────────────────────────────


def fetch_all(
    symbol: str,
    days: int,
    *,
    tfs: list[str] | None = None,
    fetch_oi_data: bool = True,
    fetch_funding_data: bool = True,
    oi_interval: str = "1h",
    cache: bool = True,
    session: requests.Session | None = None,
    progress: Callable[[str], None] | None = None,
) -> dict:
    """
    Тянет всё необходимое для бектеста: klines (multi-TF), funding, OI.
    Возвращает:
        {
            "symbol": str, "days": int,
            "klines": {tf: list[dict]},
            "funding": list[dict],
            "oi": list[dict],
        }
    """
    tfs = tfs or ["5", "15", "60", "240"]
    out = {"symbol": symbol, "days": days, "klines": {}}
    for tf in tfs:
        if progress:
            progress(f"klines {symbol} {tf}")
        out["klines"][tf] = fetch_klines(symbol, tf, days,
                                         cache=cache, session=session)
    if fetch_funding_data:
        if progress:
            progress(f"funding {symbol}")
        out["funding"] = fetch_funding(symbol, days,
                                       cache=cache, session=session)
    else:
        out["funding"] = []
    if fetch_oi_data:
        if progress:
            progress(f"oi {symbol} {oi_interval}")
        out["oi"] = fetch_open_interest(symbol, days, interval=oi_interval,
                                        cache=cache, session=session)
    else:
        out["oi"] = []
    return out


# ─── CLI ──────────────────────────────────────────────────────────────────


def _cli() -> int:
    p = argparse.ArgumentParser(
        prog="bt_data",
        description="Скачать историю Bybit для бектеста",
    )
    p.add_argument("symbol", help="Например: BTCUSDT")
    p.add_argument("days", type=int, help="За сколько дней назад")
    p.add_argument("--tfs", default="5,15,60,240",
                   help="TF через запятую (default: 5,15,60,240)")
    p.add_argument("--no-cache", action="store_true",
                   help="Игнорировать кеш и заново скачать")
    p.add_argument("--no-funding", action="store_true")
    p.add_argument("--no-oi", action="store_true")
    p.add_argument("--oi-interval", default="1h",
                   choices=["5min", "15min", "30min", "1h", "4h", "1d"])
    args = p.parse_args()

    tfs = [t.strip() for t in args.tfs.split(",") if t.strip()]
    started = time.time()

    def _log(msg: str) -> None:
        elapsed = time.time() - started
        print(f"[{elapsed:6.1f}s] {msg}")

    data = fetch_all(
        args.symbol, args.days,
        tfs=tfs,
        fetch_funding_data=not args.no_funding,
        fetch_oi_data=not args.no_oi,
        oi_interval=args.oi_interval,
        cache=not args.no_cache,
        progress=_log,
    )

    print()
    print(f"=== {args.symbol} ({args.days}d) ===")
    for tf, rows in data["klines"].items():
        if rows:
            first = datetime.fromtimestamp(rows[0]["ts"] / 1000, tz=timezone.utc)
            last  = datetime.fromtimestamp(rows[-1]["ts"] / 1000, tz=timezone.utc)
            print(f"  klines {tf:>3}: {len(rows):>6} баров  "
                  f"({first:%Y-%m-%d %H:%M} → {last:%Y-%m-%d %H:%M})")
        else:
            print(f"  klines {tf:>3}: пусто")
    print(f"  funding:   {len(data['funding']):>6} точек")
    print(f"  oi:        {len(data['oi']):>6} точек")
    print(f"  cache dir: {CACHE_DIR.resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
