"""Positioning routes: /api/positioning, /api/positioning/whales and the /positioning demo page.

Importing performs no network I/O and starts no thread. Background work (liquidation WS,
whale polling) runs only under short leases renewed by page requests.
"""
from __future__ import annotations

import re
import threading
import time

from flask import Blueprint, jsonify, render_template, request

from positioning import PERIODS

EXCHANGES = ("binance", "bybit", "hyperliquid")
SYMBOL_RE = {"binance": r"[A-Z0-9]{2,30}", "bybit": r"[A-Z0-9]{2,30}", "hyperliquid": r"[A-Za-z0-9]{1,20}"}
COIN_RE = r"[A-Za-z0-9]{1,20}"
OWNER_RE = r"[A-Za-z0-9:._-]{1,80}"


def valid_query(exchange, symbol, period):
    return (exchange in EXCHANGES and isinstance(symbol, str)
            and re.fullmatch(SYMBOL_RE[exchange], symbol) is not None and period in PERIODS)


class PositioningService:
    def __init__(self, http=None, *, store=None, collector=None, whales=None, coinglass=None,
                 clock_ms=None):
        from coinglass_client import CoinGlassClient
        from hl_whale_tracker import WhaleTracker
        from liquidations_collector import LiquidationCollector
        from liquidations_store import LiquidationStore, default_path
        from positioning_data import HttpClient

        self.http = http or HttpClient()
        # Persisted next to DB_PATH (or LIQUIDATIONS_DB_PATH) so the 24 h history and its
        # coverage windows survive a restart.
        self.store = store or LiquidationStore(path=default_path())
        self.collector = collector or LiquidationCollector(self.store)
        self.whales = whales or WhaleTracker(self.http)
        self.coinglass = coinglass or CoinGlassClient(self.http)
        self._clock = clock_ms or (lambda: int(time.time() * 1000))

    def liquidations(self, exchange, symbol, owner, watch):
        from liquidations_collector import LiquidationCapacityError
        if exchange == "hyperliquid":
            return self.store.aggregate(exchange, symbol)
        status_note = None
        if watch:
            try:
                self.collector.watch(exchange, symbol, owner)
                self.collector.ensure_running()
            except LiquidationCapacityError as exc:
                status_note = str(exc)
        agg = self.store.aggregate(exchange, symbol, now_ms=self._clock())
        agg["collector"] = self.collector.status(exchange)
        if status_note:
            agg["collector"]["note"] = status_note
        return agg

    def snapshot(self, exchange, symbol, period, *, owner="positioning", watch=True):
        from positioning_data import coin_of, fetch_raw
        from positioning_snapshot import build_snapshot

        coin = coin_of(exchange, symbol)
        raw = fetch_raw(self.http, exchange, symbol, period)
        whales = self.whales.view(coin) if self.whales.polls else None
        return build_snapshot(exchange, symbol, period, raw, now_ms=self._clock(), coin=coin,
                              liquidations=self.liquidations(exchange, symbol, owner, watch),
                              whales=whales or {"aggregate": None, "reason":
                                                "whale tracker idle; GET /api/positioning/whales starts it"},
                              flows=self.coinglass.exchange_flows(coin, now_ms=self._clock()))

    def whale_view(self, coin, *, owner="positioning", watch=True):
        if watch:
            self.whales.watch(owner)
            self.whales.ensure_running()
        return self.whales.view(coin)


def register_positioning_routes(app, *, service_factory=None):
    blueprint = Blueprint("positioning", __name__)
    lock = threading.Lock()
    state = {"service": None}

    def get_service():
        with lock:
            if state["service"] is None:
                state["service"] = service_factory() if service_factory else PositioningService()
            return state["service"]

    def flags():
        owner = request.args.get("owner", "positioning")
        watch = request.args.get("watch", "1")
        if re.fullmatch(OWNER_RE, owner) is None or watch not in ("0", "1"):
            return None
        return owner, watch == "1"

    @blueprint.get("/positioning")
    def positioning_page():
        """Temporary demo page for the mountable positioning panel. View only."""
        return render_template("positioning.html")

    @blueprint.get("/api/positioning")
    def positioning():
        exchange = request.args.get("exchange", "binance")
        symbol = request.args.get("symbol", "")
        period = request.args.get("period", "1h")
        opts = flags()
        if not valid_query(exchange, symbol, period) or opts is None:
            return jsonify({"error": "unsupported exchange, invalid symbol or period",
                            "periods": list(PERIODS)}), 400
        try:
            return jsonify(get_service().snapshot(exchange, symbol, period, owner=opts[0], watch=opts[1])), 200
        except ValueError:
            return jsonify({"error": "invalid positioning query"}), 400
        except Exception as exc:  # noqa: BLE001 - never leak internals beyond the type
            return jsonify({"error": f"positioning unavailable: {type(exc).__name__}"}), 502

    @blueprint.get("/api/positioning/whales")
    def whales():
        coin = request.args.get("coin", "")
        opts = flags()
        if re.fullmatch(COIN_RE, coin) is None or opts is None:
            return jsonify({"error": "invalid coin"}), 400
        try:
            return jsonify(get_service().whale_view(coin, owner=opts[0], watch=opts[1])), 200
        except ValueError:
            return jsonify({"error": "whale tracker at capacity, try again later"}), 429

    app.extensions["positioning"] = {"get_service": get_service}
    app.register_blueprint(blueprint)
