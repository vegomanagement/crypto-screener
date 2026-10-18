"""Read-only raw trade backfill for the big-trade bubbles: ``GET /api/trades/recent``.

Returns real public trades only, compactly, with explicit coverage:
- Binance USDT-M: contiguous ``aggTrades`` paged back to ``minutes`` ago within a request
  budget (shares the footprint aggTrades rate window). Exact back to ``oldest_ms``; a budget
  stop cuts the OLDEST trades (``truncated``), never the newest.
- Bybit linear: ``recent-trade`` (≤1000 latest) — partial by construction.
- Hyperliquid: ``recentTrades`` (a handful of latest) — partial by construction.

Rows are ``[id, time_ms, price, qty, side]`` with price/qty as exact decimal strings and
side ``1`` = taker buy (ask), ``-1`` = taker sell (bid). Nothing is aggregated or filtered
here; size thresholds and print aggregation run in the browser (static/indicators/bubbles.js).
"""
import re

from flask import Blueprint, jsonify, request

MAX_MINUTES = 60
MAX_REQUESTS = 20


def trades_payload(service, exchange, symbol, *, minutes=15, max_requests=10):
    import footprint as fp
    from footprint_data import FootprintError

    if type(minutes) is not int or not 1 <= minutes <= MAX_MINUTES:
        raise ValueError(f"minutes must be 1..{MAX_MINUTES}")
    if type(max_requests) is not int or not 1 <= max_requests <= MAX_REQUESTS:
        raise ValueError(f"max_requests must be 1..{MAX_REQUESTS}")
    warnings = []
    if exchange == "binance":
        now = service._clock()
        trades, info = service.binance_trades_since(symbol, now - minutes * 60_000, max_requests=max_requests)
        as_of, truncated, coverage = info["as_of"], info["truncated"], "exact"
        last_id = info["last_trade_id"]
        if not trades and truncated:
            raise FootprintError("binance: aggTrades rate budget busy (shared with footprint); retry shortly")
        warnings += info.get("warnings", [])
        if truncated:
            warnings.append("Request budget reached: only the newest trades were fetched; older ones are missing.")
    else:
        try:
            if exchange == "bybit":
                data = service._call("bybit", "/v5/market/recent-trade",
                                     params={"category": "linear", "symbol": symbol, "limit": 1000})
                if not isinstance(data, dict) or data.get("retCode") != 0:
                    raise FootprintError("bybit: API response rejected")
                rows = [r for r in data["result"]["list"] if r.get("symbol") == symbol]
                warnings.append("Bybit recent-trade returns only the latest ≤1000 trades: history is partial.")
            else:
                rows = service._call("hyperliquid", "/info", payload={"type": "recentTrades", "coin": symbol})
                rows = [r for r in rows if r.get("coin") == symbol]
                warnings.append("Hyperliquid recentTrades returns only a handful of latest trades: history is partial.")
            trades = sorted((fp.NORMALIZERS[exchange](r) for r in rows), key=lambda t: t.time_ms)
        except FootprintError:
            raise
        except (KeyError, TypeError, ValueError, AttributeError):
            raise FootprintError(f"{exchange}: malformed recent trades") from None
        as_of, truncated, coverage, last_id = service._clock(), False, "partial", None
    trades = [t for t in trades if t.time_ms <= as_of]
    out = [[t.ident if isinstance(t.ident, int) else str(t.ident), t.time_ms, fp.fmt(t.price), fp.fmt(t.qty),
            1 if t.side == "ask" else -1] for t in trades]
    return {
        "exchange": exchange, "symbol": symbol, "as_of": as_of, "minutes": minutes,
        "coverage": coverage, "truncated": bool(truncated),
        "oldest_ms": trades[0].time_ms if trades else None,
        "newest_ms": trades[-1].time_ms if trades else None,
        "last_trade_id": last_id, "count": len(out),
        "columns": ["id", "time_ms", "price", "qty", "side"],
        "convention": "side 1 = taker buy (ask), -1 = taker sell (bid); Binance rows are aggTrades",
        "trades": out, "warnings": warnings,
    }


def register_trades_routes(app, *, service_factory=None):
    blueprint = Blueprint('trades_recent', __name__)

    def get_service():
        if service_factory:
            return service_factory()
        shared = app.extensions.get('footprint')
        if shared:
            return shared['get_service']()  # one aggTrades rate window for all consumers
        from footprint_data import FootprintService
        return FootprintService()

    @blueprint.get('/api/trades/recent')
    def trades_recent_route():
        from footprint_data import FootprintError
        from liquidity_data import symbol_pattern

        args = request.args
        exchange, symbol = args.get('exchange', ''), args.get('symbol', '')
        if exchange not in {'bybit', 'binance', 'hyperliquid'} or not re.fullmatch(symbol_pattern(exchange), symbol or ''):
            return jsonify({'error': 'unsupported exchange or invalid symbol'}), 400
        try:
            minutes, budget = args.get('minutes', '15'), args.get('max_requests', '10')
            if not minutes.isdigit() or not budget.isdigit():
                raise ValueError('minutes and max_requests must be integers')
            payload = trades_payload(get_service(), exchange, symbol, minutes=int(minutes), max_requests=int(budget))
        except ValueError as exc:
            return jsonify({'error': f'invalid trades query: {exc}'}), 400
        except FootprintError as exc:
            return jsonify({'error': str(exc)}), 502
        response = jsonify(payload)
        response.headers['Cache-Control'] = 'no-store'
        return response, 200

    app.register_blueprint(blueprint)
