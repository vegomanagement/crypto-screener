"""
Тесты на Telegram-команды /btdiag и /hyperopt (только парсинг args
и диспатч-роутинг — сетевые вызовы не мокаются).
"""

from __future__ import annotations

import screener


# ─── _parse_btdiag_args ───────────────────────────────────────────────────


def test_btdiag_args_default():
    assert screener._parse_btdiag_args("") == ("BTCUSDT", 30, None, "5")


def test_btdiag_args_symbol_only():
    assert screener._parse_btdiag_args("ETH") == ("ETHUSDT", 30, None, "5")


def test_btdiag_args_days_only():
    assert screener._parse_btdiag_args("60") == ("BTCUSDT", 60, None, "5")


def test_btdiag_args_symbol_and_days():
    assert screener._parse_btdiag_args("SOL 90") == ("SOLUSDT", 90, None, "5")


def test_btdiag_args_order_invariant():
    assert screener._parse_btdiag_args("30 BTC") == ("BTCUSDT", 30, None, "5")


def test_btdiag_args_strips_usdt_p_suffix():
    assert screener._parse_btdiag_args("BTCUSDT.P 30") == (
        "BTCUSDT", 30, None, "5")


def test_btdiag_args_clamps_days():
    assert screener._parse_btdiag_args("BTC 0")[1] == 1
    assert screener._parse_btdiag_args("BTC 5000")[1] == 365


def test_btdiag_args_single_override():
    sym, days, ovr, tf = screener._parse_btdiag_args(
        "BTC 30 KILLZONE_GATE_ENABLED=false")
    assert sym == "BTCUSDT"
    assert days == 30
    assert ovr == {"KILLZONE_GATE_ENABLED": False}
    assert tf == "5"


def test_btdiag_args_multiple_overrides():
    _, _, ovr, _ = screener._parse_btdiag_args(
        "BTC 30 KILLZONE_GATE_ENABLED=false STRUCTURE_GATE_ENABLED=false "
        "MIN_CONFIDENCE_FOR_TRADE=55"
    )
    assert ovr == {
        "KILLZONE_GATE_ENABLED": False,
        "STRUCTURE_GATE_ENABLED": False,
        "MIN_CONFIDENCE_FOR_TRADE": 55,
    }


def test_btdiag_args_float_override():
    _, _, ovr, _ = screener._parse_btdiag_args(
        "BTC 30 SL_BUFFER_ATR=0.5")
    assert ovr == {"SL_BUFFER_ATR": 0.5}


def test_btdiag_args_string_override_fallback():
    _, _, ovr, _ = screener._parse_btdiag_args("BTC 30 MODE=experimental")
    assert ovr == {"MODE": "experimental"}


def test_btdiag_args_mixed_order_overrides_and_symbol():
    """Overrides + symbol + days в любом порядке — корректно парсится."""
    sym, days, ovr, _ = screener._parse_btdiag_args(
        "KILLZONE_GATE_ENABLED=false ETH 60 STRUCTURE_GATE_ENABLED=false"
    )
    assert sym == "ETHUSDT"
    assert days == 60
    assert ovr == {"KILLZONE_GATE_ENABLED": False,
                   "STRUCTURE_GATE_ENABLED": False}


def test_btdiag_args_ignores_lone_equals():
    _, _, ovr, _ = screener._parse_btdiag_args("BTC 30 = =foo")
    assert ovr is None or ovr == {}


# ─── _parse_btdiag_args: tf= option ───────────────────────────────────────


def test_btdiag_args_tf_default_is_5():
    _, _, _, tf = screener._parse_btdiag_args("BTC 30")
    assert tf == "5"


def test_btdiag_args_tf_numeric():
    for v in ("15", "60", "240"):
        _, _, _, tf = screener._parse_btdiag_args(f"BTC 30 tf={v}")
        assert tf == v, f"tf={v} not preserved"


def test_btdiag_args_tf_aliases_normalized():
    assert screener._parse_btdiag_args("BTC 30 tf=1H")[3] == "60"
    assert screener._parse_btdiag_args("BTC 30 tf=4H")[3] == "240"
    assert screener._parse_btdiag_args("BTC 30 tf=1D")[3] == "D"
    assert screener._parse_btdiag_args("BTC 30 tf=15M")[3] == "15"


