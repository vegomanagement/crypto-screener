"""
Тесты на Telegram-команды /btdiag и /hyperopt (только парсинг args
и диспатч-роутинг — сетевые вызовы не мокаются).
"""

from __future__ import annotations

import screener


# ─── _parse_btdiag_args ───────────────────────────────────────────────────


def test_btdiag_args_default():
    assert screener._parse_btdiag_args("") == ("BTCUSDT", 30, None)


def test_btdiag_args_symbol_only():
    assert screener._parse_btdiag_args("ETH") == ("ETHUSDT", 30, None)


def test_btdiag_args_days_only():
    assert screener._parse_btdiag_args("60") == ("BTCUSDT", 60, None)


def test_btdiag_args_symbol_and_days():
    assert screener._parse_btdiag_args("SOL 90") == ("SOLUSDT", 90, None)


def test_btdiag_args_order_invariant():
    assert screener._parse_btdiag_args("30 BTC") == ("BTCUSDT", 30, None)


def test_btdiag_args_strips_usdt_p_suffix():
    assert screener._parse_btdiag_args("BTCUSDT.P 30") == ("BTCUSDT", 30, None)


def test_btdiag_args_clamps_days():
    assert screener._parse_btdiag_args("BTC 0")[1] == 1
    assert screener._parse_btdiag_args("BTC 5000")[1] == 365


def test_btdiag_args_single_override():
    sym, days, ovr = screener._parse_btdiag_args(
        "BTC 30 KILLZONE_GATE_ENABLED=false")
    assert sym == "BTCUSDT"
    assert days == 30
    assert ovr == {"KILLZONE_GATE_ENABLED": False}


def test_btdiag_args_multiple_overrides():
    _, _, ovr = screener._parse_btdiag_args(
        "BTC 30 KILLZONE_GATE_ENABLED=false STRUCTURE_GATE_ENABLED=false "
        "MIN_CONFIDENCE_FOR_TRADE=55"
    )
    assert ovr == {
        "KILLZONE_GATE_ENABLED": False,
        "STRUCTURE_GATE_ENABLED": False,
        "MIN_CONFIDENCE_FOR_TRADE": 55,
    }


def test_btdiag_args_float_override():
    _, _, ovr = screener._parse_btdiag_args(
        "BTC 30 SL_BUFFER_ATR=0.5")
    assert ovr == {"SL_BUFFER_ATR": 0.5}


def test_btdiag_args_string_override_fallback():
    _, _, ovr = screener._parse_btdiag_args("BTC 30 MODE=experimental")
    assert ovr == {"MODE": "experimental"}


def test_btdiag_args_mixed_order_overrides_and_symbol():
    """Overrides + symbol + days в любом порядке — корректно парсится."""
    sym, days, ovr = screener._parse_btdiag_args(
        "KILLZONE_GATE_ENABLED=false ETH 60 STRUCTURE_GATE_ENABLED=false"
    )
    assert sym == "ETHUSDT"
    assert days == 60
    assert ovr == {"KILLZONE_GATE_ENABLED": False,
                   "STRUCTURE_GATE_ENABLED": False}


def test_btdiag_args_ignores_lone_equals():
    _, _, ovr = screener._parse_btdiag_args("BTC 30 = =foo")
    assert ovr is None or ovr == {}


# ─── _parse_hyperopt_args ─────────────────────────────────────────────────


def test_hyperopt_args_default():
    sym, days, trials, wf, metric, fixed = screener._parse_hyperopt_args("")
    assert sym == "BTCUSDT"
    assert days == 60
    assert trials == 30
    assert wf is False
    assert metric == "profit_factor"
    assert fixed is None


def test_hyperopt_args_full():
    sym, days, trials, wf, metric, fixed = screener._parse_hyperopt_args(
        "ETH 90 50 walkforward metric=sharpe_r"
    )
    assert sym == "ETHUSDT"
    assert days == 90
    assert trials == 50
    assert wf is True
    assert metric == "sharpe_r"
    assert fixed is None


def test_hyperopt_args_wf_aliases():
    for tag in ("walkforward", "wf", "--walkforward", "WF"):
        _, _, _, wf, _, _ = screener._parse_hyperopt_args(f"BTC 60 30 {tag}")
        assert wf is True, f"alias {tag!r} не распознан"


