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


# ─── /api/zones ───────────────────────────────────────────────────────────


def test_api_zones_invalid_symbol_400():
    c = _client()
    r = c.get("/api/zones?symbol=BTC")
    assert r.status_code == 400


def test_api_zones_empty_klines_returns_empty_zones(monkeypatch):
    c = _client()
    monkeypatch.setattr(screener, "_klines", lambda *a, **kw: [])
    r = c.get("/api/zones?symbol=BTCUSDT&interval=60")
    data = r.get_json()
    assert r.status_code == 200
    assert data["zones"]["ob"] == []
    assert data["zones"]["fvg"] == []


def test_api_zones_detects_fvg_with_synthetic_data(monkeypatch):
    """FVG: 3-candle gap (c0.l > c2.h) → bull FVG."""
    c = _client()
    rows = [{"o": 100, "h": 101, "l": 99, "c": 100, "v": 0}
            for _ in range(50)]
    rows[-3] = {"o": 100, "h": 101, "l": 99, "c": 101, "v": 0}
    rows[-2] = {"o": 101, "h": 102, "l": 100, "c": 102, "v": 0}
    rows[-1] = {"o": 103, "h": 104, "l": 103, "c": 103.5, "v": 0}
    monkeypatch.setattr(screener, "_klines", lambda *a, **kw: rows)
    r = c.get("/api/zones?symbol=BTCUSDT&interval=60&limit=10")
    data = r.get_json()
    assert r.status_code == 200
    fvgs = data["zones"]["fvg"]
    assert len(fvgs) >= 1
    bull_fvg = [f for f in fvgs if f["direction"] == "bull"]
    assert bull_fvg, "no bull FVG detected"


def test_api_zones_clamps_limit(monkeypatch):
    c = _client()
    monkeypatch.setattr(screener, "_klines", lambda *a, **kw: [])
    r = c.get("/api/zones?symbol=BTCUSDT&limit=9999")
    assert r.status_code == 200


def test_api_zones_handles_klines_exception(monkeypatch):
    c = _client()

    def _raise(*a, **kw):
        raise RuntimeError("API")
    monkeypatch.setattr(screener, "_klines", _raise)
    r = c.get("/api/zones?symbol=BTCUSDT")
    assert r.status_code == 500


def test_ui_has_zones_toggle_and_count():
    c = _client()
    r = c.get("/ui")
    body = r.get_data(as_text=True)
    assert "SMC Zones" in body
    assert 'id="toggleZones"' in body
    assert 'id="zonesCount"' in body
    assert "Show OB / FVG / MB / BB zones" in body


def test_ui_has_loadZones_js():
    c = _client()
    r = c.get("/ui")
    body = r.get_data(as_text=True)
    assert "loadZones" in body
    assert "/api/zones" in body
    assert "createPriceLine" in body


# ─── /api/analysis ────────────────────────────────────────────────────────


def test_api_analysis_invalid_symbol_400():
    c = _client()
    r = c.get("/api/analysis?symbol=BTC")
    assert r.status_code == 400


def test_api_analysis_success_with_mocked_market():
    c = _client()
    fake_market = {
        "price": 67500.0, "change_24h": 1.25,
        "bybit": {"funding": 0.0001},
        "cvd": {"trend": "up"},
        "ema_biases": {"1h": "bull", "4h": "bull", "1d": "bear"},
        "indicators": {"rsi": 55, "atr_pct": 0.5,
                       "macd": {"trend": "bull"}},
        "vp": {"poc": 67000},
        "macro": {},
    }
    with patch.object(screener, "fetch_market", return_value=fake_market):
        with patch.object(screener, "compute_confluence_score",
                          return_value=(72, ["CVD ✅", "MTF bull"])):
            r = c.get("/api/analysis?symbol=BTCUSDT&force=true")
    assert r.status_code == 200
    data = r.get_json()
    assert data["symbol"] == "BTCUSDT"
    assert data["confluence"] == 72
    assert "brief" in data
    assert "brief_raw" in data
    assert isinstance(data["confluence_factors"], list)


