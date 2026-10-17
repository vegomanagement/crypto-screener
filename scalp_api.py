"""View-only scalping page (/scalp) and its instrument metadata route.

Importing this module performs no network I/O. The page itself streams public order books and
trades straight from the venues in the browser; the server only serves the template and the
venue's own price grid (tick size / szDecimals), reusing the footprint service's metadata cache.
"""
import re

from flask import Blueprint, jsonify, render_template, request


def register_scalp_routes(app, *, service_getter=None):
    blueprint = Blueprint('scalp', __name__)

    def get_service():
        if service_getter:
            return service_getter()
        footprint = app.extensions.get('footprint')
        if footprint:
            return footprint['get_service']()
        from footprint_data import FootprintService
        return FootprintService()

    @blueprint.get('/scalp')
    def scalp_page():
        """DOM ladder + prints + clusters. Research view only: no orders, keys or accounts."""
        return render_template('scalp.html')

    @blueprint.get('/api/scalp/instrument')
    def scalp_instrument():
        from footprint_data import FootprintError
        from liquidity_data import symbol_pattern

        exchange, symbol = request.args.get('exchange', ''), request.args.get('symbol', '')
        if exchange not in {'bybit', 'binance', 'hyperliquid'} or not re.fullmatch(
                symbol_pattern(exchange), symbol or ''):
            return jsonify({'error': 'unsupported exchange or invalid symbol'}), 400
        try:
            payload = get_service().instrument(exchange, symbol)
        except ValueError as exc:
            return jsonify({'error': f'invalid instrument query: {exc}'}), 400
        except FootprintError as exc:
            return jsonify({'error': str(exc)}), 502
        response = jsonify(payload)
        response.headers['Cache-Control'] = 'public, max-age=300'
        return response, 200

    app.register_blueprint(blueprint)
