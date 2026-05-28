"""Тесты killzones.py — детект ICT killzone-окон (Этап 10, фаза 1)."""

from datetime import datetime, time, timedelta, timezone

import pytest

from killzones import (
    DEFAULT_KILLZONES,
    KillZone,
    active_killzone,
    describe,
    in_killzone,
    next_killzone,
)

EST = timezone(timedelta(hours=-5))


def utc(h, m=0):
    return datetime(2026, 5, 28, h, m, tzinfo=timezone.utc)


# ─── active_killzone: попадание в каждое окно ──────────────────────────────

@pytest.mark.parametrize(
    "h,m,expected",
    [
        (1, 0, "Asia"),
        (8, 30, "London"),
        (13, 0, "New York AM"),
        (15, 30, "London Close"),
    ],
)
def test_active_inside_each_zone(h, m, expected):
    z = active_killzone(utc(h, m))
    assert z is not None
    assert z.name == expected


@pytest.mark.parametrize("h,m", [(4, 0), (6, 0), (11, 0), (17, 0), (22, 0)])
def test_inactive_between_zones(h, m):
    assert active_killzone(utc(h, m)) is None
    assert in_killzone(utc(h, m)) is False


# ─── границы: start включительно, end исключительно ────────────────────────

def test_boundary_start_inclusive():
    z = active_killzone(utc(7, 0))  # ровно London open
    assert z is not None and z.name == "London"


def test_boundary_end_exclusive():
    # 10:00 — конец London, не входит (и не начало другого окна)
    assert active_killzone(utc(10, 0)) is None


def test_adjacent_zones_handoff():
    # 15:00 — конец NY AM (исключ.) и старт London Close (включ.)
    z = active_killzone(utc(15, 0))
    assert z is not None and z.name == "London Close"


# ─── часовые пояса ─────────────────────────────────────────────────────────

def test_naive_treated_as_utc():
    naive = datetime(2026, 5, 28, 8, 30)  # без tzinfo
    z = active_killzone(naive)
    assert z is not None and z.name == "London"


def test_aware_non_utc_converted():
    # 03:30 EST == 08:30 UTC → London killzone
    est_dt = datetime(2026, 5, 28, 3, 30, tzinfo=EST)
    z = active_killzone(est_dt)
    assert z is not None and z.name == "London"


# ─── midnight-crossing окно ────────────────────────────────────────────────

def test_wraps_midnight_contains():
    night = KillZone("Night", time(23, 0), time(1, 0))
    assert night.wraps_midnight is True
    assert night.contains(time(23, 30)) is True
    assert night.contains(time(0, 30)) is True
    assert night.contains(time(2, 0)) is False


def test_wraps_midnight_active():
    zones = [KillZone("Night", time(23, 0), time(1, 0))]
    assert active_killzone(utc(23, 30), zones) is not None
    assert active_killzone(utc(0, 30), zones) is not None
    assert active_killzone(utc(5, 0), zones) is None


# ─── next_killzone ─────────────────────────────────────────────────────────

def test_next_killzone_upcoming():
    # 06:00 UTC → ближайшее окно London (07:00), через 1 час
    nz, secs = next_killzone(utc(6, 0))
    assert nz.name == "London"
    assert secs == 3600


def test_next_killzone_wraps_to_next_day():
    # 17:00 UTC — после всех окон, ближайшее Asia (00:00 next day)
    nz, secs = next_killzone(utc(17, 0))
    assert nz.name == "Asia"
    assert secs == 7 * 3600  # 17:00 → 00:00 = 7 часов


def test_next_killzone_when_inside_returns_future_start():
    # 08:00 внутри London (07-10) — next должен дать будущий старт, secs > 0
    _, secs = next_killzone(utc(8, 0))
    assert secs > 0


# ─── describe ──────────────────────────────────────────────────────────────

def test_describe_inside():
    s = describe(utc(8, 30))
    assert "London" in s and "killzone" in s


def test_describe_outside():
    s = describe(utc(6, 0))
    assert "вне killzone" in s
    assert "London" in s


# ─── контракт дефолтных окон ───────────────────────────────────────────────

def test_default_zones_non_overlapping_starts():
    starts = [z.start for z in DEFAULT_KILLZONES]
    assert len(starts) == len(set(starts))
