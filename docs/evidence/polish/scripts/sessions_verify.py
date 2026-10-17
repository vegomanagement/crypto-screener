"""Verify live session windows against Python zoneinfo and the opening range against Binance klines."""
import json
import sys
import urllib.request
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

OUT = sys.argv[1]
data = json.load(open(f"{OUT}/sessions-live.json"))
fails, lines = 0, []
for case in data:
    for inst in case["instances"]:
        tz = inst["tz"]
        zone = ZoneInfo(tz) if "/" in tz else UTC
        start = datetime.fromtimestamp(inst["start"], UTC)
        local = start.astimezone(zone)
        end_local = datetime.fromtimestamp(inst["end"], UTC).astimezone(zone)
        lines.append(f"{case['tag']:<24} {inst['name']:<9} {tz:<17} UTC {start:%a %m-%d %H:%M} → {datetime.fromtimestamp(inst['end'], UTC):%m-%d %H:%M}"
                     f"  local {local:%a %m-%d %H:%M %Z} → {end_local:%H:%M %Z}")
        inst["local_start"], inst["local_end"] = local.strftime("%H:%M"), end_local.strftime("%H:%M")
starts = {}
for case in data:
    for inst in case["instances"]:
        starts.setdefault((case["tag"], inst["name"]), set()).add((inst["local_start"], inst["local_end"]))
for key, val in starts.items():
    ok = len(val) == 1
    fails += not ok
    lines.append(f"{key}: local windows {sorted(val)} {'OK (constant local time across DST)' if ok else 'MISMATCH'}")
# Opening range vs Binance 15m klines (GMT+0 case)
for case in data:
    if case["interval"] != "15":
        continue
    q = f"symbol=BTCUSDT&interval=15m&limit=400&endTime={case['lastBar'] * 1000 + 999}"
    with urllib.request.urlopen(f"https://fapi.binance.com/fapi/v1/klines?{q}", timeout=30) as r:
        k = {int(x[0]) // 1000: (float(x[2]), float(x[3])) for x in json.load(r)}
    for inst in case["instances"]:
        o = inst["or"]
        if not isinstance(o, dict) or inst.get("truncated"):
            continue
        bars = [t for t in sorted(k) if inst["firstTime"] <= t <= o["lastOrTime"]]
        top, btm = max(k[t][0] for t in bars), min(k[t][1] for t in bars)
        ok = len(bars) == o["bars"] == 2 and top == o["top"] and btm == o["btm"] and o["lastOrTime"] == inst["firstTime"] + 900 and o["minutes"] == 16
        fails += not ok
        lines.append(f"OR {case['tag']} {inst['name']} {datetime.fromtimestamp(inst['firstTime'], UTC):%m-%d %H:%M}: bars {o['bars']} (venue {len(bars)}), "
                     f"top {o['top']} vs {top}, btm {o['btm']} vs {btm}, N={o['minutes']} {'OK' if ok else 'MISMATCH'}")
open(f"{OUT}/sessions-verify.txt", "w").write("\n".join(lines) + f"\nfailures {fails}\n")
print("\n".join(lines[-40:]))
print("failures", fails)
