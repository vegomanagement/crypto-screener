"""
chart.py — TradingView-style PNG чарт по engine-decision.

На входе:
  • klines (1H) — список dict {o, h, l, c, v}, oldest → newest
  • decision (от make_decision) — verdict + Entry/SL/TP уровни
  • market (опционально) — для overlay'ев POC/VAL/VAH, pivots

На выходе: PNG bytes (готово для Telegram sendPhoto).

Layout (TV-style):
  • Цена справа (правая Y-ось, как в TradingView)
  • 12% правого margin под "проекцию" — туда выезжают tag'и SL/TP/Entry
  • Свечи + EMA 9/20/21 (легенда справа сверху)
  • Закрашенная Entry зона (тянется в правый margin)
  • SL / TP1 / TP2 / TP3 — линии + цветные ценовые tag'и справа
  • POC / VAH / VAL — линии + tag'и
  • Текущая цена — выделенный tag цвета последней свечи
  • Symbol/TF watermark в левом верхнем углу
  • Volume bars (компактные)
  • CVD subplot
"""

import io
from datetime import datetime, timedelta, timezone

import matplotlib
matplotlib.use("Agg")  # headless — ДО pyplot

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np


# ─── Цветовая схема (TradingView dark) ───────────────────────────────────

BG_COLOR     = "#0d1117"
PANEL_BG     = "#131722"     # чуть светлее под графики
GRID_COLOR   = "#1e222d"
TEXT_COLOR   = "#d1d4dc"
MUTED_TEXT   = "#787b86"
UP_COLOR     = "#26a69a"
DOWN_COLOR   = "#ef5350"
EMA9_COLOR   = "#ffd54f"
EMA20_COLOR  = "#42a5f5"
EMA21_COLOR  = "#ab47bc"
ENTRY_LONG   = ("#26a69a", 0.18)
ENTRY_SHORT  = ("#ef5350", 0.18)
SL_COLOR     = "#ff5252"
TP_COLOR     = "#26a69a"
POC_COLOR    = "#ffca28"
VA_COLOR     = "#8c7a3f"
VWAP_COLOR   = "#7e57c2"     # фиолетовый под VWAP
PIVOT_COLOR  = "#90a4ae"     # серо-голубой под pivots

# Подсветка торговых сессий (UTC окна), очень faint
SESSION_ASIA   = ("#4fc3f7", 0.04)   # 00:00–08:00 UTC (Tokyo)
SESSION_LONDON = ("#81c784", 0.04)   # 08:00–16:00 UTC
SESSION_NY     = ("#ffb74d", 0.04)   # 13:00–21:00 UTC

DEFAULT_BARS = 100
RIGHT_PAD    = 0.12          # 12% правого margin под проекцию (как TV)


# ─── Public API ──────────────────────────────────────────────────────────

