"""Paper journal: replay consistency with the backtest scanner, no look-ahead, restarts, versions."""
from __future__ import annotations

import functools
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import backtest_swing_universe as B  # noqa: E402
import mtf_data  # noqa: E402
import paper_journal as pj  # noqa: E402
import paper_stats  # noqa: E402
import paper_strategies as ps  # noqa: E402
import swing_model as sm  # noqa: E402
from paper_store import CLOSED_STATES, OPEN_STATES, PaperStore  # noqa: E402
from tests.test_mtf import walk  # noqa: E402

S = ps.REGISTRY[ps.SMC_ID]
H8 = 8 * 3_600_000


def data(seed):
    h1 = walk(24 * 330, 3_600_000, seed=seed)
    return {"240": mtf_data.aggregate(h1, "240", 3_600_000), "D": mtf_data.aggregate(h1, "D", 3_600_000)}


def rates(c):
    """Synthetic funding history every 8 h (venue-like), deterministic."""
    t0 = c["240"][0]["ts"]
    return [{"time": t0 + k * H8, "rate": 0.0001 * ((k % 7) - 2)} for k in range((c["240"][-1]["close_ts"] - t0) // H8 + 1)]


def funding_fn(rows):
    def f(symbol, a, b):
        return [r["rate"] for r in rows if a < r["time"] <= b]
    return f


def rowkey(r):
    return (r["key"], r["side"], r["fill_ts"], round(r["entry"], 8), round(r["stop"], 8), round(r["target"], 8),
            r["exit_ts"], r["exit_reason"], round(r["r_gross"], 8))


def tkey(t):
    return (ps.poi_key(t["poi"]), "long" if t["d"] > 0 else "short", t["fill_ts"], round(t["entry"], 8),
            round(t["stop"], 8), round(t["target"], 8), t["exit_ts"], t["exit"], round(t["r"], 8))


@functools.lru_cache(maxsize=None)
def run(seed, start_back=450):
    """Replay from the warm-up bar to the end (cached: the tests only read the result)."""
    c = data(seed)
    store = PaperStore()
    v = store.ensure_version(S)
    start = len(c["240"]) - start_back
    fr = rates(c)
    pj.replay(store, S, v, "TEST", c, start, funding=funding_fn(fr))
    return c, store, v, start, fr


@pytest.mark.parametrize("seed", [15, 21])
def test_replay_bar_by_bar_equals_backtest_scanner(seed):
    c, store, v, start, fr = run(seed)
    ltf, htf = c["240"], c["D"]
    t_start = ltf[start]["close_ts"]
    trades, state = sm.scan(ltf, htf, S["params"]["scan"])
    rows = store.setups(ps.SMC_ID, v, "TEST")
    closed = [r for r in rows if r["state"] in CLOSED_STATES]
    want = [t for t in trades if t["exit_ts"] > t_start]             # not yet closed at the warm-up
    assert want, "fixture must produce trades after the start"
    assert sorted(map(rowkey, closed)) == sorted(map(tkey, want))
    assert not store.events(ps.SMC_ID, v), "every fill must have been recorded before it happened"
    # counted = filled after the start from setups recorded after the start
    counted = sorted(rowkey(r) for r in closed if not r["seen_at_start"])
    assert set(counted) <= {tkey(t) for t in want if t["fill_ts"] >= t_start}
    # open position and resting orders at the end agree with the scanner's final state
    opened = [r for r in rows if r["state"] in OPEN_STATES]
    if state["mode"] == "trade" and state["t"]["exit"] == "open":
        assert [(r["key"], r["fill_ts"]) for r in opened] == [(ps.poi_key(state["t"]["poi"]), state["t"]["fill_ts"])]
    else:
        assert not opened
    assert {r["key"] for r in rows if r["state"] == "pending"} == {ps.poi_key(o["poi"]) for o in state.get("resting", ())}
    # costs are the backtest's own: same fee and funding per trade (funding index from the same rates)
    ts, cum, s = [], [], 0.0
    for x in fr:
        s += x["rate"]
        ts.append(x["time"])
        cum.append(s)
    by = {tkey(t): t for t in want}
    for r in closed:
        fee, _, fund = B.costs(by[rowkey(r)], (ts, cum), ltf)
        assert r["fee_r"] == pytest.approx(fee, abs=1e-6) and r["fund_r"] == pytest.approx(fund, abs=1e-6)
        assert r["net_r"] == pytest.approx(r["r_gross"] - fee + fund, abs=2e-6)


@pytest.mark.parametrize("seed", [15, 21])
def test_setups_are_recorded_when_knowable_never_before_and_planned_from_closed_bars(seed):
    c, store, v, start, _ = run(seed)
    ltf = c["240"]
    closes = {b["close_ts"] for b in ltf}
    for r in store.setups(ps.SMC_ID, v, "TEST"):
        assert r["knowable_ts"] in closes or r["seen_at_start"]
        assert r["observed_as_of"] >= r["knowable_ts"] and r["recorded_ts"] > r["observed_as_of"]
        if not r["seen_at_start"]:
            assert r["knowable_ts"] == r["observed_as_of"] and not r["late"]    # bar-by-bar: seen at once
        if r["fill_ts"] is not None:
            assert r["observed_as_of"] <= r["fill_ts"]                # resting before the fill bar opened
            d = 1 if r["side"] == "long" else -1
            assert d * (r["entry"] - r["planned_entry"]) <= 1e-9       # filled at the edge or better (gap)


def test_no_look_ahead_poisoned_future():
    c = data(15)
    ltf = c["240"]
    cut = len(ltf) - 150
    t = ltf[cut]["close_ts"]
    bad = {tf: [b if b["close_ts"] <= t else dict(b, h=b["h"] * 3, l=b["l"] / 3, c=b["c"] * 2) for b in bars]
           for tf, bars in c.items()}
    a, b = PaperStore(), PaperStore()
    for st, src in ((a, c), (b, bad)):
        v = st.ensure_version(S)
        pj.replay(st, S, v, "TEST", pj.slice_closed(src, t), cut - 300)
    strip = ("id", "recorded_ts", "updated_ts")
    rows = lambda st: [{k: x for k, x in r.items() if k not in strip} for r in st.setups()]  # noqa: E731
    assert rows(a) == rows(b) and rows(a)
    # the strategy itself, fed the poisoned future, says the same about everything closed by t
    full = ps.smc_swing(bad, S["params"])
    prefix = ps.smc_swing(pj.slice_closed(c, t), S["params"])
    done = lambda xs: sorted((s["key"], s["fill_ts"], s["exit_ts"], s["r"]) for s in xs  # noqa: E731
                             if s["status"] == "closed" and s["exit_ts"] <= t)
    assert done(full) == done(prefix)


def test_restart_is_idempotent_and_resumes(tmp_path):
    c = data(21)
    ltf = c["240"]
    start = len(ltf) - 450
    path = tmp_path / "paper.db"
    one = PaperStore(path)
    v = one.ensure_version(S)
    mid = start + 225
    fund = funding_fn(rates(c))
    pj.replay(one, S, v, "TEST", pj.slice_closed(c, ltf[mid]["close_ts"]), start, funding=fund)
    n_before = len(one.setups())
    t_mid = ltf[mid]["close_ts"]
    pj.observe(one, S, v, "TEST", pj.slice_closed(c, t_mid), t_mid + 5000, fund)     # same bar again
    assert len(one.setups()) == n_before
    two = PaperStore(path)                                            # "restart": new process, same file
    assert two.ensure_version(S) == v
    pj.replay(two, S, v, "TEST", c, mid, funding=fund)
    assert two.symbol_state(ps.SMC_ID, v, "TEST")["started_as_of"] == ltf[start]["close_ts"]
    ref = run(21)[1]
    strip = ("id", "recorded_ts", "updated_ts")
    rows = lambda st: sorted(repr([(k, r[k]) for k in sorted(r) if k not in strip]) for r in st.setups())  # noqa: E731
    assert rows(two) == rows(ref)
    assert len({(r["key"], r["knowable_ts"]) for r in two.setups()}) == len(two.setups())


def test_downtime_gap_never_records_retroactively():
    c = data(15)
    ltf = c["240"]
    start = len(ltf) - 450
    st = PaperStore()
    v = st.ensure_version(S)
    pj.replay(st, S, v, "TEST", pj.slice_closed(c, ltf[start + 50]["close_ts"]), start)
    pj.replay(st, S, v, "TEST", c, len(ltf) - 1)                      # 400 bars of downtime, one cycle
    trades, _ = sm.scan(ltf, c["D"], S["params"]["scan"])
    gap = (ltf[start + 50]["close_ts"], ltf[-1]["close_ts"])
    filled_in_gap = [t for t in trades if gap[0] <= t["fill_ts"] < gap[1]]
    rows = st.setups()
    assert filled_in_gap
    for t in filled_in_gap:
        rec = [r for r in rows if r["key"] == ps.poi_key(t["poi"]) and r["fill_ts"] == t["fill_ts"]]
        if rec:                                                       # only when recorded before the gap
            assert rec[0]["observed_as_of"] <= gap[0]
        else:
            assert any(e["kind"] == "missed_fill" and e["ts"] == t["fill_ts"] for e in st.events())
    assert all(r["observed_as_of"] in (r["knowable_ts"], ltf[-1]["close_ts"]) or r["seen_at_start"] for r in rows)
    assert any(r["late"] for r in rows) or not [r for r in rows if r["observed_as_of"] == ltf[-1]["close_ts"]]


def test_versions_are_append_only():
    st = PaperStore()
    v1 = st.ensure_version(S)
    assert st.ensure_version(S) == v1
    other = ps.register("tmp_variant", ps.smc_swing, {**S["params"], "scan": {**S["params"]["scan"], "target": "r3"}},
                        title="t", timeframes=("D", "240"), lifecycle_tf="240", code=(sm,))
    changed = dict(S, params=other["params"], hash=other["hash"])
    v2 = st.ensure_version(changed)
    assert v2 == v1 + 1
    rows = st.strategies()
    assert [r["version"] for r in rows if r["id"] == ps.SMC_ID] == [1, 2]
    assert rows[0]["params"] == S["params"] and rows[0]["params_hash"] == S["hash"]
    assert ps.params_hash(S["params"], S["code_version"]) == S["hash"]
    assert ps.params_hash(S["params"], "other-code") != S["hash"]
    ps.REGISTRY.pop("tmp_variant")


def test_warmup_rows_are_tagged_and_excluded_from_stats():
    c, store, v, start, _ = run(21)
    rows = store.setups(ps.SMC_ID, v, "TEST")
    warm = [r for r in rows if r["seen_at_start"]]
    assert warm and all(r["observed_as_of"] == c["240"][start]["close_ts"] for r in warm)
    s = paper_stats.summarize(rows, S["expectation"])
    counted = [r for r in rows if r["state"] in CLOSED_STATES and not r["seen_at_start"]]
    assert s["n"] == len(counted) and s["counts"]["seen_at_start"] == len(warm)
    assert s["vs_backtest"]["expected_mean_r"] == 0.136 and "30–50" in s["note"]


def test_stats_math():
    assert paper_stats.wilson(0, 0) is None
    lo, hi = paper_stats.wilson(35, 100)
    assert 0.26 < lo < 0.35 < hi < 0.45
    rows = [{"id": i, "symbol": "X", "exit_ts": 1_700_000_000_000 + i * 40 * 86_400_000, "net_r": x}
            for i, x in enumerate([2.0, -1.0, -1.0, 0.0, 2.0, -1.0])]
    pts, dd = paper_stats.equity(rows)
    assert [p["cum_r"] for p in pts] == [2.0, 1.0, 0.0, 0.0, 2.0, 1.0] and dd == 2.0
    lo, hi, how = paper_stats.bootstrap_ci(rows)
    assert lo <= sum(r["net_r"] for r in rows) / 6 <= hi and how == "month-block"
