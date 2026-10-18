#!/usr/bin/env python3
"""Tables (markdown) and stdlib SVG charts for docs/CARRY_RESULT.md from docs/evidence/carry/summary.json.

    python scripts/carry_report.py      # → docs/evidence/carry/{tables.md, *.svg}
"""
from __future__ import annotations

import gzip
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "scripts")]

from carry_analysis import DEV_END, OUT, RAW, UNIVERSE, VARIANTS, YEARS, load, trailing, world  # noqa: E402
from portfolio_svg import equity_svg  # noqa: E402

DAY = 86_400_000


def p(v, d=1):
    return "—" if v is None else f"{100 * v:+.{d}f}%"


def u(v, d=1):
    return "—" if v is None else f"{100 * v:.{d}f}%"


def day(ts):
    return "—" if ts is None else datetime.fromtimestamp(int(ts) / 1000, tz=UTC).strftime("%Y-%m-%d")


def table(head, rows):
    return "\n".join(["| " + " | ".join(head) + " |", "|" + "---|" * len(head)] +
                     ["| " + " | ".join(map(str, r)) + " |" for r in rows])


def tables(S):
    out = []
    D = S["descriptive"]
    rows = []
    for s in UNIVERSE:
        b = D.get(s, {}).get("binance") or {}
        yrs = [p((b.get("years") or {}).get(str(y), {}) and b["years"][str(y)]["ann"]) for y in YEARS]
        dev, y26 = b.get("dev") or {}, b.get("y2026") or {}
        rows.append([s.replace("USDT", "")] + yrs + [p(dev.get("ann")), u(dev.get("neg_prints"), 0), u(dev.get("neg_days"), 0),
                                                    f"{u(dev.get('worst_dd'), 2)} ({day(dev.get('dd_from'))}→{day(dev.get('dd_to'))})",
                                                    p(y26.get("ann")), u(y26.get("neg_prints"), 0)])
    out.append("### Binance gross funding yield (annualised, % of notional; short perp receives)\n\n" + table(
        ["coin"] + [str(y) for y in YEARS] + ["dev ann", "dev neg prints", "dev neg days", "dev worst cum. DD",
                                              "2026 ann", "2026 neg prints"], rows))
    rows = []
    for s in UNIVERSE:
        r = [s.replace("USDT", "")]
        for v in ("binance", "bybit", "hyperliquid"):
            x = D.get(s, {}).get(v) or {}
            r += [p((x.get("dev") or {}).get("ann")), p((x.get("y2026") or {}).get("ann")),
                  u((x.get("dev") or {}).get("neg_prints"), 0)]
            if v != "binance":
                r.append(day(x.get("first")))
        rows.append(r)
    out.append("### Cross-venue gross yield (dev = 2020–2025 where available; HL from its launch)\n\n" + table(
        ["coin", "BN dev", "BN 2026", "BN neg", "BY dev", "BY 2026", "BY neg", "BY from", "HL dev", "HL 2026", "HL neg",
         "HL from"], rows))
    rows = []
    for name in VARIANTS:
        v = S["variants"][name]
        d, y = v["dev"] or {}, v["y2026"] or {}
        bnb = (S["sensitivity"].get(f"{name}|bnb") or {}).get("dev") or {}
        rows.append([name, p(d.get("cagr")), u(d.get("vol")), "—" if d.get("sharpe") is None else f"{d['sharpe']:.2f}",
                     u(d.get("max_dd"), 2), p(d.get("funding_yr")), u(d.get("fees_yr"), 2), p(d.get("basis_yr"), 2),
                     u(d.get("deployed"), 0), v["rebalances"], v["n_liquidations"], p(bnb.get("cagr")),
                     p(y.get("total")), u(y.get("max_dd"), 2)])
    out.append("### Variants, net of all costs, return on TOTAL capital (L = 1)\n\n" + table(
        ["variant", "dev CAGR", "vol", "Sharpe", "max DD", "funding/yr", "fees/yr", "basis/yr", "avg deployed",
         "rebal.", "liq.", "dev CAGR (BNB fee)", "2026 total", "2026 max DD"], rows))
    rows = [[name] + [p((S["variants"][name]["years"].get(str(yy)) or {}).get("total")) for yy in YEARS]
            + [sum(1 for yy in YEARS[:-1] if ((S["variants"][name]["years"].get(str(yy)) or {}).get("total") or -1) > 0)]
            for name in VARIANTS]
    out.append("### Per-year net return (2026 = Jan 1 … Oct 17; years before a variant's data exist show its idle cash)\n\n"
               + table(["variant"] + [str(y) for y in YEARS] + ["positive dev years"], rows))
    rows = []
    for name in ("V1 AO-BTC", "V2 AO-ETH"):
        for L in (1, 2, 3):
            key = name if L == 1 else f"{name}|L{L}"
            v = S["variants"][name] if L == 1 else S["sensitivity"][key]
            n = v.get("n_liquidations", 0)
            rows.append([name, f"{L}x", p((v["dev"] or {}).get("cagr")), u((v["dev"] or {}).get("max_dd"), 2),
                         p((v["y2026"] or {}).get("total")), n])
    out.append("### Capital efficiency: short-leg margin L (return on spot + margin)\n\n" + table(
        ["variant", "L", "dev CAGR", "dev max DD", "2026 total", "liquidations"], rows))
    rows = []
    for s in UNIVERSE:
        x = S["pumps"][s]
        mm = 0.005 if s in ("BTCUSDT", "ETHUSDT") else 0.01
        worst = max(x["dev_30d"]["pump"], x["y2026_30d"]["pump"])
        w1 = max(x["dev_1d"]["pump"], x["y2026_1d"]["pump"])
        ok = ["yes" if w1 < 1 / L - mm else "NO" for L in (1, 2, 3)] + \
             ["yes" if worst < 1 / L - mm else "NO" for L in (1, 2, 3)]
        rows.append([s.replace("USDT", ""), f"{u(x['dev_1d']['pump'])} ({day(x['dev_1d']['at'])})", u(x["dev_7d"]["pump"]),
                     f"{u(x['dev_30d']['pump'])} ({day(x['dev_30d']['at'])})", u(x["y2026_1d"]["pump"]),
                     u(x["y2026_30d"]["pump"])] + ok)
    out.append("### Largest perp pumps (last-price highs) and short-leg survival at L = 1/2/3x\n\n"
               "Survives = pump < 1/L − maintenance. '1d' assumes the leg is re-margined every close (worst one-day "
               "pump); '30d' assumes no top-up for a month.\n\n" + table(
        ["coin", "dev 1d", "dev 7d", "dev 30d", "2026 1d", "2026 30d", "1d 1x", "1d 2x", "1d 3x", "30d 1x", "30d 2x",
         "30d 3x"], rows))
    rows = []
    for name in VARIANTS:
        v = S["variants"][name]
        yrs = [(v["years"].get(str(y)) or {}).get("total") for y in YEARS[:-1]]
        pos = sum(1 for x in yrs if x is not None and x > 0)
        y26 = (v["y2026"] or {}).get("total")
        dd, cagr = (v["dev"] or {}).get("max_dd"), (v["dev"] or {}).get("cagr")
        ok = pos >= 5 and y26 is not None and y26 > 0 and dd is not None and cagr is not None and dd < cagr
        rows.append([name, f"{pos}/6", "—" if y26 is None else f"{100 * y26:+.3f}%", u(dd, 2), p(cagr), "yes" if ok else "no"])
    out.append("### Pre-registered §7 screen (≥ 5/6 positive dev years AND 2026 > 0 AND max DD < dev CAGR)\n\n" + table(
        ["variant", "positive dev years", "2026 total", "dev max DD", "dev CAGR", "worth a forward paper test"], rows))
    return "\n\n".join(out) + "\n"


