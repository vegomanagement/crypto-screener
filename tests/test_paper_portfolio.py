"""Paper journal, portfolio kind (finalists A and D): bar-by-bar replay == portfolio_engine over the
same window (exact equity), no look-ahead, restart, idempotency, warm-up, migration and the API."""
from __future__ import annotations

import functools
import random
import sqlite3

import paper_portfolio as pp
import paper_portfolio_strategies as pps
import paper_strategies as ps
import portfolio_engine as pe
from paper_store import PaperStore

DAY = pe.DAY
H8 = 8 * 3_600_000
T0 = 1_700_000_000_000 // DAY * DAY
SYMS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT")
N_DAYS, START_K = 250, 170


@functools.lru_cache(maxsize=None)
def market_data(seed=11):
    rnd = random.Random(seed)
    bars, fund = {}, {}
    for j, s in enumerate(SYMS):
        px = 100.0 * (j + 1)
        drift = rnd.choice((-1, 1)) * 0.002
        for k in range(N_DAYS):
            if k % 90 == 0:
                drift = -drift
            o = px
            c = o * (1 + drift + rnd.gauss(0, 0.03))
            h, lo = max(o, c) * (1 + abs(rnd.gauss(0, 0.012))), min(o, c) * (1 - abs(rnd.gauss(0, 0.012)))
            bars.setdefault(s, []).append({"ts": T0 + k * DAY, "o": o, "h": h, "l": lo, "c": c, "v": 1000.0})
            px = c
        fr = []
        for k in range(N_DAYS * 3):
            r = 0.0001 + rnd.gauss(0, 0.00005)
            if rnd.random() < 0.02:
                r += rnd.choice((-1, 1)) * 0.002          # crowding spikes → |z| > 2 events for D
            fr.append({"time": T0 + (k + 1) * H8, "rate": r})
        fund[s] = fr
    return bars, fund


def strat(sid):
    reg = dict(ps.REGISTRY[sid]) if sid in ps.REGISTRY else None
    if reg is None:
        pps.register_all()
        reg = dict(ps.REGISTRY[sid])
    reg["params"] = dict(reg["params"], universe=list(SYMS))
    return reg


def start_day():
    return T0 + START_K * DAY


@functools.lru_cache(maxsize=None)
def journal(sid, seed=11):
    bars, fund = market_data(seed)
    store = PaperStore()
    s = strat(sid)
    v = store.ensure_version(s)
    pp.replay(store, s, v, bars, fund, start_day())
    return store, v


def reference(sid, seed=11):
    bars, fund = market_data(seed)
    mkt = pp.build_market(bars, fund, T0 + (N_DAYS - 1) * DAY)
    return pp.simulate(strat(sid), mkt, start_day()) + (mkt,)


def test_replay_bar_by_bar_equals_engine_exactly():
    for sid in (pps.A_ID, pps.D_ID):
        store, v = journal(sid)
        res, start_i, mkt = reference(sid)
        days = store.pf_days(sid, v)
        assert [d["day_ts"] for d in days] == res["ts"]
        assert [d["equity"] for d in days] == res["equity"]                     # exact, not approx
        assert [d["fees"] for d in days] == res["fees"] and [d["funding"] for d in days] == res["funding"]
        pos = store.pf_positions(sid, v)
        assert len(pos) == len(res["trades"]) >= 3, sid
        want = sorted((t["sym"], mkt.days[t["entry_i"]], t["entry_px"], t["pnl"], t["why"],
                       None if t["exit_i"] is None else mkt.days[t["exit_i"]]) for t in res["trades"])
        got = sorted((p["symbol"], p["entry_ts"], p["entry_px"], p["pnl"], p["why"], p["exit_ts"]) for p in pos)
        assert got == want
        assert any(t["funding"] for t in res["trades"])                          # funding accrues per position
        assert not [e for e in store.events(sid, v) if e["kind"] == "divergence"]


