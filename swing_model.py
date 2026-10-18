"""SMC swing-trading entry model (the 'right fractal'): one causal scanner for backtest and live.

Model (long; short is the mirror), for a fractal pair HTF → entry TF (e.g. 1d → 4h):
  1. HTF bias = HTF external structure trend (smc.py 5/5) as of the last CLOSED HTF bar.
  2. POI = an HTF zone in the bias direction, still unmitigated on closed HTF bars, in discount
     (zone top ≤ HTF dealing-range equilibrium). ``poi``: 'any' (OB or FVG — the original
     model) | 'ob' | 'fvg' | 'breaker'. ``idm``: 'none' | 'idm' (the zone's inducement — the first
     internal HTF swing low after the zone's structure break — must be confirmed ABOVE the zone
     top, so the tap sweeps it first) | 'eq' (an HTF equal-lows pool formed after the break, above
     the zone top, confirmed). ``sweep``: the tap must follow an entry-TF wick sweep of a swing
     low within ``sweep_bars`` bars (strictly earlier bars for limit entries).
  3. ``entry``:
     'retest'  an entry-TF bar trading into the POI (low ≤ top, close ≥ bottom) arms the setup
               for ``arm_htf_bars`` HTF bars; an entry-TF CHoCH in the bias direction triggers a
               limit at the top of the CHoCH's OB (else its FVG, else the close), filled within
               ``fill_bars`` (maker fill);
     'close'   same arming and trigger, market entry at the CHoCH bar's close (taker);
     'limit'   resting limit at the POI edge (zone top) from HTF bars closed by the bar's OPEN,
               filled when a bar trades to it (at min(top, open)); no entry-TF confirmation.
  4. ``stop``: 'choch' = low of the leg that made the CHoCH − stop_buf_atr × entry-TF ATR14;
     'poi' = zone bottom − stop_buf_atr × HTF ATR14 (limit entries always use the POI stop).
  5. ``target``: 'liq' = HTF dealing-range high (opposing HTF external liquidity), skipped when
     < ``min_rr`` R away; half off at ``partial_r`` R and the stop to break-even. 'r2' / 'r3' =
     everything off at a fixed 2 R / 3 R. ``be``: stop to break-even once 1 R traded. Exit at the
     close after ``max_hold`` bars; stop first when a bar touches both.
  ``filtered=False`` = the same rules without the HTF filter: every entry-TF CHoCH in either
  direction ('retest' / 'close', CHoCH stop), or a touch of any unmitigated HTF zone of the kind
  in either direction ('limit') — no HTF trend, discount, IDM or sweep condition.
Fees: fee_r = 2 × fee × entry / risk; the universe backtest adds slippage and funding itself.
"""
from __future__ import annotations

import bisect

import smc

DEFAULTS = {"ltf_len": 3, "poi": "any", "idm": "none", "sweep": False, "entry": "retest", "stop": "choch",
            "target": "liq", "be": False, "arm_htf_bars": 3, "fill_bars": 12, "min_rr": 2.0, "partial_r": 2.0,
            "max_hold": 300, "stop_buf_atr": 0.1, "sweep_bars": 24}
FIXED_R = {"r2": 2.0, "r3": 3.0}
KIND = {"obs": "ob", "fvgs": "fvg", "breakers": "breaker"}


def htf_arrays(htf, ext=smc.EXTERNAL):
    """Per HTF bar k: trend, dealing-range low/high, ATR14 as of the close of k, plus the zones
    (OB, FVG, breaker) each with its inducement (price, confirm_i) and later equal-H/L pools."""
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
    internal = smc.structure(htf, *smc.INTERNAL, tf="htf", zones=False)["swings"]
    idms = smc.inducements(htf, S["breaks"], internal)
    idm = {(x["break_i"], x["dir"]): x for x in idms}
    atr = smc.atr14(htf)
    pools = smc.equal_levels(htf, internal, atr=atr)
    zones = []
    for kind in ("obs", "fvgs", "breakers"):
        for z in S[kind]:
            z = dict(z, kind=KIND[kind])
            b = z.get("break_i", z["confirm_i"])
            x = idm.get((b, z["dir"]))
            z["idm"] = None if x is None else (x["price"], x["confirm_i"])
            side = "low" if z["dir"] == "bull" else "high"
            z["eq"] = [(q["price"], q["confirm_i"]) for q in pools
                       if q["side"] == side and b < q["first_i"] <= b + smc.ZONE_MAX_AGE]
            zones.append(z)
    zones.sort(key=lambda z: z["confirm_i"])
    return {"trend": S["trend_at"], "lo": lo, "hi": hi, "atr": atr, "zones": zones, "sweeps": S["sweeps"],
            "close_ts": [c["close_ts"] for c in htf], "idm": idms, "pools": pools}


