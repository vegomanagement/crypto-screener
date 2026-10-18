# Positioning fixtures

Recorded 2026-10-17 ~17:38 UTC from the live public endpoints (no key), unmodified except:

* `hl_metaAndAssetCtxs.json` — the real response trimmed to the first 5 universe entries and
  their matching 5 asset contexts (index alignment preserved; BTC=0, ETH=1).
* `hl_leaderboard_head.json` — the first 6 rows of the real ~38 MB leaderboard (same row shape).
* `binance_forceOrder_ws.json`, `bybit_allLiquidation_ws.json` — raw WS messages captured with
  `ws_client.py` (Binance via `/market/ws/!forceOrder@arr`; the legacy `/ws/` path delivered nothing).

CoinGlass fixtures are NOT recorded (no key is used): they are written from the official docs.

* `coinglass_exchange_balance_list.json` — the documented response example of
  https://docs.coinglass.com/reference/exchange-balance-list verbatim (JSON comments removed).
* `coinglass_error_401.json` — built from the documented error table
  (https://docs.coinglass.com/reference/responses-error-codes: code 401 = "Invalid or missing API
  key") in the documented `{code, msg}` envelope; the msg text is the table description.
