"""
chart.py — рендер торгового PNG-чарта по решению engine.

На входе:
  • klines (1H) — список dict {o, h, l, c, v}, oldest → newest
  • decision (от make_decision) — verdict + Entry/SL/TP уровни
  • market (опционально) — для overlay'ев POC/VAL/VAH, pivots

На выходе: PNG bytes (готово для Telegram sendPhoto).

Layout: 3 ряда (chart 65% · volume 20% · CVD 15%):
  • Свечи + EMA 9/20/21
  • Закрашенная Entry зона
  • SL красная линия
  • TP1/TP2/TP3 зелёные пунктиры с подписями
  • POC/VAH/VAL горизонтали (если есть в market)
  • Volume bars (зелёные/красные)
  • CVD линия

Если verdict = WAIT или SKIP — чарт всё равно рендерится, но без
торговых зон (показываются только свечи + EMA + контекст).
"""

import io
from datetime import datetime, timedelta, timezone

import matplotlib
matplotlib.use("Agg")  # headless — обязательно ДО pyplot

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np


# ─── Visual constants ────────────────────────────────────────────────────

BG_COLOR     = "#0d1117"
GRID_COLOR   = "#21262d"
TEXT_COLOR   = "#c9d1d9"
UP_COLOR     = "#26a69a"
DOWN_COLOR   = "#ef5350"
EMA9_COLOR   = "#ffd54f"
EMA20_COLOR  = "#42a5f5"
EMA21_COLOR  = "#ab47bc"
ENTRY_LONG   = ("#26a69a", 0.18)   # (color, alpha)
ENTRY_SHORT  = ("#ef5350", 0.18)
SL_COLOR     = "#ff5252"
TP_COLOR     = "#69f0ae"
POC_COLOR    = "#ffca28"
VA_COLOR     = "#7e6f3a"
PIVOT_COLOR  = "#90a4ae"

DEFAULT_BARS = 100


# ─── Public API ──────────────────────────────────────────────────────────