def test_btdiag_args_tf_uppercase_case_insensitive():
    assert screener._parse_btdiag_args("BTC 30 TF=4h")[3] == "240"


def test_btdiag_args_tf_not_in_overrides():
    """tf=COL должен идти ТОЛЬКО в tf_primary, не в overrides."""
    _, _, ovr, tf = screener._parse_btdiag_args(
        "BTC 30 tf=15 KILLZONE_GATE_ENABLED=false")
    assert tf == "15"
    assert ovr == {"KILLZONE_GATE_ENABLED": False}


def test_btdiag_args_tf_with_preset():
    """tf= и preset= одновременно работают."""
    _, _, ovr, tf = screener._parse_btdiag_args(
        "BTC 30 tf=60 preset=no_p3")
    assert tf == "60"
    assert ovr == {"KILLZONE_GATE_ENABLED": False,
                   "STRUCTURE_GATE_ENABLED": False}


def test_normalize_tf_handles_unknown_passthrough():
    """Неизвестный TF возвращается as-is (для forward-compat)."""
    assert screener._normalize_tf("99") == "99"
    assert screener._normalize_tf("garbage") == "GARBAGE"


# ─── _parse_hyperopt_args ─────────────────────────────────────────────────


def test_hyperopt_args_default():
    sym, days, trials, wf, metric, fixed, tf = \
        screener._parse_hyperopt_args("")
    assert sym == "BTCUSDT"
    assert days == 60
    assert trials == 30
    assert wf is False
    assert metric == "profit_factor"
    assert fixed is None
    assert tf == "5"


def test_hyperopt_args_full():
    sym, days, trials, wf, metric, fixed, tf = \
        screener._parse_hyperopt_args(
            "ETH 90 50 walkforward metric=sharpe_r"
        )
    assert sym == "ETHUSDT"
    assert days == 90
    assert trials == 50
    assert wf is True
    assert metric == "sharpe_r"
    assert fixed is None
    assert tf == "5"


def test_hyperopt_args_wf_aliases():
    for tag in ("walkforward", "wf", "--walkforward", "WF"):
        _, _, _, wf, *_ = screener._parse_hyperopt_args(f"BTC 60 30 {tag}")
        assert wf is True, f"alias {tag!r} не распознан"


def test_hyperopt_args_two_ints_are_days_then_trials():
    sym, days, trials, *_ = screener._parse_hyperopt_args("BTC 90 100")
    assert days == 90
    assert trials == 100


def test_hyperopt_args_invalid_metric_falls_back_to_default():
    _, _, _, _, metric, _, _ = screener._parse_hyperopt_args(
        "BTC 60 30 metric=garbage")
    assert metric == "profit_factor"


def test_hyperopt_args_valid_metrics():
    for m in ("avg_r", "avg_r_net", "win_rate", "sortino_r", "expectancy"):
        _, _, _, _, metric, _, _ = screener._parse_hyperopt_args(
            f"BTC 60 30 metric={m}")
        assert metric == m


def test_hyperopt_args_clamps_days_and_trials():
    _, days, trials, *_ = screener._parse_hyperopt_args("BTC 5 3")
    assert days == 7
    assert trials == 5
    _, days, trials, *_ = screener._parse_hyperopt_args("BTC 9999 9999")
    assert days == 365
    assert trials == 200


def test_hyperopt_args_fixed_params_single():
    _, _, _, _, _, fixed, _ = screener._parse_hyperopt_args(
        "BTC 60 30 KILLZONE_GATE_ENABLED=false")
    assert fixed == {"KILLZONE_GATE_ENABLED": False}


def test_hyperopt_args_fixed_params_multiple_with_metric():
    _, _, _, _, metric, fixed, _ = screener._parse_hyperopt_args(
        "BTC 60 30 metric=sharpe_r HTF_BIAS_GATE_ENABLED=false "
        "MIN_CONFIDENCE_FOR_TRADE=57"
    )
    assert metric == "sharpe_r"
    assert fixed == {"HTF_BIAS_GATE_ENABLED": False,
                     "MIN_CONFIDENCE_FOR_TRADE": 57}