def test_api_analysis_caches_response():
    """Второй запрос (без force) возвращает cached=True."""
    c = _client()
    fake_market = {"price": 100, "bybit": {}, "cvd": {},
                   "ema_biases": {}, "indicators": {},
                   "vp": {}, "macro": {}}
    with patch.object(screener, "fetch_market",
                      return_value=fake_market) as mock_fm:
        with patch.object(screener, "compute_confluence_score",
                          return_value=(50, [])):
            r1 = c.get("/api/analysis?symbol=BTCUSDT")
            r2 = c.get("/api/analysis?symbol=BTCUSDT")
    assert r1.get_json()["cached"] is False
    assert r2.get_json()["cached"] is True
    assert mock_fm.call_count == 1


def test_api_analysis_force_bypasses_cache():
    c = _client()
    fake_market = {"price": 100, "bybit": {}, "cvd": {},
                   "ema_biases": {}, "indicators": {},
                   "vp": {}, "macro": {}}
    with patch.object(screener, "fetch_market",
                      return_value=fake_market) as mock_fm:
        with patch.object(screener, "compute_confluence_score",
                          return_value=(50, [])):
            c.get("/api/analysis?symbol=BTCUSDT")
            c.get("/api/analysis?symbol=BTCUSDT&force=true")
    assert mock_fm.call_count == 2


def test_api_analysis_handles_empty_market():
    c = _client()
    with patch.object(screener, "fetch_market", return_value=None):
        r = c.get("/api/analysis?symbol=BTCUSDT&force=true")
    assert r.status_code == 502


def test_api_analysis_handles_exception():
    c = _client()
    with patch.object(screener, "fetch_market",
                      side_effect=RuntimeError("API down")):
        r = c.get("/api/analysis?symbol=BTCUSDT&force=true")
    assert r.status_code == 500


def test_ui_has_bottom_analysis_panel():
    c = _client()
    r = c.get("/ui")
    body = r.get_data(as_text=True)
    assert "Engine Analysis" in body
    assert 'id="bottomAnalysis"' in body
    assert 'id="confluencePanel"' in body
    assert 'id="briefPanel"' in body


def test_ui_has_loadAnalysis_js():
    c = _client()
    r = c.get("/ui")
    body = r.get_data(as_text=True)
    assert "loadAnalysis" in body
    assert "/api/analysis" in body


# ─── Drawing tools (H-Line, Clear) ────────────────────────────────────────


def test_ui_has_drawing_tool_buttons():
    c = _client()
    r = c.get("/ui")
    body = r.get_data(as_text=True)
    assert 'id="btnHLine"' in body
    assert 'id="btnClearDrawings"' in body
    assert "H-Line" in body
    assert "Clear" in body


def test_ui_has_drawing_js_functions():
    c = _client()
    r = c.get("/ui")
    body = r.get_data(as_text=True)
    # ключевые функции
    for fn in ("toggleTool", "addHLine", "clearDrawings",
               "loadDrawingsFromStorage", "saveDrawingsToStorage"):
        assert fn in body, f"{fn} not in UI body"
    # subscribeClick для drawing
    assert "subscribeClick" in body
    # localStorage используется
    assert "localStorage" in body


def test_ui_drawing_mode_css_class():
    c = _client()
    r = c.get("/ui")
    body = r.get_data(as_text=True)
    assert "drawing-mode" in body   # для cursor crosshair
    assert "tool-btn" in body
    assert "tool-btn.active" in body


def test_ui_drawings_state_key_pattern():
    """JS должен использовать ключ типа screener_drawings_BTCUSDT."""
    c = _client()
    r = c.get("/ui")
    body = r.get_data(as_text=True)
    assert "screener_drawings_" in body

# ─── Favorites system ────────────────────────────────────────────────────


def test_ui_has_favorites_js_helpers():
    c = _client()
    r = c.get("/ui")
    body = r.get_data(as_text=True)
    for fn in ("getFavorites", "saveFavorites",
               "isFavorite", "toggleFavorite"):
        assert fn in body, f"{fn} not in UI body"


