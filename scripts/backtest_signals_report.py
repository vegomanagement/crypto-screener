"""Markdown tables for scripts/backtest_signals.py results."""
from __future__ import annotations


def _ci(x):
    return "—" if not x else f"[{x[0]:+.3f}, {x[1]:+.3f}]"


def _pct(x):
    return "—" if x is None else f"{100 * x:.1f}%"


def _wci(x):
    return "—" if not x else f"{100 * x[0]:.1f}–{100 * x[1]:.1f}%"


def _params(v):
    name, params, filt = v
    p = ",".join(f"{k}={val}" for k, val in params.items() if k != "delta") or "—"
    return f"{p} · {filt}"


def write_tables(report, path):
    lines = [f"# Event-signal backtest — generated {report['generated']}", "",
             f"Variants tested (signal × params × filter × timeframe): **{report['variants_tested']}**; "
             f"OOS hypotheses (one chosen variant per signal × timeframe): **{report['oos_hypotheses']}**; "
             f"Bonferroni z = {report['z_bonferroni']} (α = 0.05 / hypotheses).", "",
             "Rules: " + report["rules"].replace("\n", " "), "",
             "## Series", "", "| symbol | tf | bars | first | OOS from |", "|---|---|---:|---|---|"]
    from datetime import UTC, datetime

    def day(ms):
        return datetime.fromtimestamp(ms / 1000, tz=UTC).strftime("%Y-%m-%d")
    for m in report["series"]:
        lines.append(f"| {m['symbol']} | {m['interval']} | {m['bars']} | {day(m['first_ts'])} | "
                     f"{day(m['oos_from_ts'])} |")
    lines += ["", "## Out-of-sample, chosen variant per signal × timeframe (net of fees, 1.5R)", "",
              "| tf | signal | variant (fit on IS) | IS n | IS E[R] | OOS n | ±1ATR hit (decided) | "
              "Wilson 95% | 1.5R win | E[R] net | 95% CI | Bonferroni CI | E[R] 2R net | E[R] gross | "
              "fee R | pass |",
              "|---|---|---|---:|---:|---:|---:|---|---:|---:|---|---|---:|---:|---:|---|"]
    for key, b in report["choices"].items():
        iv, fam = key.split(":")
        o, ob, o2, og = b["oos_net_1.5"], b["oos_net_1.5_bonf"], b["oos_net_2"], b["oos_gross_1.5"]
        if not o.get("n"):
            lines.append(f"| {iv} | {fam} | {_params(b['variant'])} | {b['is']['n']} | — | 0 |"
                         + " — |" * 9 + " no |")
            continue
        lines.append(
            f"| {iv} | {fam} | {_params(b['variant'])} | {b['is']['n']} | "
            f"{b['is'].get('expectancy_r', 0):+.3f} | {o['n']} | {_pct(o['hit_rate_decided'])} | "
            f"{_wci(o['hit_ci95_decided'])} | {_pct(o['rr_win_rate'])} | {o['expectancy_r']:+.3f} | "
            f"{_ci(o['expectancy_ci'])} | {_ci(ob['expectancy_ci'])} | {o2['expectancy_r']:+.3f} | "
            f"{og['expectancy_r']:+.3f} | {o['mean_fee_r']:.3f} | {'**yes**' if b['passes'] else 'no'} |")
    lines += ["", "## OOS by symbol (net 1.5R expectancy, n)", "",
              "| tf | signal | BTC | ETH | SOL |", "|---|---|---|---|---|"]
    for key, b in report["choices"].items():
        iv, fam = key.split(":")
        cells = []
        for sym in ("BTCUSDT", "ETHUSDT", "SOLUSDT"):
            v = b["oos_by_symbol"].get(sym, {})
            cells.append("—" if not v.get("n") else f"{v['expectancy_r']:+.3f} (n={v['n']})")
        lines.append(f"| {iv} | {fam} | " + " | ".join(cells) + " |")
    lines += ["", "## OOS by regime (net 1.5R expectancy, n)", "",
              "| tf | signal | trend aligned | counter | flat | vol high | vol low |",
              "|---|---|---|---|---|---|---|"]
    for key, b in report["choices"].items():
        iv, fam = key.split(":")
        cells = []
        for reg in ("trend=aligned", "trend=counter", "trend=flat", "vol=high", "vol=low"):
            v = b["oos_by_regime"].get(reg, {})
            cells.append("—" if not v.get("n") else f"{v['expectancy_r']:+.3f} ({v['n']})")
        lines.append(f"| {iv} | {fam} | " + " | ".join(cells) + " |")
    lines.append("")
    path.write_text("\n".join(lines))
