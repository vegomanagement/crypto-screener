"""Derived, descriptive positioning labels with documented formulas (NOT trade signals).

All thresholds are fixed constants below so the labels are reproducible; each label reports
its formula and the exact inputs it used. Missing inputs -> state None with a reason.
"""
from __future__ import annotations

from decimal import Decimal

from positioning import Q2, dec, fmt, q

NOTE = "Descriptive label of current positioning, not a trade signal."
TOP_VS_CROWD_PP = Decimal("5")         # percentage points of long share
OI_MOVE_PCT = Decimal("1")             # |OI change| over 4h that counts as a move
PRICE_MOVE_PCT = Decimal("0.3")        # |price change| over 4h that counts as a move
TAKER_HIGH, TAKER_LOW = Decimal("1.10"), Decimal("0.90")
CROWD_HIGH, CROWD_LOW = Decimal("0.70"), Decimal("0.30")
WHALE_HIGH, WHALE_LOW = Decimal("0.60"), Decimal("0.40")
LIQ_HIGH, LIQ_LOW = Decimal("0.70"), Decimal("0.30")


def _signal(sid, label, formula, state, value=None, inputs=None, reason=None):
    return {"id": sid, "label": label, "formula": formula, "state": state,
            "value": fmt(value) if isinstance(value, Decimal) else value,
            "inputs": inputs or {}, "reason": None if state else (reason or "inputs unavailable"),
            "note": NOTE}


def top_vs_crowd(top_position, crowd):
    formula = ("d = top-trader POSITION long share − global ACCOUNT long share (Binance), in pp; "
               f"d ≥ +{TOP_VS_CROWD_PP} → top traders more long than the crowd; "
               f"d ≤ −{TOP_VS_CROWD_PP} → more short; else aligned. "
               "Also reported: top position long/short ratio − global account long/short ratio.")
    sid, label = "top_vs_crowd", "Top traders vs crowd"
    top_long, crowd_long = dec((top_position or {}).get("long")), dec((crowd or {}).get("long"))
    if top_long is None or crowd_long is None:
        return _signal(sid, label, formula, None, reason="needs Binance top-position and global ratios")
    d = q((top_long - crowd_long) * 100, Q2)
    ratio_diff = None
    if dec(top_position.get("value")) is not None and dec(crowd.get("value")) is not None:
        ratio_diff = fmt(dec(top_position["value"]) - dec(crowd["value"]))
    state = ("top_more_long" if d >= TOP_VS_CROWD_PP else
             "top_more_short" if d <= -TOP_VS_CROWD_PP else "aligned")
    return _signal(sid, label, formula, state, d, {"top_position_long": fmt(top_long),
                   "global_account_long": fmt(crowd_long), "ratio_diff": ratio_diff})


def oi_price(change_4h):
    formula = (f"Over 4h: OI% = ΔOI/OI, price% = Δ(kline open)/open at the same timestamps. "
               f"OI ≥ +{OI_MOVE_PCT}% & price ≥ +{PRICE_MOVE_PCT}% → longs building; "
               "OI up & price down → shorts building; OI down & price up → short covering; "
               "OI down & price down → long unwinding; otherwise no clear change.")
    sid, label = "oi_price_4h", "OI vs price (4h)"
    if not change_4h or change_4h.get("value") is None or change_4h.get("price_pct") is None:
        return _signal(sid, label, formula, None, reason="needs 4h OI history and price at the same stamps")
    oi, px = dec(change_4h["value"]), dec(change_4h["price_pct"])
    oi_dir = 1 if oi >= OI_MOVE_PCT else -1 if oi <= -OI_MOVE_PCT else 0
    px_dir = 1 if px >= PRICE_MOVE_PCT else -1 if px <= -PRICE_MOVE_PCT else 0
    state = {(1, 1): "longs_building", (1, -1): "shorts_building", (-1, 1): "short_covering",
             (-1, -1): "long_unwinding"}.get((oi_dir, px_dir), "no_clear_change")
    return _signal(sid, label, formula, state, oi, {"oi_pct": fmt(oi), "price_pct": fmt(px)})