def render_signal_chart(
    symbol: str,
    klines: list,
    decision: dict,
    market: dict | None = None,
    tf_minutes: int = 60,
    bars: int = DEFAULT_BARS,
) -> bytes | None:
    """
    Возвращает PNG bytes, или None если данных недостаточно.
    """
    if not klines or len(klines) < 20:
        return None

    market  = market or {}
    klines  = klines[-bars:]  # последние N баров
    n       = len(klines)
    closes  = np.array([c["c"] for c in klines], dtype=float)
    opens   = np.array([c["o"] for c in klines], dtype=float)
    highs   = np.array([c["h"] for c in klines], dtype=float)
    lows    = np.array([c["l"] for c in klines], dtype=float)
    volumes = np.array([c["v"] for c in klines], dtype=float)

    # Синтезируем timestamps — последний бар = now, шагаем назад на tf_minutes
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    times = np.array([
        now - timedelta(minutes=tf_minutes * (n - 1 - i)) for i in range(n)
    ])
    times_num = mdates.date2num(times)
    bar_width = (tf_minutes / 60.0) / 24.0 * 0.7  # 70% of bar width in days

    # ─── Figure & axes ───────────────────────────────────────────────────
    fig, (ax_price, ax_vol, ax_cvd) = plt.subplots(
        nrows=3, ncols=1, sharex=True,
        gridspec_kw={"height_ratios": [6.5, 1.5, 1.5], "hspace": 0.05},
        figsize=(12, 8), facecolor=BG_COLOR,
    )

    for ax in (ax_price, ax_vol, ax_cvd):
        _style_axis(ax)

    # ─── Candles ─────────────────────────────────────────────────────────
    _draw_candles(ax_price, times_num, opens, highs, lows, closes, bar_width)

    # ─── EMA overlay ─────────────────────────────────────────────────────
    if n >= 22:
        ema9  = _ema(closes, 9)
        ema20 = _ema(closes, 20)
        ema21 = _ema(closes, 21)
        ax_price.plot(times_num, ema9,  color=EMA9_COLOR,  lw=1.1, label="EMA 9",  alpha=0.9)
        ax_price.plot(times_num, ema20, color=EMA20_COLOR, lw=1.1, label="EMA 20", alpha=0.9)
        ax_price.plot(times_num, ema21, color=EMA21_COLOR, lw=1.1, label="EMA 21", alpha=0.9)

    # ─── Volume Profile overlay (POC / VAH / VAL) ────────────────────────
    vp = market.get("vp") or {}
    if vp.get("poc"):
        _hline(ax_price, vp["poc"], POC_COLOR, 1.4, "-",
               f"POC ${_fmt_price(vp['poc'])}", alpha=0.85)
    if vp.get("vah"):
        _hline(ax_price, vp["vah"], VA_COLOR, 0.8, ":",
               f"VAH ${_fmt_price(vp['vah'])}", alpha=0.7)
    if vp.get("val"):
        _hline(ax_price, vp["val"], VA_COLOR, 0.8, ":",
               f"VAL ${_fmt_price(vp['val'])}", alpha=0.7)

    # ─── Trade zones (Entry / SL / TP) — только для LONG/SHORT ──────────
    verdict = decision.get("verdict", "WAIT")
    if verdict in ("LONG", "SHORT"):
        _draw_trade_zones(ax_price, times_num, decision, verdict)

    # ─── Volume subplot ──────────────────────────────────────────────────
    colors_v = [UP_COLOR if c >= o else DOWN_COLOR
                for c, o in zip(closes, opens)]
    ax_vol.bar(times_num, volumes, color=colors_v, width=bar_width, alpha=0.7)
    ax_vol.set_ylabel("Vol", color=TEXT_COLOR, fontsize=9)
    ax_vol.tick_params(labelbottom=False)

    # ─── CVD subplot ─────────────────────────────────────────────────────
    cvd_series = _compute_cvd_series(opens, closes, volumes)
    cvd_color = UP_COLOR if cvd_series[-1] >= cvd_series[0] else DOWN_COLOR
    ax_cvd.plot(times_num, cvd_series, color=cvd_color, lw=1.4)
    ax_cvd.fill_between(times_num, cvd_series, 0,
                        color=cvd_color, alpha=0.15)
    ax_cvd.axhline(0, color=GRID_COLOR, lw=0.8)
    ax_cvd.set_ylabel("CVD", color=TEXT_COLOR, fontsize=9)

    # ─── X axis formatting ───────────────────────────────────────────────
    ax_cvd.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M\n%d.%m"))
    ax_cvd.xaxis.set_major_locator(mdates.AutoDateLocator(maxticks=8))

    # ─── Title ───────────────────────────────────────────────────────────
    title = _build_title(symbol, tf_minutes, decision)
    fig.suptitle(title, color=TEXT_COLOR, fontsize=13,
                 fontweight="bold", y=0.985)

    # ─── Legend (top-left of price axis) ─────────────────────────────────
    if n >= 22:
        leg = ax_price.legend(loc="upper left", facecolor=BG_COLOR,
                              edgecolor=GRID_COLOR, fontsize=8,
                              framealpha=0.85)
        for text in leg.get_texts():
            text.set_color(TEXT_COLOR)

    # ─── Export ──────────────────────────────────────────────────────────
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=120, facecolor=BG_COLOR,
                bbox_inches="tight", pad_inches=0.3)
    plt.close(fig)
    buf.seek(0)
    return buf.getvalue()


# ─── Drawing primitives ──────────────────────────────────────────────────

def _style_axis(ax):
    ax.set_facecolor(BG_COLOR)
    ax.grid(color=GRID_COLOR, lw=0.4, alpha=0.6)
    ax.tick_params(colors=TEXT_COLOR, labelsize=8)
    for spine in ax.spines.values():
        spine.set_color(GRID_COLOR)


def _draw_candles(ax, times_num, opens, highs, lows, closes, bar_width):
    """Ручная отрисовка свечей через vlines (wick) + bar (body)."""
    up   = closes >= opens
    down = ~up

    # Wicks
    ax.vlines(times_num[up],   lows[up],   highs[up],
              color=UP_COLOR, lw=0.8)
    ax.vlines(times_num[down], lows[down], highs[down],
              color=DOWN_COLOR, lw=0.8)

    # Bodies
    body_h_up   = closes[up]   - opens[up]
    body_h_down = opens[down]  - closes[down]
    ax.bar(times_num[up],   body_h_up,   bottom=opens[up],
           width=bar_width, color=UP_COLOR,   edgecolor=UP_COLOR)
    ax.bar(times_num[down], body_h_down, bottom=closes[down],
           width=bar_width, color=DOWN_COLOR, edgecolor=DOWN_COLOR)

    ax.set_ylabel("Price", color=TEXT_COLOR, fontsize=9)


