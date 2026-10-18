"""Funding carry panel: annualisation, trailing windows, the net-APR estimate and the route; the
paper routes for portfolio / observer strategies."""
from __future__ import annotations

from flask import Flask

import carry_api as ca

DAY = 86_400_000
NOW = 1_800_000_000_000


def fake_http(url, payload=None):
    if url.endswith("/fapi/v1/premiumIndex"):
        return [{"symbol": "BTCUSDT", "lastFundingRate": "0.0001"}, {"symbol": "ETHUSDT", "lastFundingRate": "-0.00005"}]
    if url.endswith("/fapi/v1/fundingInfo"):
        return [{"symbol": "ETHUSDT", "fundingIntervalHours": 4}]
    if "tickers" in url:
        return {"result": {"list": [{"symbol": "BTCUSDT", "fundingRate": "0.0002", "fundingIntervalHour": "8"}]}}
    if payload == {"type": "metaAndAssetCtxs"}:
        return [{"universe": [{"name": "BTC"}, {"name": "kPEPE"}]}, [{"funding": "0.0000125"}, {"funding": "0.00001"}]]
    if "fundingRate?symbol=BTCUSDT" in url:
        return [{"fundingTime": NOW - k * 8 * 3_600_000, "fundingRate": "0.0001" if k % 10 else "-0.0001"}
                for k in range(270)]
    if "fundingRate?symbol=" in url:
        return []
    raise AssertionError(url)


def svc():
    return ca.CarryService(http=fake_http, clock=lambda: NOW / 1000, symbols=("BTCUSDT", "ETHUSDT", "1000PEPEUSDT"),
                           sleep=lambda s: None)


def test_round_trip_and_net_apr_by_hand():
    assert abs(ca.round_trip() - 2 * (0.001 + 0.0005 + 0.0002)) < 1e-15
    # 10.95 % gross annualised at 1x: half the capital earns it; 0.34 % round trip every 30 days
    assert abs(ca.net_apr(0.1095, 1) - (0.1095 / 2 - 0.0034 / 2 * 365 / 30)) < 1e-12
    assert abs(ca.net_apr(0.1095, 3) - (0.1095 * 0.75 - 0.0034 * 0.75 * 365 / 30)) < 1e-12
    assert ca.net_apr(None) is None


def test_panel_annualises_each_venue_and_the_trailing_windows():
    p = svc().panel(1)
    btc = next(r for r in p["rows"] if r["symbol"] == "BTCUSDT")
    assert abs(btc["venues"]["binance"]["ann"] - 0.0001 * 3 * 365) < 1e-12
    assert abs(btc["venues"]["bybit"]["ann"] - 0.0002 * 3 * 365) < 1e-12
    assert abs(btc["venues"]["hyperliquid"]["ann"] - 0.0000125 * 24 * 365) < 1e-12
    eth = next(r for r in p["rows"] if r["symbol"] == "ETHUSDT")
    assert abs(eth["venues"]["binance"]["ann"] - (-0.00005 * 6 * 365)) < 1e-12      # 4 h interval
    # 7 days = prints at NOW - k·8h for k < 21 (k = 21 is exactly 7 d back: excluded), k % 10 == 0 negative
    w7 = [(-0.0001 if k % 10 == 0 else 0.0001) for k in range(21)]
    assert abs(btc["ann_7d"] - sum(w7) * 365 / 7) < 1e-12
    assert btc["prints_90d"] == 270 and abs(btc["neg_share_90d"] - 27 / 270) < 1e-12
    assert abs(btc["net_apr_est"] - ca.net_apr(btc["ann_30d"], 1)) < 1e-15
    pepe = next(r for r in p["rows"] if r["symbol"] == "1000PEPEUSDT")
    assert pepe["venues"]["hyperliquid"]["rate"] == 0.00001 and pepe["ann_30d"] is None and pepe["net_apr_est"] is None


def test_carry_route_and_validation():
    app = Flask(__name__, template_folder="../templates")
    ca.register_carry_routes(app, service=svc())
    c = app.test_client()
    r = c.get("/api/carry?L=2")
    assert r.status_code == 200 and r.json["L"] == 2 and "ESTIMATE" in r.json["label"]
    assert c.get("/api/carry?L=5").status_code == 400
    assert c.get("/carry").status_code == 200


def test_paper_portfolio_and_observer_routes():
    import paper_portfolio as pp
    import paper_portfolio_strategies as pps
    import paper_strategies as ps
    from paper_api import register_paper_routes
    from paper_store import PaperStore
    from tests.test_paper_portfolio import SYMS, market_data, start_day
    pps.register_all()
    s = dict(ps.REGISTRY[pps.D_ID], params=dict(ps.REGISTRY[pps.D_ID]["params"], universe=list(SYMS)))
    store = PaperStore()
    v = store.ensure_version(s)
    bars, fund = market_data()
    for d in range(start_day(), start_day() + 20 * DAY, DAY):
        pp.observe(store, s, v, bars, fund, d + DAY + 900_000)
    app = Flask(__name__, template_folder="../templates")
    register_paper_routes(app, store=store, env={}, start=False)
    c = app.test_client()
    r = c.get(f"/api/paper/portfolio?strategy={pps.D_ID}").json
    assert r["n_days"] == 19 and len(r["curve"]) == 20 and r["curve"][0]["ret_pct"] == 0.0
    assert r["curve"][-1]["btc_pct"] is not None and r["curve"][-1]["band_lo_pct"] < r["curve"][-1]["band_hi_pct"]
    assert "not a signal" in r["note"]
    assert c.get("/api/paper/portfolio?strategy=nope").status_code == 404
    assert c.get(f"/api/paper/observer?strategy={pps.D_ID}").status_code == 404