def _beyond(z, price, d):
    return price > z["top"] if d > 0 else price < z["bottom"]


def eligible(z, k, d, H, p, filtered):
    """Is zone z a valid POI for side d as of HTF bar k (every input closed by k)."""
    if (z["dir"] == "bull") != (d > 0):
        return False
    if (p["poi"] == "any" and z["kind"] == "breaker") or (p["poi"] != "any" and z["kind"] != p["poi"]):
        return False
    if not filtered:
        return True
    if H["trend"][k] != d or H["lo"][k] is None or H["hi"][k] is None:
        return False
    eq = (H["lo"][k] + H["hi"][k]) / 2
    if (z["top"] > eq) if d > 0 else (z["bottom"] < eq):
        return False
    if p["idm"] == "idm":
        x = z["idm"]
        return x is not None and x[1] <= k and _beyond(z, x[0], d)
    if p["idm"] == "eq":
        return any(c <= k and _beyond(z, px, d) for px, c in z["eq"])
    return True


class _Zones:
    """Active (confirmed, unmitigated, not invalidated) HTF zones as of HTF bar k."""

    def __init__(self, H):
        self.H, self.zp, self.active, self.k = H, 0, [], None

    def at(self, k):
        if k == self.k:
            return self.active
        Z = self.H["zones"]
        while self.zp < len(Z) and Z[self.zp]["confirm_i"] <= k:
            self.active.append(Z[self.zp])
            self.zp += 1
        self.active = [z for z in self.active if not ((z["invalidated_i"] is not None and z["invalidated_i"] <= k)
                                                      or (z["mitigated_i"] is not None and z["mitigated_i"] <= k))]
        self.k = k
        return self.active


def _poi(z):
    return None if z is None else {k2: z[k2] for k2 in ("kind", "dir", "top", "bottom", "ts")}


def _plan(z, d, H, k, p, a, b):
    """Stop and target of a setup (the entry is set at the fill); None when not tradable."""
    if b is None or (p["stop"] == "poi" and z is not None):
        if z is None or not H["atr"][k]:
            return None
        buf = p["stop_buf_atr"] * H["atr"][k]
        stop = z["bottom"] - buf if d > 0 else z["top"] + buf
    else:
        stop = b["leg_price"] - d * p["stop_buf_atr"] * a
    target = H["hi"][k] if d > 0 else H["lo"][k]
    if p["target"] == "liq" and target is None:
        return None
    return {"d": d, "stop": stop, "target": target, "target_kind": p["target"], "entry_zone": None}