def charts(S):
    with gzip.open(RAW / "carry_series.json.gz", "rt") as fh:
        series = json.load(fh)
    pick = ("V1 AO-BTC", "V2 AO-ETH", "V3 AO-EW", "V4 TOP3-30", "V8 T30-10/5", "V10 XV-BN-BY", "V11 XV-BN-HL")
    (OUT / "variants.svg").write_text(equity_svg([(k, series[k]["ts"], series[k]["equity"], False) for k in pick],
                                                 "Carry variants, net, return on total capital (L = 1; split at 2026)",
                                                 split_ts=DEV_END, log=False, split_label="2026 (reported separately)"))
    blob = load()
    days, perp, spot, pairs, xv = world(blob)
    lines = []
    for s in ("BTCUSDT", "ETHUSDT"):
        t30 = trailing(perp[s], days, 30)
        idx = [i for i, v in enumerate(t30) if v is not None]
        lines.append((f"{s[:-4]} Binance 30d", [days[i] for i in idx], [1 + t30[i] for i in idx], False))
        for v in ("bybit", "hyperliquid"):
            t = trailing(xv[(v, s)], days, 30)
            idx = [i for i, x in enumerate(t) if x is not None]
            if idx:
                lines.append((f"{s[:-4]} {v} 30d", [days[i] for i in idx], [1 + t[i] for i in idx], True))
    (OUT / "funding_30d.svg").write_text(equity_svg(lines, "Trailing 30-day funding, annualised (1.00× = 0 %, 1.10× = +10 %)",
                                                    split_ts=DEV_END, log=False, split_label="2026 (reported separately)"))


def main():
    S = json.loads((OUT / "summary.json").read_text())
    (OUT / "tables.md").write_text(tables(S))
    charts(S)
    print((OUT / "tables.md").read_text())


if __name__ == "__main__":
    main()
