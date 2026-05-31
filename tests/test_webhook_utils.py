"""Тесты webhook_utils.parse_alert_ts — TS из webhook payload."""

from datetime import datetime, timezone

from webhook_utils import parse_alert_ts


def _expected_utc(year, month, day, hour=0, minute=0, second=0):
    return datetime(year, month, day, hour, minute, second, tzinfo=timezone.utc)


# ─── Отсутствие ts ─────────────────────────────────────────────────────────

def test_no_payload_returns_none():
    assert parse_alert_ts(None) is None
    assert parse_alert_ts({}) is None


def test_no_recognized_key_returns_none():
    assert parse_alert_ts({"foo": "bar", "price": 42500}) is None


def test_none_value_returns_none():
    assert parse_alert_ts({"ts": None, "time": None}) is None


# ─── ISO 8601 ──────────────────────────────────────────────────────────────

def test_iso_with_z_suffix():
    ts = parse_alert_ts({"time": "2026-05-31T03:21:20Z"})
    assert ts == _expected_utc(2026, 5, 31, 3, 21, 20)


def test_iso_with_offset():
    # 03:21 EDT (UTC-4) → 07:21 UTC
    ts = parse_alert_ts({"time": "2026-05-31T03:21:20-04:00"})
    assert ts == _expected_utc(2026, 5, 31, 7, 21, 20)


def test_iso_naive_treated_as_utc():
    ts = parse_alert_ts({"time": "2026-05-31T03:21:20"})
    assert ts == _expected_utc(2026, 5, 31, 3, 21, 20)


def test_iso_malformed_returns_none():
    assert parse_alert_ts({"time": "yesterday"}) is None
    assert parse_alert_ts({"time": "2026-13-45"}) is None


# ─── Unix timestamps ───────────────────────────────────────────────────────

def test_unix_seconds_int():
    # 2026-05-31 03:21:20 UTC ≈ 1779413280
    ts = parse_alert_ts({"ts": 1779413280})
    assert ts is not None
    assert ts.year == 2026
    assert ts.month == 5
    assert ts.tzinfo == timezone.utc


def test_unix_milliseconds_int():
    ts = parse_alert_ts({"ts": 1779413280000})
    assert ts is not None
    assert ts.year == 2026
    assert ts.month == 5


def test_unix_seconds_as_string():
    ts = parse_alert_ts({"timestamp": "1779413280"})
    assert ts is not None
    assert ts.year == 2026


def test_unix_zero_returns_none():
    assert parse_alert_ts({"ts": 0}) is None


# ─── datetime прямо ────────────────────────────────────────────────────────

def test_datetime_utc_passthrough():
    expected = _expected_utc(2026, 5, 31, 8, 30)
    assert parse_alert_ts({"time": expected}) == expected


def test_datetime_other_tz_converted():
    est = timezone(__import__("datetime").timedelta(hours=-5))
    d = datetime(2026, 5, 31, 3, 30, tzinfo=est)  # 08:30 UTC
    assert parse_alert_ts({"time": d}) == _expected_utc(2026, 5, 31, 8, 30)


def test_naive_datetime_treated_as_utc():
    naive = datetime(2026, 5, 31, 8, 30)
    assert parse_alert_ts({"ts": naive}) == _expected_utc(2026, 5, 31, 8, 30)


# ─── Ключи и приоритет ─────────────────────────────────────────────────────

def test_uses_first_recognized_key_in_order():
    """`ts` имеет приоритет над `time`, etc."""
    ts = parse_alert_ts({
        "time": "2099-01-01T00:00:00Z",
        "ts":   "2026-05-31T03:21:20Z",
    })
    assert ts == _expected_utc(2026, 5, 31, 3, 21, 20)


def test_alert_time_camelcase_key():
    ts = parse_alert_ts({"alertTime": "2026-05-31T03:21:20Z"})
    assert ts == _expected_utc(2026, 5, 31, 3, 21, 20)


# ─── Bool не интерпретируется как unix ─────────────────────────────────────

def test_bool_value_not_interpreted_as_int():
    # True/False — int subclass в Python, но мы не должны считать его за время
    assert parse_alert_ts({"ts": True}) is None
    assert parse_alert_ts({"ts": False}) is None
