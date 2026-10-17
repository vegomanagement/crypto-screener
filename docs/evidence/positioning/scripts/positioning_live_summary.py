"""Summarise docs/evidence/positioning/live_run.jsonl (whale polls, liquidation counts per venue)."""
import json

PATH = "/Users/maxbelov/Projects/wt/positioning-data/docs/evidence/positioning/live_run.jsonl"
lines = [json.loads(x) for x in open(PATH)]
start = next(x for x in lines if x["event"] == "start")
end = [x for x in lines if x["event"] == "end"]
print("run minutes", round(((end[-1]["at"] if end else lines[-1]["at"]) - start["at"]) / 60000, 1))
polls = {}
for x in lines:
    if x["event"] == "whales" and x.get("last_poll"):
        polls[x["last_poll"]["poll_number"]] = x["last_poll"]
lb = next((x["leaderboard"] for x in lines if x["event"] == "whales" and x.get("leaderboard")), None)
print("leaderboard", lb)
for n, p in sorted(polls.items()):
    print(f"poll {n}: ok {p['ok']}/{p['requested']} failed {p['failed']} duration {p['duration_ms'] / 1000:.1f}s "
          f"changes {p['changes_detected']}")
for coin in ("BTC", "ETH"):
    last = [x for x in lines if x["event"] == "whales" and x["coin"] == coin][-1]
    a = last["aggregate"]
    print(coin, {k: a.get(k) for k in ("long_notional", "short_notional", "long_count", "short_count",
                                       "avg_entry_long", "avg_entry_short", "long_share")})
    seen = {}
    for x in lines:
        if x["event"] == "whales" and x["coin"] == coin:
            for c in x["changes"]:
                seen[(c["ts"], c["address"], c["kind"])] = c
    for c in sorted(seen.values(), key=lambda c: c["ts"]):
        print("  change", c["kind"], c["side"], c["address"][:10], c["from_szi"], "->", c["to_szi"], c["delta_notional"])
liq_last = {}
for x in lines:
    if x["event"] == "liq":
        liq_last[(x["venue"], x["symbol"])] = x
for venue in ("binance", "bybit"):
    tot_l = tot_s = cnt = 0.0
    col = None
    for (v, s), x in sorted(liq_last.items()):
        if v != venue:
            continue
        w = x["windows"]["1h"] if x["windows"] else None
        col = x["collector"]
        if w:
            cnt += w["long_count"] + w["short_count"]
            tot_l += float(w["long_usd"])
            tot_s += float(w["short_usd"])
            print(f"  {venue} {s}: long {w['long_count']} ${w['long_usd']} short {w['short_count']} ${w['short_usd']} "
                  f"covered {w['covered_ms'] / 60000:.1f} min")
    print(venue, "events stored", int(cnt), f"long ${tot_l:,.2f} short ${tot_s:,.2f}",
          "collector:", {k: col.get(k) for k in ("messages", "events", "connects", "error", "connected")})
lat = [x["latency_s"] for x in lines if x["event"] in ("liq", "whales")]
print("api latency s: median", sorted(lat)[len(lat) // 2], "max", max(lat), "n", len(lat))
