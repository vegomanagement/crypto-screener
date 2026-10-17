# Ревью интеграции: claude/scalping-phase2..claude/integration (PR #77)

Независимое ревью диффа `claude/scalping-phase2..claude/integration` (c5b3dd8, ~279 файлов,
+40k строк) с исправлением подтверждённых дефектов в ветке `claude/review-fixes`.
Номера строк ниже указаны по c5b3dd8 (до правок). На каждую исправленную находку есть
регрессионный тест, который до правки падал (или живой repro, если модуль не тестируется в node).

Новые тесты: `tests/test_review_integration.py` (Python), `tests/drawings_ui_dispose.test.cjs`,
`tests/analyst_panel_stats_race.test.cjs`, `tests/opens_loader_cache.test.cjs`.
Живые скрипты (headless Chrome, CDP :9568, сервер-лаунчер Flask на :5095, базовая ветка
scalping-phase2 для сравнения — на :5096): `docs/evidence/review-integration/`
(`rv_leak.cjs`, `rv_panes.cjs`, `rv_retainer.cjs`, `rv_funding_race.cjs`, `rv_fuzz.cjs`,
`review-fixes-launch.py`).

## Итог проверок

| Проверка | До | После |
|---|---|---|
| `pytest` (весь набор) | 1411 passed | 1437 passed |
| `node --test tests/*.cjs` | 203 pass, 6 skip | 206 pass, 6 skip (те же 6 браузерных skip) |
| ruff как в `.github/workflows/ci.yml` (`--select E4,E7,E9,F`) | clean | clean |
| ruff на всех новых .py диффа (19 модулей + 13 тестов + ws_client) | clean | clean |
| py_compile из CI | ok | ok |
| 30 смен раскладки, /ui, 4grid в конце: DOM-узлы / JS-слушатели / куча | 16 048 → 28 925 / 3 021 → 3 946 / 5,8 → 28,9 МБ | 16 048 → 16 181 / 3 021 → 3 021 / 6,3 → 10,4 МБ |
| Уничтоженные Pane, удерживаемые в памяти (10 смен раскладки, heap snapshot) | 15 из 15 | 0 |

Таймеры, WebSocket'ы и ResizeObserver в живом прогоне масштабируются ровно с числом панелей
(4 интервала + 1 WS + 1 RO на панель, +4 глобальных интервала, +1 у открытой секции
Positioning) и до, и после — утечек в них нет. ruff 0.17.0 (`.venv` проекта).

## Исправлено

### 1. [Высокая · утечка UI] Панель инструментов рисования держит уничтоженный Pane
`static/drawings/ui.js:120` — `listen(document, 'pointerdown', …, true)` без удаления в
`dispose()`. Замыкание держало `controller` → `getContext` → весь `Pane` (график LWC, свечи,
DOM). Repro: `rv_panes.cjs` — после 10 смен раскладки 19 экземпляров Pane при 4 живых;
retainer-путь из heap snapshot (`rv_retainer.cjs`): `RegisteredEventListener → closure →
controller → getContext → Pane`. 30 смен раскладки: +12,9 тыс. DOM-узлов, +925 слушателей,
+23 МБ кучи. Та же утечка была и в старом `drawings.js` на scalping-phase2 (дельта
идентична), но в диффе код переписан заново и дефект сохранён.
Тест: `tests/drawings_ui_dispose.test.cjs`. Правка: все слушатели хрома через один
`AbortController`, `dispose()` вызывает `abort()`.

### 2. [Средняя · ошибки] Ответ Hermes с не-списком в levels/scenarios/risks → HTTP 500
`analyst_hermes.py:304, 336, 343` — `(raw.get("levels") or [])[:24]` на объекте или числе
давал `KeyError`/`TypeError`, которые не ловятся веткой `ValueError` в `ask_hermes`;
`POST /api/agent/analysis` отвечал 500 без baseline. `analyst_hermes.py:366` — шлюз,
вернувший `content` списком частей, давал `AttributeError` в `parse_reply`.
Тесты: `test_validate_ignores_non_list_sections` (6 случаев),
`test_non_string_gateway_content_falls_back_instead_of_crashing`.
Правка: `_list()` игнорирует не-список с предупреждением; не-строковый ответ → `""` → обычный
повтор → baseline.

