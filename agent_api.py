"""Hermes analyst routes; importing performs no network I/O and creates no database.

POST /api/agent/analysis {exchange, symbol, interval[, force][, ema_periods]} → validated analysis
     ema_periods: the active chart's EMA ribbon periods, 1-3 integers 2..400 (default 7/14/21)
GET  /api/agent/stats[?exchange&symbol&interval]               → calibration stats
GET  /api/agent/structure?exchange&symbol&interval → Wyckoff + order-flow context (no LLM, 30 s cache)
GET  /api/agent/mtf?exchange&symbol&interval[&ema=7,14,21] → multi-timeframe ladder (no LLM;
     cached per venue/symbol/tf/last closed bar)
The browser never talks to Hermes; the gateway URL/key stay server-side.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from threading import Lock
from urllib.parse import urlsplit

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


def default_structure_chart(exchange, symbol, interval):
    from analyst import default_providers
    return default_providers()["chart"](exchange, symbol, interval)


def register_agent_routes(app, *, market=None, confluence=None, funding=None, service_factory=None,
                          store_path=None, fetch_bars=None, env=None, positioning=None,
                          structure_chart=None):
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
        try:
            origin_host = urlsplit(origin).netloc.lower() if origin else None
        except ValueError:   # e.g. "http://[::1" — not a real browser origin
            origin_host = ''
        # Compare hosts only: behind a TLS-terminating proxy (Railway) the app sees http://
        # while the browser's Origin is https://, so a scheme compare rejected every POST.
        if (request.headers.get('Sec-Fetch-Site') == 'cross-site'
                or (origin and origin_host != request.host.lower())):
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
        from analyst import normalize_ema_periods
        try:
            ema_periods = normalize_ema_periods(data.get('ema_periods'))
        except ValueError as exc:
            return jsonify({'error': f'invalid ema_periods: {exc}'}), 400
        from analyst_service import RateLimited, SnapshotUnavailable
        try:
            result = get_service().analyze(exchange, symbol, interval, force=force,
                                           ema_periods=ema_periods)
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

    structure_cache = {}

    @blueprint.get('/api/agent/structure')
    def structure():
        """Same closed candles the analyst snapshot uses; never starts the footprint collector."""
        exchange, symbol = request.args.get('exchange'), request.args.get('symbol')
        interval = request.args.get('interval')
        if not valid_identity(exchange, symbol) or not valid_interval(interval):
            return jsonify({'error': 'invalid exchange, symbol or interval'}), 400
        key, now = (exchange, symbol, interval), time.monotonic()
        hit = structure_cache.get(key)
        if hit and now - hit[0] < 30:
            return jsonify(hit[1]), 200
        from structure_context import structure_block
        try:
            chart = (structure_chart or default_structure_chart)(exchange, symbol, interval)
        except Exception as exc:  # noqa: BLE001
            log.warning("structure candles unavailable for %s %s %s: %s", exchange, symbol, interval, exc)
            return jsonify({'error': 'chart data unavailable', 'detail': str(exc)[:160]}), 502
        candles = chart['candles']
        payload = {'exchange': exchange, 'symbol': symbol, 'interval': interval,
                   'last_close_ts': candles[-1]['close_ts'] if candles else None,
                   **structure_block(candles, interval)}
        if len(structure_cache) > 256:
            structure_cache.clear()
        structure_cache[key] = (now, payload)
        return jsonify(payload), 200

    mtf_state = {'svc': None}

    @blueprint.get('/api/agent/mtf')
    def mtf_route():
        """TF ladder, reconciliation, SMC sync and merged levels; computed only when requested."""
        exchange, symbol = request.args.get('exchange'), request.args.get('symbol')
        interval = request.args.get('interval')
        if not valid_identity(exchange, symbol) or not valid_interval(interval):
            return jsonify({'error': 'invalid exchange, symbol or interval'}), 400
        from analyst import normalize_ema_periods
        try:
            raw = request.args.get('ema')
            periods = normalize_ema_periods([int(x) for x in raw.split(',')] if raw else None)
        except ValueError as exc:
            return jsonify({'error': f'invalid ema: {exc}'}), 400
        import mtf
        if mtf_state['svc'] is None:
            mtf_state['svc'] = (mtf.MtfService(structure_chart) if structure_chart
                                else mtf.default_service())
        try:
            block = mtf.build_block(mtf_state['svc'], exchange, symbol, interval, periods)
        except Exception as exc:  # noqa: BLE001
            log.warning("mtf unavailable for %s %s %s: %s", exchange, symbol, interval, exc)
            return jsonify({'error': 'multi-timeframe data unavailable', 'detail': str(exc)[:160]}), 502
        return jsonify({'exchange': exchange, 'symbol': symbol, 'interval': interval, 'mtf': block}), 200

    app.extensions['agent'] = {'path': path, 'get_service': get_service, 'get_store': get_store}
    app.register_blueprint(blueprint)