def test_ui_favorites_localStorage_key():
    c = _client()
    r = c.get("/ui")
    body = r.get_data(as_text=True)
    assert "FAVORITES_KEY" in body
    assert "screener_favorites" in body


def test_ui_watchlist_has_star_column():
    c = _client()
    r = c.get("/ui")
    body = r.get_data(as_text=True)
    assert "watchlist-star" in body
    assert "data-fav-symbol" in body
    # star emoji в коде
    assert "⭐" in body
    assert "☆" in body


def test_ui_watchlist_sort_favorites_first():
    """В JS должна быть сортировка favorites first."""
    c = _client()
    r = c.get("/ui")
    body = r.get_data(as_text=True)
    # sort by favorite status
    assert "favs.has" in body
    assert "favorites first" in body or "favs first" in body or "af !== bf" in body


def test_api_klines_uppercases_symbol():
    """btcusdt → BTCUSDT."""
    c = _client()
    fake_rows = [{"o": 100, "h": 101, "l": 99, "c": 100, "v": 0}]
    with patch.object(screener, "_klines", return_value=fake_rows):
        r = c.get("/api/klines?symbol=btcusdt&interval=60")
    data = r.get_json()
    assert data["symbol"] == "BTCUSDT"


# ─── /api/market ──────────────────────────────────────────────────────────


def test_api_market_invalid_symbol_400():
    c = _client()
    r = c.get("/api/market?symbol=BTC")
    assert r.status_code == 400


def test_api_market_success_with_mock():
    """Полный fake market dict → compact JSON с правильной shape."""
    c = _client()
    fake_market = {
        "price": 67500.0,
        "change_24h": 1.25,
        "bybit": {"funding": 0.0001, "vol_24h": 1500000, "open_interest": 250000},
        "hl": {"funding": 0.00008},
        "cvd": {"trend": "up", "divergence": False, "delta_5": 25000},
        "vp": {"poc": 67000, "vah": 68000, "val": 66000},
        "ema_biases": {"1h": "bull", "4h": "bull", "1d": "bear"},
        "vwap": 67200,
        "indicators": {"rsi": 55, "macd": "bull", "atr": 350, "atr_pct": 0.5},
        "turtle_1h": "MID", "turtle_4h": "HIGH",
        "liquidations": {"long_24h": 1500000, "short_24h": 800000},
        "btc_corr": 1.0,
    }
    with patch.object(screener, "fetch_market", return_value=fake_market):
        r = c.get("/api/market?symbol=BTCUSDT")
    assert r.status_code == 200
    data = r.get_json()
    assert data["symbol"] == "BTCUSDT"
    assert data["price"] == 67500.0
    assert data["cvd"]["trend"] == "up"
    assert data["ema_bias"]["1h"] == "bull"
    assert data["funding"]["bybit"] == 0.0001
    assert data["vp"]["poc"] == 67000
    assert data["vwap"] == 67200
    assert data["indicators"]["rsi"] == 55
    assert data["turtle_1h"] == "MID"
    assert data["liquidations"]["long_24h"] == 1500000


def test_api_market_handles_missing_fields():
    """Если fields отсутствуют — возвращаются None, без падений."""
    c = _client()
    with patch.object(screener, "fetch_market", return_value={"price": 100}):
        r = c.get("/api/market?symbol=BTCUSDT")
    assert r.status_code == 200
    data = r.get_json()
    assert data["price"] == 100
    assert data["cvd"]["trend"] is None
    assert data["ema_bias"]["1h"] is None


def test_api_market_handles_fetch_exception():
    """Если fetch_market падает — 500."""
    c = _client()
    with patch.object(screener, "fetch_market",
                      side_effect=RuntimeError("API down")):
        r = c.get("/api/market?symbol=BTCUSDT")
    assert r.status_code == 500


def test_api_market_handles_empty_market():
    """fetch_market returns None/empty → 502."""
    c = _client()
    with patch.object(screener, "fetch_market", return_value=None):
        r = c.get("/api/market?symbol=BTCUSDT")
    assert r.status_code == 502