### 3. [Средняя · корректность] Неоцениваемые строки навсегда блокируют калибровку
`analyst_store.py:209` + `agent_api.py:39` — строка старше 500 баров (для 1m это ~8 ч)
при каждой оценке давала `ValueError` и оставалась pending; строка без ATR — `None` навсегда.
`evaluate_pending(limit=10)` брал первые 10 «созревших» строк, поэтому 10 таких строк
останавливали оценку всех новых анализов навсегда (и каждое `GET /api/agent/stats` заново
тянуло для них свечи). `analyst_store.py:161` — `ORDER BY ts LIMIT 500`: 500 ещё не
созревших недельных анализов скрывали созревшие.
Тесты: `test_too_old_rows_are_expired_and_newer_rows_still_get_scored`,
`test_rows_without_atr_are_marked_unscorable_without_fetching`,
`test_pending_looks_past_the_first_500_not_yet_due_rows`,
`test_default_fetch_bars_signals_expiry_with_bars_unavailable`.
Правка: `BarsUnavailable` → outcome `expired`, нет цены/ATR → `unscorable` (оба терминальные,
в hit rate не входят, видны в `stats.expired/unscorable`); очередь читается батчами по id.

### 4. [Средняя · корректность] ws_client рассинхронизирует поток при таймауте посреди кадра
`ws_client.py:126` — `decode_frame(self._read_exact)` потреблял заголовок кадра; при
`settimeout(1.0)` таймаут между заголовком и payload терял заголовок, и следующий `recv()`
разбирал байты payload как заголовок (мусорный opcode/длина). Тест:
`test_recv_survives_timeout_between_header_and_payload` (до правки — неверный разбор).
Правка: `_read_frame()` подглядывает в буфер и потребляет кадр только целиком.

### 5. [Средняя · DoS] Сборщик ликвидаций переподключается каждые 2 с
`liquidations_collector.py:137` — `attempt = 0` сразу после `connect()`: сокет, который
сервер закрывает сразу после handshake (лимит по IP, отказ), давал бесконечные
переподключения с задержкой 2 с. Тест: `test_collector_backs_off_when_connections_drop_immediately`
(до правки задержки `[2, 2, 2, …]`) и `test_collector_resets_backoff_after_a_stable_connection`.
Правка: сброс backoff только после соединения, продержавшегося 60 с (`STABLE_CONNECTION_MS`).

### 6. [Средняя · DoS] Leaderboard Hyperliquid (~38 МБ) скачивается каждые 10 с при ошибке
`hl_whale_tracker.py:131` — после любой ошибки опроса `sleep(10)` и новый опрос, который
снова качает leaderboard; `hl_whale_tracker.py:53` — неудачное почасовое обновление
обнуляло опрос, хотя список адресов уже был. Модель с ошибкой загрузки: 60 скачиваний за
10 минут. Тесты: `test_failing_leaderboard_backs_off` (≤ 8 за 10 минут; после правки 6),
`test_stale_leaderboard_is_reused_when_the_hourly_refresh_fails`.
Правка: экспоненциальный backoff 10 с → 10 мин; при упавшем обновлении опрос идёт по
старому списку, `leaderboard.refresh_error` в ответе, повтор скачивания не чаще раза в 10 мин.

### 7. [Средняя · ошибки] Неожиданная форма JSON биржи → HTML-500 вместо 502 / null+reason
`funding_data.py:101-106` — ответ 200 со списком из не-объектов (Bybit `result.list: ["x"]`,
HL `universe: ["BTC"]`) давал `AttributeError` вне `FundingError`: `/api/funding` отвечал
500. `positioning_snapshot.py:43` — `section()` ловил `KeyError/TypeError/IndexError`, но не
`AttributeError`, и снимок позиционирования HL падал целиком вместо null+reason по секции.
Тесты: `test_funding_malformed_venue_payload_is_a_funding_error`,
`test_funding_route_returns_502_on_malformed_payload`,
`test_positioning_snapshot_survives_attribute_errors_in_one_section`.
Правка: такие ошибки → `FundingError("unexpected … response shape")` (502); `AttributeError`
добавлен в перечень ошибок секции.

