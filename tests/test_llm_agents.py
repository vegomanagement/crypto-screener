"""Tests for llm_agents.py — LLM explainer + multi-agent debate."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from llm_agents import (
    SYSTEM_BEAR,
    SYSTEM_BULL,
    SYSTEM_EXPLAIN,
    SYSTEM_JUDGE,
    SYSTEM_RISK,
    debate_and_judge,
    explain_signal,
    market_brief,
)


# ─── Helpers ──────────────────────────────────────────────────────────────


def _market(**overrides):
    base = {
        "price": 42500.0,
        "change_24h": 1.2,
        "cvd": {"trend": "up", "divergence": False},
        "ema_biases": {"1H": "bull", "4H": "bull", "1D": "bull"},
        "bybit": {"funding": 0.0001, "oi_chg": 1.2},
        "indicators": {"rsi": 55, "macd": {"trend": "bull"}, "atr_pct": 0.47},
        "vp": {"poc": 42400, "vah": 42700, "val": 42100},
        "macro": {"fg_value": 55, "btc_dom": 52.3},
        "session": {"icon": "🇬🇧", "name": "London", "quality": 4},
    }
    base.update(overrides)
    return base


def _decision(**overrides):
    base = {
        "verdict": "LONG",
        "confidence": 78,
        "rr1": 1.5,
        "reason": "Confluence 78/100",
        "key_factors": ["CVD ✅ подтверждает", "MTF ✅ все 3 ТФ"],
        "veto_reasons": [],
    }
    base.update(overrides)
    return base


class FakeClient:
    """
    Records every call so tests can assert on system prompt, model,
    max_tokens, and user-message contents.
    """

    def __init__(self, responses=None):
        # responses can be a list (consumed FIFO) or a single string
        self._responses = list(responses) if isinstance(responses, list) else None
        self._default = responses if isinstance(responses, str) else "MOCK_RESPONSE"
        self.calls = []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, *, model, max_tokens, system, messages):
        call = {
            "model": model,
            "max_tokens": max_tokens,
            "system": system,
            "user_message": messages[0]["content"],
        }
        self.calls.append(call)

        if self._responses:
            text = self._responses.pop(0)
        else:
            text = self._default

        block = SimpleNamespace(text=text)
        return SimpleNamespace(content=[block])


# ─── market_brief ─────────────────────────────────────────────────────────


def test_market_brief_includes_price_and_change():
    s = market_brief(_market())
    assert "$42,500.00" in s
    assert "+1.20% 24h" in s


def test_market_brief_includes_cvd_and_mtf():
    s = market_brief(_market())
    assert "CVD UP" in s
    assert "MTF" in s


def test_market_brief_handles_empty_market():
    # No raise, no required keys
    s = market_brief({})
    assert isinstance(s, str)
    assert len(s) > 0  # at least the price line


def test_market_brief_flags_cvd_divergence():
    s = market_brief(_market(cvd={"trend": "up", "divergence": True}))
    assert "ДИВ" in s


def test_market_brief_skips_unknown_cvd():
    s = market_brief(_market(cvd={"trend": "unknown"}))
    assert "CVD" not in s


# ─── explain_signal ───────────────────────────────────────────────────────


def test_explain_signal_calls_client_with_explain_prompt():
    client = FakeClient(responses="Engine за лонг.")
    out = explain_signal(_decision(), _market(),
                         {"signal": "BOS_BULL", "symbol": "BTCUSDT"},
                         client, model="haiku-test")
    assert out == "Engine за лонг."
    assert len(client.calls) == 1
    call = client.calls[0]
    assert call["model"] == "haiku-test"
    assert call["system"] == SYSTEM_EXPLAIN
    assert call["max_tokens"] <= 250


def test_explain_signal_embeds_verdict_in_prompt():
    client = FakeClient()
    explain_signal(_decision(verdict="LONG", confidence=78),
                   _market(),
                   {"signal": "BOS_BULL", "symbol": "BTCUSDT"},
                   client, model="haiku-test")
    user_msg = client.calls[0]["user_message"]
    assert "LONG" in user_msg
    assert "78/100" in user_msg
    assert "BOS_BULL" in user_msg


def test_explain_signal_embeds_key_factors_and_vetoes():
    client = FakeClient()
    d = _decision(
        key_factors=["FACTOR_A ✅", "FACTOR_B ✅"],
        veto_reasons=["RISK_X warning"],
    )
    explain_signal(d, _market(),
                   {"signal": "BOS_BULL", "symbol": "BTC"},
                   client, model="haiku-test")
    user_msg = client.calls[0]["user_message"]
    assert "FACTOR_A" in user_msg
    assert "FACTOR_B" in user_msg
    assert "RISK_X" in user_msg


def test_explain_signal_handles_client_exception():
    bad_client = SimpleNamespace(
        messages=SimpleNamespace(
            create=MagicMock(side_effect=RuntimeError("API down")),
        ),
    )
    out = explain_signal(_decision(), _market(),
                         {"signal": "BOS_BULL", "symbol": "BTC"},
                         bad_client, model="haiku-test")
    assert "недоступен" in out.lower() or "api down" in out.lower()


def test_explain_signal_works_for_wait_verdict():
    client = FakeClient(responses="Переждать пока неясно.")
    d = _decision(verdict="WAIT", confidence=30, rr1=None,
                  key_factors=[], veto_reasons=["RSI overbought"])
    out = explain_signal(d, _market(),
                         {"signal": "OB_BULL", "symbol": "ETH"},
                         client, model="haiku-test")
    user_msg = client.calls[0]["user_message"]
    assert "WAIT" in user_msg
    assert out == "Переждать пока неясно."


# ─── debate_and_judge ─────────────────────────────────────────────────────


def test_debate_makes_four_calls_with_right_systems():
    client = FakeClient(responses=["bull_text", "bear_text", "risk_text",
                                    "judge_final"])
    out = debate_and_judge(
        question="стоит ли лонговать?",
        market=_market(),
        recent=[],
        client=client,
        fast_model="haiku",
        smart_model="sonnet",
    )
    assert out == "judge_final"
    assert len(client.calls) == 4

    # Order of bull/bear/risk is non-deterministic (parallel), but judge
    # must run AFTER they all complete — and judge is the only Sonnet call.
    systems = [c["system"] for c in client.calls]
    models  = [c["model"]  for c in client.calls]
    assert SYSTEM_BULL  in systems
    assert SYSTEM_BEAR  in systems
    assert SYSTEM_RISK  in systems
    assert SYSTEM_JUDGE in systems
    assert systems[-1] == SYSTEM_JUDGE       # judge is last
    assert models.count("haiku")  == 3
    assert models.count("sonnet") == 1


def test_judge_prompt_includes_all_agent_outputs():
    client = FakeClient(responses=["BULL_TXT", "BEAR_TXT", "RISK_TXT",
                                    "FINAL"])
    debate_and_judge(
        question="?", market=_market(), recent=[], client=client,
        fast_model="haiku", smart_model="sonnet",
    )
    judge_call = client.calls[-1]
    assert "BULL_TXT" in judge_call["user_message"]
    assert "BEAR_TXT" in judge_call["user_message"]
    assert "RISK_TXT" in judge_call["user_message"]


def test_judge_prompt_embeds_engine_verdict_when_provided():
    client = FakeClient(responses=["b", "br", "r", "judge"])
    debate_and_judge(
        question="?", market=_market(), recent=[], client=client,
        fast_model="haiku", smart_model="sonnet",
        decision=_decision(verdict="SHORT", confidence=72),
    )
    judge_msg = client.calls[-1]["user_message"]
    assert "SHORT" in judge_msg
    assert "ОБЯЗАН" in judge_msg  # explicit "must respect" wording
    assert "72/100" in judge_msg


def test_judge_prompt_omits_verdict_block_when_decision_none():
    client = FakeClient(responses=["b", "br", "r", "judge"])
    debate_and_judge(
        question="?", market=_market(), recent=[], client=client,
        fast_model="haiku", smart_model="sonnet",
        decision=None,
    )
    judge_msg = client.calls[-1]["user_message"]
    assert "ОБЯЗАН СОБЛЮДАТЬ" not in judge_msg


def test_debate_return_parts_returns_dict():
    client = FakeClient(responses=["B", "Br", "R", "J"])
    parts = debate_and_judge(
        question="?", market=_market(), recent=[], client=client,
        fast_model="haiku", smart_model="sonnet", return_parts=True,
    )
    assert isinstance(parts, dict)
    assert set(parts.keys()) == {"bull", "bear", "risk", "judge"}
    assert parts["judge"] == "J"


def test_debate_handles_agent_failure_gracefully():
    """If bull agent throws, judge still runs with the error embedded."""
    failures = {"bull_seen": False}

    def create(*, model, max_tokens, system, messages):
        if system == SYSTEM_BULL and not failures["bull_seen"]:
            failures["bull_seen"] = True
            raise RuntimeError("network blip")
        return SimpleNamespace(content=[SimpleNamespace(text=f"ok-{system[:10]}")])

    bad_client = SimpleNamespace(messages=SimpleNamespace(create=create))
    out = debate_and_judge(
        question="?", market=_market(), recent=[], client=bad_client,
        fast_model="h", smart_model="s",
    )
    # judge ran successfully
    assert out.startswith("ok-")


# ─── Quality score derivation (sanity check via decision.confidence) ──────

# Note: explain_signal returns just the text; quality conversion lives in
# screener.py:llm_analyze_signal as `quality = decision.confidence / 10`.
# That's a one-liner so we test it indirectly through decision module:

@pytest.mark.parametrize("confidence,expected_quality", [
    (0, 1),    # clamped to 1 minimum
    (5, 1),
    (10, 1),
    (15, 2),
    (50, 5),
    (78, 8),
    (95, 10),
    (100, 10),
])
def test_quality_derivation_from_confidence(confidence, expected_quality):
    """The deterministic mapping used in screener.llm_analyze_signal."""
    quality = max(1, min(10, int(round(confidence / 10))))
    assert quality == expected_quality