def test_api_market_does_not_include_klines():
    """JSON ответ не должен содержать heavy klines поле."""
    c = _client()
    fake_market = {
        "price": 100, "_klines": {"60": [{"o": 1}] * 200},
    }
    with patch.object(screener, "fetch_market", return_value=fake_market):
        r = c.get("/api/market?symbol=BTCUSDT")
    data = r.get_json()
    # Klines не должно быть в compact ответе
    assert "_klines" not in data
    assert "klines" not in data


# ─── /ui updates: Engine Market panel ────────────────────────────────────


def test_ui_html_has_engine_market_panel():
    c = _client()
    r = c.get("/ui")
    body = r.get_data(as_text=True)
    assert "Engine Market" in body
    for ind_name in ("CVD trend", "MTF EMA bias", "Funding",
                     "Open Interest", "VWAP", "VP POC",
                     "Turtle 1H", "Liq long"):
        assert ind_name in body, f"{ind_name} not in UI body"


def test_ui_html_has_loadMarket_js():
    c = _client()
    r = c.get("/ui")
    body = r.get_data(as_text=True)
    assert "loadMarket" in body
    assert "/api/market" in body


# ─── /api/prices ──────────────────────────────────────────────────────────


def test_api_prices_default_returns_watchlist():
    """Без параметров — возвращает UI_DEFAULT_WATCHLIST."""
    c = _client()
    fake_prices = {
        sym: {"price": 100.0, "change_24h": 1.5, "vol_24h": 1000}
        for sym in screener.UI_DEFAULT_WATCHLIST
    }
    with patch.object(screener, "_fetch_bulk_prices_bybit",
                      return_value=fake_prices):
        r = c.get("/api/prices")
    assert r.status_code == 200
    data = r.get_json()
    assert "prices" in data
    assert len(data["prices"]) == len(screener.UI_DEFAULT_WATCHLIST)
    for item in data["prices"]:
        assert "symbol" in item
        assert "price" in item
        assert "change_24h" in item


def test_api_prices_custom_symbols_sorted_by_change():
    """С параметром symbols — фильтр и сортировка по abs change."""
    c = _client()
    fake = {
        "BTCUSDT": {"price": 100, "change_24h": 2.5, "vol_24h": 0},
        "ETHUSDT": {"price": 50, "change_24h": -3.0, "vol_24h": 0},
    }
    with patch.object(screener, "_fetch_bulk_prices_bybit",
                      return_value=fake):
        r = c.get("/api/prices?symbols=BTCUSDT,ETHUSDT")
    data = r.get_json()
    syms = [p["symbol"] for p in data["prices"]]
    # ETH с -3.0% должен идти первым (abs change бóльше)
    assert syms[0] == "ETHUSDT"
    assert syms[1] == "BTCUSDT"


def test_api_prices_invalid_symbols_filtered():
    """Не-USDT символы отфильтровываются."""
    c = _client()
    with patch.object(screener, "_fetch_bulk_prices_bybit",
                      return_value={}):
        with patch.object(screener, "_fetch_bulk_prices_binance",
                          return_value={}):
            r = c.get("/api/prices?symbols=BTC,ETH,SOLUSDT")
    assert r.status_code == 200
    data = r.get_json()
    # Только SOLUSDT мог попасть в запрос — но fake возвращает пусто
    assert data["prices"] == []


def test_api_prices_uses_binance_fallback_when_bybit_misses():
    """Если Bybit вернул не все символы — Binance fallback на missing."""
    c = _client()
    with patch.object(
        screener, "_fetch_bulk_prices_bybit",
        return_value={"BTCUSDT": {"price": 100, "change_24h": 1, "vol_24h": 0}},
    ) as bybit_mock:
        with patch.object(
            screener, "_fetch_bulk_prices_binance",
            return_value={"ETHUSDT": {"price": 50, "change_24h": 2, "vol_24h": 0}},
        ) as binance_mock:
            r = c.get("/api/prices?symbols=BTCUSDT,ETHUSDT")
    data = r.get_json()
    syms = {p["symbol"] for p in data["prices"]}
    assert syms == {"BTCUSDT", "ETHUSDT"}
    # Binance был вызван ТОЛЬКО для отсутствующего ETHUSDT
    assert binance_mock.called
    binance_args = binance_mock.call_args[0][0]
    assert binance_args == ["ETHUSDT"]
    assert bybit_mock.called


