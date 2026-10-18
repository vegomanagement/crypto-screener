"""Paper journal: routes are read-only, worker gating/once-only, venue data parsing, scheduler."""
from __future__ import annotations

from flask import Flask

import paper_api
import paper_strategies as ps
from paper_data import VenueData, parse_kline
from paper_store import PaperStore, default_path

S = ps.REGISTRY[ps.SMC_ID]


def app_with(store, env=None, start=False):
    app = Flask(__name__, template_folder="../templates")
    paper_api.register_paper_routes(app, store=store, env=env or {}, start=start)
    return app.test_client()


def seed_rows(store):
    v = store.ensure_version(S)
    base = {"strategy_id": ps.SMC_ID, "version": v, "symbol": "BTCUSDT", "side": "long", "knowable_ts": 1000,
            "recorded_ts": 1001, "observed_as_of": 1000, "planned_entry": 100.0, "planned_stop": 95.0,
            "planned_target": 110.0, "entry_type": "maker", "inputs_json": '{"poi": {"kind": "ob"}}'}
    store.insert_setup(dict(base, key="a", state="pending"))
    rid = store.insert_setup(dict(base, key="b", state="pending"))
    store.update_setup(rid, state="target", fill_ts=2000, entry=100.0, stop=95.0, target=110.0, exit_ts=5000,
                       exit_reason="target", r_gross=2.0, fee_r=0.008, fund_r=-0.01, net_r=1.982, net_pct=1.982 * 5)
    rid = store.insert_setup(dict(base, key="c", state="pending", seen_at_start=1))
    store.update_setup(rid, state="stop", fill_ts=900, entry=100.0, stop=95.0, target=110.0, exit_ts=4000,
                       exit_reason="stop", r_gross=-1.0, fee_r=0.01, fund_r=0.0, net_r=-1.01, net_pct=-5.05)
    return v


def test_routes_read_only_and_stats_exclude_warmup():
    store = PaperStore()
    v = seed_rows(store)
    c = app_with(store)
    meta = c.get("/api/paper/strategies").get_json()
    assert meta["paper_only"] and meta["worker"]["enabled"] is False and meta["worker"]["running"] is False
    assert [(r["id"], r["version"], r["active"]) for r in meta["strategies"]] == [(ps.SMC_ID, v, True)]
    tr = c.get(f"/api/paper/trades?strategy={ps.SMC_ID}&state=closed").get_json()
    assert sorted(t["key"] for t in tr["trades"]) == ["b", "c"] and tr["trades"][0]["inputs"]["poi"]["kind"] == "ob"
    assert c.get(f"/api/paper/trades?strategy={ps.SMC_ID}&state=pending").get_json()["trades"][0]["key"] == "a"
    assert c.get("/api/paper/trades?state=bogus").status_code == 400
    st = c.get(f"/api/paper/stats?strategy={ps.SMC_ID}").get_json()
    assert st["n"] == 1 and st["mean_r"] == 1.982 and st["counts"]["seen_at_start"] == 1
    assert st["vs_backtest"]["expected_mean_r"] == 0.136 and st["vs_backtest"]["enough_trades"] is False
    assert c.get("/api/paper/stats").status_code == 400
    for path in ("/api/paper/strategies", "/api/paper/trades", "/api/paper/stats"):
        assert c.post(path).status_code == 405
    page = c.get("/paper").get_data(as_text=True)
    assert "not a signal" in page and "/static/paper-journal.js" in page


def test_worker_off_by_default_and_started_once(tmp_path, monkeypatch):
    assert not paper_api.enabled({}) and paper_api.enabled({"PAPER_JOURNAL": "1"})
    assert default_path({"DB_PATH": str(tmp_path / "signals.db")}) == str(tmp_path / "paper_journal.db")
    assert default_path({"PAPER_DB_PATH": "/x/p.db", "DB_PATH": "/y/s.db"}) == "/x/p.db"
    store = PaperStore(tmp_path / "p.db")
    started = []
    monkeypatch.setattr(paper_api.threading, "Thread",
                        lambda target, name, daemon: type("T", (), {"start": lambda self: started.append(name)})())
    app_with(store, {"PAPER_JOURNAL": "0"}, start=None)
    assert not started and store.path not in paper_api._STARTED
    env = {"PAPER_JOURNAL": "1", "PAPER_UNIVERSE": "BTCUSDT, ethusdt"}
    app_with(store, env, start=None)
    app_with(store, env, start=None)                       # second registration: no second worker
    w = paper_api._STARTED[store.path]
    assert started == ["paper-journal"] and w.symbols == ["BTCUSDT", "ETHUSDT"]
    other = PaperStore(tmp_path / "p.db")                  # another "process" on the same file
    other.path = str(tmp_path / "p.db")
    paper_api._STARTED.pop(store.path)
    assert paper_api.start_worker(other, env, venue=object(), thread=False) is None   # flock held
    paper_api._STARTED.pop("lock_fh").close()


def test_next_wake_prefers_the_4h_close():
    h4 = 4 * 3600
    t = 1_800_000_000 - 1_800_000_000 % h4                 # a 4h boundary (seconds)
    assert paper_api.next_wake(t + 10, 900, 30) == (20.0, "bar_close")
    assert paper_api.next_wake(t + 31, 900, 30) == (900, "interval")
    assert paper_api.next_wake(t + h4 - 300, 900, 30) == (330.0, "bar_close")


def test_venue_klines_closed_only_and_funding_cached():
    calls = []
    now_s = 1_800_100_000

    def http(url):
        calls.append(url)
        if "klines" in url:
            t = 1_800_100_000_000 - 1_800_100_000_000 % 14_400_000
            return [[t - 14_400_000, "1", "2", "0.5", "1.5", "10"], [t, "1.5", "2", "1", "1.2", "3"]]
        return [{"fundingTime": 1_799_990_000_000, "fundingRate": "0.0001"},
                {"fundingTime": 1_799_990_000_000 + 28_800_000, "fundingRate": "-0.0002"}]
    store = PaperStore()
    vd = VenueData(store, http=http, clock=lambda: now_s, sleep=lambda s: None)
    bars = vd.candles("BTCUSDT", ["240"], start_ms=0)["240"]
    assert len(bars) == 1 and bars[0]["close_ts"] <= now_s * 1000          # the forming bar is dropped
    a, b = 1_799_980_000_000, 1_799_990_000_000 + 28_800_000
    assert vd.funding("BTCUSDT", a, b) == [0.0001, -0.0002]
    n = len(calls)
    assert vd.funding("BTCUSDT", a, b - 1) == [0.0001] and len(calls) == n        # served from the cache
    assert parse_kline([0, "1", "2", "0.5", "1.5", "1"], "D")["close_ts"] == 86_400_000


def test_registry_interface_accepts_a_plugin():
    def momentum(candles_by_tf, params):
        return []
    s = ps.register("demo_momentum", momentum, {"lookback": 20}, title="demo", timeframes=("D",), lifecycle_tf="D")
    try:
        assert s["hash"] == ps.params_hash({"lookback": 20}, s["code_version"]) and s["history_start"] is None
        store = PaperStore()
        from paper_journal import observe
        bars = [{"ts": i * 86_400_000, "close_ts": (i + 1) * 86_400_000, "o": 1, "h": 1, "l": 1, "c": 1, "v": 1}
                for i in range(3)]
        assert observe(store, s, store.ensure_version(s), "X", {"D": bars})["warmup"]
    finally:
        ps.REGISTRY.pop("demo_momentum")
