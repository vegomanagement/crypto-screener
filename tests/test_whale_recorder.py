"""Whale recorder (C): event derivation from snapshot diffs, idempotency, restart, gating, the shared
tracker's leaderboard caching, and the observer's no-look-ahead follow trades."""
from __future__ import annotations

from decimal import Decimal

import paper_strategies as ps
import whale_observer as wo
import whale_recorder as wr
from paper_store import PaperStore
from whale_store import WhaleStore

MIN, DAY = 60_000, 86_400_000
T = 1_800_000_000_000


def P(szi, entry="100", value=None):
    szi = Decimal(szi)
    return {"szi": szi, "entry": Decimal(entry), "value": abs(szi) * Decimal(entry) if value is None else Decimal(value),
            "liq": None, "upnl": Decimal("1.5"), "leverage": 5}


def poll(ts, n=2):
    return {"started_at": ts - 50_000, "finished_at": ts, "requested": n, "ok": n, "failed": 0}


LB = {"fetched_at": (T - 3600_000) / 1000, "rows": [
    {"address": "0xa", "account_value": Decimal("9e6"), "display_name": None, "pnl": {"month": Decimal("500")}},
    {"address": "0xb", "account_value": Decimal("8e6"), "display_name": None, "pnl": {"month": Decimal("-20")}}]}


def rec(store, ts, states, lb=LB):
    return wr.record(store, ts, poll(ts), states, lb, interval_ms=5 * MIN, full_every_ms=60 * MIN, recorded_ts=ts + 1)


def test_classify_all_kinds():
    c = wr.classify
    D = Decimal
    assert [c(D(0), D(1)), c(D(1), D(2)), c(D(2), D(1)), c(D(1), D(0)), c(D(1), D(-1)), c(D(-1), D(-3)), c(D(1), D(1))] == \
        ["open", "add", "reduce", "close", "flip", "add", None]


def test_events_from_snapshot_diffs_baseline_and_gaps():
    st = WhaleStore()
    r0 = rec(st, T, {"0xa": {"BTC": P("1"), "ETH": P("-2")}, "0xb": {}})
    assert r0["events"] == 0 and r0["full"] == 1                     # first sight = baseline, never events
    r1 = rec(st, T + 5 * MIN, {"0xa": {"BTC": P("1.5"), "ETH": P("1"), "SOL": P("10")}, "0xb": {"BTC": P("-3")}})
    ev = {(e["address"], e["coin"]): e for e in st.events()}
    assert {k: e["kind"] for k, e in ev.items()} == {("0xa", "BTC"): "add", ("0xa", "ETH"): "flip",
                                                    ("0xa", "SOL"): "open", ("0xb", "BTC"): "open"}
    e = ev[("0xa", "ETH")]
    assert (e["from_szi"], e["to_szi"], e["side"], e["prev_ts"], e["knowable_ts"], e["gap"]) == \
        ("-2", "1", "long", T, T + 5 * MIN, 0)
    assert r1["full"] == 0 and r1["rows"] == 4                         # only changed positions are written
    rec(st, T + 10 * MIN, {"0xa": {"BTC": P("0.5"), "ETH": P("1"), "SOL": P("10")}})       # 0xb did not answer
    rec(st, T + 60 * MIN, {"0xa": {"ETH": P("1"), "SOL": P("10")}, "0xb": {}})
    kinds = [(e["address"], e["coin"], e["kind"], e["gap"]) for e in st.events(since_ts=T + 10 * MIN)]
    assert kinds == [("0xa", "BTC", "reduce", 0), ("0xa", "BTC", "close", 1), ("0xb", "BTC", "close", 1)]
    closed = st.q("SELECT szi, full FROM whale_positions WHERE poll_ts=? AND address='0xa' AND coin='BTC'", (T + 60 * MIN,))
    assert closed == [{"szi": "0", "full": 1}]


def test_idempotent_and_restart_safe(tmp_path):
    path = str(tmp_path / "w.db")
    one = WhaleStore(path)
    rec(one, T, {"0xa": {"BTC": P("1")}})
    n = one.counts()
    assert rec(one, T, {"0xa": {"BTC": P("9")}})["skipped"] == "already recorded"
    assert one.counts() == n
    del one
    two = WhaleStore(path)                                             # process restart: state comes from the DB
    rec(two, T + 5 * MIN, {"0xa": {"BTC": P("-1")}})
    assert [(e["kind"], e["prev_ts"]) for e in two.events()] == [("flip", T)]
    assert two.leaderboard_at(T)[0]["pnl_month"] == "500"


class FakeTracker:
    def __init__(self, ts):
        import threading
        self._lock = threading.RLock()
        self.leaderboard_ttl_s, self.error, self.leaderboard = 3600, None, LB
        self.ts, self.calls = ts, 0
        self.last_poll, self.states = None, {}

    def poll_once(self):
        self.calls += 1
        self.last_poll, self.states = poll(self.ts), {"0xa": {"BTC": P("1")}}
        return self.last_poll