### 8. [Средняя · ошибки] agent_api отклоняет все POST за TLS-прокси (Railway)
`agent_api.py:99` — `Origin` сравнивался с `request.host_url` вместе со схемой. Приложение
без ProxyFix за прокси Railway (README: `https://…up.railway.app/ui`) видит `http://`, браузер
шлёт `Origin: https://…` → 403 на каждый «Analyse». Тест:
`test_same_origin_check_ignores_proxy_scheme` (https-случай падал). Правка: сравнение хоста
(`netloc`), чужие хосты, `Origin: null` и битый Origin (`http://[::1` — иначе 500 из
`urlsplit`) → 403. Та же схема в `liquidity_api.py:45` существовала до этого PR
(на scalping-phase2) — вне диапазона, не тронута, но имеет ту же проблему.

### 9. [Низкая · корректность] Снимок аналитика ждёт по 25 с на каждый зависший провайдер
`analyst.py:377` — `fut.result(timeout=PROVIDER_TIMEOUT_S)` по очереди: N зависших
провайдеров → до N × 25 с до вызова Hermes (у которого ещё 60 с). Тест:
`test_hanging_providers_share_one_deadline` (6 провайдеров при таймауте 0,3 с ждали 1,87 с).
Правка: один общий дедлайн для всех futures.

### 10. [Низкая · UI] Чип funding пуст до 30 с после смены символа во время запроса
`static/workspace.js:1278` — пока шёл запрос старого рынка (`fundingBusy`), запрос нового
пропускался, а ответ старого отбрасывался по ключу; чип и секция Funding в карточке
аналитика пустовали до следующего тика 30 с. Живой repro `rv_funding_race.cjs` (запросы
`/api/funding` задержаны на 3 с через CDP Fetch): до правки — один запрос, `data=null`,
чип скрыт; после — второй запрос и чип `F 0.0050% → 0.0050% · …` для нового символа.
Правка: в `finally` перезапуск `refreshFunding()`, если ключ сменился.

### 11. [Низкая · UI] Запоздалая статистика калибровки прошлого символа
`static/analyst-panel.js:235` — `loadStats()` записывал ответ без проверки активного ключа;
строка «Calibration» показывала hit rate предыдущего рынка. Тест:
`tests/analyst_panel_stats_race.test.cjs`. Правка: ответ отбрасывается, если
`exchange:symbol:interval` сменился.

### 12. [Низкая · память UI] Кэш старших ТФ Opens/Key levels растёт без предела
`static/indicators/opens.js:263` — `createLoader` хранил D/W/M свечи каждой пары
(биржа, символ, ТФ) навсегда. Живой прогон: 48 записей после 30 смен символа (≈ 55 КБ каждая).
Тест: `tests/opens_loader_cache.test.cjs` (до правки 40 записей на 20 символов). Правка:
истёкшие записи (старше `ttlMs`, их всё равно перезапрашивают) удаляются в `ensure()`.
Повторный живой прогон показывает 39–48 записей на 4–6 панелей — это свежие записи текущих
рынков (TTL 60 с при шаге 2,5 с), а не накопление.

## Не исправлено (с причиной)

- **[Низкая · DoS] `_locks` в `analyst_service.py:110` и `funding_data.py:56` растут на каждый
  новый ключ.** Lock создаётся до rate limit для любого символа, прошедшего regex, включая
  несуществующие. Рост ~200 байт на уникальный символ; кэш результатов при этом ограничен
  (ошибки не кэшируются, успешные ключи ограничены списком рынков). Приложение однопользовательское
  без авторизации; безопасная очистка lock'ов требует подсчёта ссылок — изменение больше,
  чем оправдывает риск. Оставлено как известное ограничение.
