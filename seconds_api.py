"""Read-only seconds-candle backfill: ``GET /api/seconds`` (Binance aggTrades only).

Bybit and Hyperliquid have no public historical trades endpoint, so their seconds
candles are built in the browser from the live trade stream only; this route says so
with a 400 instead of returning a fabricated or partial history.
"""
import re

from flask import Blueprint, jsonify, request


def register_seconds_routes(app, *, service_factory=None):
    blueprint = Blueprint('seconds', __name__)

    def get_service():
        if service_factory:
            return service_factory()
        shared = app.extensions.get('footprint')
        if shared:
            return shared['get_service']()  # share the aggTrades rate window
        from footprint_data import FootprintService
        return FootprintService()

    @blueprint.get('/api/seconds')
    def seconds_route():
        from footprint_data import FootprintError
        from liquidity_data import symbol_pattern
        from seconds_candles import binance_backfill

        args = request.args
        exchange, symbol = args.get('exchange', ''), args.get('symbol', '')
        if exchange in {'bybit', 'hyperliquid'}:
            return jsonify({'error': 'no public trade history for this venue; seconds candles '
                                     'are built from the live stream only'}), 400
        if exchange != 'binance' or not re.fullmatch(symbol_pattern(exchange), symbol or ''):
            return jsonify({'error': 'unsupported exchange or invalid symbol'}), 400
        try:
            minutes_text, seconds_text = args.get('minutes', '5'), args.get('seconds', '1')
            if not minutes_text.isdigit() or not seconds_text.isdigit():
                raise ValueError('minutes and seconds must be integers')
            payload = binance_backfill(get_service(), symbol, minutes=int(minutes_text),
                                       seconds=int(seconds_text))
        except ValueError as exc:
            return jsonify({'error': f'invalid seconds query: {exc}'}), 400
        except FootprintError as exc:
            return jsonify({'error': str(exc)}), 502
        response = jsonify(payload)
        response.headers['Cache-Control'] = 'no-store'
        return response, 200

    app.register_blueprint(blueprint)
