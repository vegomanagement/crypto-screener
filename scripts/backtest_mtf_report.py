"""Aggregation and markdown tables for scripts/backtest_mtf.py and scripts/backtest_swing.py."""
from __future__ import annotations

import json
from datetime import UTC, datetime

import signal_eval as se

FIELDS = ("atr_rule", "fee_r", "r1.5", "r2", "r3")
SETTINGS = (("r1.5", 1.0), ("r3", 1.0), ("r1.5", None), ("r3", None))   # (target, fee scale; None=maker)


def _rows(outs, maker_scale, maker):
    out = []
    for o in outs:
        d = dict(zip(FIELDS, o[:5]))
        if maker:
            d["fee_r"] = d["fee_r"] * maker_scale
        out.append(d)
    return out


def aggregate_align(results, a, filters, min_is, maker_scale, newest_from):
    report = {"generated": datetime.now(UTC).isoformat(timespec="seconds"), "end": a.end,
              "series": [m for m, _r in results], "filters": list(filters), "choices": {}}
    by_iv = {}
    for meta, rows in results:
        for key, outs in rows.items():
            by_iv.setdefault(meta["interval"], {}).setdefault(key, []).extend((meta["symbol"], o) for o in outs)
    tested, chosen = 0, []
    for iv, rows in by_iv.items():
        fams = sorted({json.loads(k)[0] for k in rows})
        for fam in fams:
            for f in filters:
                best = None
                for key, outs in rows.items():
                    name, params, filt = json.loads(key)
                    if name != fam or filt != f:
                        continue
                    tested += 1
                    st = se.summarize(_rows([o for _s, o in outs if not o[5]], maker_scale, False))
                    score = st.get("expectancy_r", -9) if st["n"] >= min_is else -9
                    if best is None or score > best[0]:
                        best = (score, key, st)
                chosen.append((iv, fam, f, best))
    m = sum(1 for c in chosen if c[3][0] > -9 and not c[1].startswith("control")) * len(SETTINGS)
    z = se.z_for(0.05 / max(1, m))
    report.update(variants_tested=tested, oos_hypotheses=m, z_bonferroni=round(z, 3), passed=[])
    for iv, fam, f, (score, key, is_st) in chosen:
        outs = by_iv[iv][key]
        oos = [(s, o) for s, o in outs if o[5]]
        blk = {"variant": json.loads(key), "is": {k: is_st.get(k) for k in ("n", "expectancy_r", "hit_rate_decided")}}
        for tgt, scale in SETTINGS:
            maker = scale is None
            tag = f"{tgt}_{'maker' if maker else 'taker'}"
            rows = _rows([o for _s, o in oos], maker_scale, maker)
            blk[tag] = se.summarize(rows, key=tgt)
            blk[tag + "_bonf"] = se.summarize(rows, key=tgt, z=z)
            blk[tag + "_newest"] = se.summarize(_rows([o for _s, o in oos if o[6] >= newest_from], maker_scale, maker), key=tgt)
            pos = sum(1 for sym in a.symbols
                      if (st := se.summarize(_rows([o for s, o in oos if s == sym], maker_scale, maker), key=tgt))
                      .get("n", 0) >= 30 and st["expectancy_r"] > 0)
            ci = blk[tag + "_bonf"].get("expectancy_ci")
            ok = (not fam.startswith("control") and is_st["n"] >= min_is and blk[tag]["n"] >= 100
                  and ci and ci[0] > 0 and pos >= 2)
            if ok:
                report["passed"].append(f"{iv}:{fam}:{f}:{tag}")
        blk["gross_1.5"] = se.summarize(_rows([o for _s, o in oos], maker_scale, False), net=False)
        report["choices"][f"{iv}:{fam}:{f}"] = blk
    return report


def _e(st):
    if not st or not st.get("n"):
        return "—"
    ci = st.get("expectancy_ci")
    return f"{st['expectancy_r']:+.3f}" + (f" [{ci[0]:+.2f}, {ci[1]:+.2f}]" if ci else "")


def _h(st):
    if not st or st.get("hit_rate_decided") is None:
        return "—"
    ci = st.get("hit_ci95_decided") or [0, 0]
    return f"{st['hit_rate_decided'] * 100:.1f} % [{ci[0] * 100:.1f}–{ci[1] * 100:.1f}]"


