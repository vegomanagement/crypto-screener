"""Тесты UI routes (/ui, /api/symbols, /api/klines)."""

from __future__ import annotations

import importlib
from unittest.mock import patch

import screener


def _client():
    """Возвращает Flask test client (изолированный)."""
    importlib.reload(screener)
    screener.app.config["TESTING"] = True
    return screener.app.test_client()


# ─── /ui ──────────────────────────────────────────────────────────────────


def test_ui_route_returns_html():
    c = _client()
    r = c.get("/ui")
    assert r.status_code == 200
    assert r.content_type.startswith("text/html")


def test_ui_html_contains_chart_lib_cdn():
    """HTML должен подключать Lightweight Charts через CDN."""
    c = _client()
    r = c.get("/ui")
    body = r.get_data(as_text=True)
    assert "lightweight-charts" in body
    assert "unpkg.com" in body or "cdn" in body.lower()


def test_ui_html_has_symbol_dropdown():
    c = _client()
    r = c.get("/ui")
    body = r.get_data(as_text=True)
    assert 'id="symbol"' in body
    assert "BTCUSDT" in body
    assert "ETHUSDT" in body


def test_ui_html_has_indicators_panel():
    c = _client()
    r = c.get("/ui")
    body = r.get_data(as_text=True)
    for ind in ("EMA 20", "EMA 50", "RSI", "ATR", "Price"):
        assert ind in body, f"{ind} not in UI body"


def test_ui_html_has_interval_dropdown():
    c = _client()
    r = c.get("/ui")
    body = r.get_data(as_text=True)
    assert 'id="interval"' in body
    for iv in ("5m", "15m", "1H", "4H", "1D"):
        assert iv in body


# ─── /api/symbols ─────────────────────────────────────────────────────────


def test_api_symbols_returns_json():
    c = _client()
    r = c.get("/api/symbols")
    assert r.status_code == 200
    data = r.get_json()
    assert "symbols" in data
    assert isinstance(data["symbols"], list)


# ─── /api/klines ──────────────────────────────────────────────────────────


def test_api_klines_invalid_symbol_400():
    c = _client()
    r = c.get("/api/klines?symbol=BTC&interval=60")
    assert r.status_code == 400
    assert "error" in r.get_json()


def test_api_klines_empty_symbol_400():
    c = _client()
    r = c.get("/api/klines?symbol=&interval=60")
    assert r.status_code == 400


def test_api_klines_success_with_mocked_data():
    """Мокаем _klines и проверяем что JSON собирается корректно."""
    c = _client()
    fake_rows = [
        {"o": 100, "h": 101, "l": 99, "c": 100.5, "v": 12.0}
        for _ in range(10)
    ]
    with patch.object(screener, "_klines", return_value=fake_rows):
        r = c.get("/api/klines?symbol=BTCUSDT&interval=60&limit=10")
    assert r.status_code == 200
    data = r.get_json()
    assert data["symbol"] == "BTCUSDT"
    assert data["interval"] == "60"
    assert len(data["klines"]) == 10
    for k in data["klines"]:
        assert "ts" in k and "o" in k and "c" in k
        assert isinstance(k["ts"], int)
    # Timestamps должны быть восходящими (oldest → newest)
    tss = [k["ts"] for k in data["klines"]]
    assert tss == sorted(tss)


def test_api_klines_clamps_limit():
    """limit=9999 должен быть clamp'нут до 500."""
    c = _client()
    fake_rows = [
        {"o": 100, "h": 101, "l": 99, "c": 100.5, "v": 0}
        for _ in range(5)
    ]
    with patch.object(screener, "_klines",
                      return_value=fake_rows) as mock_kl:
        c.get("/api/klines?symbol=BTCUSDT&interval=60&limit=9999")
    # Должен быть вызван с limit=500
    assert mock_kl.call_args[0][2] == 500


def test_api_klines_default_interval_60():
    """Если interval не указан — должен быть 60 (1h)."""
    c = _client()
    fake_rows = [{"o": 100, "h": 101, "l": 99, "c": 100, "v": 0}]
    with patch.object(screener, "_klines", return_value=fake_rows):
        r = c.get("/api/klines?symbol=BTCUSDT")
    data = r.get_json()
    assert data["interval"] == "60"


def test_api_klines_handles_klines_exception():
    """Если _klines падает — 500 с error message."""
    c = _client()
    with patch.object(screener, "_klines",
                      side_effect=RuntimeError("all exchanges failed")):
        r = c.get("/api/klines?symbol=BTCUSDT&interval=60")
    assert r.status_code == 500
    assert "error" in r.get_json()


def test_api_klines_uppercases_symbol():
    """btcusdt → BTCUSDT."""
    c = _client()
    fake_rows = [{"o": 100, "h": 101, "l": 99, "c": 100, "v": 0}]
    with patch.object(screener, "_klines", return_value=fake_rows):
        r = c.get("/api/klines?symbol=btcusdt&interval=60")
    data = r.get_json()
    assert data["symbol"] == "BTCUSDT"