def render_signal_chart(
    symbol: str,
    klines: list,
    decision: dict,
    market: dict | None = None,
    tf_minutes: int = 60,
    bars: int = DEFAULT_BARS,
) -> bytes | None:
    """Возвращает PNG bytes, или None если данных недостаточно."""
    if not klines or len(klines) < 20:
        return None

    market  = market or {}
    klines  = klines[-bars:]
    n       = len(klines)
    closes  = np.array([c["c"] for c in klines], dtype=float)
    opens   = np.array([c["o"] for c in klines], dtype=float)
    highs   = np.array([c["h"] for c in klines], dtype=float)
    lows    = np.array([c["l"] for c in klines], dtype=float)
    volumes = np.array([c["v"] for c in klines], dtype=float)

    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    times = np.array([
        now - timedelta(minutes=tf_minutes * (n - 1 - i)) for i in range(n)
    ])
    times_num = mdates.date2num(times)
    bar_width = (tf_minutes / 60.0) / 24.0 * 0.7

    # Правый margin (проекция как в TV)
    span     = times_num[-1] - times_num[0]
    right_x  = times_num[-1] + span * RIGHT_PAD

    # ─── Figure & axes (TV proportions) ─────────────────────────────────
    fig, (ax_price, ax_vol, ax_cvd) = plt.subplots(
        nrows=3, ncols=1, sharex=True,
        gridspec_kw={"height_ratios": [8, 1.1, 1.4], "hspace": 0.03},
        figsize=(14, 8.5), facecolor=BG_COLOR,
    )

    for ax in (ax_price, ax_vol, ax_cvd):
        _style_axis(ax)

    # ─── Session highlights (бэкграунд под свечами) ─────────────────────
    _draw_session_highlights(ax_price, times)

    # ─── Candles ─────────────────────────────────────────────────────────
    _draw_candles(ax_price, times_num, opens, highs, lows, closes, bar_width)

    # ─── EMA overlay ─────────────────────────────────────────────────────
    if n >= 22:
        ema9  = _ema(closes, 9)
        ema20 = _ema(closes, 20)
        ema21 = _ema(closes, 21)
        ax_price.plot(times_num, ema9,  color=EMA9_COLOR,  lw=1.2,
                      label="EMA 9",  alpha=0.95)
        ax_price.plot(times_num, ema20, color=EMA20_COLOR, lw=1.2,
                      label="EMA 20", alpha=0.95)
        ax_price.plot(times_num, ema21, color=EMA21_COLOR, lw=1.2,
                      label="EMA 21", alpha=0.95)

    # ─── Сбор всех ценовых уровней (для y-range + tag'ов справа) ────────
    levels = []   # [(price, color, label, line_style)]
    vp = market.get("vp") or {}
    if vp.get("poc"):
        levels.append((vp["poc"], POC_COLOR,
                       f"POC {_fmt_price(vp['poc'])}", "-"))
    if vp.get("vah"):
        levels.append((vp["vah"], VA_COLOR,
                       f"VAH {_fmt_price(vp['vah'])}", ":"))
    if vp.get("val"):
        levels.append((vp["val"], VA_COLOR,
                       f"VAL {_fmt_price(vp['val'])}", ":"))

    # Daily VWAP (только daily — weekly слишком шумит на 1H графике)
    vwap_obj = market.get("vwap") or {}
    daily_vwap = (vwap_obj.get("daily") or {}).get("vwap")
    if daily_vwap:
        levels.append((daily_vwap, VWAP_COLOR,
                       f"VWAP {_fmt_price(daily_vwap)}", "-"))

    # Pivot Points (P + R1/S1 — самые важные; R2/R3/S2/S3 шумят)
    piv = market.get("pivots") or {}
    if piv.get("P") is not None:
        levels.append((piv["P"], PIVOT_COLOR,
                       f"P {_fmt_price(piv['P'])}", ":"))
    if piv.get("R1") is not None:
        levels.append((piv["R1"], PIVOT_COLOR,
                       f"R1 {_fmt_price(piv['R1'])}", ":"))
    if piv.get("S1") is not None:
        levels.append((piv["S1"], PIVOT_COLOR,
                       f"S1 {_fmt_price(piv['S1'])}", ":"))

    verdict = decision.get("verdict", "WAIT")
    if verdict in ("LONG", "SHORT"):
        entry = decision.get("entry") or {}
        e_min, e_max = entry.get("min"), entry.get("max")
        if e_min is not None and e_max is not None:
            color, alpha = ENTRY_LONG if verdict == "LONG" else ENTRY_SHORT
            ax_price.axhspan(e_min, e_max, color=color, alpha=alpha,
                             zorder=0)
            # Entry zone уже визуализирована полосой; tag не добавляем,
            # чтобы не накладывался на ● current-price (entry всегда вокруг цены).

        if (sl := decision.get("sl")) is not None:
            levels.append((sl, SL_COLOR, f"SL {_fmt_price(sl)}", "--"))
        for tp_key, lbl, rr_key in (
            ("tp1", "TP1", "rr1"),
            ("tp2", "TP2", "rr2"),
            ("tp3", "TP3", "rr3"),
        ):
            tp = decision.get(tp_key)
            if tp is not None:
                rr = decision.get(rr_key)
                text = f"{lbl} {_fmt_price(tp)}"
                if rr:
                    text += f" · RR {rr}"
                levels.append((tp, TP_COLOR, text, "--"))

    # ─── Линии уровней через всю ось (включая проекцию) ─────────────────
    for price, color, _label, ls in levels:
        if ls is None:
            continue
        ax_price.axhline(price, color=color, lw=1.3, ls=ls,
                         alpha=0.85, zorder=1)

    # ─── Текущая цена (highlighted tag в стиле TV) ──────────────────────
    last_close  = closes[-1]
    last_color  = UP_COLOR if closes[-1] >= opens[-1] else DOWN_COLOR
    ax_price.axhline(last_close, color=last_color, lw=0.6,
                     ls=(0, (4, 4)), alpha=0.4, zorder=0)

    # ─── Y-axis справа (как в TV) ───────────────────────────────────────
    ax_price.yaxis.tick_right()
    ax_price.yaxis.set_label_position("right")
    ax_price.tick_params(axis="y", colors=TEXT_COLOR, labelsize=8,
                         pad=2)

    # Установим xlim с правым margin ДО размещения tag'ов,
    # чтобы autoscale не съел нашу проекцию
    ax_price.set_xlim(times_num[0], right_x)

    # ─── Ценовые tag'и справа (TV-style цветные пилюли) ─────────────────
    _place_price_tags(ax_price, levels, last_close, last_color)

    # ─── Volume subplot ──────────────────────────────────────────────────
    colors_v = [UP_COLOR if c >= o else DOWN_COLOR
                for c, o in zip(closes, opens)]
    ax_vol.bar(times_num, volumes, color=colors_v, width=bar_width,
               alpha=0.75, linewidth=0)
    ax_vol.yaxis.tick_right()
    ax_vol.set_ylabel("")
    ax_vol.tick_params(axis="y", labelsize=7, colors=MUTED_TEXT)
    ax_vol.tick_params(labelbottom=False)
    ax_vol.text(0.005, 0.92, "Vol", transform=ax_vol.transAxes,
                color=MUTED_TEXT, fontsize=8, va="top")

    # ─── CVD subplot ─────────────────────────────────────────────────────
    cvd_series = _compute_cvd_series(opens, closes, volumes)
    cvd_color = UP_COLOR if cvd_series[-1] >= cvd_series[0] else DOWN_COLOR
    ax_cvd.plot(times_num, cvd_series, color=cvd_color, lw=1.4)
    ax_cvd.fill_between(times_num, cvd_series, 0,
                        color=cvd_color, alpha=0.18)
    ax_cvd.axhline(0, color=GRID_COLOR, lw=0.6)
    ax_cvd.yaxis.tick_right()
    ax_cvd.tick_params(axis="y", labelsize=7, colors=MUTED_TEXT)
    ax_cvd.text(0.005, 0.92, "CVD", transform=ax_cvd.transAxes,
                color=MUTED_TEXT, fontsize=8, va="top")

    # ─── X axis formatting ───────────────────────────────────────────────
    ax_cvd.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M\n%d.%m"))
    locator = mdates.AutoDateLocator(maxticks=9, minticks=5)
    ax_cvd.xaxis.set_major_locator(locator)

    # ─── Symbol watermark + verdict header (top-left) ────────────────────
    sym = symbol.replace("USDT.P", "").replace("USDT", "")
    tf_label = _tf_label(tf_minutes)
    ax_price.text(
        0.005, 0.985,
        f"{sym}/USDT · {tf_label}",
        transform=ax_price.transAxes,
        color=TEXT_COLOR, fontsize=15, fontweight="bold",
        va="top", ha="left",
    )
    v       = decision.get("verdict", "?")
    v_color = UP_COLOR if v == "LONG" else (
        DOWN_COLOR if v == "SHORT" else MUTED_TEXT)
    rr      = decision.get("rr1") or "—"
    conf    = decision.get("confidence", 0)
    ax_price.text(
        0.005, 0.935,
        f"{v}  ·  RR(TP1) {rr}  ·  Confidence {conf}/100",
        transform=ax_price.transAxes,
        color=v_color, fontsize=10, fontweight="bold",
        va="top", ha="left",
    )

    # ─── Legend (top-right) ──────────────────────────────────────────────
    if n >= 22:
        # Чуть отступим от правого края (там сидят tag'и)
        leg = ax_price.legend(
            loc="upper right",
            bbox_to_anchor=(0.95, 0.99),
            facecolor=PANEL_BG, edgecolor=GRID_COLOR,
            fontsize=8, framealpha=0.85,
        )
        for text in leg.get_texts():
            text.set_color(TEXT_COLOR)

    # ─── Export ──────────────────────────────────────────────────────────
    buf = io.BytesIO()
    try:
        fig.savefig(buf, format="png", dpi=120, facecolor=BG_COLOR,
                    bbox_inches="tight", pad_inches=0.25)
    finally:
        # plt.close ОБЯЗАТЕЛЕН — иначе figure утекает в pyplot state
        # и накапливается RAM при потоке сигналов
        plt.close(fig)
    buf.seek(0)
    return buf.getvalue()


