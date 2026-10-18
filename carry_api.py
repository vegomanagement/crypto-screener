"""Funding-carry panel (/carry, /api/carry): delta-neutral long spot + short perp, research only.

Per coin of the 20-coin universe: the current funding rate annualised per venue (Binance last
settled ``lastFundingRate`` from /fapi/v1/premiumIndex with the interval from /fapi/v1/fundingInfo;
Bybit ``fundingRate`` from /v5/market/tickers (the predicted next rate) with ``fundingIntervalHour``;
Hyperliquid ``funding`` from metaAndAssetCtxs, hourly), the trailing 7/30/90-day Binance carry
(sum of prints × 365 / days), the share of negative prints over 90 days, and an ESTIMATED net APR
on total capital = trailing-30d annualised × L/(L+1) − a round trip amortised over a 30-day hold.
Past funding does not predict future funding; the estimate ignores basis moves and rebalancing.
Public endpoints only, cached (current 60 s, history 1 h). No orders, no keys.
"""
from __future__ import annotations

import threading
import time

from flask import Blueprint, jsonify, render_template, request

from funding_data import FundingError, http_json
from paper_strategies import DEFAULT_UNIVERSE

BINANCE, BYBIT, HL = "https://fapi.binance.com", "https://api.bybit.com", "https://api.hyperliquid.xyz/info"
DAY = 86_400_000
SPOT_TAKER, SPOT_TAKER_BNB, PERP_TAKER, SLIP = 0.0010, 0.00075, 0.0005, 0.0001
HOLD_DAYS = 30


def hl_coin(sym):
    base = sym[:-4]
    return "kPEPE" if base == "1000PEPE" else base


def round_trip(spot_fee=SPOT_TAKER):
    """Entry + exit on both legs, per unit of hedged notional."""
    return 2 * (spot_fee + PERP_TAKER + 2 * SLIP)


def net_apr(gross_ann, L=1, spot_fee=SPOT_TAKER, hold_days=HOLD_DAYS):
    """Estimated net APR on total capital (spot notional + perp margin = notional × (L+1)/L)."""
    if gross_ann is None:
        return None
    share = L / (L + 1)
    return gross_ann * share - round_trip(spot_fee) * share * 365 / hold_days


def trailing(rows, now_ms):
    """{7,30,90: annualised sum} and the share of negative prints over 90 days from [{time, rate}]."""
    out = {}
    for d in (7, 30, 90):
        w = [r["rate"] for r in rows if now_ms - d * DAY < r["time"] <= now_ms]
        out[f"ann_{d}d"] = (sum(w) * 365 / d) if w else None
    w90 = [r["rate"] for r in rows if now_ms - 90 * DAY < r["time"] <= now_ms]
    out["neg_share_90d"] = (sum(x < 0 for x in w90) / len(w90)) if w90 else None
    out["prints_90d"] = len(w90)
    return out


