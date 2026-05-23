"""
llm_agents.py — LLM-обвязка поверх детерминистского engine из decision.py.

Архитектура:
  • Per-signal (Telegram-сигналы):
      explain_signal() — одиночный Haiku-агент. Verdict уже принят
      движком, LLM ТОЛЬКО объясняет его в 2-3 предложениях.
      Это устраняет противоречия.

  • Deep dive (/ask, /digest):
      debate_and_judge() — параллельно Bull / Bear / Risk (Haiku) →
      Sonnet-judge синтезирует финальный разбор со ссылками на тезисы.
      Если есть engine-verdict — judge ОБЯЗАН его уважать.
"""

from concurrent.futures import ThreadPoolExecutor
from typing import Optional


# ─── Промпты ──────────────────────────────────────────────────────────────

SYSTEM_EXPLAIN = """\
Ты — старший трейдер на prop desk. Verdict уже принят детерминистским
engine, твоя задача — кратко объяснить ПОЧЕМУ.

ЖЁСТКИЕ ПРАВИЛА:
1. Не меняй verdict. Если engine сказал LONG — ты НЕ пишешь "но возможно
   лучше переждать". Если WAIT — не убеждаешь входить.
2. Только русский. Ровно 2–3 предложения. Без приветствий.
3. Опирайся на key_factors (за) и veto_reasons (против) — это уже
   отфильтровано engine.
4. Последнее предложение: "Отменит сделку: <конкретное условие>"
   (для LONG/SHORT) или "Сигнал войдёт в силу при: <условие>" (для WAIT).

Без воды, без оговорок типа "следите за рынком", без переспросов."""


SYSTEM_BULL = """\
Ты — buy-side аналитик с bull bias. Твоя роль — найти лучший long thesis
в текущем рынке (даже если он окажется слабым). Только русский.

Структура (3–4 предложения):
1. Конкретный long-setup: где зайти, что подтверждает.
2. Какие индикаторы / structure / order flow это поддерживают.
3. Реалистичная цель.

Не оправдывайся, не пиши "но может быть и шорт" — твоя роль bull, сомнения
оставь Bear-аналитику."""


SYSTEM_BEAR = """\
Ты — sell-side аналитик с bear bias. Зеркало Bull-аналитика. Только русский.

Структура (3–4 предложения): конкретный short-setup, что подтверждает,
реалистичная цель. Без оговорок про возможный long."""


SYSTEM_RISK = """\
Ты — риск-менеджер prop firm. Не торгуешь, только оцениваешь риски.
Только русский.

Структура (3–4 предложения):
1. Структурные риски прямо сейчас: макро, funding, ликвидность, BTC.D.
2. Что может убить И long, И short идею одновременно (volatility events,
   новости, ликвидации).
3. Один honest call: "торговать имеет смысл" ИЛИ "лучше переждать".

Не выбирай сторону рынка. Только риски."""


SYSTEM_JUDGE = """\
Ты — главный трейдер, синтезируешь анализ команды (Bull / Bear / Risk).
Только русский.

На входе ты получаешь:
  • Engine verdict (если есть) — он ОБЯЗАТЕЛЕН к соблюдению (право вето).
  • Bull thesis
  • Bear thesis
  • Risk inventory
  • Рыночные данные

Правила:
1. Если engine дал verdict (LONG/SHORT/WAIT/SKIP) — ты его ОБЪЯСНЯЕШЬ,
   не флипаешь. Можно усилить или умерить уверенность, но направление не
   меняешь.
2. Если engine verdict отсутствует — выбираешь сторону или WAIT.
3. Обязательно цитируй кого-то из аналитиков: "Bull прав, что …",
   "Risk предупреждает о …".

Ровно 5–7 предложений. Без приветствий, без общих фраз "следите за рынком"."""


# ─── Компактный market brief для промптов ─────────────────────────────────

def market_brief(market: dict) -> str:
    """5–7 строк рыночного контекста — не полный дамп."""
    parts = []
    price = market.get("price", 0) or 0
    chg   = market.get("change_24h", 0) or 0
    parts.append(f"Цена ${price:,.2f} ({chg:+.2f}% 24h)")

    cvd = market.get("cvd", {}) or {}
    if cvd.get("trend") and cvd["trend"] != "unknown":
        div = " ДИВ!" if cvd.get("divergence") else ""
        parts.append(f"CVD {cvd['trend'].upper()}{div}")

    biases = market.get("ema_biases", {}) or {}
    if biases:
        parts.append("MTF " + " ".join(f"{t}:{(b or '?')[:4]}" for t, b in biases.items()))

    b = market.get("bybit", {}) or {}
    if b:
        parts.append(f"Bybit FR {b.get('funding',0)*100:+.3f}% · "
                     f"OI Δ {b.get('oi_chg',0):+.2f}%")

    indic = market.get("indicators", {}) or {}
    rsi = indic.get("rsi")
    if rsi is not None:
        macd_t = (indic.get("macd") or {}).get("trend", "?")
        parts.append(f"RSI {rsi:.0f} · MACD {macd_t} · ATR% "
                     f"{indic.get('atr_pct',0):.2f}")

    vp = market.get("vp", {}) or {}
    if vp.get("poc"):
        parts.append(f"VP POC ${vp['poc']:,.0f} · "
                     f"VAL/VAH ${vp.get('val',0):,.0f}–${vp.get('vah',0):,.0f}")

    macro = market.get("macro", {}) or {}
    if macro.get("fg_value") is not None:
        dom = f" · BTC.D {macro.get('btc_dom','?')}%" if macro.get("btc_dom") else ""
        parts.append(f"F&G {macro['fg_value']}{dom}")

    sess = market.get("session", {}) or {}
    if sess.get("name"):
        parts.append(f"Сессия {sess.get('icon','')} {sess['name']} "
                     f"[{sess.get('quality','?')}/5]")

    return "\n".join(f"  • {p}" for p in parts)