# ─── Drawing primitives ──────────────────────────────────────────────────

def _style_axis(ax):
    ax.set_facecolor(PANEL_BG)
    ax.grid(color=GRID_COLOR, lw=0.5, alpha=0.7)
    ax.tick_params(colors=MUTED_TEXT, labelsize=8)
    for spine in ax.spines.values():
        spine.set_color(GRID_COLOR)
        spine.set_linewidth(0.8)


def _draw_session_highlights(ax, times):
    """
    Полупрозрачные вертикальные полосы Asia/London/NY (UTC окна).
    Рисуется до свечей (zorder=0) — не мешает читать прайс.
    """
    if len(times) < 2:
        return

    sessions = [
        (SESSION_ASIA,   0,  8),
        (SESSION_LONDON, 8, 16),
        (SESSION_NY,    13, 21),
    ]

    first, last = times[0], times[-1]
    cur_day = first.replace(hour=0, minute=0, second=0, microsecond=0)
    end_day = last.replace(hour=0, minute=0, second=0, microsecond=0) \
              + timedelta(days=1)

    while cur_day <= end_day:
        for (color, alpha), h_start, h_end in sessions:
            s = cur_day + timedelta(hours=h_start)
            e = cur_day + timedelta(hours=h_end)
            # Кропаем по видимому диапазону
            if e < first or s > last:
                continue
            ax.axvspan(mdates.date2num(max(s, first)),
                       mdates.date2num(min(e, last)),
                       color=color, alpha=alpha, zorder=0,
                       linewidth=0)
        cur_day += timedelta(days=1)