def align_tables(r):
    lines = [f"# HTF-alignment filter — OOS tables (generated {r['generated']}, data to {r['end']})", "",
             f"Variants tried: {r['variants_tested']}; OOS hypotheses (× 4 target/fee settings): "
             f"{r['oos_hypotheses']}; Bonferroni z = {r['z_bonferroni']}; passed: {r['passed'] or 'none'}.", "",
             "| tf | signal | filter | params | OOS n | ±1 ATR hit [95 %] | E[R] 1.5 taker [95 %] | E[R] 3 taker | "
             "E[R] 1.5 maker | E[R] 3 maker | gross 1.5 | newest n · E 1.5 taker |",
             "|---|---|---|---|---:|---|---|---|---|---|---:|---|"]
    for key, b in sorted(r["choices"].items()):
        iv, fam, f = key.split(":")
        t = b["r1.5_taker"]
        nw = b["r1.5_taker_newest"]
        lines.append(f"| {iv} | {fam} | {f} | {json.dumps(b['variant'][1])} | {t.get('n', 0)} | {_h(t)} | {_e(t)} | "
                     f"{_e(b['r3_taker'])} | {_e(b['r1.5_maker'])} | {_e(b['r3_maker'])} | "
                     f"{b['gross_1.5'].get('expectancy_r', '—')} | {nw.get('n', 0)} · {nw.get('expectancy_r', '—')} |")
    return "\n".join(lines) + "\n"


def _s(st):
    if not st or not st.get("n"):
        return "—"
    ci = st.get("expectancy_ci")
    return f"{st['expectancy_r']:+.3f}" + (f" [{ci[0]:+.2f}, {ci[1]:+.2f}]" if ci else "")


def swing_tables(r):
    lines = [f"# SMC swing model — OOS tables (generated {r['generated']}, data to {r['end']})", "",
             f"Variants tried: {r['variants_tested']}; OOS hypotheses: {r['oos_hypotheses']}; Bonferroni z = "
             f"{r['z_bonferroni']}.", "",
             "| pair | model | variant | IS n · E taker | OOS n | win rate [95 %] | E[R] taker [95 %] | E[R] maker | "
             "E[R] gross | max DD (R) | trades / month / symbol | random control E (p05–p95) | pass |",
             "|---|---|---|---|---:|---|---|---|---:|---:|---:|---|---|"]
    for pair, blk in r["pairs"].items():
        for k in ("filtered", "unfiltered"):
            b = blk.get(k)
            if not b:
                continue
            o = b["oos_taker"]
            rc = b.get("random_control")
            wr = "—" if not o.get("n") else f"{o['win_rate'] * 100:.1f} % [{o['win_ci95'][0] * 100:.1f}–{o['win_ci95'][1] * 100:.1f}]"
            rcs = "—" if not rc or rc.get("mean_expectancy_taker") is None else \
                f"{rc['mean_expectancy_taker']:+.3f} ({rc['p05']:+.2f}…{rc['p95']:+.2f})"
            lines.append(f"| {pair} | {'HTF POI + LTF CHoCH' if k == 'filtered' else 'LTF CHoCH only'} | "
                         f"{json.dumps(b['variant'][1])} | {b['is'].get('n', 0)} · {b['is'].get('expectancy_r', '—')} | "
                         f"{o.get('n', 0)} | {wr} | {_s(o)} | {_s(b['oos_maker'])} | "
                         f"{b['oos_gross'].get('expectancy_r', '—')} | {o.get('max_dd_r', '—')} | "
                         f"{b.get('oos_trades_per_month_per_symbol')} | {rcs} | {'yes' if b['passes'] else 'no'} |")
    lines += ["", "Per symbol (OOS, taker):", ""]
    for pair, blk in r["pairs"].items():
        for k in ("filtered", "unfiltered"):
            b = blk.get(k) or {}
            per = " · ".join(f"{s} n {v.get('n', 0)} E {v.get('expectancy_r', '—')}" for s, v in (b.get("oos_by_symbol") or {}).items())
            lines.append(f"- {pair} {k}: {per}")
    return "\n".join(lines) + "\n"
