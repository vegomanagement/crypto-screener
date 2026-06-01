"""
bt_data.py — historical data fetcher для бектеста.

Тянет klines (multi-TF), funding rate, open interest с публичных Bybit V5
endpoints (без аутентификации). Кеширует в bt_cache/{symbol}_{kind}.jsonl,
чтобы не делать повторные запросы.

API endpoints:
  • /v5/market/kline           — klines history (max 1000 per request)
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
CACHE_DIR           = Path("bt_cache")
KLINE_MAX_LIMIT     = 1000
FUNDING_MAX_LIMIT   = 200
OI_MAX_LIMIT        = 200
DEFAULT_CATEGORY    = "linear"  # USDT perpetual futures
REQUEST_TIMEOUT     = 15
RETRY_ATTEMPTS      = 3
RETRY_BACKOFF_SEC   = 2


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
) -> dict:
    """Bybit GET с retry на network errors / 5xx."""
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
    raise RuntimeError(f"Bybit request failed after {RETRY_ATTEMPTS} retries: "
                       f"{last_exc}")


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
    Klines за последние `days` дней для symbol на TF.
    Bybit отдаёт макс 1000 свечей за запрос — пагинируем назад.

    Кеш: bt_cache/{symbol}_klines_{tf}.jsonl.

    Возвращает list[dict] oldest→newest.
    """
    iv = tf_to_bybit_interval(tf)
    path = _cache_path(symbol, "klines", tf)
    if cache and path.exists():
        return _read_cache(path)

    start_ms = _ago_ms(days)
    end_ms   = _now_ms()
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
        # фильтр под окно (некоторые ответы могут переползать)
        parsed = [p for p in parsed if start_ms <= p["ts"] < cur_end]
        if not parsed:
            break
        out = parsed + out
        cur_end = parsed[0]["ts"]   # сдвигаем окно ещё дальше назад
        if len(rows) < bars_per_request:
            break

    # Дедупликация по ts (на случай пересечений окон)
    seen: set[int] = set()
    deduped: list[dict] = []
    for r in out:
        if r["ts"] in seen:
            continue
        seen.add(r["ts"])
        deduped.append(r)
    deduped.sort(key=lambda x: x["ts"])

    if cache:
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
    if cache and path.exists():
        return _read_cache(path)

    start_ms = _ago_ms(days)
    end_ms   = _now_ms()
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
    if cache and path.exists():
        return _read_cache(path)

    start_ms = _ago_ms(days)
    end_ms   = _now_ms()
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
