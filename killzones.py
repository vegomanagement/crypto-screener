"""
killzones.py — детект ICT killzone-окон (Этап 10, фаза 1).

ICT killzone — узкое временное окно повышенной институциональной активности,
когда формируется большинство дневных манипуляций ликвидностью. Сигнал вне
killzone — низкоприоритетный (см. фазу 3: hard WAIT вне окна).

Окна заданы в UTC фиксированно (без авто-DST) — для крипты это приемлемо,
рынок 24/7 и ориентир на UTC устойчивее. Окна узкие (2-3ч), а не полные сессии.

Чистый модуль без внешних зависимостей — только stdlib. Все функции
детерминированы и тестируемы: на вход datetime (naive трактуется как UTC,
aware конвертируется в UTC), на выход — активная зона / ближайшая / флаг.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone

__all__ = [
    "KillZone",
    "DEFAULT_KILLZONES",
    "active_killzone",
    "in_killzone",
    "next_killzone",
    "describe",
]


@dataclass(frozen=True)
class KillZone:
    """Killzone-окно [start, end) в UTC. Поддерживает переход через полночь."""

    name: str
    start: time
    end: time
    emoji: str = "🕐"

    @property
    def wraps_midnight(self) -> bool:
        return self.start >= self.end

    def contains(self, t: time) -> bool:
        """t внутри окна? Начало включительно, конец исключительно."""
        if self.wraps_midnight:
            return t >= self.start or t < self.end
        return self.start <= t < self.end

    def label(self) -> str:
        return f"{self.emoji} {self.name} killzone"


# Узкие ICT killzone-окна в UTC (по умолчанию). Калибруются здесь.
DEFAULT_KILLZONES: list[KillZone] = [
    KillZone("Asia", time(0, 0), time(3, 0), "🌏"),
    KillZone("London", time(7, 0), time(10, 0), "🇬🇧"),
    KillZone("New York AM", time(12, 0), time(15, 0), "🗽"),
    KillZone("London Close", time(15, 0), time(16, 0), "🔔"),
]


def _to_utc(ts: datetime | None) -> datetime:
    """Нормализует ts в aware-UTC. None → текущее время. naive → считаем UTC."""
    if ts is None:
        return datetime.now(timezone.utc)
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


def active_killzone(
    ts: datetime | None = None,
    zones: list[KillZone] | None = None,
) -> KillZone | None:
    """Активная killzone для момента ts (или None, если вне окон)."""
    zones = zones if zones is not None else DEFAULT_KILLZONES
    t = _to_utc(ts).timetz().replace(tzinfo=None)
    for z in zones:
        if z.contains(t):
            return z
    return None


def in_killzone(
    ts: datetime | None = None,
    zones: list[KillZone] | None = None,
) -> bool:
    """True, если ts попадает в любую killzone."""
    return active_killzone(ts, zones) is not None


def next_killzone(
    ts: datetime | None = None,
    zones: list[KillZone] | None = None,
) -> tuple[KillZone, int]:
    """
    Ближайшая будущая killzone и секунды до её старта.
    Если ts уже внутри окна — вернёт следующее окно (start в будущем).
    """
    zones = zones if zones is not None else DEFAULT_KILLZONES
    now = _to_utc(ts)
    best: tuple[KillZone, int] | None = None
    for z in zones:
        start_today = now.replace(
            hour=z.start.hour, minute=z.start.minute,
            second=0, microsecond=0,
        )
        start = start_today if start_today > now else start_today + timedelta(days=1)
        delta = int((start - now).total_seconds())
        if best is None or delta < best[1]:
            best = (z, delta)
    assert best is not None  # zones непуст по контракту
    return best


def describe(
    ts: datetime | None = None,
    zones: list[KillZone] | None = None,
) -> str:
    """Короткий человекочитаемый статус killzone для логов/Telegram."""
    z = active_killzone(ts, zones)
    if z is not None:
        return z.label()
    nz, secs = next_killzone(ts, zones)
    mins = secs // 60
    return f"вне killzone (до {nz.name} ~{mins} мин)"
