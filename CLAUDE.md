# CLAUDE.md — инструкции для Claude в этом репозитории

## Архитектура проекта

Crypto-screener: Flask webhook принимает TradingView-алерты, прогоняет через
deterministic decision engine + LLM analysis, шлёт сигналы в Telegram.

**Стек:** Python 3.11, Flask, SQLite (signals.db), anthropic SDK,
matplotlib (PNG-чарты), pytest+ruff (CI).

**Ключевые модули:**
- `screener.py` — главный сервис (legacy 4k-line, webhook + commands + scheduler)
- `decision.py` — детерминированный verdict engine (LONG/SHORT/WAIT/SKIP)
- `signal_gate.py` — aggregator 30s окно + cooldown по TF (анти-дубль)
- `tracking.py` — TP/SL outcome tracking, R-multiple, /stats
- `chart.py` — PNG-рендеринг свечного графика с зонами
- `llm_agents.py` — LLM объяснитель verdict, multi-agent debate
- `tests/` — 136 pytest тестов

## CI/CD поток

```
push в branch → CI (pytest + ruff) → PR → auto-merge при 🟢 → main → Railway deploy
```

CI: `.github/workflows/ci.yml` — pytest + ruff (с `--ignore E701,E702` для
screener.py, legacy-стиль one-line ifs).

Railway деплоит при push в main автоматически.

## Правила для Claude

### Ветки и PR
- Develop **всегда** на ветке `claude/<feature-name>` — никогда не push в main
- После пуша **всегда** создавать PR (как draft, если нужен review; ready —
  если safe)
- Squash-merge как метод по умолчанию

### Auto-merge

**Включай auto-merge сам через `enable_pr_auto_merge`** для:
- ✅ Новые модули с тестами (изолированные, не трогают prod pipeline)
- ✅ Чисто рефакторинг + тесты не сломались
- ✅ Bugfix в покрытой тестами области
- ✅ Документация (README, CLAUDE.md, docstrings)
- ✅ Lint fixes, ruff-issue resolutions
- ✅ Tests-only PRs

**НЕ включай auto-merge, оставляй draft и спрашивай пользователя** для:
- 🛑 Изменения схемы БД (новые таблицы, ALTER, миграции)
- 🛑 Правки `decision.py` (verdict logic влияет на торговые решения)
- 🛑 Изменения `webhook()` / `_process_winner()` (точка входа сигналов)
- 🛑 Изменения в `signal_gate` cooldown rules / REVERSAL_CONF_DELTA
- 🛑 Изменения config (env vars, MIN_QUALITY, EXPIRY_HOURS)
- 🛑 Зависимости (requirements.txt, новые пакеты)
- 🛑 CI workflow (`.github/workflows/`) — может сломать сам себя
- 🛑 Любая правка > 500 строк или > 5 файлов одновременно

### Тесты обязательны
- Новый код = новые тесты в `tests/`
- Перед коммитом: `pytest tests/` (должно быть 0 fail)
- `ruff check signal_gate.py tests/test_signal_gate.py` (на новых файлах
  должно быть clean; screener.py — legacy, его не трогаем сверх изменения)

### Commit messages
- На русском, формат: `Этап N: краткое описание` или `<scope>: что сделано`
- Тело: 1-2 абзаца почему, не что
- Окончание: `https://claude.ai/code/session_<id>` (автогенерация)

### Git safety
- Никогда не push --force в main
- Никогда не --amend опубликованные коммиты
- Никогда не --no-verify (если pre-commit hook упал — фиксим причину)
- Никогда не trogать `.env` / `config.py` секреты в коммитах

## Команды разработчика

```bash
# Запустить тесты
pytest tests/                          # все
pytest tests/test_signal_gate.py -v    # один файл

# Линтинг
ruff check signal_gate.py tests/       # новые модули должны быть clean
ruff check --ignore E701,E702 screener.py  # legacy

# Локальный webhook (требует config.py с TG-токенами)
python screener.py
```

## Известные ограничения

- `screener.py` — 4k-строчный legacy-файл. Не рефакторим без явного запроса.
  Игнорируем E701/E702 ruff issues — это исторический стиль.
- `webhook_server.py` — устаревший stub, реальный webhook в `screener.py`.
- Тесты бирж не пишем — Binance/Bybit API нестабильны в CI.
- SQLite используется in-memory только в тестах; prod на файле `signals.db`.
- Railway in-memory state теряется при рестарте (aggregator buffer 30s — OK).
