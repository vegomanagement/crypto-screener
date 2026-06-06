"""
bt_equity_chart.py — рендер equity curve как PNG для бектест-результатов.

Используется в /btdiag для визуального отображения накопленного R после
текстовой статистики. Визуальная картинка equity моментально показывает
«растёт стратегия или падает», что текстовый summary не передаёт.

API:
  render_equity_curve(equity, symbol, days, stats=None) → bytes (PNG)

Зависит только от matplotlib + numpy (уже в requirements.txt).
"""

from __future__ import annotations

import io

import matplotlib

matplotlib.use("Agg")  # headless — ДО pyplot

import matplotlib.pyplot as plt  # noqa: E402

__all__ = ["render_equity_curve", "render_multi_equity_curves"]

BG_COLOR    = "#0d1117"
PANEL_COLOR = "#161b22"
GRID_COLOR  = "#21262d"
TEXT_COLOR  = "#c9d1d9"
LINE_COLOR  = "#3fb950"   # GitHub-зелёный
LOSS_COLOR  = "#f85149"   # GitHub-красный
ZERO_COLOR  = "#8b949e"

# Палитра для multi-curve (5 цветов хватает на /scanbt и compare)
MULTI_PALETTE = [
    "#3fb950",   # green
    "#58a6ff",   # blue
    "#d29922",   # orange
    "#bc8cff",   # purple
    "#f85149",   # red
    "#39c5cf",   # cyan
    "#e9a8ff",   # pink
]


def render_equity_curve(
    equity: list,
    symbol: str,
    days: int,
    stats: dict | None = None,
) -> bytes:
    """
    Рендерит equity curve в PNG.

    equity — список cumulative R values (как в BacktestResult.stats['equity'])
    symbol, days — для заголовка
    stats — опциональный dict для footer-line (WR, PF, итоговый R)

    Возвращает PNG как bytes. Пустой equity → возвращает PNG с заглушкой
    «no trades» (не raise).
    """
    fig, ax = plt.subplots(figsize=(10, 5), facecolor=BG_COLOR)
    ax.set_facecolor(PANEL_COLOR)

    if not equity:
        # Заглушка для пустого результата
        ax.text(0.5, 0.5, "No closed trades",
                transform=ax.transAxes,
                ha="center", va="center",
                color=TEXT_COLOR, fontsize=14)
        ax.set_xticks([])
        ax.set_yticks([])
    else:
        # 0 в начале для baseline (до первой сделки equity=0)
        full_equity = [0.0] + list(equity)
        xs = list(range(len(full_equity)))

        # Сегментируем линию по цвету: зелёный когда equity > 0, красный когда < 0
        # Простой подход: рисуем основную линию + закрашиваем под кривой
        final = full_equity[-1]
        line_color = LINE_COLOR if final >= 0 else LOSS_COLOR

        ax.plot(xs, full_equity, color=line_color, linewidth=2.0)
        ax.fill_between(xs, 0, full_equity,
                        where=[y >= 0 for y in full_equity],
                        color=LINE_COLOR, alpha=0.2, interpolate=True)
        ax.fill_between(xs, 0, full_equity,
                        where=[y < 0 for y in full_equity],
                        color=LOSS_COLOR, alpha=0.2, interpolate=True)

        # Zero-line
        ax.axhline(0, color=ZERO_COLOR, linewidth=0.8, linestyle="--",
                   alpha=0.6)

        # Аннотация на конечной точке
        ax.annotate(
            f"{final:+.2f}R",
            xy=(xs[-1], final),
            xytext=(8, 0),
            textcoords="offset points",
            color=line_color, fontsize=11, fontweight="bold",
            va="center",
        )

        ax.set_xlim(0, len(xs) - 1 + 5)

    # Заголовок
    title = f"Equity curve · {symbol} · {days}d"
    if stats:
        wr = stats.get("win_rate")
        n = stats.get("closed", 0)
        risk = stats.get("risk") or {}
        pf = risk.get("profit_factor")
        title += f"  ·  n={n}"
        if wr is not None:
            title += f" · WR={wr}%"
        if pf is not None:
            title += f" · PF={pf}"

    ax.set_title(title, color=TEXT_COLOR, fontsize=12, pad=12)
    ax.set_xlabel("Trade #", color=TEXT_COLOR, fontsize=10)
    ax.set_ylabel("Cumulative R", color=TEXT_COLOR, fontsize=10)

    ax.tick_params(colors=TEXT_COLOR)
    ax.grid(True, color=GRID_COLOR, linestyle="-", linewidth=0.5, alpha=0.6)
    for spine in ax.spines.values():
        spine.set_color(GRID_COLOR)

    fig.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=100, facecolor=BG_COLOR,
                bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf.read()


def render_multi_equity_curves(
    curves: list,
    symbol: str,
    days: int,
) -> bytes:
    """
    Рендерит несколько equity curves на одном графике для сравнения.

    curves — список (label: str, equity: list[float], stats: dict | None).
    stats опциональны, если есть — используется для подписи в легенде
    (например «no_p3 · +25.5R»).

    Возвращает PNG как bytes. Пустой curves → PNG-заглушка.
    """
    fig, ax = plt.subplots(figsize=(11, 5.5), facecolor=BG_COLOR)
    ax.set_facecolor(PANEL_COLOR)

    if not curves:
        ax.text(0.5, 0.5, "No data to compare",
                transform=ax.transAxes,
                ha="center", va="center",
                color=TEXT_COLOR, fontsize=14)
        ax.set_xticks([])
        ax.set_yticks([])
    else:
        max_len = 0
        for i, (label, equity, _stats) in enumerate(curves):
            if not equity:
                continue
            full_equity = [0.0] + list(equity)
            xs = list(range(len(full_equity)))
            max_len = max(max_len, len(xs))
            color = MULTI_PALETTE[i % len(MULTI_PALETTE)]
            final_r = equity[-1]
            ax.plot(
                xs, full_equity,
                color=color, linewidth=1.8,
                label=f"{label} · {final_r:+.2f}R (n={len(equity)})",
            )

        # Zero-line
        ax.axhline(0, color=ZERO_COLOR, linewidth=0.8, linestyle="--",
                   alpha=0.6)

        if max_len > 0:
            ax.set_xlim(0, max_len + 2)

        # Легенда
        legend = ax.legend(
            loc="best", facecolor=PANEL_COLOR,
            edgecolor=GRID_COLOR, fontsize=9,
        )
        if legend:
            for text in legend.get_texts():
                text.set_color(TEXT_COLOR)

    title = f"Equity curves comparison · {symbol} · {days}d"
    ax.set_title(title, color=TEXT_COLOR, fontsize=12, pad=12)
    ax.set_xlabel("Trade #", color=TEXT_COLOR, fontsize=10)
    ax.set_ylabel("Cumulative R", color=TEXT_COLOR, fontsize=10)

    ax.tick_params(colors=TEXT_COLOR)
    ax.grid(True, color=GRID_COLOR, linestyle="-", linewidth=0.5, alpha=0.6)
    for spine in ax.spines.values():
        spine.set_color(GRID_COLOR)

    fig.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=100, facecolor=BG_COLOR,
                bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf.read()
