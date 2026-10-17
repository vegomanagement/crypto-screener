"""Isolated footprint route; importing it performs no network I/O."""
import re
from threading import Lock

from flask import Blueprint, jsonify, request

_SETTING_KEYS = ("input", "va_pct", "imbalance_ratio", "imbalance_min_diff", "include_zero",
                 "stacked_min", "min_delta")


def _settings(args):
    import footprint

    raw = {}
    for key in _SETTING_KEYS:
        if key not in args:
            continue
        value = args[key]
        if key == "include_zero":
            if value not in ("0", "1", "true", "false"):
                raise ValueError("include_zero must be 0/1")
            value = value in ("1", "true")
        elif key in ("va_pct", "stacked_min"):
            if not value.isdigit():
                raise ValueError(f"{key} must be an integer")
            value = int(value)
        raw[key] = value
    return footprint.settings_from_json(raw)


def register_footprint_routes(app, *, service_factory=None):
    blueprint = Blueprint('footprint', __name__)
    lock = Lock()
    state = {'service': None, 'locks': {}}

    def get_service():
        with lock:
            if state['service'] is None:
                if service_factory:
                    state['service'] = service_factory()
                else:
                    from footprint_data import FootprintService
                    state['service'] = FootprintService()
            return state['service']

    def key_lock(key):
        with lock:
            if len(state['locks']) > 512:
                state['locks'].clear()
            return state['locks'].setdefault(key, Lock())

    @blueprint.get('/api/footprint')
    def footprint_route():
        from footprint_data import FootprintError
        from liquidity_data import symbol_pattern

        args = request.args
        exchange, symbol = args.get('exchange', ''), args.get('symbol', '')
        interval = args.get('interval', '1')
        if exchange not in {'bybit', 'binance', 'hyperliquid'} or not re.fullmatch(
                symbol_pattern(exchange), symbol or ''):
            return jsonify({'error': 'unsupported exchange or invalid symbol'}), 400
        try:
            limit_text = args.get('limit', '60')
            if not limit_text.isdigit():
                raise ValueError('limit must be an integer')
            group_text = args.get('group_ticks', 'auto')
            if group_text != 'auto' and not group_text.isdigit():
                raise ValueError('group_ticks must be auto or an integer')
            group = group_text if group_text == 'auto' else int(group_text)
            forming = args.get('include_forming', '1')
            if forming not in ('0', '1'):
                raise ValueError('include_forming must be 0/1')
            settings = _settings(args)
            service = get_service()
            with key_lock((exchange, symbol, interval)):
                payload = service.footprint(exchange, symbol, interval, limit=int(limit_text),
                                            group_ticks=group, settings=settings,
                                            include_forming=forming == '1')
        except ValueError as exc:
            return jsonify({'error': f'invalid footprint query: {exc}'}), 400
        except FootprintError as exc:
            return jsonify({'error': str(exc)}), 502
        response = jsonify(payload)
        response.headers['Cache-Control'] = 'no-store'
        return response, 200

    app.extensions['footprint'] = {'get_service': get_service}
    app.register_blueprint(blueprint)
