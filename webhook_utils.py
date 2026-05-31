"""
webhook_utils.py — утилиты для парсинга TradingView webhook payload.

Изолированный модуль для логики, которую нужно тестировать (screener.py
тяжело импортировать в тестах — зависит от config.py с секретами).
"""

from __future__ import annotations

from datetime import datetime, timezone

__all__ = ["parse_alert_ts"]

# Ключи, в которых TradingView/пользователи передают время сигнала.
_TS_KEYS = ("ts", "time", "timestamp", "alert_time", "alertTime")


def _from_unix(n: float) -> datetime | None:
    """Распознать unix seconds (~10 цифр) или milliseconds (~13 цифр)."""
    if n <= 0:
        return None
    # 10**12 ≈ Sep 2001 в секундах; всё что выше — миллисекунды
    seconds = n / 1000.0 if n >= 1e12 else float(n)
    try:
        return datetime.fromtimestamp(seconds, tz=timezone.utc)
    except (OSError, OverflowError, ValueError):
        return None


def _from_iso(s: str) -> datetime | None:
    """ISO 8601 с поддержкой 'Z' (Python <3.11)."""
    s = s.strip()
    if not s:
        return None
    # fromisoformat не принимает 'Z' в Python <3.11; нормализуем
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        # Naive → считаем UTC (стандарт для TV алертов)
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def parse_alert_ts(payload: dict | None) -> datetime | None:
    """
    Достаёт timestamp алерта из webhook payload, поддерживая разные форматы:
      • datetime (any tz) → конвертация в UTC
      • int/float — unix seconds или milliseconds (авто-определение)
      • str — ISO 8601, число unix seconds/ms в строке

    Ищет в payload ключи: ts / time / timestamp / alert_time / alertTime.
    Возвращает aware-UTC datetime или None, если нет/не распарсилось.

    Используется в _process_winner: market['ts'] = parse_alert_ts(payload)
    → killzones-гейт работает по времени АЛЕРТА, а не now().
    """
    if not payload:
        return None
    for key in _TS_KEYS:
        if key not in payload:
            continue
        v = payload[key]
        if v is None:
            continue
        if isinstance(v, datetime):
            return v.astimezone(timezone.utc) if v.tzinfo else \
                   v.replace(tzinfo=timezone.utc)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            dt = _from_unix(v)
            if dt is not None:
                return dt
            continue
        if isinstance(v, str):
            # Сначала пробуем как число (TV иногда шлёт unix в строке)
            stripped = v.strip()
            try:
                dt = _from_unix(float(stripped))
                if dt is not None:
                    return dt
            except (ValueError, TypeError):
                pass
            # Иначе ISO 8601
            dt = _from_iso(stripped)
            if dt is not None:
                return dt
    return None
