"""Markdown tables for docs/evidence/swing-htf/results.md (from swing_universe_report.main)."""
from __future__ import annotations

from datetime import UTC, datetime


def _d(ms):
    return datetime.fromtimestamp(ms / 1000, tz=UTC).strftime("%Y-%m-%d")


def _f(x, nd=3):
    if x is None:
        return "–"
    if isinstance(x, float):
        return f"{x:.{nd}f}" if abs(x) >= 1e-3 or x == 0 else f"{x:.1e}"
    return str(x)


HDR = "| variant | n | coins | hit | gross R | fees R | funding R | net R | net (all-taker) | z | Holm p | PF | maxDD R | hold d | /coin/yr |"
SEP = "|" + "---|" * 15


def row(name, o):
    if not o or not o.get("n"):
        return f"| {name} | 0 |" + " |" * 14
    return (f"| {name} | {o['n']} | {o.get('coins', '–')} | {_f(o['hit'])} | {_f(o['gross'])} | {_f(o['fees'])} | "
            f"{_f(o['funding'])} | **{_f(o['net'])}** | {_f(o['net_taker'])} | {_f(o['z'], 2)} | "
            f"{_f(o.get('p_holm'), 3)} | {_f(o['pf'], 2)} | {_f(o['max_dd_r'], 1)} | {_f(o['hold_days'], 1)} | "
            f"{_f(o['per_coin_year'], 2)} |")


def render(res, full):
    L = [f"# Swing HTF universe backtest — raw tables\n",
         f"Split: OOS from {_d(res['oos_start'])} to {_d(res['end'])} (same date for every coin). "
         f"Variant grid per pair: {res['grid_size']}; Holm family (all variants tried incl. references): "
         f"{res['family_size']} (Bonferroni one-sided z ≥ {res['bonferroni_z_one_sided']}); with OOS n ≥ 10: "
         f"{res['variants_with_oos_n_ge_10']}. "
         f"Any variant passing every check: **{res['any_pass']}**.\n",
         "## Universe\n", f"Rule: {res['universe']['rule']}.\n",
         "| decision date | candidates | coins (mean daily quote volume, $M) |", "|---|---|---|"]
    for k in ("main", "asof_oos"):
        u = res["universe"][k]
        coins = ", ".join(f"{c['symbol']} ({c['mean_qv_usd'] / 1e6:.0f})" for c in u["coins"])
        L.append(f"| {u['decision_date']} | {u['candidates']} | {coins} |")
    for pair, p in res["pairs"].items():
        L += [f"\n## {pair}\n", f"Variants {p['n_variants']}, IS n ≥ 30: {p['n_is_ge_30']}, OOS n ≥ 10: {p['n_oos_ge_10']}.\n",
              "### Pre-registered candidates (best 5 by in-sample net), in-sample → out-of-sample\n", HDR, SEP]
        for v in p["pre_registered"]:
            L.append(row(f"IS {v}", full[f"{pair} {v}"]["is"]))
            L.append(row(f"OOS {v}", full[f"{pair} {v}"]["oos"]))
        L += ["\n### Closest to passing (best 5 by OOS z — post hoc)\n", HDR, SEP]
        for v in p["closest_to_passing"]:
            L.append(row(f"OOS {v}", full[f"{pair} {v}"]["oos"]))
        L += ["\n### Factor levels (OOS net R, trade-weighted over every variant with that level)\n",
              "| factor | levels (net R / trades) |", "|---|---|"]
        for f, lv in p["factor_means"].items():
            L.append(f"| {f} | " + "; ".join(f"{k}: {_f(x['net'])} / {x['trades']}" for k, x in lv.items()) + " |")
        L += ["\n### Expanding walk-forward selection over the whole grid (yearly folds)\n",
              "| fold | chosen (best net on all earlier trades, n ≥ 30) | train net | fold n | fold net |", "|---|---|---|---|---|"]
        for w in p["walk_forward_selection"]:
            L.append(f"| {w['fold']}..{w['to']} | {w['chosen']} | {_f(w['train_net'])} | {w['n']} | {_f(w['net'])} |")
    L += ["\n## Candidate controls (OOS)\n",
          "| pair variant | IS net (n) | net R [95 % month-block CI] | coins + | w/o best coin | top-2 share | random gross (share ≥) | "
          "variant gross | no-HTF net | as-of-universe net (n) | yearly folds net (n) | passes |", "|" + "---|" * 11]
    for name, d in res["candidates"].items():
        o, r = d["oos"], d["random_oos_gross"] or {}
        ci = o.get("ci95") or [None, None]
        wb = o.get("without_best_coin", {})
        folds = ", ".join(f"{f['fold'][:4]}: {_f(f['net'], 2)} ({f['n']})" for f in d["folds"])
        failed = [k for k, v in d["passes"].items() if not v and k != "all"]
        a = d["asof_universe_oos"]
        L.append(f"| {name} | {_f(d['is'].get('net'))} ({d['is'].get('n', 0)}) | {_f(o.get('net'))} [{_f(ci[0])}, {_f(ci[1])}] (n {o.get('n', 0)}) | {o.get('coins_positive', '–')} | "
                 f"{_f(wb.get('net'))} ({wb.get('coin')}) | {_f(o.get('top2_share_of_total'), 2)} | "
                 f"{_f(r.get('mean'))} ({_f(r.get('share_seeds_ge_variant'), 2)}) | {_f(o.get('gross'))} | "
                 f"{_f(d['no_htf_oos'].get('net'))} | {_f(a.get('net'))} ({a.get('n', 0)}) | {folds} | "
                 f"{'PASS' if d['passes']['all'] else 'fail: ' + ', '.join(failed)} |")
    L += ["\n## References (original model, same universe)\n", HDR, SEP]
    for name, t in res["references"].items():
        L.append(row(f"IS {name}", t["is"]))
        L.append(row(f"OOS {name}", t["oos"]))
    L += ["\n## Lowest Holm p over the whole family (OOS n ≥ 10)\n", HDR, SEP]
    for name, o in res["closest"].items():
        L.append(row(name, o))
    return "\n".join(L) + "\n"
