"""Hermes analyst routes; importing performs no network I/O and creates no database.

POST /api/agent/analysis {exchange, symbol, interval[, force]} → validated analysis
GET  /api/agent/stats[?exchange&symbol&interval]               → calibration stats
The browser never talks to Hermes; the gateway URL/key stay server-side.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from threading import Lock

from flask import Blueprint, jsonify, request

from liquidity_api import valid_identity

log = logging.getLogger(__name__)


def valid_interval(interval):
    from chart_data import list_chart_intervals
    return isinstance(interval, str) and interval in {
        i["value"] for i in list_chart_intervals() if i["available"] and i.get("source") != "trades"}


def interval_duration_ms(interval):
    from chart_data import _duration
    return _duration(interval)


def default_fetch_bars(exchange, symbol, interval, after_ms, count):
    """Closed bars opening at/after ``after_ms`` (the analysed bar's close), oldest first."""
    from chart_data import build_chart_snapshot
    from exchange_markets import fetch_exchange_candles

    from analyst_store import BarsUnavailable
    elapsed = int(time.time() * 1000) - after_ms
    need = elapsed // interval_duration_ms(interval) + 2
    if need > 500:
        raise BarsUnavailable("analysis too old for the candle window")
    chart = build_chart_snapshot(symbol, interval, int(max(need, count)),
                                 lambda s, i, n: fetch_exchange_candles(exchange, s, i, n),
                                 exchange=exchange)
    return [c for c in chart["candles"] if c["ts"] >= after_ms]


def app_positioning(app):
    """Positioning provider bound late to the app's PositioningService (registered later).

    ``watch=False``: an analysis reads whatever the liquidation collector and the whale
    tracker already hold and never starts them (cold collectors → null + reason).
    """
    def provider(exchange, symbol, period):
        ext = app.extensions.get('positioning')
        if not ext:
            raise RuntimeError('positioning routes are not registered')
        return ext['get_service']().snapshot(exchange, symbol, period, owner='analyst', watch=False)
    return provider


def register_agent_routes(app, *, market=None, confluence=None, funding=None, service_factory=None,
                          store_path=None, fetch_bars=None, env=None, positioning=None):
    """``market``/``confluence`` are screener callables (late-bound lambdas are fine)."""
    blueprint = Blueprint('agent', __name__)
    path = Path(store_path) if store_path else Path(app.root_path) / '.data' / 'agent_analyses.sqlite3'
    lock = Lock()
    state = {'service': None, 'store': None}

    def get_store():
        with lock:
            if state['store'] is None:
                from analyst_store import AnalysisStore
                state['store'] = AnalysisStore(path)
            return state['store']

    def get_service():
        if state['service'] is None:
            store = get_store()
            with lock:
                if state['service'] is None:
                    if service_factory:
                        state['service'] = service_factory(store)
                    else:
                        from analyst import default_providers
                        from analyst_hermes import config_from_env, make_transport
                        from analyst_service import AnalystService
                        config = config_from_env(env)
                        state['service'] = AnalystService(
                            default_providers(market=market, confluence=confluence, funding=funding,
                                              positioning=positioning or app_positioning(app)),
                            make_transport(config), store, configured=config is not None)
        return state['service']

    @blueprint.before_request
    def protect_post():
        if request.method != 'POST':
            return None
        origin = request.headers.get('Origin')
        if (request.headers.get('Sec-Fetch-Site') == 'cross-site'
                or (origin and origin.rstrip('/') != request.host_url.rstrip('/'))):
            return jsonify({'error': 'cross-site analyst requests forbidden'}), 403
        if request.content_length is not None and request.content_length > 2048:
            return jsonify({'error': 'request body too large'}), 413
        return None

    @blueprint.post('/api/agent/analysis')
    def analysis():
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify({'error': 'JSON object required'}), 400
        exchange, symbol, interval = data.get('exchange'), data.get('symbol'), data.get('interval')
        force = data.get('force', False)
        if (not valid_identity(exchange, symbol) or not valid_interval(interval)
                or not isinstance(force, bool)):
            return jsonify({'error': 'invalid exchange, symbol or interval'}), 400
        from analyst_service import RateLimited, SnapshotUnavailable
        try:
            result = get_service().analyze(exchange, symbol, interval, force=force)
        except RateLimited as exc:
            return (jsonify({'error': 'analyst rate limit reached', 'retry_after_s': exc.retry_after}),
                    429, {'Retry-After': str(exc.retry_after)})
        except SnapshotUnavailable as exc:
            log.warning("analyst snapshot unavailable for %s %s %s: %s", exchange, symbol, interval, exc)
            return jsonify({'error': 'chart data unavailable for analysis', 'detail': str(exc)}), 502
        return jsonify(result), 200

    @blueprint.get('/api/agent/stats')
    def stats():
        exchange = request.args.get('exchange') or None
        symbol = request.args.get('symbol') or None
        interval = request.args.get('interval') or None
        if (exchange and not valid_identity(exchange, symbol or 'BTCUSDT')) \
                or (symbol and not exchange) or (interval and not valid_interval(interval)):
            return jsonify({'error': 'invalid filter'}), 400
        store = get_store()
        evaluated = 0
        if request.args.get('evaluate', 'true') != 'false':
            from analyst_store import evaluate_pending
            try:
                evaluated = evaluate_pending(store, fetch_bars or default_fetch_bars,
                                             int(time.time() * 1000), interval_duration_ms, limit=10)
            except Exception as exc:  # noqa: BLE001 — stats must still be served
                log.warning("analyst evaluation failed: %s", exc)
        payload = store.stats(exchange, symbol, interval)
        payload.update(evaluated_now=evaluated, filters={'exchange': exchange, 'symbol': symbol,
                                                         'interval': interval},
                       rule='win = +1 ATR in bias direction before -1 ATR against, within horizon_bars')
        return jsonify(payload), 200

    app.extensions['agent'] = {'path': path, 'get_service': get_service, 'get_store': get_store}
    app.register_blueprint(blueprint)
