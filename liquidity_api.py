"""Isolated liquidity routes; imports never start workers or create a database."""
from pathlib import Path
from threading import Lock
import re
import time

from flask import Blueprint, jsonify, request


def valid_identity(exchange, symbol, owner=None):
    if not isinstance(exchange, str) or exchange not in {'bybit', 'binance', 'hyperliquid'}:
        return False
    from liquidity_data import symbol_pattern
    pattern = symbol_pattern(exchange)
    return (isinstance(symbol, str) and re.fullmatch(pattern, symbol) is not None
            and (owner is None or (isinstance(owner, str)
                 and re.fullmatch(r'[A-Za-z0-9:._-]{1,80}', owner) is not None)))


def register_liquidity_routes(app, *, store_path=None, service_factory=None):
    blueprint = Blueprint('liquidity', __name__)
    path = Path(store_path) if store_path else Path(app.root_path) / '.data' / 'liquidity.sqlite3'
    lock = Lock()
    state = {'service': None}

    def get_service():
        with lock:
            if state['service'] is None:
                if service_factory:
                    state['service'] = service_factory(path)
                else:
                    from liquidity_data import fetch_depth_snapshot
                    from liquidity_store import LiquidityStore, LiquidityCollector
                    path.parent.mkdir(parents=True, exist_ok=True)
                    store = LiquidityStore(path)
                    state['service'] = (store, LiquidityCollector(store, fetch_depth_snapshot))
            return state['service']

    @blueprint.before_request
    def protect_collection_controls():
        if request.method != 'POST':
            return None
        origin = request.headers.get('Origin')
        if (request.headers.get('Sec-Fetch-Site') == 'cross-site'
                or (origin and origin.rstrip('/') != request.host_url.rstrip('/'))):
            return jsonify({'error': 'cross-site collection controls forbidden'}), 403
        if request.content_length is not None and request.content_length > 4096:
            return jsonify({'error': 'request body too large'}), 413
        return None

    @blueprint.get('/api/liquidity/history')
    def history():
        exchange = request.args.get('exchange', '')
        symbol = request.args.get('symbol', '')
        if not valid_identity(exchange, symbol):
            return jsonify({'error': 'unsupported exchange or invalid symbol'}), 400
        try:
            end_ms = int(request.args.get('end_ms', int(time.time() * 1000)))
            start_ms = int(request.args.get('start_ms', end_ms - 3_600_000))
            max_frames = int(request.args.get('max_frames', 360))
            if not (0 <= start_ms < end_ms and end_ms - start_ms <= 604_800_000
                    and 10 <= max_frames <= 600):
                raise ValueError('history bounds exceeded')
            if not service_factory and state['service'] is None and not path.exists():
                return jsonify({'exchange': exchange, 'symbol': symbol, 'coverage_start': None,
                                'coverage_end': None, 'samples': 0, 'frames': [], 'latest': None,
                                'sample_period_ms': 10000, 'gaps': [],
                                'warnings': ['No order book history has been collected yet'],
                                'status': {'collecting': False, 'last_success': None, 'error': None}}), 200
            store, collector = get_service()
            payload = store.history(exchange, symbol, start_ms, end_ms, max_frames=max_frames)
            return jsonify({**payload, 'status': collector.status(exchange, symbol)}), 200
        except ValueError:
            return jsonify({'error': 'invalid history query'}), 400

    @blueprint.post('/api/liquidity/watch')
    def watch():
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify({'error': 'JSON object required'}), 400
        exchange, symbol, owner = data.get('exchange'), data.get('symbol'), data.get('owner')
        if not isinstance(owner, str) or not valid_identity(exchange, symbol, owner):
            return jsonify({'error': 'invalid exchange, symbol or owner'}), 400
        from liquidity_store import CapacityError
        try:
            _, collector = get_service()
            payload = collector.watch(exchange, symbol, owner)
            collector.start()
            return jsonify(payload), 200
        except CapacityError:
            return jsonify({'error': 'collector at capacity, try again later'}), 429
        except ValueError:
            return jsonify({'error': 'invalid subscription'}), 400

    @blueprint.post('/api/liquidity/unwatch')
    def unwatch():
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify({'error': 'JSON object required'}), 400
        exchange, symbol, owner = data.get('exchange'), data.get('symbol'), data.get('owner')
        if not isinstance(owner, str) or not valid_identity(exchange, symbol, owner):
            return jsonify({'error': 'invalid exchange, symbol or owner'}), 400
        try:
            _, collector = get_service()
            collector.unwatch(exchange, symbol, owner)
            return jsonify(collector.status(exchange, symbol)), 200
        except ValueError:
            return jsonify({'error': 'invalid subscription'}), 400

    app.extensions['liquidity'] = {'path': path, 'get_service': get_service}
    app.register_blueprint(blueprint)