def taker_flow(taker):
    formula = (f"Taker buy/sell volume ratio (latest period): ≥ {TAKER_HIGH} buyers aggressive, "
               f"≤ {TAKER_LOW} sellers aggressive, else balanced.")
    ratio = dec((taker or {}).get("value"))
    if ratio is None:
        return _signal("taker_flow", "Taker flow", formula, None, reason="no taker ratio for this venue")
    state = ("buyers_aggressive" if ratio >= TAKER_HIGH else
             "sellers_aggressive" if ratio <= TAKER_LOW else "balanced")
    return _signal("taker_flow", "Taker flow", formula, state, ratio, {"buy_sell_ratio": fmt(ratio)})


def crowd_extreme(crowd):
    formula = (f"Global account long share: ≥ {CROWD_HIGH} crowd heavily long, "
               f"≤ {CROWD_LOW} crowd heavily short, else not extreme.")
    long_share = dec((crowd or {}).get("long"))
    if long_share is None:
        return _signal("crowd_extreme", "Crowd positioning", formula, None,
                       reason="no global account ratio for this venue")
    state = ("crowd_heavily_long" if long_share >= CROWD_HIGH else
             "crowd_heavily_short" if long_share <= CROWD_LOW else "not_extreme")
    return _signal("crowd_extreme", "Crowd positioning", formula, state, long_share,
                   {"long_share": fmt(long_share)})


def funding(basis):
    formula = "Current funding rate sign: > 0 longs pay shorts, < 0 shorts pay longs, 0 neutral."
    rate = dec((basis or {}).get("funding_rate"))
    if rate is None:
        return _signal("funding", "Funding", formula, None, reason="no funding rate")
    state = "longs_pay" if rate > 0 else "shorts_pay" if rate < 0 else "neutral"
    return _signal("funding", "Funding", formula, state, rate, {"funding_rate": fmt(rate)})


def whale_bias(whales):
    formula = (f"Hyperliquid top-N accounts: long notional / (long + short notional): "
               f"≥ {WHALE_HIGH} net long, ≤ {WHALE_LOW} net short, else mixed.")
    agg = (whales or {}).get("aggregate") or {}
    long_n, short_n = dec(agg.get("long_notional")), dec(agg.get("short_notional"))
    if long_n is None or short_n is None or long_n + short_n == 0:
        return _signal("whale_bias", "HL whales", formula, None, reason="no whale positions in this coin yet")
    share = q(long_n / (long_n + short_n))
    state = "whales_net_long" if share >= WHALE_HIGH else "whales_net_short" if share <= WHALE_LOW else "mixed"
    return _signal("whale_bias", "HL whales", formula, state, share,
                   {"long_notional": fmt(long_n), "short_notional": fmt(short_n)})


def liquidation_imbalance(liquidations):
    formula = (f"1h long-liquidation USD / total liquidation USD (lower bound): ≥ {LIQ_HIGH} longs flushed, "
               f"≤ {LIQ_LOW} shorts squeezed, else mixed; nothing recorded → none recorded.")
    win = ((liquidations or {}).get("windows") or {}).get("1h")
    if not win:
        return _signal("liquidation_imbalance", "Liquidations (1h)", formula, None,
                       reason=(liquidations or {}).get("reason") or "liquidation collector has no data")
    long_usd, short_usd = dec(win["long_usd"]), dec(win["short_usd"])
    total = long_usd + short_usd
    if total == 0:
        return _signal("liquidation_imbalance", "Liquidations (1h)", formula, "none_recorded", Decimal(0),
                       {"long_usd": "0", "short_usd": "0"})
    share = q(long_usd / total)
    state = "longs_flushed" if share >= LIQ_HIGH else "shorts_squeezed" if share <= LIQ_LOW else "mixed"
    return _signal("liquidation_imbalance", "Liquidations (1h)", formula, state, share,
                   {"long_usd": fmt(long_usd), "short_usd": fmt(short_usd)})


def derive_signals(snapshot):
    oi_changes = (snapshot.get("open_interest") or {}).get("changes") or {}
    return [top_vs_crowd((snapshot.get("top_traders") or {}).get("position"), snapshot.get("crowd")),
            oi_price(oi_changes.get("4h")),
            taker_flow(snapshot.get("taker")),
            crowd_extreme(snapshot.get("crowd")),
            funding(snapshot.get("basis")),
            whale_bias(snapshot.get("whales")),
            liquidation_imbalance(snapshot.get("liquidations"))]
