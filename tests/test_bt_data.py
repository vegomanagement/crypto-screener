"""Тесты bt_data.py — historical data fetcher (мок Bybit endpoints)."""

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import bt_data


# ─── Helpers / fixtures ───────────────────────────────────────────────────


FAKE_NOW_MS = None  # инициализируется в fixture


@pytest.fixture
def tmp_cache(monkeypatch, tmp_path):
    """
    Изолированный кеш и фиксированное «now» — чтобы фильтры по времени
    в фетчерах работали стабильно с тестовыми данными около 2026-06-02 00:00.
    """
    monkeypatch.setattr(bt_data, "CACHE_DIR", tmp_path / "cache")
    fake_now = _ms(2026, 6, 2, 0)
    monkeypatch.setattr(bt_data, "_now_ms", lambda: fake_now)
    yield tmp_path / "cache"


def _ms(year, month, day, hour=0, minute=0):
    from datetime import datetime, timezone
    return int(datetime(year, month, day, hour, minute,
                        tzinfo=timezone.utc).timestamp() * 1000)


def _kline_row(ts_ms, o, h, lo, c, v):
    """Bybit kline row format: [start, open, high, low, close, volume, turnover]."""
    return [str(ts_ms), str(o), str(h), str(lo), str(c), str(v), "0"]


def _mock_session(responses):
    """
    Возвращает session-like объект, у которого .get(url, params, timeout)
    возвращает последовательно из списка responses.
    """
    sess = MagicMock()
    iterator = iter(responses)

    def _get(url, params=None, timeout=None):
        resp = MagicMock()
        try:
            payload = next(iterator)
        except StopIteration:
            payload = {"result": {"list": []}}
        resp.json = lambda: payload
        resp.raise_for_status = lambda: None
        return resp

    sess.get = _get
    return sess


# ─── TF normalization ────────────────────────────────────────────────────


def test_tf_to_bybit_interval_aliases():
    assert bt_data.tf_to_bybit_interval("5") == "5"
    assert bt_data.tf_to_bybit_interval("1H") == "60"
    assert bt_data.tf_to_bybit_interval("4H") == "240"
    assert bt_data.tf_to_bybit_interval("1D") == "D"
    assert bt_data.tf_to_bybit_interval("15m") == "15"


def test_tf_to_minutes():
    assert bt_data.tf_to_minutes("5") == 5
    assert bt_data.tf_to_minutes("1H") == 60
    assert bt_data.tf_to_minutes("4H") == 240
    assert bt_data.tf_to_minutes("D") == 1440


# ─── Kline parsing ────────────────────────────────────────────────────────


def test_parse_kline_row():
    row = _kline_row(1700000000000, 100, 101, 99, 100.5, 12.3)
    parsed = bt_data._parse_kline_row(row)
    assert parsed == {"ts": 1700000000000, "o": 100.0, "h": 101.0,
                      "l": 99.0, "c": 100.5, "v": 12.3}


def test_fetch_klines_single_page(tmp_cache):
    """Один ответ < limit → стопится после первой итерации."""
    rows = [_kline_row(_ms(2026, 6, 1, 10) - i * 300_000, 100, 101, 99, 100, 1)
            for i in range(10)]  # 10 баров 5m
    sess = _mock_session([{"result": {"list": rows}}])
    result = bt_data.fetch_klines("BTCUSDT", "5", days=1,
                                  cache=False, session=sess)
    assert len(result) == 10
    # Sorted oldest→newest
    for i in range(len(result) - 1):
        assert result[i]["ts"] < result[i + 1]["ts"]


def test_fetch_klines_dedup_overlap(tmp_cache):
    """Если ответы пересекаются по ts — дубликаты убираются."""
    rows1 = [_kline_row(_ms(2026, 6, 1, 10) - i * 300_000, 100, 101, 99, 100, 1)
             for i in range(5)]
    # Второй ответ — те же первые 3 ts + новые более старые
    rows2 = rows1[:3] + [
        _kline_row(_ms(2026, 6, 1, 10) - i * 300_000, 100, 101, 99, 100, 1)
        for i in range(5, 10)
    ]
    sess = _mock_session([
        {"result": {"list": rows1}},
        {"result": {"list": rows2}},
        {"result": {"list": []}},  # стоп
    ])
    result = bt_data.fetch_klines("BTCUSDT", "5", days=2,
                                  cache=False, session=sess)
    seen_ts = [r["ts"] for r in result]
    assert len(seen_ts) == len(set(seen_ts)), "должно быть без дубликатов"