def test_hyperopt_args_metric_not_in_fixed_params():
    """metric=... должен идти ТОЛЬКО в metric, не в fixed_params."""
    _, _, _, _, metric, fixed, _ = screener._parse_hyperopt_args(
        "BTC 60 30 metric=sharpe_r"
    )
    assert metric == "sharpe_r"
    assert fixed is None


def test_hyperopt_args_tf_option():
    """tf= в /hyperopt тоже работает."""
    _, _, _, _, _, _, tf = screener._parse_hyperopt_args(
        "BTC 60 30 tf=15")
    assert tf == "15"
    _, _, _, _, _, _, tf = screener._parse_hyperopt_args(
        "BTC 60 30 tf=1H")
    assert tf == "60"


def test_hyperopt_args_tf_not_in_fixed():
    """tf=... должен идти ТОЛЬКО в tf_primary, не в fixed_params."""
    _, _, _, _, _, fixed, tf = screener._parse_hyperopt_args(
        "BTC 60 30 tf=15 KILLZONE_GATE_ENABLED=false")
    assert tf == "15"
    assert fixed == {"KILLZONE_GATE_ENABLED": False}


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
    assert "/scanbt" in full


# ─── _parse_scanbt_args ───────────────────────────────────────────────────


def test_scanbt_args_default():
    import importlib
    importlib.reload(screener)
    syms, days, ovr, sort_by, tf = screener._parse_scanbt_args("")
    assert syms == ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    assert days == 30
    assert ovr is None
    assert sort_by == "pf"
    assert tf == "5"


def test_scanbt_args_comma_separated_symbols():
    syms, days, *_ = screener._parse_scanbt_args("BTC,ETH,SOL 60")
    assert syms == ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    assert days == 60


def test_scanbt_args_single_symbol():
    syms, *_ = screener._parse_scanbt_args("BTC 30")
    assert syms == ["BTCUSDT"]


def test_scanbt_args_strips_usdt_p_suffix():
    syms, *_ = screener._parse_scanbt_args("BTCUSDT.P,ETHUSDT 30")
    assert syms == ["BTCUSDT", "ETHUSDT"]


def test_scanbt_args_dedupes():
    syms, *_ = screener._parse_scanbt_args("BTC,ETH,BTC,ETH,SOL 30")
    assert syms == ["BTCUSDT", "ETHUSDT", "SOLUSDT"]


def test_scanbt_args_overrides():
    syms, days, ovr, *_ = screener._parse_scanbt_args(
        "BTC,ETH 30 KILLZONE_GATE_ENABLED=false MIN_CONFIDENCE_FOR_TRADE=55"
    )
    assert syms == ["BTCUSDT", "ETHUSDT"]
    assert days == 30
    assert ovr == {"KILLZONE_GATE_ENABLED": False,
                   "MIN_CONFIDENCE_FOR_TRADE": 55}


def test_scanbt_args_clamps_days():
    _, days, *_ = screener._parse_scanbt_args("BTC,ETH 0")
    assert days == 1
    _, days, *_ = screener._parse_scanbt_args("BTC,ETH 5000")
    assert days == 365


def test_scanbt_args_ignores_empty_symbols():
    syms, *_ = screener._parse_scanbt_args("BTC,,ETH, 30")
    assert syms == ["BTCUSDT", "ETHUSDT"]


# ─── /scanbt sort= option ─────────────────────────────────────────────────


def test_scanbt_args_sort_valid_values():
    for col in ("pf", "wr", "avg_r", "avg_r_net", "max_dd", "closed"):
        _, _, _, sort_by, _ = screener._parse_scanbt_args(
            f"BTC,ETH 30 sort={col}")
        assert sort_by == col, f"sort={col} не распознался"


def test_scanbt_args_sort_invalid_falls_back_to_pf():
    _, _, _, sort_by, _ = screener._parse_scanbt_args(
        "BTC,ETH 30 sort=garbage")
    assert sort_by == "pf"


