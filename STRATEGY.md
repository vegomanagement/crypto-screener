# STRATEGY.md — путь к рабочей crypto-стратегии

Документ описывает что было сделано, что найдено, и как двигаться дальше.
Обновляется по мере новых экспериментов.

---

## 📊 Текущее состояние (по последнему hyperopt анализу)

**BTC 60d 5m с DEFAULT_SEARCH_SPACE (PR #42):**
- In-sample best PF: **1.21** (52 trades, WR 21.2%, avgR +0.16)
- **Out-of-sample mean PF: 0.62** ← стратегия теряет деньги на новых данных
- `avg_r_net = -0.98` (после комиссии 0.06% taker × 2)

**Прод-стратегия (без тюнинга) — /stats 30 показал:**
- WinR 18.4%
- AvgR -0.41
- PF 0.42

**Вывод:** стратегия в текущем виде НЕ имеет устойчивого edge'а.

---

## 🔍 Что найдено в экспериментах

### Killzone gate РАБОТАЕТ — НЕ убирать
Hyperopt-анализ нашёл что лучшие конфиги (top-3 trials) все имели `KILLZONE_GATE_ENABLED=True`. Без killzone gate было 800-1100 трейдов с WR 17-18% и MaxDD **-129R** (катастрофа). С killzone gate — 52 трейда с WR 21%, MaxDD -23R.

Это противоречит интуиции «выкидываем 50% сигналов = плохо», но математика подтверждает: killzone фильтрует к лучшему качеству.

### HTF gate НЕ работает
Optuna сходится к `HTF_BIAS_GATE_ENABLED=False`. P4 HTF gate блокирует мало сигналов когда P3 (killzone+structure) уже работает — большинство сигналов умирают раньше. P4 имеет смысл только если P3 ослаблен, но тогда WR хуже.

### Edge существует в подмножестве — нужно найти
Best_R = +10.95R в одном из trials. Значит есть сетапы которые дают 10R+ прибыли. Задача — выделить их и фильтровать к ним.

### Структура воронки (на BTC 30d)
```
474 detected
  ↓
13 passed WAIT (97% killed)
  ↓
2 passed SKIP (99.6% killed total)
  ↓
2 trades, обе SL
```

Топ-причины WAIT:
1. **236× (50%)** Вне ICT killzone — это правильно
2. **101× (21%)** Нет слома структуры 5m+15m — спорно
3. **42× (9%)** Слом структуры обратный — правильно
4. **24× (5%)** HTF strong bias contra — спорно

### Комиссии съедают edge
Best in-sample avgR = +0.16, но **avg_r_net = -0.98**. Round-trip комиссия 0.12% против R-unit ~0.1% от цены = **fee_r ≈ 1.0**. На текущем размере R (узкий SL) комиссия в R-эквиваленте астрономическая.

**Решение:** либо больше TPs, либо больше R-unit (шире SL), либо обе.

---

## 🛠 Доступные команды (после всех PR'ов)

### `/btdiag SYMBOL DAYS [tf=N] [preset=NAME] [KEY=VAL ...]`
Диагностический backtest с funnel + breakdown.

```
/btdiag                                              — BTC 30d default
/btdiag ETH 60                                       — другая монета
/btdiag BTC 30 tf=15                                 — на 15m
/btdiag BTC 30 preset=no_p3                          — без P3 gates
/btdiag BTC 30 preset=wide_tp                        — TP 3/5/8 ATR
/btdiag BTC 30 KILLZONE_GATE_ENABLED=false           — конкретный override
/btdiag BTC 60 tf=1H preset=no_gates                 — комбо
```

### `/hyperopt SYMBOL DAYS TRIALS [tf=N] [walkforward] [metric=X] [preset=NAME] [KEY=VAL ...]`
Optuna-тюнинг параметров.

```
/hyperopt                                            — BTC 60d, 30 trials, PF
/hyperopt BTC 60 30 walkforward                      — с OOS валидацией (важно!)
/hyperopt ETH 90 50 metric=sharpe_r                  — оптимизировать Sharpe
/hyperopt BTC 60 30 preset=no_p4                     — fix HTF=False, искать остальное
/hyperopt BTC 60 30 tf=1H walkforward                — 1h primary + OOS
```

### `/scanbt SYM1,SYM2,SYM3 DAYS [tf=N] [sort=COL] [preset=NAME] [KEY=VAL ...]`
Multi-symbol backtest comparison.

```
/scanbt                                              — BTC,ETH,SOL 30d default
/scanbt BTC,ETH,SOL,ARB,DOGE 30                      — 5 монет
/scanbt BTC,ETH 30 sort=avg_r_net                    — сортировка по чистому R
/scanbt BTC,ETH,SOL 30 preset=no_p3 sort=max_dd      — где DD меньше
/scanbt BTC,ETH 60 tf=1H preset=wide_tp              — 1h + wide TPs
```

### `/backtest SYMBOL DAYS [compare] [tf=N]`
Существующая команда — baseline + 4 config comparison.

---

## 🎯 Доступные presets

| Preset | Что включает |
|---|---|
| `no_p3` | KILLZONE=False, STRUCTURE=False |
| `no_p4` | HTF=False |
| `no_gates` | все три gates OFF |
| `wide_tp` | TP1/2/3 = 3/5/8 ATR |
| `narrow_tp` | TP1/2/3 = 1.0/1.8/3.0 ATR |
| `tight_sl` | SL_DIST=0.7 |
| `loose_sl` | SL_DIST=1.5 |
| `aggressive` | MIN_CONFIDENCE=55 + wide_tp |
| `conservative` | MIN_CONFIDENCE=72 + gates ON |

---

## 🧪 Рекомендуемые эксперименты (по приоритету)

### #1 (CRITICAL) Wider TPs на BTC
Гипотеза: текущий TP1=1.5×ATR закрывает прибыль слишком рано. С WR 19% нам нужен avgWin > 4.26R для positive expectancy.

```
/hyperopt BTC 60 50 walkforward preset=wide_tp
```

Это найдёт лучшие комбинации с wide TPs. Сравнить mean OOS с baseline PF 0.62.

**Если mean OOS > 1.0** — найден edge через геометрию entry/exit.

### #2 Multi-symbol edge discovery
Гипотеза: BTC может не подходить для текущего setup'а. Другие монеты могут иметь edge.

```
/scanbt BTC,ETH,SOL,ARB,DOGE,LINK,AVAX,MATIC 30 sort=avg_r_net
/scanbt BTC,ETH,SOL,ARB,DOGE,LINK,AVAX,MATIC 30 preset=no_p3 sort=avg_r_net
```

Ищем монету с **positive avg_r_net** в обоих режимах. Эта монета имеет edge независимо от gates.

### #3 Higher TF — меньше шума
Гипотеза: на 1h/4h меньше шума → больше edge'а.

```
/btdiag BTC 60 tf=1H preset=no_gates    — увидеть структуру сигналов на 1h
/hyperopt BTC 90 30 tf=1H walkforward   — найти best params на 1h
/scanbt BTC,ETH,SOL 60 tf=1H            — сравнить монеты на 1h
```

### #4 Per-signal-type cleanup
Гипотеза: некоторые типы сигналов системно убыточны. Их нужно выключить.

```
/btdiag BTC 90 preset=no_gates    — получим 1000+ trades, /pre>
```

В выходе будет секция «Expectancy by signal type» с verdict (✅/❌). Если FVG_BULL имеет ❌ verdict при n>30 — отключить через override типа FVG детектора (требует PR в `backtest._detect_signals_minimal`).

### #5 Optimize for net (after fees)
Гипотеза: текущий hyperopt оптимизирует PF gross, но avg_r_net остаётся отрицательным.

```
/hyperopt BTC 60 50 walkforward metric=avg_r_net
```

Метрика напрямую учитывает комиссии — будет искать конфиг с positive net.

---

## 🚧 Не делать (anti-patterns)

### НЕ убирать killzone gate напрямую
Hyperopt подтвердил: killzone оптимален. Если убрать — MaxDD взрывается до -130R. Лучшие комбинации сохраняют его включённым.

### НЕ доверять in-sample PF без walk-forward
In-sample PF 1.21 → OOS PF 0.62. Разница 50%. Любой hyperopt без `walkforward` флага потенциально overfit'ed. Всегда добавлять walkforward для оценки.

### НЕ менять prod-defaults в decision.py без OOS-подтверждения
Параметры в decision.py — это live торговля. Меняем через config_overrides в backtest до тех пор, пока walk-forward не покажет mean OOS > 1.0 (с запасом).

### НЕ запускать compute-heavy hyperopt без min_trades
По умолчанию `min_trades=10` (защита от overfit). Если поставить меньше, Optuna найдёт «победителей» с 1-2 трейдами что бесполезно.

---

## 📈 Метрики которые смотреть

| Метрика | Что значит | Цель |
|---|---|---|
| OOS mean PF | Profit factor on out-of-sample | > 1.5 |
| avg_r_net | Средний R за вычетом комиссии | > +0.20 |
| Winrate | Доля выигрышных сделок | > 35% |
| Sharpe_R | Sharpe ratio в R-units | > 0.5 |
| MaxDD_R | Maximum drawdown в R | > -20 |
| BE WR | Breakeven winrate (in Expectancy line) | мы должны быть выше BE |
| Verdict | ✅/❌/🟡 в `Expectancy:` строке | стремимся к ✅ |

---

## 🛡 Безопасность экспериментов

### Что НЕ ломаем
- `webhook()` — точка входа сигналов из TradingView
- `_process_winner()` — отправка в Telegram после aggregator
- `signal_gate.py` cooldown rules
- Prod-defaults в `decision.py`

### Что можно безопасно экспериментировать
- `config_overrides` в `/btdiag`, `/hyperopt`, `/scanbt` — НЕ влияет на live trading
- Новые модули (например `bt_*`) — изолированы от prod pipeline
- UI-улучшения в `screener.py` (новые команды) — не трогают webhook'и

### Когда делать draft PR в decision.py
После того как walk-forward на 60-90d показал:
- mean OOS PF > 1.3
- avg_r_net > +0.10
- MaxDD > -25R
- На минимум 2-3 символах consistently

И только под двойным review с явным согласием пользователя (decision.py в blacklist auto-merge).

---

## 📚 История изменений

См. git log. Ключевые milestone-PR'ы для документации стратегии:
- #41: backtest diagnostic (funnel + breakdown + комиссии)
- #42: Optuna hyperopt с walk-forward защитой
- #43: Telegram `/btdiag` и `/hyperopt`
- #44/#45: Hyperliquid fallback (geo-block solution)
- #47: TP/SL multipliers в hyperopt search-space
- #48: `/scanbt` multi-symbol
- #49: expectancy summary с breakeven WR
- #50: expectancy by signal_type breakdown
- #51: CONFIG_PRESETS
- #52: `/scanbt sort=` option
- #53: `/btdiag tf=` option
- #54: `/scanbt` и `/hyperopt` тоже принимают `tf=`

---

## 🔮 Возможные будущие направления

Если из текущих experiments не найдётся положительного edge, рассмотреть:

1. **ML-based signal scoring** — обучить classifier на исторических сделках предсказывать SL vs TP
2. **Multi-confluence signals** — требовать одновременной активации 2+ паттернов (FVG + OB)
3. **Volume confirmation** — фильтровать сигналы по volume profile
4. **Different TF for entry** — generate signals на 1h, executr на 5m
5. **Move to Jesse/Freqtrade** — если оказывается что у Jesse эта стратегия работает (нет смысла самим строить framework)
6. **Funding rate filtering** — учитывать perp funding в decision
7. **Switch to mean-reversion** — если ICT/SMC не работает, пробовать VWAP-bounce, support-resistance bounce
