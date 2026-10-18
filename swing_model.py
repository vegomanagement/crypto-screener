"""SMC swing-trading entry model (the 'right fractal'): one causal scanner for backtest and live.

Model (long; short is the mirror), for a fractal pair HTF → entry TF (e.g. 4h → 15m):
  1. HTF bias = HTF external structure trend (smc.py 5/5) as of the last CLOSED HTF bar.
  2. POI = an HTF order block or FVG in the bias direction that is still unmitigated on closed
     HTF bars and lies in discount (zone top ≤ HTF dealing-range equilibrium). An entry-TF bar
     trading into it (low ≤ top, close ≥ bottom) arms the setup for ``arm_htf_bars`` HTF bars.
     Variant ``sweep``: the touch must follow an entry-TF wick sweep of a swing low within
     ``sweep_bars`` bars.
  3. Trigger = an entry-TF CHoCH in the bias direction (smc.py, strength ``ltf_len``) while armed.
  4. Entry = limit at the top of the entry-TF OB created by that CHoCH (else its FVG, else the
     close), filled within ``fill_bars``; stop = the low of the leg that made the CHoCH minus
     0.1 ATR; target = the HTF dealing-range high (opposing HTF liquidity) — skipped when it is
     < ``min_rr`` R away. Half off at 2R and the stop to break-even; the rest at the target or at
     the close after ``max_hold`` bars. Stop first when a bar touches both.
  ``filtered=False`` = the same CHoCH → retest entries in either direction without HTF bias/POI.
Fees in R = 2 × fee × entry / risk (taker 0.05 % or maker 0.02 % per side).
"""
from __future__ import annotations

import bisect

import smc

DEFAULTS = {"ltf_len": 3, "poi": "any", "sweep": False, "arm_htf_bars": 3, "fill_bars": 12,
            "min_rr": 2.0, "partial_r": 2.0, "max_hold": 300, "stop_buf_atr": 0.1, "sweep_bars": 24}


def htf_arrays(htf, ext=smc.EXTERNAL):
    """Per HTF bar k: trend, dealing-range low/high/eq as of the close of k, plus the zones."""
    S = smc.structure(htf, *ext, tf="htf")
    n = len(htf)
    lo, hi = [None] * n, [None] * n
    by_c = {}
    for s in S["swings"]:
        by_c.setdefault(s["confirm_i"], []).append(s)
    ch = cl = None
    for k in range(n):
        for s in by_c.get(k, ()):
            if s["kind"] == "H":
                ch = max(c["h"] for c in htf[s["i"]:k + 1])
            else:
                cl = min(c["l"] for c in htf[s["i"]:k + 1])
        if ch is not None:
            ch = max(ch, htf[k]["h"])
        if cl is not None:
            cl = min(cl, htf[k]["l"])
        lo[k], hi[k] = cl, ch
    for kind in ("obs", "fvgs"):
        for z in S[kind]:
            z["kind"] = kind[:-1]
    zones = sorted(S["obs"] + S["fvgs"], key=lambda z: z["confirm_i"])
    return {"trend": S["trend_at"], "lo": lo, "hi": hi, "zones": zones,
            "sweeps": S["sweeps"], "close_ts": [c["close_ts"] for c in htf]}