def _draw_candles(ax, times_num, opens, highs, lows, closes, bar_width):
    up   = closes >= opens
    down = ~up

    # Тонкие wicks
    ax.vlines(times_num[up],   lows[up],   highs[up],
              color=UP_COLOR, lw=0.7)
    ax.vlines(times_num[down], lows[down], highs[down],
              color=DOWN_COLOR, lw=0.7)

    # Bodies
    body_h_up   = closes[up]   - opens[up]
    body_h_down = opens[down]  - closes[down]
    ax.bar(times_num[up],   body_h_up,   bottom=opens[up],
           width=bar_width, color=UP_COLOR,   edgecolor=UP_COLOR,
           linewidth=0.5)
    ax.bar(times_num[down], body_h_down, bottom=closes[down],
           width=bar_width, color=DOWN_COLOR, edgecolor=DOWN_COLOR,
           linewidth=0.5)


def _place_price_tags(ax, levels, last_close, last_color):
    """
    TV-style ценовые tag'и справа: цветной прямоугольник с белым
    текстом у правой оси, точно на уровне цены.

    Стратегия от коллизий: НЕ двигаем tag'и по Y (тогда они указывают
    на неправильную цену). Вместо этого скрываем VP-уровни (POC/VAH/VAL)
    которые слишком близко к торговым уровням (SL/TP/Entry/current).
    """
    # Нормализуем к (price, color, label) — отбрасываем ls
    trio = [(t[0], t[1], t[2]) for t in levels]

    # Trade-уровни (приоритетные)
    trade_levels = [t for t in trio
                    if t[1] in (SL_COLOR, TP_COLOR, ENTRY_LONG[0], ENTRY_SHORT[0])]
    # Контекстные уровни: VP + VWAP + Pivots (скрываем при коллизии с trade)
    vp_levels    = [t for t in trio
                    if t[1] in (POC_COLOR, VA_COLOR, VWAP_COLOR, PIVOT_COLOR)]

    important_prices = [t[0] for t in trade_levels] + [last_close]

    # Скрываем VP-уровень, если он ближе 0.4% к important price
    def _too_close(price):
        for ip in important_prices:
            denom = max(abs(ip), 1e-6)
            if abs(price - ip) / denom < 0.004:
                return True
        return False

    visible = trade_levels + [t for t in vp_levels if not _too_close(t[0])]
    visible.append((last_close, last_color,
                    f"● {_fmt_price(last_close)}"))

    for price, color, label in visible:
        ax.annotate(
            label,
            xy=(1.0, price),
            xytext=(8, 0),
            xycoords=("axes fraction", "data"),
            textcoords="offset points",
            color="white", fontsize=8, fontweight="bold",
            va="center", ha="left",
            bbox=dict(boxstyle="round,pad=0.25",
                      facecolor=color, edgecolor="none", alpha=0.95),
            clip_on=False, zorder=10,
        )


# ─── Helpers ─────────────────────────────────────────────────────────────

def _ema(values: np.ndarray, span: int) -> np.ndarray:
    if len(values) == 0:
        return values
    alpha = 2.0 / (span + 1)
    out = np.empty_like(values)
    out[0] = values[0]
    for i in range(1, len(values)):
        out[i] = alpha * values[i] + (1 - alpha) * out[i - 1]
    return out


def _compute_cvd_series(opens, closes, volumes) -> np.ndarray:
    sign  = np.where(closes >= opens, 1.0, -1.0)
    delta = sign * volumes
    return np.cumsum(delta)


def _tf_label(tf_minutes: int) -> str:
    return {15: "15m", 30: "30m", 60: "1H", 120: "2H", 240: "4H",
            720: "12H", 1440: "1D"}.get(tf_minutes, f"{tf_minutes}m")


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
