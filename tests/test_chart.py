"""Tests for chart.py — PNG signal chart renderer."""

import struct

import pytest

from chart import render_signal_chart


PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


# ─── Fixtures ─────────────────────────────────────────────────────────────


def _make_klines(n: int = 120, start_price: float = 42_000.0,
                 drift: float = 5.0, amp: float = 80.0) -> list:
    """Synthesize OHLCV bars with mild trend + noise."""
    import math
    klines = []
    price = start_price
    for i in range(n):
        # Deterministic-ish wiggle so test output is reproducible
        wiggle = math.sin(i / 7) * amp + math.cos(i / 13) * (amp / 2)
        o = price
        c = price + drift + wiggle * 0.05
        h = max(o, c) + abs(wiggle) * 0.3
        low = min(o, c) - abs(wiggle) * 0.3
        v = 100 + abs(wiggle) * 2
        klines.append({"o": o, "h": h, "l": low, "c": c, "v": v})
        price = c
    return klines


def _decision(**overrides) -> dict:
    base = {
        "verdict":    "LONG",
        "direction":  "long",
        "entry":      {"min": 42_400.0, "max": 42_560.0},
        "sl":         42_300.0,
        "tp1":        42_800.0,
        "tp2":        43_000.0,
        "tp3":        43_300.0,
        "rr1":        1.5, "rr2": 2.5, "rr3": 4.0,
        "confidence": 78,
        "veto_reasons": [],
        "key_factors":  ["CVD ✅", "MTF ✅"],
        "atr":        200.0,
        "reason":     "ok",
    }
    base.update(overrides)
    return base


def _market(**overrides) -> dict:
    base = {
        "price": 42_500.0,
        "vp":    {"poc": 42_450, "vah": 42_700, "val": 42_200},
    }
    base.update(overrides)
    return base


# ─── PNG validity ─────────────────────────────────────────────────────────


def test_render_returns_valid_png_for_long():
    out = render_signal_chart("BTCUSDT", _make_klines(),
                              _decision(verdict="LONG"), _market())
    assert isinstance(out, bytes)
    assert out.startswith(PNG_MAGIC)
    assert len(out) > 5_000  # non-trivial image


def test_render_returns_valid_png_for_short():
    d = _decision(
        verdict="SHORT", direction="short",
        sl=42_700, tp1=42_200, tp2=42_000, tp3=41_700,
    )
    out = render_signal_chart("BTCUSDT", _make_klines(), d, _market())
    assert out.startswith(PNG_MAGIC)


def test_render_works_without_market_overlays():
    """vp/pivots отсутствуют — рендер всё равно успешный."""
    out = render_signal_chart("BTCUSDT", _make_klines(),
                              _decision(verdict="LONG"), {})
    assert out.startswith(PNG_MAGIC)


def test_render_with_no_market_arg():
    """market arg=None должен быть валидным."""
    out = render_signal_chart("BTCUSDT", _make_klines(),
                              _decision(verdict="LONG"))
    assert out.startswith(PNG_MAGIC)


def test_render_for_wait_verdict_skips_trade_zones():
    """
    WAIT не должен рисовать Entry/SL/TP, но чарт всё равно рендерится
    (свечи + EMA + контекст). Проверяем что PNG валиден.
    """
    d = _decision(verdict="WAIT", entry=None, sl=None,
                  tp1=None, tp2=None, tp3=None,
                  rr1=None, rr2=None, rr3=None,
                  confidence=30)
    out = render_signal_chart("BTCUSDT", _make_klines(), d, _market())
    assert out.startswith(PNG_MAGIC)


def test_render_for_low_cap_altcoin_with_decimal_prices():
    """sub-dollar цены не должны ломать форматирование подписей."""
    klines = _make_klines(start_price=0.5234, drift=0.0001, amp=0.005)
    d = _decision(
        verdict="LONG",
        entry={"min": 0.5221, "max": 0.5247},
        sl=0.5192, tp1=0.5297, tp2=0.5339, tp3=0.5402,
    )
    out = render_signal_chart("PEPEUSDT", klines, d,
                              _market(price=0.5234,
                                      vp={"poc": 0.5215, "vah": 0.5260,
                                          "val": 0.5170}))
    assert out.startswith(PNG_MAGIC)