# ─── Per-signal explainer ─────────────────────────────────────────────────

def explain_signal(
    decision: dict,
    market: dict,
    sig_data: dict,
    client,
    model: str,
    max_tokens: int = 220,
) -> str:
    """
    Возвращает 2–3 предложения от Haiku, объясняющие verdict.
    LLM не может перевернуть verdict — это гарантируется промптом.
    """
    v   = decision.get("verdict", "WAIT")
    kf  = decision.get("key_factors", []) or []
    vr  = decision.get("veto_reasons", []) or []
    sig = sig_data.get("signal", "ALERT")
    sym = sig_data.get("symbol", "?")

    facts = "\n".join(f"  + {f}" for f in kf[:5]) or "  (нет сильных факторов)"
    risks = "\n".join(f"  - {r}" for r in vr[:5]) or "  (риски не выявлены)"

    prompt = f"""Сигнал: {sig} · {sym}
Engine verdict: {v}  ·  Confidence: {decision.get('confidence',0)}/100  ·  \
RR(TP1): {decision.get('rr1') or '—'}
Причина engine: {decision.get('reason','')}

Факторы ЗА направление:
{facts}

Veto / риски engine:
{risks}

Рынок сейчас:
{market_brief(market)}

Объясни verdict в 2–3 предложениях по правилам системы."""

    try:
        resp = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=SYSTEM_EXPLAIN,
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.content[0].text.strip()
    except Exception as e:
        return f"⚠️ LLM недоступен: {e}"


# ─── Multi-agent debate (Bull / Bear / Risk → Judge) ──────────────────────

def _agent_call(client, model: str, system: str, prompt: str,
                max_tokens: int = 250) -> str:
    try:
        resp = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.content[0].text.strip()
    except Exception as e:
        return f"[agent error: {e}]"


def debate_and_judge(
    question: str,
    market: dict,
    recent: list,
    client,
    fast_model: str,
    smart_model: str,
    decision: Optional[dict] = None,
    return_parts: bool = False,
):
    """
    3 параллельных Haiku-агента (bull/bear/risk) → 1 Sonnet-judge.

    Если decision передан — judge ОБЯЗАН соблюдать verdict.
    Если decision is None — judge свободно выбирает сторону.

    return_parts=True вернёт dict со всеми частями (для отладки и тестов).
    """
    brief = market_brief(market)

    recent_lines = "\n".join(
        f"  • {r[0]} {r[3]} {r[1]} {r[2]}" for r in (recent or [])[:6]
    ) or "  (нет недавних сигналов)"

    common_ctx = (
        f"Рынок сейчас:\n{brief}\n\n"
        f"Последние сигналы:\n{recent_lines}\n\n"
        f"Вопрос трейдера: {question}"
    )

    with ThreadPoolExecutor(max_workers=3) as ex:
        bull_f = ex.submit(_agent_call, client, fast_model, SYSTEM_BULL, common_ctx, 280)
        bear_f = ex.submit(_agent_call, client, fast_model, SYSTEM_BEAR, common_ctx, 280)
        risk_f = ex.submit(_agent_call, client, fast_model, SYSTEM_RISK, common_ctx, 280)
        bull = bull_f.result()
        bear = bear_f.result()
        risk = risk_f.result()

    verdict_block = ""
    if decision and decision.get("verdict") in ("LONG", "SHORT", "WAIT", "SKIP"):
        verdict_block = (
            "\n\nEngine verdict (ОБЯЗАН СОБЛЮДАТЬ): "
            f"{decision['verdict']}\n"
            f"Confidence: {decision.get('confidence',0)}/100 · "
            f"RR(TP1): {decision.get('rr1') or '—'}\n"
            f"Причина engine: {decision.get('reason','')}"
        )

    judge_prompt = (
        f"{common_ctx}"
        f"{verdict_block}\n\n"
        f"═══ BULL АНАЛИТИК ═══\n{bull}\n\n"
        f"═══ BEAR АНАЛИТИК ═══\n{bear}\n\n"
        f"═══ RISK МЕНЕДЖЕР ═══\n{risk}\n\n"
        "Синтезируй финальный ответ. Цитируй аналитиков. Уважай "
        "engine verdict (если он указан)."
    )

    final = _agent_call(client, smart_model, SYSTEM_JUDGE, judge_prompt, 600)

    if return_parts:
        return {
            "bull":  bull,
            "bear":  bear,
            "risk":  risk,
            "judge": final,
        }
    return final