def test_scanbt_args_sort_not_in_overrides():
    """sort=COL не должен попадать в overrides как KEY=VAL."""
    _, _, ovr, sort_by, _ = screener._parse_scanbt_args(
        "BTC,ETH 30 sort=avg_r_net KILLZONE_GATE_ENABLED=false"
    )
    assert sort_by == "avg_r_net"
    assert ovr == {"KILLZONE_GATE_ENABLED": False}


# ─── /scanbt tf= option ───────────────────────────────────────────────────


def test_scanbt_args_tf_option():
    """tf= в /scanbt тоже работает."""
    _, _, _, _, tf = screener._parse_scanbt_args("BTC,ETH 30 tf=15")
    assert tf == "15"
    _, _, _, _, tf = screener._parse_scanbt_args("BTC,ETH 30 tf=1H")
    assert tf == "60"
    _, _, _, _, tf = screener._parse_scanbt_args("BTC,ETH 30 tf=4H")
    assert tf == "240"
    _, _, _, _, tf = screener._parse_scanbt_args("BTC,ETH 30 tf=1D")
    assert tf == "D"


def test_scanbt_args_tf_not_in_overrides():
    """tf=COL не попадает в overrides как KEY=VAL."""
    _, _, ovr, _, tf = screener._parse_scanbt_args(
        "BTC,ETH 30 tf=60 KILLZONE_GATE_ENABLED=false")
    assert tf == "60"
    assert ovr == {"KILLZONE_GATE_ENABLED": False}


def test_scanbt_sort_key_handles_infinity_pf():
    """∞ → inf, не-числовое → -inf."""
    assert screener._scanbt_sort_key({"pf": "∞"}, "pf") == float("inf")
    assert screener._scanbt_sort_key({"pf": "garbage"}, "pf") == -float("inf")
    assert screener._scanbt_sort_key({"pf": 1.5}, "pf") == 1.5


def test_scanbt_sort_key_maps_to_correct_field():
    row = {"pf": 1.5, "win_rate": 30.0, "avg_r": 0.5,
           "avg_r_net": 0.4, "max_dd": -10.0, "closed": 42}
    assert screener._scanbt_sort_key(row, "pf") == 1.5
    assert screener._scanbt_sort_key(row, "wr") == 30.0
    assert screener._scanbt_sort_key(row, "avg_r") == 0.5
    assert screener._scanbt_sort_key(row, "avg_r_net") == 0.4
    assert screener._scanbt_sort_key(row, "max_dd") == -10.0
    assert screener._scanbt_sort_key(row, "closed") == 42


def test_scanbt_sort_key_unknown_col_falls_back_to_pf():
    row = {"pf": 1.5}
    assert screener._scanbt_sort_key(row, "garbage") == 1.5


# ─── _format_scanbt_table ─────────────────────────────────────────────────


def test_format_scanbt_table_empty():
    out = screener._format_scanbt_table([])
    assert "no symbols" in out


def test_format_scanbt_table_basic():
    rows = [
        {"symbol": "BTC", "closed": 42, "win_rate": 26.2, "avg_r": 0.46,
         "avg_r_net": 0.40, "pf": 1.62, "max_dd": -9.0},
        {"symbol": "ETH", "closed": 30, "win_rate": 18.5, "avg_r": -0.20,
         "avg_r_net": -0.30, "pf": 0.85, "max_dd": -15.0},
    ]
    out = screener._format_scanbt_table(rows)
    assert "Symbol" in out
    assert "WR%" in out
    assert "PF" in out
    assert "BTC" in out
    assert "ETH" in out
    assert "26.2" in out


def test_format_scanbt_table_handles_infinity_pf():
    rows = [{"symbol": "BTC", "closed": 5, "win_rate": 100.0, "avg_r": 1.5,
             "avg_r_net": 1.4, "pf": "∞", "max_dd": 0.0}]
    out = screener._format_scanbt_table(rows)
    assert "∞" in out


# ─── /scanbt routing ──────────────────────────────────────────────────────