def _draw_trade_zones(ax, times_num, decision, verdict):
    """Закрашенная Entry zone + SL + TP1/2/3 с подписями."""
    entry = decision.get("entry") or {}
    e_min = entry.get("min")
    e_max = entry.get("max")
    sl    = decision.get("sl")
    tp1   = decision.get("tp1")
    tp2   = decision.get("tp2")
    tp3   = decision.get("tp3")

    if e_min is not None and e_max is not None:
        color, alpha = ENTRY_LONG if verdict == "LONG" else ENTRY_SHORT
        ax.axhspan(e_min, e_max, color=color, alpha=alpha, zorder=0)
        mid = (e_min + e_max) / 2
        _annotate(ax, times_num[-1], mid,
                  f"  Entry {_fmt_price(e_min)}–{_fmt_price(e_max)}",
                  color="white", weight="bold")

    if sl is not None:
        _hline(ax, sl, SL_COLOR, 1.6, "--",
               f"SL {_fmt_price(sl)}", alpha=0.95)

    for tp, label in ((tp1, "TP1"), (tp2, "TP2"), (tp3, "TP3")):
        if tp is not None:
            rr_key = {"TP1": "rr1", "TP2": "rr2", "TP3": "rr3"}[label]
            rr     = decision.get(rr_key)
            txt    = f"{label} {_fmt_price(tp)}"
            if rr:
                txt += f"  (RR {rr})"
            _hline(ax, tp, TP_COLOR, 1.2, "--", txt, alpha=0.9)


def _hline(ax, y, color, lw, ls, label, alpha=1.0):
    ax.axhline(y, color=color, lw=lw, ls=ls, alpha=alpha)
    ax.annotate(
        f"  {label}",
        xy=(1.001, y), xycoords=("axes fraction", "data"),
        color=color, fontsize=8, va="center", weight="bold",
        clip_on=False,
    )


def _annotate(ax, x, y, text, color="white", weight="normal"):
    ax.text(x, y, text, color=color, fontsize=8,
            va="center", ha="left", weight=weight,
            bbox=dict(boxstyle="round,pad=0.2",
                      facecolor="#000000", alpha=0.55,
                      edgecolor="none"))


# ─── Helpers ─────────────────────────────────────────────────────────────

def _ema(values: np.ndarray, span: int) -> np.ndarray:
    """Стандартная EMA (тот же расчёт, что в screener._ema)."""
    if len(values) == 0:
        return values
    alpha = 2.0 / (span + 1)
    out = np.empty_like(values)
    out[0] = values[0]
    for i in range(1, len(values)):
        out[i] = alpha * values[i] + (1 - alpha) * out[i - 1]
    return out


def _compute_cvd_series(opens, closes, volumes) -> np.ndarray:
    """Cumulative volume delta: green = +vol, red = -vol."""
    sign  = np.where(closes >= opens, 1.0, -1.0)
    delta = sign * volumes
    return np.cumsum(delta)


def _build_title(symbol: str, tf_minutes: int, decision: dict) -> str:
    sym = symbol.replace("USDT.P", "").replace("USDT", "")
    tf_label = {60: "1H", 240: "4H", 15: "15m", 30: "30m", 1440: "1D"}.get(
        tf_minutes, f"{tf_minutes}m")
    v   = decision.get("verdict", "?")
    rr  = decision.get("rr1") or "—"
    cf  = decision.get("confidence", 0)
    return f"{sym}/USDT · {tf_label} · {v} · RR(TP1) {rr} · Conf {cf}/100"


def _fmt_price(p) -> str:
    if p is None:
        return "—"
    ap = abs(float(p))
    if ap >= 1000:
        return f"{p:,.2f}"
    if ap >= 10:
        return f"{p:,.3f}"
    if ap >= 1:
        return f"{p:,.4f}"
    if ap >= 0.01:
        return f"{p:.5f}"
    return f"{p:.7f}"
