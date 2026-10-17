"""Funding API routes: GET /api/funding?exchange=&symbol= (read-only, cached 30 s)."""
from __future__ import annotations

import logging
from threading import Lock

from flask import Blueprint, jsonify, request

from liquidity_api import valid_identity

log = logging.getLogger(__name__)


def register_funding_routes(app, *, service_factory=None):
    blueprint = Blueprint('funding', __name__)
    lock, state = Lock(), {'service': None}

    def get_service():
        with lock:
            if state['service'] is None:
                if service_factory:
                    state['service'] = service_factory()
                else:
                    from funding_data import FundingService
                    state['service'] = FundingService()
            return state['service']

    @blueprint.get('/api/funding')
    def funding_route():
        exchange, symbol = request.args.get('exchange'), request.args.get('symbol')
        if not valid_identity(exchange, symbol):
            return jsonify({'error': 'invalid exchange or symbol'}), 400
        from funding_data import FundingError
        try:
            return jsonify(get_service().get(exchange, symbol)), 200
        except FundingError as exc:
            log.warning("funding unavailable for %s %s: %s", exchange, symbol, exc)
            return jsonify({'error': 'funding unavailable', 'detail': str(exc)[:300]}), 502
        except ValueError as exc:
            return jsonify({'error': str(exc)}), 400

    app.extensions['funding'] = {'get_service': get_service}
    app.register_blueprint(blueprint)