def test_fetch_klines_uses_cache_when_covers_range(tmp_cache):
    """Если кеш покрывает запрошенный диапазон — net не вызываем."""
    cache_path = bt_data._cache_path("BTCUSDT", "klines", "5")
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    # FAKE_NOW = 2026-06-02 00:00. days=1 → start ≥ 2026-06-01.
    # Кешируем 30-дневное окно (5 мая → 2 июня) — покрывает 1-day запрос.
    import datetime as _dt
    base = _dt.datetime(2026, 5, 3, 0, 0, tzinfo=_dt.timezone.utc)
    fake = [
        {"ts": int((base + _dt.timedelta(hours=12 * i)).timestamp() * 1000),
         "o": 100, "h": 101, "l": 99, "c": 100, "v": 1}
        for i in range(60)
    ]
    cache_path.write_text("\n".join(json.dumps(r) for r in fake))

    sess = MagicMock()
    sess.get.side_effect = AssertionError("net вызван при наличии кеша")
    result = bt_data.fetch_klines("BTCUSDT", "5", days=1,
                                  cache=True, session=sess)
    # Возвращены только бары с ts >= start (1 день назад)
    assert all(r["ts"] >= _ms(2026, 6, 1, 0) for r in result)


def test_fetch_klines_refetches_when_cache_too_short(tmp_cache):
    """
    Главный bug-fix: cache был 7d, но запрашиваем 30d → должны re-fetch,
    НЕ возвращать кеш.
    """
    cache_path = bt_data._cache_path("BTCUSDT", "klines", "5")
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    # Кешируем всего 1 день (29 мая → 1 июня)
    fake_cached = [
        {"ts": _ms(2026, 6, 1, h), "o": 100, "h": 101, "l": 99, "c": 100, "v": 1}
        for h in range(24)
    ]
    cache_path.write_text("\n".join(json.dumps(r) for r in fake_cached))

    # Запрашиваем 30 дней — кеш покрывает только 1 → должен re-fetch
    import datetime as _dt
    base2 = _dt.datetime(2026, 5, 3, 0, 0, tzinfo=_dt.timezone.utc)
    new_rows = [
        _kline_row(int((base2 + _dt.timedelta(hours=i)).timestamp() * 1000),
                   200, 201, 199, 200, 5)
        for i in range(50)
    ]
    sess = _mock_session([{"result": {"list": new_rows}}])
    result = bt_data.fetch_klines("BTCUSDT", "5", days=30,
                                  cache=True, session=sess)
    # Should NOT be old fake — re-fetched fresh
    assert len(result) > 0
    # И не равно старому кешу
    assert result != fake_cached


def test_fetch_klines_writes_cache(tmp_cache):
    rows = [_kline_row(_ms(2026, 6, 1, 10), 100, 101, 99, 100, 1)]
    sess = _mock_session([{"result": {"list": rows}}])
    bt_data.fetch_klines("BTCUSDT", "5", days=1, cache=True, session=sess)
    cache_path = bt_data._cache_path("BTCUSDT", "klines", "5")
    assert cache_path.exists()
    reloaded = bt_data._read_cache(cache_path)
    assert len(reloaded) == 1


def test_fetch_klines_empty_response(tmp_cache):
    sess = _mock_session([{"result": {"list": []}}])
    result = bt_data.fetch_klines("BTCUSDT", "5", days=1,
                                  cache=False, session=sess)
    assert result == []


# ─── Funding ──────────────────────────────────────────────────────────────


def test_parse_funding_row():
    row = {"symbol": "BTCUSDT", "fundingRate": "0.0001",
           "fundingRateTimestamp": "1700000000000"}
    parsed = bt_data._parse_funding_row(row)
    assert parsed == {"ts": 1700000000000, "funding": 0.0001}


def test_fetch_funding_basic(tmp_cache):
    rows = [
        {"symbol": "BTC", "fundingRate": "0.0001",
         "fundingRateTimestamp": str(_ms(2026, 6, 1, h * 8))}
        for h in range(3)
    ]
    sess = _mock_session([{"result": {"list": rows}}])
    result = bt_data.fetch_funding("BTCUSDT", days=1,
                                   cache=False, session=sess)
    assert len(result) == 3
    assert all("funding" in r for r in result)


# ─── Open Interest ────────────────────────────────────────────────────────