# ─── Insufficient data handling ───────────────────────────────────────────


def test_render_returns_none_when_too_few_bars():
    klines = _make_klines(n=10)
    out = render_signal_chart("BTCUSDT", klines, _decision(), _market())
    assert out is None


def test_render_returns_none_for_empty_klines():
    out = render_signal_chart("BTCUSDT", [], _decision(), _market())
    assert out is None


# ─── PNG dimensions sanity ────────────────────────────────────────────────


def test_png_has_sensible_dimensions():
    """
    PNG IHDR chunk: bytes 16-19 = width, 20-23 = height.
    figsize=(12,8) at dpi=120 → expect roughly 1440×960 (± bbox padding).
    """
    out = render_signal_chart("BTCUSDT", _make_klines(),
                              _decision(verdict="LONG"), _market())
    # IHDR starts at byte 8 (right after PNG magic)
    w, h = struct.unpack(">II", out[16:24])
    assert 1000 < w < 2000
    assert 600  < h < 1500


# ─── Custom bars / timeframe args ─────────────────────────────────────────


@pytest.mark.parametrize("bars", [30, 100, 200])
def test_render_respects_bars_arg(bars):
    klines = _make_klines(n=300)
    out = render_signal_chart("BTCUSDT", klines, _decision(), _market(),
                              bars=bars)
    assert out.startswith(PNG_MAGIC)


@pytest.mark.parametrize("tf_min", [15, 60, 240, 1440])
def test_render_respects_tf_minutes(tf_min):
    """Different timeframes should affect title and x-axis spacing."""
    out = render_signal_chart("BTCUSDT", _make_klines(), _decision(),
                              _market(), tf_minutes=tf_min)
    assert out.startswith(PNG_MAGIC)


# ─── Liquidity pools overlay ───────────────────────────────────────────────


def _market_with_liquidity():
    """Market с klines/pivots → build_liquidity_map даст структурные пулы."""
    k1h = _make_klines(120)
    kD = [{"o": 41000, "h": 43500, "l": 40500, "c": 42000, "v": 1000},
          {"o": 42000, "h": 43200, "l": 41200, "c": 42500, "v": 1000},
          {"o": 42500, "h": 42900, "l": 42100, "c": 42500, "v": 1000}]
    return {
        "price": 42_500.0,
        "vp": {"poc": 42_450, "vah": 42_700, "val": 42_200},
        "pivots": {"P": 42_400, "R1": 42_800, "S1": 42_100,
                   "R2": 43_100, "S2": 41_800},
        "_klines": {"60": k1h, "240": [], "D": kD},
    }


def test_render_with_liquidity_pools_valid_png():
    out = render_signal_chart(
        "BTCUSDT", _make_klines(),
        _decision(liq_target={"price": 42_900.0, "kind": "PWH",
                              "strength": 4, "dist_pct": 0.9}),
        _market_with_liquidity(),
    )
    assert isinstance(out, bytes)
    assert out.startswith(PNG_MAGIC)
    assert len(out) > 5_000


def test_render_without_klines_skips_liquidity_safely():
    # market без _klines → liquidity overlay не строится, чарт не падает
    out = render_signal_chart("BTCUSDT", _make_klines(),
                              _decision(), _market())
    assert out.startswith(PNG_MAGIC)


def test_safe_liquidity_map_handles_empty_market():
    from chart import _safe_liquidity_map
    assert _safe_liquidity_map({}) is None
    assert _safe_liquidity_map(None) is None


def test_safe_liquidity_map_returns_map_with_pools():
    from chart import _safe_liquidity_map
    lmap = _safe_liquidity_map(_market_with_liquidity())
    assert lmap is not None
    assert lmap.pools
