"""Тесты bt_equity_chart.py — рендер equity curve в PNG."""

from __future__ import annotations

import bt_equity_chart


def test_render_returns_png_bytes():
    """PNG-сигнатура (8 bytes): 89 50 4E 47 0D 0A 1A 0A."""
    equity = [0.5, 1.5, 0.5, 2.0, 3.5]
    out = bt_equity_chart.render_equity_curve(equity, "BTC", 30)
    assert isinstance(out, bytes)
    assert len(out) > 1000   # not a tiny corrupt PNG
    assert out[:8] == b"\x89PNG\r\n\x1a\n", "PNG header mismatch"


def test_render_empty_equity_returns_placeholder_png():
    """Пустой equity не должен raise — рендерится «no trades» картинка."""
    out = bt_equity_chart.render_equity_curve([], "BTC", 30)
    assert isinstance(out, bytes)
    assert out[:8] == b"\x89PNG\r\n\x1a\n"


def test_render_with_negative_equity():
    """Отрицательный equity рендерится без ошибок."""
    equity = [-0.5, -1.0, -2.0, -1.5, -3.0]
    out = bt_equity_chart.render_equity_curve(equity, "ETH", 30)
    assert out[:8] == b"\x89PNG\r\n\x1a\n"


def test_render_with_mixed_equity():
    """equity пересекающий 0 — должны рендериться оба сегмента."""
    equity = [1.5, 2.0, 0.5, -1.0, -2.5, 0.5, 2.0]
    out = bt_equity_chart.render_equity_curve(equity, "SOL", 60)
    assert out[:8] == b"\x89PNG\r\n\x1a\n"


def test_render_with_stats_in_title():
    """stats={} → title без сабтайтла; stats={...} → title с метриками."""
    equity = [0.5, 1.5]
    no_stats = bt_equity_chart.render_equity_curve(equity, "BTC", 30)
    with_stats = bt_equity_chart.render_equity_curve(
        equity, "BTC", 30,
        stats={"closed": 2, "win_rate": 50.0,
               "risk": {"profit_factor": 1.5}},
    )
    # Обе валидные PNG, но разный контент (заголовок отличается)
    assert no_stats[:8] == b"\x89PNG\r\n\x1a\n"
    assert with_stats[:8] == b"\x89PNG\r\n\x1a\n"


def test_render_single_trade_equity():
    """Один трейд — должна быть валидная PNG."""
    out = bt_equity_chart.render_equity_curve([1.5], "BTC", 7)
    assert out[:8] == b"\x89PNG\r\n\x1a\n"


def test_render_long_equity_series():
    """Длинная серия (100 трейдов) — рендерится без проблем."""
    import random
    random.seed(42)
    equity = []
    cum = 0.0
    for _ in range(100):
        cum += random.choice([1.5, 2.5, 4.0, -1.0])
        equity.append(round(cum, 2))
    out = bt_equity_chart.render_equity_curve(equity, "BTC", 90,
                                              stats={"closed": 100})
    assert len(out) > 5000   # достаточно большой PNG
    assert out[:8] == b"\x89PNG\r\n\x1a\n"
