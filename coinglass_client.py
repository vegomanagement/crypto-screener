"""Optional CoinGlass official API (v4) adapter for on-chain exchange balances / net flows.

Inactive unless COINGLASS_API_KEY is set in the environment. The key is only ever sent as
the documented ``CG-API-KEY`` header; it is never logged, cached under, returned or put in
error text. Response shapes follow https://docs.coinglass.com/reference/exchange-balance-list
(``{"code": "0", "msg": "success", "data": [...]}``; non-"0" codes are errors).
We do not scrape coinglass.com: its Terms of Service forbid scraping and bulk extraction.
"""
from __future__ import annotations

import os

from positioning import NormaliseError, Q2, dec, fmt, q

BASE = "https://open-api-v4.coinglass.com"
BALANCE_PATH = "/api/exchange/balance/list"
SOURCE = "CoinGlass API v4 GET /api/exchange/balance/list"
NO_KEY = "requires CoinGlass API key (paid)"
# "Supported symbol" list from the endpoint's documented `symbol` parameter.
BALANCE_SYMBOLS = frozenset(
    "BTC ETH 1INCH AAVE AMP BAL BLZ BNT BUSD CRV DAI DODO ENS FET FTT GRT GTC GUSD HOT HUSD JASMY "
    "KNC LINK MASK MATIC OCEAN OMG POLY QNT SAI SKL SRM STORJ SUSD SUSHI TUSD UNI USDC USDK USDP "
    "VGX XRP YFI ZRX".split())
CACHE_TTL = 600   # endpoint updates hourly per the docs


def parse_balance_list(payload):
    """Documented exchange balance list -> rows + summed changes (coin units)."""
    if not isinstance(payload, dict) or "code" not in payload:
        raise NormaliseError("unexpected CoinGlass response shape")
    if str(payload["code"]) != "0":
        raise NormaliseError(f"CoinGlass error code {payload['code']}: {str(payload.get('msg'))[:120]}")
    data = payload.get("data")
    if not isinstance(data, list):
        raise NormaliseError("CoinGlass data is not a list")
    rows, totals = [], {"total_balance": 0, "change_1d": 0, "change_7d": 0, "change_30d": 0}
    for r in data:
        row = {"exchange": r["exchange_name"], "total_balance": dec(r.get("total_balance")),
               "change_1d": dec(r.get("balance_change_1d")), "change_pct_1d": dec(r.get("balance_change_percent_1d")),
               "change_7d": dec(r.get("balance_change_7d")), "change_pct_7d": dec(r.get("balance_change_percent_7d")),
               "change_30d": dec(r.get("balance_change_30d")),
               "change_pct_30d": dec(r.get("balance_change_percent_30d"))}
        for k in totals:
            if row[k] is not None:
                totals[k] += row[k]
        rows.append(row)
    return rows, totals


class CoinGlassClient:
    def __init__(self, http=None, *, api_key=None, env=None):
        env = os.environ if env is None else env
        key = api_key if api_key is not None else env.get("COINGLASS_API_KEY", "")
        self._key = key.strip() if isinstance(key, str) else ""
        self._http = http

    @property
    def enabled(self):
        return bool(self._key)

    def __repr__(self):
        return f"CoinGlassClient(enabled={self.enabled})"

    def _missing(self, coin, reason):
        return {"available": False, "value": None, "reason": reason, "source": SOURCE,
                "venue": "coinglass", "coin": coin, "timestamp": None, "period": "1d/7d/30d"}

    def exchange_flows(self, coin, now_ms=None):
        if not self.enabled:
            return self._missing(coin, NO_KEY)
        if coin not in BALANCE_SYMBOLS:
            return self._missing(coin, f"CoinGlass exchange balance does not cover {coin}")
        if self._http is None:
            from positioning_data import HttpClient
            self._http = HttpClient()
        try:
            payload = self._http.request_json(f"{BASE}{BALANCE_PATH}?symbol={coin}",
                                              headers={"CG-API-KEY": self._key}, ttl=CACHE_TTL,
                                              cache_key=("coinglass", BALANCE_PATH, coin))
            rows, totals = parse_balance_list(payload)
        except Exception as exc:  # noqa: BLE001 - message never contains the key
            text = str(exc).replace(self._key, "***") if self._key else str(exc)
            return self._missing(coin, f"CoinGlass request failed: {text[:160]}")
        rows.sort(key=lambda r: abs(r["change_1d"] or 0), reverse=True)
        return {"available": True, "value": fmt(totals["change_1d"]), "unit": coin,
                "label": ("On-chain exchange balance change (net inflow − outflow) across exchanges "
                          "tracked by CoinGlass; positive = coins moved onto exchanges"),
                "source": SOURCE, "venue": "coinglass", "coin": coin, "timestamp": now_ms,
                "period": "1d", "reason": None,
                "totals": {k: fmt(v) for k, v in totals.items()},
                "exchanges": [{k: (fmt(q(v, Q2)) if k.startswith("change_pct") and v is not None else
                                   fmt(v) if not isinstance(v, str) else v) for k, v in r.items()}
                              for r in rows[:12]]}