def test_every_fill_was_announced_at_the_previous_close():
    import json
    for sid in (pps.A_ID, pps.D_ID):
        store, v = journal(sid)
        orders = {d["day_ts"]: json.loads(d["orders_json"])["orders"] for d in store.pf_days(sid, v)}
        for p in store.pf_positions(sid, v):
            o = orders[p["entry_ts"] - DAY].get(p["symbol"])
            assert o and o["w"] != 0, (sid, p)


def test_no_look_ahead_future_bars_and_funding_are_ignored():
    bars, fund = market_data()
    k = START_K + 40
    cut = T0 + k * DAY
    rnd = random.Random(5)
    bad_bars = {s: [b if b["ts"] <= cut else dict(b, o=b["o"] * 3, h=b["h"] * 4, l=b["l"] * 0.1,
                                                     c=b["c"] * rnd.uniform(0.2, 5)) for b in bb] for s, bb in bars.items()}
    bad_fund = {s: [r if r["time"] <= cut + DAY else dict(r, rate=0.05) for r in rr] for s, rr in fund.items()}
    for sid in (pps.A_ID, pps.D_ID):
        clean, v = journal(sid)
        store = PaperStore()
        s = strat(sid)
        v2 = store.ensure_version(s)
        for d in range(START_K, k + 1):                     # the loader returns the poisoned future too
            pp.observe(store, s, v2, bad_bars, bad_fund, T0 + (d + 1) * DAY + 15 * 60_000)
        got = [(d["day_ts"], d["equity"], d["orders_json"]) for d in store.pf_days(sid, v2)]
        want = [(d["day_ts"], d["equity"], d["orders_json"]) for d in clean.pf_days(sid, v) if d["day_ts"] <= cut]
        assert got == want
        # the engine itself is causal: a full run on poisoned data equals the clean one up to the cut
        res, start_i, _ = reference(sid)
        mkt = pp.build_market(bad_bars, bad_fund, T0 + (N_DAYS - 1) * DAY)
        bad, _ = pp.simulate(s, mkt, start_day())
        n = k - START_K + 1
        assert bad["equity"][:n] == res["equity"][:n]


def test_unsettled_close_is_not_used():
    bars, fund = market_data()
    last_close = T0 + (START_K + 1) * DAY
    assert pp.as_of_day(bars, last_close + 60_000) == T0 + (START_K - 1) * DAY       # 1 min after: too early
    assert pp.as_of_day(bars, last_close + pp.SETTLE_MS) == T0 + START_K * DAY


def test_restart_with_a_gap_and_idempotency(tmp_path):
    bars, fund = market_data()
    path = str(tmp_path / "pj.db")
    for sid in (pps.A_ID, pps.D_ID):
        s = strat(sid)
        one = PaperStore(path)
        v = one.ensure_version(s)
        days = [T0 + d * DAY for d in range(START_K, N_DAYS)]
        mid = len(days) // 2
        for d in days[:mid]:
            pp.observe(one, s, v, bars, fund, d + DAY + 900_000)
        before = one.pf_days(sid, v)
        again = pp.observe(one, s, v, bars, fund, days[mid - 1] + DAY + 900_000)       # same cycle twice
        assert again.get("skipped") == "no new daily close" and one.pf_days(sid, v) == before
        del one
        two = PaperStore(path)                                                          # process restart
        assert two.ensure_version(s) == v
        for d in days[mid + 3:]:                                                        # 3 cycles missed
            pp.observe(two, s, v, bars, fund, d + DAY + 900_000)
        clean, cv = journal(sid)
        got = [(d["day_ts"], d["equity"]) for d in two.pf_days(sid, v)]
        assert got == [(d["day_ts"], d["equity"]) for d in clean.pf_days(sid, cv)]
        late = [d["day_ts"] for d in two.pf_days(sid, v) if d["late"]]
        assert late == days[mid:mid + 3]          # first recorded > 1 day after their close
        pos = [(p["symbol"], p["entry_ts"], p["pnl"], p["exit_ts"]) for p in two.pf_positions(sid, v)]
        assert pos == [(p["symbol"], p["entry_ts"], p["pnl"], p["exit_ts"]) for p in clean.pf_positions(sid, cv)]


