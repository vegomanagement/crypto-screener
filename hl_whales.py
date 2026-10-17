"""Hyperliquid whale tracker: top-N accounts by account value from the public leaderboard.

Label honestly: these are "Hyperliquid top-N accounts by account value", not all whales.
The ~38 MB leaderboard is stream-parsed row by row (only address, accountValue and
displayName are kept, in a bounded heap) and cached for an hour. Positions come from the
public ``clearinghouseState`` info request, paced at <= 1 request/s with a 60 s poll cache.
"""
from __future__ import annotations

import codecs
import heapq
import json
from decimal import Decimal

from positioning import NormaliseError, Q2, dec, fmt, q

LEADERBOARD_URL = "https://stats-data.hyperliquid.xyz/Mainnet/leaderboard"
MAX_ROW_CHARS = 1_000_000


def parse_leaderboard_stream(stream, top_n, chunk_size=65536):
    """Return (top rows sorted by accountValue desc, total rows). Memory ~ chunk + one row."""
    decoder = codecs.getincrementaldecoder("utf-8")()
    jd = json.JSONDecoder()
    buf, pos, started, total, heap = "", 0, False, 0, []
    eof = False

    def more():
        nonlocal buf, pos, eof
        data = stream.read(chunk_size)
        if not data:
            eof = True
            buf = buf[pos:] + decoder.decode(b"", final=True)
        else:
            buf = buf[pos:] + decoder.decode(data)
        pos = 0

    while not started:
        i = buf.find('"leaderboardRows"', pos)
        j = buf.find("[", i) if i >= 0 else -1
        if j >= 0:
            pos, started = j + 1, True
            break
        if eof:
            raise NormaliseError("leaderboardRows not found")
        pos = max(0, len(buf) - 32)
        more()
    while True:
        while pos < len(buf) and buf[pos] in " \t\r\n,":
            pos += 1
        if pos >= len(buf):
            if eof:
                raise NormaliseError("leaderboard truncated")
            more()
            continue
        if buf[pos] == "]":
            break
        try:
            row, end = jd.raw_decode(buf, pos)
        except json.JSONDecodeError:
            if eof or len(buf) - pos > MAX_ROW_CHARS:
                raise NormaliseError("leaderboard row malformed") from None
            more()
            continue
        pos = end
        total += 1
        try:
            value = dec(row.get("accountValue"))
            address = row["ethAddress"]
        except (AttributeError, KeyError):
            continue
        if value is None:
            continue
        item = (value, total, address, row.get("displayName"))
        if len(heap) < top_n:
            heapq.heappush(heap, item)
        elif item > heap[0]:
            heapq.heapreplace(heap, item)
    ranked = sorted(heap, reverse=True)
    return [{"address": a, "account_value": v, "display_name": n} for v, _, a, n in ranked], total


def parse_positions(state):
    """clearinghouseState -> {coin: position} for non-zero perps."""
    if not isinstance(state, dict) or not isinstance(state.get("assetPositions"), list):
        raise NormaliseError("unexpected clearinghouseState shape")
    out = {}
    for item in state["assetPositions"]:
        p = item.get("position") or {}
        szi = dec(p.get("szi"))
        if not szi:
            continue
        out[p["coin"]] = {"szi": szi, "entry": dec(p.get("entryPx")), "value": dec(p.get("positionValue")),
                          "liq": dec(p.get("liquidationPx")), "upnl": dec(p.get("unrealizedPnl")),
                          "leverage": (p.get("leverage") or {}).get("value")}
    return out


def aggregate_coin(states, coin, top=10):
    """states: {address: {coin: position}} for successfully polled accounts."""
    long_n = short_n = long_sz = short_sz = long_cost = short_cost = upnl = Decimal(0)
    long_c = short_c = 0
    rows = []
    for address, positions in states.items():
        p = positions.get(coin)
        if not p:
            continue
        size, value = abs(p["szi"]), p["value"] or Decimal(0)
        entry = p["entry"] or Decimal(0)
        if p["szi"] > 0:
            long_n, long_sz, long_cost, long_c = long_n + value, long_sz + size, long_cost + size * entry, long_c + 1
        else:
            short_n, short_sz, short_cost, short_c = short_n + value, short_sz + size, short_cost + size * entry, short_c + 1
        upnl += p["upnl"] or 0
        rows.append({"address": address, "side": "long" if p["szi"] > 0 else "short", "size": fmt(size),
                     "notional": fmt(q(value, Q2)), "entry": fmt(p["entry"]), "liquidation_px": fmt(p["liq"]),
                     "leverage": p["leverage"], "unrealized_pnl": fmt(q(p["upnl"], Q2)) if p["upnl"] is not None else None,
                     "_n": value})
    rows.sort(key=lambda r: r["_n"], reverse=True)
    for r in rows:
        del r["_n"]
    total = long_n + short_n
    return {"coin": coin, "long_notional": fmt(q(long_n, Q2)), "short_notional": fmt(q(short_n, Q2)),
            "net_notional": fmt(q(long_n - short_n, Q2)),
            "long_share": fmt(q(long_n / total)) if total else None,
            "long_count": long_c, "short_count": short_c,
            "avg_entry_long": fmt(q(long_cost / long_sz, Q2)) if long_sz else None,
            "avg_entry_short": fmt(q(short_cost / short_sz, Q2)) if short_sz else None,
            "unrealized_pnl": fmt(q(upnl, Q2)),
            "liquidation_levels": [{"address": r["address"], "side": r["side"], "liquidation_px": r["liquidation_px"],
                                    "notional": r["notional"]} for r in rows if r["liquidation_px"]][:top],
            "positions": rows[:top]}


def detect_changes(prev, cur, ts, threshold=Decimal("0.05")):
    """Compare two polls ({address: {coin: pos}}); only addresses present in both are compared."""
    changes = []
    for address in sorted(set(prev) & set(cur)):
        before, after = prev[address], cur[address]
        for coin in sorted(set(before) | set(after)):
            p = before.get(coin, {}).get("szi") or Decimal(0)
            c = after.get(coin, {}).get("szi") or Decimal(0)
            if p == c:
                continue
            ref = after.get(coin) or before.get(coin)
            unit = (ref["value"] / abs(ref["szi"])) if ref.get("value") and ref["szi"] else None
            if p == 0:
                kind = "opened"
            elif c == 0:
                kind = "closed"
            elif (p > 0) != (c > 0):
                kind = "flipped"
            elif abs(c) >= abs(p) * (1 + threshold):
                kind = "increased"
            elif abs(c) <= abs(p) * (1 - threshold):
                kind = "decreased"
            else:
                continue
            delta = abs(c - p)
            changes.append({"ts": ts, "address": address, "coin": coin, "kind": kind,
                            "side": "long" if (c or p) > 0 else "short",
                            "from_szi": fmt(p), "to_szi": fmt(c),
                            "delta_notional": fmt(q(delta * unit, Q2)) if unit is not None else None})
    return changes