def scan(ltf, htf, params=None, filtered=True, atr=None, H=None, L=None):
    """Trades + the final state. ``atr`` = per-bar ATR14 of ``ltf``; ``H`` / ``L`` = precomputed
    htf_arrays(htf) / smc.structure(ltf, ltf_len, ltf_len) (computed when None)."""
    p = {**DEFAULTS, **(params or {})}
    if atr is None:
        import signal_features as sf
        atr = sf.atr([c["h"] for c in ltf], [c["l"] for c in ltf], [c["c"] for c in ltf])
    H = H or htf_arrays(htf)
    L = L or smc.structure(ltf, p["ltf_len"], p["ltf_len"], tf="ltf")
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
    zc, zo = _Zones(H), _Zones(H)
    limit = p["entry"] == "limit"
    trades, state, used = [], {"mode": "idle"}, set()
    idle_from = -1                         # last bar at which a finished trade released the coin

    def swept_before(d, j, strict):
        s = sweep_i[d]
        q = (bisect.bisect_left(s, j) if strict else bisect.bisect_right(s, j)) - 1
        return q >= 0 and j - s[q] <= p["sweep_bars"]

    def fill(t, j, px, at_close):
        t.update(entry=px, i_fill=j, fill_ts=ltf[j]["close_ts"] if at_close else ltf[j]["ts"])
        t["risk"] = t["d"] * (px - t["stop"])
        if t["risk"] <= 0:
            return None
        if t["target_kind"] != "liq":
            t["target"] = px + t["d"] * FIXED_R[t["target_kind"]] * t["risk"]
        t["rt"] = t["d"] * (t["target"] - px) / t["risk"]
        if t["target_kind"] == "liq" and t["rt"] < p["min_rr"]:
            return None
        a = atr[t["i_signal"]]
        info = {}
        r, ei, why, part = manage(ltf, j, t["d"], px, t["stop"], t["target"], p, at_close=at_close, info=info)
        t.update(r=r, i_exit=ei, exit=why, partial=part, partial_i=info.get("partial_i"), be_i=info.get("be_i"),
                 exit_ts=ltf[ei]["close_ts"], risk_atr=t["risk"] / a if a else None)
        if why == "open":
            return {"mode": "trade", "until": len(ltf), "t": t}
        trades.append(t)
        return {"mode": "trade", "until": ei, "t": t}

    for j, bar in enumerate(ltf):
        k = bisect.bisect_right(hct, bar["close_ts"]) - 1   # last HTF bar CLOSED by this bar's close
        if k < 0:
            continue
        mode = state["mode"]
        if mode == "trade":
            if j >= state["until"]:
                state = {"mode": "idle"}
                idle_from = j
            continue
        if mode == "pending":
            t = state["t"]
            d = t["d"]
            if t["target_kind"] == "liq" and ((bar["h"] >= t["target"]) if d > 0 else (bar["l"] <= t["target"])):
                state = {"mode": "idle", "last": "cancelled: target reached before the retest"}
            elif j > state["expiry"]:
                state = {"mode": "idle", "last": "cancelled: no retest fill"}
            elif (bar["l"] <= t["entry"]) if d > 0 else (bar["h"] >= t["entry"]):
                state = fill(t, j, t["entry"], False) or {"mode": "idle", "last": "invalid risk"}
            continue
        if limit:
            k0 = bisect.bisect_right(hct, bar["ts"]) - 1     # HTF bars closed by this bar's OPEN
            if k0 < 0 or not atr[j]:
                continue
            for z in list(zo.at(k0)):
                d = 1 if z["dir"] == "bull" else -1
                if id(z) in used or not eligible(z, k0, d, H, p, filtered):
                    continue
                edge = z["top"] if d > 0 else z["bottom"]
                if not ((bar["l"] <= edge) if d > 0 else (bar["h"] >= edge)):
                    continue
                if filtered and p["sweep"] and not swept_before(d, j, True):
                    continue
                t = _plan(z, d, H, k0, p, atr[j], None)
                if t is None:
                    continue
                t.update(poi=_poi(z), i_signal=j, signal_ts=bar["ts"], htf_k=k0, entry_type="maker")
                st = fill(t, j, min(edge, bar["o"]) if d > 0 else max(edge, bar["o"]), False)
                if st:
                    used.add(id(z))                  # one limit fill per zone
                    state = st
                    break
            continue
        trend = H["trend"][k]
        if mode == "armed" and (j > state["expiry"] or trend != state["d"]):
            state = {"mode": "idle", "last": "armed setup expired"}
            mode = "idle"
        b = chochs.get(j)
        if b is not None and (mode == "armed" or not filtered) and atr[j]:
            d = 1 if b["dir"] == "bull" else -1
            if not filtered or d == state.get("d"):
                t = _plan(state.get("_zone") if filtered else None, d, H, k, p, atr[j], b)
                if t:
                    t.update(poi=state.get("poi"), i_signal=j, signal_ts=bar["ts"], htf_k=k)
                    if p["entry"] == "close":
                        t["entry_type"] = "taker"
                        state = fill(t, j, bar["c"], True) or {"mode": "idle", "last": "invalid risk"}
                        continue
                    zone, close = ob_at.get((j, b["dir"])), bar["c"]
                    entry = close if zone is None else (min(zone["top"], close) if d > 0 else max(zone["bottom"], close))
                    risk = d * (entry - t["stop"])
                    if risk > 0 and (t["target_kind"] != "liq" or d * (t["target"] - entry) >= p["min_rr"] * risk):
                        t.update(entry=entry, risk=risk, entry_type="maker",
                                 entry_zone=None if zone is None else zone["kind"])
                        state = {"mode": "pending", "t": t, "expiry": j + p["fill_bars"]}
                        continue
        if mode == "idle" and filtered and trend:
            d = trend
            for z in zc.at(k):
                if not eligible(z, k, d, H, p, True):
                    continue
                touch = (bar["l"] <= z["top"] and bar["c"] >= z["bottom"]) if d > 0 else \
                    (bar["h"] >= z["bottom"] and bar["c"] <= z["top"])
                if not touch or (p["sweep"] and not swept_before(d, j, False)):
                    continue
                state = {"mode": "armed", "d": d, "poi": _poi(z), "_zone": z,
                         "expiry": j + p["arm_htf_bars"] * ratio, "armed_i": j}
                break
    state.pop("_zone", None)
    if limit and state["mode"] == "idle" and ltf:
        state["resting"] = resting_limits(ltf, H, p, zo, used, filtered, idle_from)
    return trades, state