def test_run_cycle_fetches_only_when_a_new_close_can_exist():
    bars, fund = market_data()
    s = strat(pps.D_ID)
    store = PaperStore()
    calls = []

    def candles(sym, tfs):
        calls.append(sym)
        return {"D": bars[sym]}

    def funding(sym, a, b):
        return [r for r in fund[sym] if a < r["time"] <= b]
    t = {"now": (start_day() + DAY + pp.SETTLE_MS) / 1000}
    r = pp.run_cycle(store, s, candles, funding, clock=lambda: t["now"])
    assert r["results"][0]["as_of"] == start_day() and len(calls) == len(SYMS)
    t["now"] += 6 * 3600
    assert pp.run_cycle(store, s, candles, funding, clock=lambda: t["now"])["results"][0]["skipped"] and \
        len(calls) == len(SYMS)                                                  # no venue request
    t["now"] = (start_day() + 2 * DAY + pp.SETTLE_MS) / 1000
    r = pp.run_cycle(store, s, candles, funding, clock=lambda: t["now"])
    assert r["results"][0]["new_days"] == 1 and len(calls) == 2 * len(SYMS)


def test_warmup_starts_flat_and_trend_needs_a_fresh_cross():
    bars, _ = market_data()
    for sid in (pps.A_ID, pps.D_ID):
        store, v = journal(sid)
        first = store.pf_days(sid, v)[0]
        assert first["day_ts"] == start_day() and first["equity"] == 1.0 and first["n_pos"] == 0
    res, start_i, mkt = reference(pps.A_ID)
    import portfolio_strategies as pst
    ind = pst.Ind(mkt)
    for t in res["trades"]:
        s, e = t["sym"], t["entry_i"]
        assert any(mkt.c[s][i] <= ind.ema[s][50][i] for i in range(start_i, e - 1)), t   # re-armed after start


def test_kind_migration_keeps_old_databases(tmp_path):
    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE paper_strategies (id TEXT NOT NULL, version INTEGER NOT NULL, params_json TEXT NOT NULL, "
                "params_hash TEXT NOT NULL, code_version TEXT NOT NULL, title TEXT, expectation_json TEXT, "
                "created_ts INTEGER NOT NULL, PRIMARY KEY (id, version), UNIQUE (id, params_hash))")
    con.execute("INSERT INTO paper_strategies VALUES ('smc', 1, '{}', 'h', 'c', 't', 'null', 1)")
    con.commit()
    con.close()
    st = PaperStore(str(path))
    assert [(r["id"], r["kind"]) for r in st.strategies()] == [("smc", "setup")]
    assert st.ensure_version(strat(pps.D_ID)) == 1
    assert {r["id"]: r["kind"] for r in st.strategies()}[pps.D_ID] == "portfolio"


def test_frozen_params_match_the_research_selection():
    import json
    from pathlib import Path
    sel = json.loads((Path(__file__).resolve().parents[1] / "docs/evidence/strategy-research/dev_selection.json")
                     .read_text())["finalists"]
    pps.register_all()
    a, d = ps.REGISTRY[pps.A_ID]["params"], ps.REGISTRY[pps.D_ID]["params"]
    va, vd = sel["A"]["v"], sel["D"]["v"]
    assert (a["source_id"], a["entry"], a["n"], a["ls"], a["k"]) == (va["id"], va["entry"], va["n"], va["ls"], va["k"])
    assert (d["source_id"], d["W"], d["H"], d["trend"]) == (vd["id"], vd["W"], vd["H"], vd["trend"])
    assert a["universe"] == list(ps.DEFAULT_UNIVERSE) and (a["risk"], a["cap"], a["gross"]) == (0.01, 0.30, 2.0)
    assert (d["thr"], d["gross"]) == (2.0, 1.5)