def test_api_prices_empty_symbols_param_returns_200():
    c = _client()
    with patch.object(screener, "_fetch_bulk_prices_bybit",
                      return_value={}):
        with patch.object(screener, "_fetch_bulk_prices_binance",
                          return_value={}):
            r = c.get("/api/prices?symbols=")
    # symbols=empty → fallback на UI_DEFAULT_WATCHLIST
    # mocks возвращают пусто → prices=[]
    assert r.status_code == 200


# ─── /ui updates: watchlist panel ────────────────────────────────────────


def test_ui_html_has_watchlist_panel():
    c = _client()
    r = c.get("/ui")
    body = r.get_data(as_text=True)
    assert "Watchlist" in body
    assert 'id="watchlist"' in body


def test_ui_html_has_loadWatchlist_js():
    c = _client()
    r = c.get("/ui")
    body = r.get_data(as_text=True)
    assert "loadWatchlist" in body
    assert "/api/prices" in body


# ─── /api/signals ─────────────────────────────────────────────────────────


def _setup_signals_db(monkeypatch, tmp_path):
    """Создаёт временную БД с signal_outcomes и подменяет DB_PATH."""
    import sqlite3 as _sq
    db_path = str(tmp_path / "signals.db")
    monkeypatch.setattr(screener, "DB_PATH", db_path)

    conn = _sq.connect(db_path)
    # Минимальная schema — нужна только для тестов api_signals
    conn.execute("""
        CREATE TABLE signal_outcomes(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            signal_id INTEGER, symbol TEXT, signal_type TEXT,
            direction TEXT, entry_price REAL, entry_ts TEXT,
            decision_json TEXT, verdict TEXT,
            entry_min REAL, entry_max REAL,
            sl REAL, tp1 REAL, tp2 REAL, tp3 REAL,
            rr1 REAL, rr2 REAL, rr3 REAL, confidence INTEGER,
            status TEXT, hit_level TEXT, hit_at TEXT,
            r_multiple REAL, expires_at TEXT, last_checked TEXT
        )
    """)
    conn.commit()
    return conn, db_path


def test_api_signals_returns_empty_when_no_data(monkeypatch, tmp_path):
    conn, _ = _setup_signals_db(monkeypatch, tmp_path)
    conn.close()
    c = _client()
    # После reload screener — DB_PATH пересоздаст путь. Подменим снова.
    monkeypatch.setattr(screener, "DB_PATH",
                        str(tmp_path / "signals.db"))
    r = c.get("/api/signals?symbol=BTCUSDT")
    assert r.status_code == 200
    data = r.get_json()
    assert data["symbol"] == "BTCUSDT"
    assert data["signals"] == []