def test_hyperopt_args_two_ints_are_days_then_trials():
    sym, days, trials, *_ = screener._parse_hyperopt_args("BTC 90 100")
    assert days == 90
    assert trials == 100


def test_hyperopt_args_invalid_metric_falls_back_to_default():
    *_, metric, _ = screener._parse_hyperopt_args("BTC 60 30 metric=garbage")
    assert metric == "profit_factor"


def test_hyperopt_args_valid_metrics():
    for m in ("avg_r", "avg_r_net", "win_rate", "sortino_r", "expectancy"):
        *_, metric, _ = screener._parse_hyperopt_args(f"BTC 60 30 metric={m}")
        assert metric == m


def test_hyperopt_args_clamps_days_and_trials():
    _, days, trials, *_ = screener._parse_hyperopt_args("BTC 5 3")
    assert days == 7
    assert trials == 5
    _, days, trials, *_ = screener._parse_hyperopt_args("BTC 9999 9999")
    assert days == 365
    assert trials == 200


def test_hyperopt_args_fixed_params_single():
    _, _, _, _, _, fixed = screener._parse_hyperopt_args(
        "BTC 60 30 KILLZONE_GATE_ENABLED=false")
    assert fixed == {"KILLZONE_GATE_ENABLED": False}


def test_hyperopt_args_fixed_params_multiple_with_metric():
    _, _, _, _, metric, fixed = screener._parse_hyperopt_args(
        "BTC 60 30 metric=sharpe_r HTF_BIAS_GATE_ENABLED=false "
        "MIN_CONFIDENCE_FOR_TRADE=57"
    )
    assert metric == "sharpe_r"
    assert fixed == {"HTF_BIAS_GATE_ENABLED": False,
                     "MIN_CONFIDENCE_FOR_TRADE": 57}


def test_hyperopt_args_metric_not_in_fixed_params():
    """metric=... должен идти ТОЛЬКО в metric, не в fixed_params."""
    _, _, _, _, metric, fixed = screener._parse_hyperopt_args(
        "BTC 60 30 metric=sharpe_r"
    )
    assert metric == "sharpe_r"
    assert fixed is None


def test_hyperopt_args_strips_usdt_p():
    sym, *_ = screener._parse_hyperopt_args("ETHUSDT.P 60")
    assert sym == "ETHUSDT"


# ─── dispatch / handle_update routing ─────────────────────────────────────


def test_btdiag_command_routed(monkeypatch):
    """Проверяем что строка '/btdiag BTC 30' вызывает cmd_btdiag."""
    called = {}

    def fake_cmd(chat_id, args):
        called["chat_id"] = chat_id
        called["args"] = args

    monkeypatch.setattr(screener, "cmd_btdiag", fake_cmd)
    update = {"message": {"chat": {"id": 123},
                          "text": "/btdiag BTC 30",
                          "from": {"id": 123}}}
    # Поднимаем dispatch напрямую — handle_update публичный entry
    screener.handle_update(update)
    assert called.get("chat_id") == 123
    assert called.get("args") == "BTC 30"


def test_hyperopt_command_routed(monkeypatch):
    called = {}

    def fake_cmd(chat_id, args):
        called["chat_id"] = chat_id
        called["args"] = args

    monkeypatch.setattr(screener, "cmd_hyperopt", fake_cmd)
    update = {"message": {"chat": {"id": 456},
                          "text": "/hyperopt ETH 60 50 walkforward",
                          "from": {"id": 456}}}
    screener.handle_update(update)
    assert called.get("chat_id") == 456
    assert called.get("args") == "ETH 60 50 walkforward"


# ─── Команды зарегистрированы в menu list ─────────────────────────────────


def test_btdiag_and_hyperopt_in_help_text():
    """В /help-тексте упоминаются новые команды."""
    # Снимем cmd_help через монипатч tg_send чтобы перехватить отправляемый текст
    captured = []

    def fake_send(text, chat_id=None, **kw):
        captured.append(text)
        return True

    import importlib
    importlib.reload(screener)   # сбросить state монипатча из других тестов
    screener.tg_send = fake_send
    screener.cmd_help(0)
    full = "\n".join(captured)
    assert "/btdiag" in full
    assert "/hyperopt" in full