def scan(ltf, htf, params=None, filtered=True, atr=None):
    """Trades + the final state. ``atr`` = per-bar ATR14 of ``ltf`` (computed when None)."""
    p = {**DEFAULTS, **(params or {})}
    if atr is None:
        import signal_features as sf
        atr = sf.atr([c["h"] for c in ltf], [c["l"] for c in ltf], [c["c"] for c in ltf])
    H = htf_arrays(htf)
    L = smc.structure(ltf, p["ltf_len"], p["ltf_len"], tf="ltf")
    chochs = {b["i"]: b for b in L["breaks"] if b["kind"] == "CHoCH"}
    ob_at = {}
    for z in L["fvgs"]:                      # an OB of the CHoCH wins over its FVG
        ob_at.setdefault((z["confirm_i"], z["dir"]), dict(z, kind="fvg"))
    for z in L["obs"]:
        ob_at[(z["confirm_i"], z["dir"])] = dict(z, kind="ob")
    sweep_i = {1: sorted(s["i"] for s in L["sweeps"] if s["side"] == "low"),
               -1: sorted(s["i"] for s in L["sweeps"] if s["side"] == "high")}
    ratio = max(1, round((htf[1]["ts"] - htf[0]["ts"]) / (ltf[1]["ts"] - ltf[0]["ts"]))) if len(htf) > 1 else 1
    hct = H["close_ts"]
    trades, state, zp, active = [], {"mode": "idle"}, 0, []
    for j, bar in enumerate(ltf):
        k = bisect.bisect_right(hct, bar["close_ts"]) - 1   # last HTF bar CLOSED by this bar's close
        if k < 0:
            continue
        while zp < len(H["zones"]) and H["zones"][zp]["confirm_i"] <= k:
            active.append(H["zones"][zp])
            zp += 1
        active = [z for z in active if not ((z["invalidated_i"] is not None and z["invalidated_i"] <= k)
                                            or (z["mitigated_i"] is not None and z["mitigated_i"] <= k))]
        mode = state["mode"]
        if mode == "trade":
            t = state["t"]
            d, e, risk = t["d"], t["entry"], t["risk"]
            stop_hit = bar["l"] <= state["stop"] if d > 0 else bar["h"] >= state["stop"]
            if stop_hit:
                t["r"] = state["real"] + state["left"] * d * (state["stop"] - e) / risk
                _close(trades, t, j, "stop", state)
                state = {"mode": "idle"}
                continue
            part = e + d * p["partial_r"] * risk
            if not state["partial"] and ((bar["h"] >= part) if d > 0 else (bar["l"] <= part)):
                state.update(partial=True, real=0.5 * p["partial_r"], left=0.5, stop=e)
            if (bar["h"] >= t["target"]) if d > 0 else (bar["l"] <= t["target"]):
                t["r"] = state["real"] + state["left"] * t["rt"]
                _close(trades, t, j, "target", state)
                state = {"mode": "idle"}
            elif j - t["i_fill"] >= p["max_hold"]:
                t["r"] = state["real"] + state["left"] * d * (bar["c"] - e) / risk
                _close(trades, t, j, "timeout", state)
                state = {"mode": "idle"}
            continue
        if mode == "pending":
            t = state["t"]
            d = t["d"]
            if (bar["h"] >= t["target"]) if d > 0 else (bar["l"] <= t["target"]):
                state = {"mode": "idle", "last": "cancelled: target reached before the retest"}
            elif j > state["expiry"]:
                state = {"mode": "idle", "last": "cancelled: no retest fill"}
            elif (bar["l"] <= t["entry"]) if d > 0 else (bar["h"] >= t["entry"]):
                t["i_fill"], t["fill_ts"] = j, bar["ts"]
                if (bar["l"] <= t["stop"]) if d > 0 else (bar["h"] >= t["stop"]):
                    t["r"] = -1.0
                    _close(trades, t, j, "stop", {"partial": False})
                    state = {"mode": "idle"}
                else:
                    state = {"mode": "trade", "t": t, "stop": t["stop"], "partial": False, "real": 0.0, "left": 1.0}
            continue
        trend = H["trend"][k]
        if mode == "armed" and (j > state["expiry"] or trend != state["d"]):
            state = {"mode": "idle", "last": "armed setup expired"}
            mode = "idle"
        b = chochs.get(j)
        if b is not None and (mode == "armed" or not filtered):
            d = 1 if b["dir"] == "bull" else -1
            if (not filtered or d == state.get("d")) and atr[j]:
                t = _entry(ltf, j, b, d, ob_at.get((j, b["dir"])), H, k, atr[j], p)
                if t:
                    t.update(poi=state.get("poi"), i_signal=j, signal_ts=bar["ts"], htf_k=k)
                    state = {"mode": "pending", "t": t, "expiry": j + p["fill_bars"]}
                    continue
        if mode == "idle" and filtered and trend:
            d = trend
            eq = None if H["lo"][k] is None or H["hi"][k] is None else (H["lo"][k] + H["hi"][k]) / 2
            for z in active:
                if (z["dir"] == "bull") != (d > 0) or (p["poi"] != "any" and z["kind"] != p["poi"]) or eq is None:
                    continue
                if (z["top"] > eq) if d > 0 else (z["bottom"] < eq):
                    continue
                touch = (bar["l"] <= z["top"] and bar["c"] >= z["bottom"]) if d > 0 else \
                    (bar["h"] >= z["bottom"] and bar["c"] <= z["top"])
                if not touch:
                    continue
                if p["sweep"]:
                    s = sweep_i[d]
                    q = bisect.bisect_right(s, j) - 1
                    if q < 0 or j - s[q] > p["sweep_bars"]:
                        continue
                state = {"mode": "armed", "d": d, "poi": {k2: z[k2] for k2 in ("kind", "dir", "top", "bottom", "ts")},
                         "expiry": j + p["arm_htf_bars"] * ratio, "armed_i": j}
                break
    return trades, state


def _entry(ltf, j, b, d, zone, H, k, a, p):
    close = ltf[j]["c"]
    entry = close if zone is None else (min(zone["top"], close) if d > 0 else max(zone["bottom"], close))
    stop = b["leg_price"] - d * p["stop_buf_atr"] * a
    target = H["hi"][k] if d > 0 else H["lo"][k]
    if target is None:
        return None
    risk = d * (entry - stop)
    if risk <= 0:
        return None
    rt = d * (target - entry) / risk
    if rt < p["min_rr"]:
        return None
    return {"d": d, "entry": entry, "stop": stop, "target": target, "risk": risk, "rt": rt,
            "risk_atr": risk / a, "entry_zone": None if zone is None else zone["kind"]}


def _close(trades, t, j, why, state):
    t.update(i_exit=j, exit=why, partial=bool(state.get("partial")))
    trades.append(t)


def fee_r(t, fee):
    return 2 * fee * t["entry"] / t["risk"]