def test_api_signals_returns_signals_from_db(monkeypatch, tmp_path):
    conn, db_path = _setup_signals_db(monkeypatch, tmp_path)
    from datetime import datetime as _dt, timezone as _tz
    now = _dt.now(_tz.utc)
    conn.execute(
        """INSERT INTO signal_outcomes
           (symbol, signal_type, direction, verdict,
            entry_price, entry_ts, sl, tp1, tp2, tp3,
            status, hit_level, hit_at, r_multiple, confidence)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        ("BTCUSDT", "OB_BULL", "long", "LONG",
         50000.0, now.isoformat(),
         49500.0, 50500.0, 51000.0, 52000.0,
         "tp1_hit", "TP1", now.isoformat(),
         1.5, 75),
    )
    conn.commit()
    conn.close()
    c = _client()
    monkeypatch.setattr(screener, "DB_PATH", db_path)
    r = c.get("/api/signals?symbol=BTCUSDT")
    data = r.get_json()
    assert len(data["signals"]) == 1
    s = data["signals"][0]
    assert s["direction"] == "long"
    assert s["signal_type"] == "OB_BULL"
    assert s["entry"] == 50000.0
    assert s["sl"] == 49500.0
    assert s["hit_level"] == "TP1"
    assert s["r_multiple"] == 1.5
    assert "ts" in s and isinstance(s["ts"], int)
    assert "hit_ts" in s and isinstance(s["hit_ts"], int)


def test_api_signals_filters_by_symbol(monkeypatch, tmp_path):
    """Сигналы других символов не возвращаются."""
    conn, db_path = _setup_signals_db(monkeypatch, tmp_path)
    from datetime import datetime as _dt, timezone as _tz
    now = _dt.now(_tz.utc)
    for sym in ("BTCUSDT", "ETHUSDT"):
        conn.execute(
            """INSERT INTO signal_outcomes
               (symbol, signal_type, direction, verdict,
                entry_price, entry_ts, status)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (sym, "OB_BULL", "long", "LONG",
             100.0, now.isoformat(), "open"),
        )
    conn.commit()
    conn.close()
    c = _client()
    monkeypatch.setattr(screener, "DB_PATH", db_path)
    r = c.get("/api/signals?symbol=BTCUSDT")
    data = r.get_json()
    assert len(data["signals"]) == 1


def test_api_signals_clamps_days_and_limit(monkeypatch, tmp_path):
    _, db_path = _setup_signals_db(monkeypatch, tmp_path)
    c = _client()
    monkeypatch.setattr(screener, "DB_PATH", db_path)
    # days=9999 → clamp на 90
    r = c.get("/api/signals?symbol=BTCUSDT&days=9999&limit=9999")
    data = r.get_json()
    assert data["days"] == 90


def test_api_signals_excludes_non_long_short(monkeypatch, tmp_path):
    """WAIT / SKIP verdict сигналы НЕ возвращаются."""
    conn, db_path = _setup_signals_db(monkeypatch, tmp_path)
    from datetime import datetime as _dt, timezone as _tz
    now = _dt.now(_tz.utc)
    for verdict in ("LONG", "WAIT", "SKIP", "SHORT"):
        conn.execute(
            """INSERT INTO signal_outcomes
               (symbol, signal_type, direction, verdict,
                entry_price, entry_ts, status)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            ("BTCUSDT", "OB_BULL", "long", verdict,
             100.0, now.isoformat(), "open"),
        )
    conn.commit()
    conn.close()
    c = _client()
    monkeypatch.setattr(screener, "DB_PATH", db_path)
    r = c.get("/api/signals?symbol=BTCUSDT")
    data = r.get_json()
    verdicts = {s["verdict"] for s in data["signals"]}
    assert verdicts == {"LONG", "SHORT"}


def test_api_signals_handles_missing_db(monkeypatch, tmp_path):
    """Если DB не существует — 200 + signals=[]."""
    monkeypatch.setattr(screener, "DB_PATH",
                        str(tmp_path / "nonexistent.db"))
    c = _client()
    monkeypatch.setattr(screener, "DB_PATH",
                        str(tmp_path / "nonexistent.db"))
    r = c.get("/api/signals?symbol=BTCUSDT")
    # Эндпойнт graceful: пустая БД → пустой список
    assert r.status_code == 200
    data = r.get_json()
    assert data["signals"] == []


def test_ui_has_signals_toggle():
    """UI содержит чекбокс toggleSignals и метку Signals."""
    c = _client()
    r = c.get("/ui")
    body = r.get_data(as_text=True)
    assert 'id="toggleSignals"' in body
    assert "Show entries / TP / SL markers" in body
    assert 'id="signalsCount"' in body


def test_ui_has_loadSignals_js():
    c = _client()
    r = c.get("/ui")
    body = r.get_data(as_text=True)
    assert "loadSignals" in body
    assert "/api/signals" in body
    assert "setMarkers" in body   # вызов LightweightCharts API