def test_recorder_reuses_a_fresh_poll_and_is_gated(tmp_path):
    tr = FakeTracker(T)
    r = wr.WhaleRecorder(WhaleStore(), tr, interval_s=300, clock=lambda: (T + 60_000) / 1000)
    r.record_once()
    r.record_once()                                                    # poll is 1 min old < half interval: reused
    assert tr.calls == 1 and r.store.counts()["polls"] == 1
    assert wr.start(lambda: tr, env={}) is None                        # off by default
    env = {"WHALE_RECORDER": "1", "WHALES_DB_PATH": str(tmp_path / "g.db"), "WHALE_LEADERBOARD_TTL_MIN": "360"}
    started = wr.start(lambda: tr, env=env, thread=False)
    assert started is not None and tr.leaderboard_ttl_s == 21600
    assert wr.start(lambda: tr, env=env, thread=False) is started      # once per DB per process


def test_shared_tracker_does_not_redownload_the_leaderboard_after_errors():
    from hl_whale_tracker import WhaleTracker
    calls = {"n": 0}

    def opener():
        calls["n"] += 1
        raise OSError("boom")

    now = {"t": 1000.0}
    tr = WhaleTracker(client=None, open_leaderboard=opener, clock=lambda: now["t"])
    tr.leaderboard = {"rows": [], "total_rows": 0, "fetched_at": -9000.0, "duration_s": 1}
    for _ in range(5):
        tr.load_leaderboard()
        now["t"] += 60
    assert calls["n"] == 1                                             # retried at most every 10 min


def test_observer_gating():
    ps.load_plugins({})
    assert wo.OBS_ID not in ps.REGISTRY
    ps.load_plugins({"WHALE_RECORDER": "1"})
    assert ps.REGISTRY[wo.OBS_ID]["kind"] == "observer"
    ps.load_plugins({})
    assert wo.OBS_ID not in ps.REGISTRY


class FakePrices:
    def __init__(self):
        self.asked = []

    def open_at(self, coin, t):
        self.asked.append(t)
        return 100.0 + (t - T) / DAY

    def funding(self, coin, a, b):
        return 0.0001 * (b - a) / 3_600_000


def test_observer_follows_top_k_without_look_ahead():
    whales = WhaleStore()
    rec(whales, T, {"0xa": {}, "0xb": {}})
    lb_late = dict(LB, fetched_at=(T + 6 * MIN) / 1000, rows=[dict(LB["rows"][0], pnl={"month": Decimal("-9")}),
                                                              dict(LB["rows"][1], pnl={"month": Decimal("9")})])
    rec(whales, T + 5 * MIN + 1234, {"0xa": {"BTC": P("2")}, "0xb": {"ETH": P("-1")}})
    rec(whales, T + 10 * MIN, {"0xa": {"BTC": P("2")}, "0xb": {"ETH": P("-1")}}, lb=lb_late)
    store = PaperStore()
    ps.load_plugins({"WHALE_RECORDER": "1"})
    strat = dict(ps.REGISTRY[wo.OBS_ID], params=dict(wo.PARAMS, top_k=1))
    clock = {"t": (T - MIN) / 1000}
    prices = FakePrices()
    wo.cycle(store, strat, clock=lambda: clock["t"], whales=whales, prices=prices)      # start before the events
    clock["t"] = (T + 5 * MIN + 1234 + 30_000) / 1000
    wo.cycle(store, strat, clock=lambda: clock["t"], whales=whales, prices=prices)
    rows = store.q("SELECT * FROM paper_follow ORDER BY horizon_d")
    assert [(r["address"], r["coin"], r["side"], r["horizon_d"], r["wallet_rank"]) for r in rows] == \
        [("0xa", "BTC", "long", h, 1) for h in (1, 3, 7)]             # top-1 as known at the event, not later
    t0 = rows[0]["entry_ts"]
    assert t0 == T + 6 * MIN and t0 > rows[0]["knowable_ts"]
    assert all(r["state"] == "waiting_entry" for r in rows) and not prices.asked        # minute not finished yet
    clock["t"] = (t0 + 2 * DAY) / 1000
    wo.cycle(store, strat, clock=lambda: clock["t"], whales=whales, prices=prices)
    wo.cycle(store, strat, clock=lambda: clock["t"], whales=whales, prices=prices)      # idempotent
    rows = {r["horizon_d"]: r for r in store.q("SELECT * FROM paper_follow")}
    assert len(rows) == 3 and rows[1]["state"] == "closed" and rows[3]["state"] == "open"
    assert max(prices.asked) <= clock["t"] * 1000 - MIN                # never priced a minute not yet closed
    rec(whales, T + 60 * MIN, {"0xa": {"BTC": P("-1")}, "0xb": {"ETH": P("-1")}})       # flip within 24 h
    rec(whales, T + 25 * 3_600_000, {"0xa": {"BTC": P("1")}, "0xb": {"ETH": P("-1")}},  # flip after 24 h
        lb=dict(LB, fetched_at=(T + 24 * 3_600_000) / 1000))                            # 0xa top-1 again
    wo.cycle(store, strat, clock=lambda: (T + 26 * 3_600_000) / 1000, whales=whales, prices=prices)
    followed = [r["knowable_ts"] for r in store.q("SELECT DISTINCT knowable_ts FROM paper_follow ORDER BY 1")]
    assert followed == [T + 5 * MIN + 1234, T + 25 * 3_600_000]          # one per wallet+coin per 24 h
    r = rows[1]
    gross = r["exit_px"] / r["entry_px"] - 1
    assert abs(r["ret_net"] - (gross - 2 * 0.00045 - 0.0001 * 24)) < 1e-12
    ps.load_plugins({})