- **`liquidity_api.py:45` — та же проверка Origin со схемой** (находка 8): код до этого PR.
- **Работа другого агента (ветка polish)**: Market Sessions/DST, tick sizes, персистентность
  ликвидаций, мёртвый CSS, легенда EMA «warming up» — не трогались по условию задачи.

## Проверено и отклонено (ложные срабатывания)

- **Секреты.** Ключ Hermes (загружен в процесс через `hermes_env`, значение не печаталось)
  искался байтово: `git log --all -p` по всем refs, все файлы worktree, `.data` живого
  checkout и логи в scratch — совпадений нет. `COINGLASS_API_KEY` в истории — только имя
  переменной и тестовый `SECRET`. `CoinGlassClient` шлёт ключ только заголовком, `__repr__`
  без ключа, текст ошибки маскируется. В ответах `/api/agent/*` ключа нет (существующий
  `tests/test_agent_api.py` + живой ответ с недоступным Hermes: только `URLError`).
- **XSS.** `innerHTML` в новом коде: `analyst-panel.js` — все строки из Hermes/бирж через
  `esc()`; неэкранированные вставки — числа (`long_pct` через `Math.round`, `strength` —
  целое 1–3 после `validate`, `price` — `Number.isFinite`), цвета и классы из фиксированных
  таблиц. `positioning-panel.js` и `funding.js` — через `esc()`. В `static/drawings/*` нет
  `innerHTML` вообще: Text/Note/Callout/Table рисуются `fillText` на canvas, панели
  настроек и дерево строятся через `textContent`/`value`.
- **SSRF / инъекции.** exchange — whitelist во всех маршрутах; symbol — `symbol_pattern`
  (без `/`, `?`, `#`, пробелов), period — `PERIODS`, coin — `[A-Za-z0-9]{1,20}`, owner —
  `[A-Za-z0-9:._-]{1,80}`; query собирается через `urlencode`, тела HL — `json.dumps`;
  WS-подписки Bybit берут символ только из `collector.watch()`, а туда он попадает лишь из `/api/positioning` после `valid_query` (`[A-Z0-9]{2,30}`); URL WS фиксированы.
  Живой прогон: `symbol=btc%0A` → 400, `force:"yes"` → 400, `Origin: http://evil.example` → 403.
- **Ограничения ресурсов.** Hermes: глобальный rate limit 6/60 с и один запрос на ключ;
  whale tracker: ≤ 20 lease-владельцев, один поток, лимит по lease; сборщик ликвидаций:
  ≤ 10 символов на биржу, ≤ 40 владельцев, поток завершается, когда lease истекли
  (`wanted()` пуст). `ws_client` ограничивает кадр/сообщение 4 МБ.
- **Миграции localStorage.** Фаззинг `migrateLegacyState` (v4→v5 настроек индикаторов,
  2 744 комбинаций null/числа/строки/массивы/объекты) и `migrateV1`/`parseState`
  рисунков (v1→v2, включая ключ `__proto__`) — ни одного исключения.
- **Время за последним баром.** `geometry.js` `logicalForTime`/`timeForLogical` за последним
  баром шагают на `stepMs` от якоря симметрично — обратимо, без деления на ноль.
- **Funding.** Знак берётся как есть у биржи, годовая ставка — `rate × 24 / interval × 365`
  в Decimal, интервал из `fundingInfo`/ответа биржи, иначе `null` + причина.
- **Ключ кэша анализа** — `(exchange, symbol, interval)`; кэш funding — `(exchange, symbol)`.
- **Таймеры/WS/ResizeObserver UI** — по живому прогону растут и падают строго с числом
  панелей; утечка была только в слушателе `document` (находка 1).

Не проверялось глубоко: математика распределения long/short (`analyst.split`) и окна
изменения OI не пересчитывались вручную — существующие тесты (`test_analyst_split.py`,
`test_positioning.py`) зелёные, расхождений при чтении кода не найдено.