def test_parse_oi_row():
    row = {"openInterest": "12345.6", "timestamp": "1700000000000"}
    parsed = bt_data._parse_oi_row(row)
    assert parsed == {"ts": 1700000000000, "oi": 12345.6}


def test_fetch_oi_basic(tmp_cache):
    rows = [
        {"openInterest": str(1000 + i),
         "timestamp": str(_ms(2026, 6, 1, h))}
        for i, h in enumerate(range(24))
    ]
    sess = _mock_session([{"result": {"list": rows, "nextPageCursor": ""}}])
    result = bt_data.fetch_open_interest("BTCUSDT", days=1,
                                         cache=False, session=sess)
    assert len(result) == 24


# ─── Retry on failure ─────────────────────────────────────────────────────


def test_request_retry_on_network_error(monkeypatch):
    """Если первый запрос упал, retry через RETRY_BACKOFF_SEC."""
    import requests as _r
    call_count = {"n": 0}

    def _failing_get(url, params=None, timeout=None):
        call_count["n"] += 1
        if call_count["n"] < 2:
            raise _r.exceptions.ConnectionError("fake net down")
        m = MagicMock()
        m.json = lambda: {"result": {"list": []}}
        m.raise_for_status = lambda: None
        return m

    sess = MagicMock()
    sess.get = _failing_get
    # ускоряем retry, чтобы тест не тормозил
    monkeypatch.setattr(bt_data, "RETRY_BACKOFF_SEC", 0)

    res = bt_data._request_with_retry("http://x", {}, session=sess)
    assert res == {"result": {"list": []}}
    assert call_count["n"] == 2  # один fail + один успешный


def test_request_retry_exhausted(monkeypatch):
    import requests as _r

    def _always_fail(url, params=None, timeout=None):
        raise _r.exceptions.ConnectionError("always down")

    sess = MagicMock()
    sess.get = _always_fail
    monkeypatch.setattr(bt_data, "RETRY_BACKOFF_SEC", 0)

    with pytest.raises(RuntimeError, match="failed after"):
        bt_data._request_with_retry("http://x", {}, session=sess)


# ─── fetch_all ────────────────────────────────────────────────────────────


def test_fetch_all_collects_everything(tmp_cache):
    """fetch_all возвращает klines (per TF) + funding + oi."""
    # mock: 1 kline для каждого TF, 1 funding row, 1 oi row
    kline_resp = {"result": {"list": [
        _kline_row(_ms(2026, 6, 1, 10), 100, 101, 99, 100, 1)
    ]}}
    fund_resp = {"result": {"list": [
        {"symbol": "BTC", "fundingRate": "0.0001",
         "fundingRateTimestamp": str(_ms(2026, 6, 1, 8))}
    ]}}
    oi_resp = {"result": {"list": [
        {"openInterest": "1000", "timestamp": str(_ms(2026, 6, 1, 10))}
    ], "nextPageCursor": ""}}
    # 4 TF (5, 15, 60, 240) + 1 funding + 1 oi = 6 calls
    sess = _mock_session([
        kline_resp, kline_resp, kline_resp, kline_resp,
        fund_resp, oi_resp,
    ])
    data = bt_data.fetch_all("BTCUSDT", days=1, cache=False, session=sess)
    assert data["symbol"] == "BTCUSDT"
    assert set(data["klines"].keys()) == {"5", "15", "60", "240"}
    assert len(data["funding"]) == 1
    assert len(data["oi"]) == 1


def test_fetch_all_skip_funding_and_oi(tmp_cache):
    kline_resp = {"result": {"list": [
        _kline_row(_ms(2026, 6, 1, 10), 100, 101, 99, 100, 1)
    ]}}
    sess = _mock_session([kline_resp, kline_resp, kline_resp, kline_resp])
    data = bt_data.fetch_all("BTCUSDT", days=1,
                             fetch_oi_data=False, fetch_funding_data=False,
                             cache=False, session=sess)
    assert data["funding"] == []
    assert data["oi"] == []


# ─── Cache helpers ────────────────────────────────────────────────────────


def test_cache_round_trip(tmp_cache):
    path = bt_data._cache_path("BTCUSDT", "test")
    rows = [{"ts": 1, "v": "a"}, {"ts": 2, "v": "b"}]
    bt_data._write_cache(path, rows)
    assert bt_data._read_cache(path) == rows


def test_read_cache_missing_returns_empty(tmp_cache):
    assert bt_data._read_cache(Path("/tmp/nonexistent_xyz_123.jsonl")) == []
