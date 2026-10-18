"""Minimal stdlib SVG line charts for the strategy-research report (log-scale equity curves)."""
from __future__ import annotations

import math
from datetime import UTC, datetime

COLORS = ["#1f77b4", "#d62728", "#2ca02c", "#9467bd", "#ff7f0e", "#8c564b", "#17becf"]


def _esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def equity_svg(series, title, split_ts=None, width=900, height=420, log=True):
    """``series`` = [(label, ts list, equity list, dashed?)]; every curve starts at 1.0."""
    pad_l, pad_r, pad_t = 60, 20, 34
    pad_b = 40 + 16 * len(series)
    height += 16 * len(series)
    t0 = min(s[1][0] for s in series)
    t1 = max(s[1][-1] for s in series)
    f = (lambda v: math.log10(max(v, 1e-6))) if log else (lambda v: v)
    ys = [f(v) for s in series for v in s[2]]
    lo, hi = min(ys), max(ys)
    if hi - lo < 1e-9:
        hi = lo + 1

    def X(t):
        return pad_l + (t - t0) / max(1, t1 - t0) * (width - pad_l - pad_r)

    def Y(v):
        return pad_t + (hi - f(v)) / (hi - lo) * (height - pad_t - pad_b)
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
           f'font-family="sans-serif" font-size="11">',
           f'<rect width="{width}" height="{height}" fill="white"/>',
           f'<text x="{pad_l}" y="20" font-size="14" font-weight="bold">{_esc(title)}</text>']
    if log:
        for k in range(math.floor(lo), math.ceil(hi) + 1):
            for m in (1, 2, 5):
                v = m * 10 ** k
                if lo <= math.log10(v) <= hi:
                    y = Y(v)
                    out.append(f'<line x1="{pad_l}" x2="{width - pad_r}" y1="{y:.1f}" y2="{y:.1f}" stroke="#eee"/>')
                    out.append(f'<text x="{pad_l - 6}" y="{y + 4:.1f}" text-anchor="end">{v:g}×</text>')
    else:
        k = 10 ** math.floor(math.log10((hi - lo) / 4))
        step = next(m * k for m in (1, 2, 5, 10) if (hi - lo) / (m * k) <= 6)
        v = math.ceil(lo / step) * step
        while v <= hi:
            y = Y(v)
            out.append(f'<line x1="{pad_l}" x2="{width - pad_r}" y1="{y:.1f}" y2="{y:.1f}" stroke="#eee"/>')
            out.append(f'<text x="{pad_l - 6}" y="{y + 4:.1f}" text-anchor="end">{v:.2f}×</text>')
            v += step
    if t1 - t0 < 2 * 365 * 86_400_000:
        d = datetime.fromtimestamp(t0 / 1000, tz=UTC)
        yr, mo = d.year, d.month
        while True:
            t = datetime(yr, mo, 1, tzinfo=UTC).timestamp() * 1000
            if t > t1:
                break
            if t >= t0:
                x = X(t)
                out.append(f'<line x1="{x:.1f}" x2="{x:.1f}" y1="{pad_t}" y2="{height - pad_b}" stroke="#eee"/>')
                out.append(f'<text x="{x:.1f}" y="{height - pad_b + 14}" text-anchor="middle">{yr}-{mo:02d}</text>')
            yr, mo = (yr + 1, 1) if mo == 12 else (yr, mo + 1)
    y0 = datetime.fromtimestamp(t0 / 1000, tz=UTC).year
    y1 = datetime.fromtimestamp(t1 / 1000, tz=UTC).year
    for yr in range(y0, y1 + 2) if t1 - t0 >= 2 * 365 * 86_400_000 else ():
        t = datetime(yr, 1, 1, tzinfo=UTC).timestamp() * 1000
        if t0 <= t <= t1:
            x = X(t)
            out.append(f'<line x1="{x:.1f}" x2="{x:.1f}" y1="{pad_t}" y2="{height - pad_b}" stroke="#eee"/>')
            out.append(f'<text x="{x:.1f}" y="{height - pad_b + 14}" text-anchor="middle">{yr}</text>')
    if split_ts is not None and t0 <= split_ts <= t1:
        x = X(split_ts)
        out.append(f'<rect x="{x:.1f}" y="{pad_t}" width="{width - pad_r - x:.1f}" height="{height - pad_t - pad_b}" '
                   f'fill="#fff3cd" opacity="0.6"/>')
        out.append(f'<text x="{x + 4:.1f}" y="{pad_t + 12}" fill="#8a6d3b">holdout</text>')
    for j, (label, ts, eq, dashed) in enumerate(series):
        step = max(1, len(ts) // 900)
        pts = " ".join(f"{X(t):.1f},{Y(v):.1f}" for t, v in list(zip(ts, eq))[::step] + [(ts[-1], eq[-1])])
        col = COLORS[j % len(COLORS)]
        dash = ' stroke-dasharray="5,3"' if dashed else ""
        out.append(f'<polyline fill="none" stroke="{col}" stroke-width="1.6"{dash} points="{pts}"/>')
        ly = height - pad_b + 32 + j * 16
        out.append(f'<line x1="{pad_l}" x2="{pad_l + 20}" y1="{ly - 4}" y2="{ly - 4}" '
                   f'stroke="{col}" stroke-width="2"{dash}/>')
        out.append(f'<text x="{pad_l + 26}" y="{ly}">{_esc(label)} — final {eq[-1]:.2f}×</text>')
    out.append("</svg>")
    return "\n".join(out)