class CarryService:
    def __init__(self, http=http_json, clock=time.time, symbols=DEFAULT_UNIVERSE, sleep=time.sleep):
        self.http, self.clock, self.symbols, self.sleep = http, clock, list(symbols), sleep
        self._cache, self._lock = {}, threading.Lock()

    def _cached(self, key, ttl, loader):
        with self._lock:
            hit = self._cache.get(key)
            if hit and self.clock() - hit[0] < ttl:
                return hit[1]
        val = loader()
        with self._lock:
            self._cache[key] = (self.clock(), val)
        return val

    def _try(self, fn, missing, name):
        try:
            return fn()
        except (FundingError, KeyError, TypeError, ValueError) as exc:
            missing[name] = f"{type(exc).__name__}: {str(exc)[:120]}"
            return None

    def current(self, missing):
        def binance():
            prem = {r["symbol"]: float(r["lastFundingRate"]) for r in self.http(f"{BINANCE}/fapi/v1/premiumIndex")}
            iv = {r["symbol"]: int(r["fundingIntervalHours"]) for r in self.http(f"{BINANCE}/fapi/v1/fundingInfo")}
            return {s: (prem[s], iv.get(s, 8)) for s in self.symbols if s in prem}

        def bybit():
            rows = self.http(f"{BYBIT}/v5/market/tickers?category=linear")["result"]["list"]
            by = {r["symbol"]: r for r in rows}
            return {s: (float(by[s]["fundingRate"]), int(by[s].get("fundingIntervalHour") or 8))
                    for s in self.symbols if s in by and by[s].get("fundingRate") not in (None, "")}

        def hyperliquid():
            meta, ctxs = self.http(HL, {"type": "metaAndAssetCtxs"})
            names = {u["name"]: i for i, u in enumerate(meta["universe"])}
            return {s: (float(ctxs[names[hl_coin(s)]]["funding"]), 1) for s in self.symbols if hl_coin(s) in names}
        return self._cached("current", 60, lambda: {
            "binance": self._try(binance, missing, "binance"), "bybit": self._try(bybit, missing, "bybit"),
            "hyperliquid": self._try(hyperliquid, missing, "hyperliquid"), "fetched_at": int(self.clock() * 1000)})

    def history(self, sym):
        def load():
            now = int(self.clock() * 1000)
            rows = self.http(f"{BINANCE}/fapi/v1/fundingRate?symbol={sym}&startTime={now - 90 * DAY}&limit=1000")
            self.sleep(0.1)
            return [{"time": int(r["fundingTime"]), "rate": float(r["fundingRate"])} for r in rows]
        return self._cached(("hist", sym), 3600, load)

    def panel(self, L=1):
        missing = {}
        cur = self.current(missing)
        now = int(self.clock() * 1000)
        rows = []
        for s in self.symbols:
            row = {"symbol": s, "venues": {}}
            for v in ("binance", "bybit", "hyperliquid"):
                x = (cur.get(v) or {}).get(s)
                row["venues"][v] = None if x is None else {"rate": x[0], "interval_h": x[1],
                                                           "ann": x[0] * 24 / x[1] * 365}
            hist = self._try(lambda s=s: self.history(s), missing, f"history:{s}")
            row.update(trailing(hist or [], now))
            row["net_apr_est"] = net_apr(row["ann_30d"], L)
            row["net_apr_est_bnb"] = net_apr(row["ann_30d"], L, SPOT_TAKER_BNB)
            rows.append(row)
        return {"rows": rows, "L": L, "now": now, "fetched_at": cur.get("fetched_at"), "missing": missing,
                "costs": {"spot_taker": SPOT_TAKER, "spot_taker_bnb": SPOT_TAKER_BNB, "perp_taker": PERP_TAKER,
                          "slippage_per_leg": SLIP, "round_trip": round_trip(), "hold_days": HOLD_DAYS},
                "label": "ESTIMATE: trailing 30-day Binance funding annualised × L/(L+1) minus a round trip "
                         "(both legs, entry + exit) amortised over 30 days; ignores basis moves, rebalancing and "
                         "spot borrow; past funding does not predict future funding. Research only, no orders.",
                "sources": {"binance": "GET /fapi/v1/premiumIndex (lastFundingRate), /fapi/v1/fundingInfo, "
                                       "/fapi/v1/fundingRate (90 days)",
                            "bybit": "GET /v5/market/tickers?category=linear (fundingRate = predicted next)",
                            "hyperliquid": "POST /info metaAndAssetCtxs (funding, hourly)"},
                "result_doc": "docs/CARRY_RESULT.md"}


def register_carry_routes(app, *, service=None):
    bp = Blueprint("carry", __name__)
    state, lock = {"svc": service}, threading.Lock()

    def svc():
        with lock:
            if state["svc"] is None:
                state["svc"] = CarryService()
            return state["svc"]

    @bp.get("/api/carry")
    def carry():
        L = request.args.get("L", 1, type=int)
        if L not in (1, 2, 3):
            return jsonify({"error": "L must be 1, 2 or 3"}), 400
        return jsonify(svc().panel(L))

    @bp.get("/carry")
    def carry_page():
        return render_template("carry.html")

    app.register_blueprint(bp)