def resting_limits(ltf, H, p, zo, used, filtered, idle_from):
    """The POI-edge limit orders that ``scan`` would check on the NEXT entry-TF bar (after the last
    closed one): the same zones, eligibility and plan as the fill loop, from HTF bars closed by the
    last bar's close. ``knowable_ts`` = the later of the HTF close since which the zone has been
    continuously eligible and the bar close at which the last trade released the coin."""
    hct = H["close_ts"]
    kn = bisect.bisect_right(hct, ltf[-1]["close_ts"]) - 1
    if kn < 0:
        return []
    free_ts = ltf[idle_from]["close_ts"] if idle_from >= 0 else ltf[0]["ts"]
    out = []
    for z in zo.at(kn):
        d = 1 if z["dir"] == "bull" else -1
        if id(z) in used or not eligible(z, kn, d, H, p, filtered):
            continue
        t = _plan(z, d, H, kn, p, None, None)
        if t is None:
            continue
        k0 = kn
        while k0 - 1 >= z["confirm_i"] and eligible(z, k0 - 1, d, H, p, filtered):
            k0 -= 1
        edge = z["top"] if d > 0 else z["bottom"]
        risk = d * (edge - t["stop"])
        if risk <= 0:
            continue
        target = edge + d * FIXED_R[t["target_kind"]] * risk if t["target_kind"] != "liq" else t["target"]
        out.append({"poi": _poi(z), "d": d, "edge": edge, "stop": t["stop"], "target": target,
                    "target_kind": t["target_kind"], "htf_k": kn, "htf_close_ts": hct[kn],
                    "zone_confirm_ts": hct[z["confirm_i"]], "eligible_from_ts": hct[k0],
                    "knowable_ts": max(hct[k0], free_ts), "htf_atr": H["atr"][kn], "trend": H["trend"][kn],
                    "range_lo": H["lo"][kn], "range_hi": H["hi"][kn],
                    "sweep_required": bool(filtered and p["sweep"])})
    return out


def manage(bars, j0, d, entry, stop, target, p=None, at_close=False, info=None):
    """Exit of a position filled at ``entry`` on bar j0: (r, exit_i, why, partial_taken).

    Stop first (also on the fill bar); with a 'liq' target half off at ``partial_r`` R and the stop
    to break-even; ``be``: stop to break-even once 1 R traded; the rest at ``target`` or at the
    close after ``max_hold`` bars; 'open' when the data ends. ``at_close``: entered at bar j0's
    close, so bar j0 itself is skipped. ``info`` (dict) receives partial_i / be_i."""
    p = {**DEFAULTS, **(p or {})}
    info = {} if info is None else info
    risk = d * (entry - stop)
    rt = d * (target - entry) / risk
    part_px = entry + d * p["partial_r"] * risk if p["target"] == "liq" else None
    be_px = entry + d * risk if p["be"] else None
    real, left, cur, part = 0.0, 1.0, stop, False
    for q in range(j0 + at_close, min(len(bars), j0 + p["max_hold"] + 1)):
        b = bars[q]
        if (b["l"] <= cur) if d > 0 else (b["h"] >= cur):
            return real + left * d * (cur - entry) / risk, q, "stop", part
        if q == j0 and not at_close:
            continue                       # fill bar: only the stop is assumed to trade after the fill
        if part_px is not None and not part and ((b["h"] >= part_px) if d > 0 else (b["l"] <= part_px)):
            real, left, cur, part = 0.5 * p["partial_r"], 0.5, entry, True
            info["partial_i"] = q
        if be_px is not None and "be_i" not in info and ((b["h"] >= be_px) if d > 0 else (b["l"] <= be_px)):
            cur = entry
            info["be_i"] = q
        if (b["h"] >= target) if d > 0 else (b["l"] <= target):
            return real + left * rt, q, "target", part
    q = min(len(bars), j0 + p["max_hold"] + 1) - 1
    if j0 + p["max_hold"] >= len(bars):
        return None, q, "open", part
    return real + left * d * (bars[q]["c"] - entry) / risk, q, "timeout", part


def fee_r(t, fee):
    return 2 * fee * t["entry"] / t["risk"]