def test_scanbt_command_routed(monkeypatch):
    called = {}

    def fake_cmd(chat_id, args):
        called["chat_id"] = chat_id
        called["args"] = args

    monkeypatch.setattr(screener, "cmd_scanbt", fake_cmd)
    update = {"message": {"chat": {"id": 789},
                          "text": "/scanbt BTC,ETH 30",
                          "from": {"id": 789}}}
    screener.handle_update(update)
    assert called.get("chat_id") == 789
    assert called.get("args") == "BTC,ETH 30"


# ─── CONFIG_PRESETS ──────────────────────────────────────────────────────


def test_config_presets_has_expected_keys():
    assert "no_gates" in screener.CONFIG_PRESETS
    assert "no_p3" in screener.CONFIG_PRESETS
    assert "no_p4" in screener.CONFIG_PRESETS
    assert "wide_tp" in screener.CONFIG_PRESETS
    assert "aggressive" in screener.CONFIG_PRESETS


def test_extract_preset_tokens_extracts_and_removes():
    parts = ["BTC", "30", "preset=no_p3", "MIN_CONFIDENCE_FOR_TRADE=55"]
    other, ovr = screener._extract_preset_tokens(parts)
    assert "preset=no_p3" not in other
    assert "BTC" in other
    assert "30" in other
    assert "MIN_CONFIDENCE_FOR_TRADE=55" in other
    assert ovr == {"KILLZONE_GATE_ENABLED": False,
                   "STRUCTURE_GATE_ENABLED": False}


def test_extract_preset_tokens_unknown_preset_silently_skipped():
    other, ovr = screener._extract_preset_tokens(["BTC", "preset=garbage"])
    assert ovr == {}
    assert "preset=garbage" not in other


def test_extract_preset_tokens_multiple_presets_merge():
    other, ovr = screener._extract_preset_tokens(
        ["BTC", "preset=no_p3", "preset=wide_tp"])
    # Оба применились
    assert ovr["KILLZONE_GATE_ENABLED"] is False
    assert ovr["ATR_TP1_DIST"] == 3.0


def test_btdiag_args_preset_applied():
    sym, days, ovr, _ = screener._parse_btdiag_args("BTC 30 preset=no_p3")
    assert ovr == {"KILLZONE_GATE_ENABLED": False,
                   "STRUCTURE_GATE_ENABLED": False}


def test_btdiag_args_explicit_override_beats_preset():
    """preset=no_p3 включает STRUCTURE=False, но явное STRUCTURE=True перебивает."""
    _, _, ovr, _ = screener._parse_btdiag_args(
        "BTC 30 preset=no_p3 STRUCTURE_GATE_ENABLED=true"
    )
    assert ovr["KILLZONE_GATE_ENABLED"] is False
    assert ovr["STRUCTURE_GATE_ENABLED"] is True


def test_btdiag_args_no_preset_no_explicit_returns_none():
    _, _, ovr, _ = screener._parse_btdiag_args("BTC 30")
    assert ovr is None


def test_scanbt_args_preset_applied():
    syms, days, ovr, *_ = screener._parse_scanbt_args(
        "BTC,ETH 30 preset=wide_tp"
    )
    assert syms == ["BTCUSDT", "ETHUSDT"]
    assert ovr == {"ATR_TP1_DIST": 3.0, "ATR_TP2_DIST": 5.0,
                   "ATR_TP3_DIST": 8.0}


def test_hyperopt_args_preset_applied():
    _, _, _, _, _, fixed, _ = screener._parse_hyperopt_args(
        "BTC 60 30 preset=no_p4"
    )
    assert fixed == {"HTF_BIAS_GATE_ENABLED": False}


def test_hyperopt_args_preset_plus_explicit():
    _, _, _, _, metric, fixed, _ = screener._parse_hyperopt_args(
        "BTC 60 30 metric=sharpe_r preset=aggressive MIN_CONFIDENCE_FOR_TRADE=60"
    )
    assert metric == "sharpe_r"
    # aggressive preset: MIN_CONFIDENCE=55, TP wide
    # explicit MIN_CONFIDENCE_FOR_TRADE=60 перебивает 55
    assert fixed["MIN_CONFIDENCE_FOR_TRADE"] == 60
    assert fixed["ATR_TP1_DIST"] == 3.0
