"""Wyckoff detection on synthetic schematics (accumulation, distribution, phases A–E, trends)."""
import pytest

import wyckoff
from tests.wyckoff_fixtures import accumulation, mirror, trend


def test_full_accumulation_reaches_phase_e_with_events_in_order():
    rows, marks = accumulation()
    out = wyckoff.analyze(rows)
    assert out["status"] == "structure" and out["schematic"] == "accumulation"
    assert out["phase"] == "E"
    seq = [e["event"] for e in out["events"]]
    assert seq == ["SC", "AR", "ST", "Spring", "Test", "SOS", "LPS", "Markup"]
    by = {e["event"]: e for e in out["events"]}
    step = rows[1]["ts"] - rows[0]["ts"]
    for code in ("SC", "AR", "ST", "Spring", "SOS"):
        assert by[code]["ts"] == rows[marks[code]]["ts"], code
    assert out["tr"]["support"] == 74.0 and abs(out["tr"]["resistance"] - rows[marks["AR"]]["h"]) < 1e-6
    assert out["creek"] == out["tr"]["resistance"] and out["ice"] is None
    for e in out["events"]:
        assert e["rule"] and 0 <= e["confidence"] <= 1 and e["known_at"] >= e["ts"] + step
        assert "{" not in e["rule"]
    assert out["confirm"] and out["invalidate"] and out["confidence_label"] in ("high", "medium", "low")


@pytest.mark.parametrize("cut, phase, last", [
    ("SC", "A", "SC"), ("ST", "B", "ST"), ("Spring", "C", "Spring"), ("SOS", "D", "SOS"),
    ("E", "E", "Markup")])
def test_phases_a_to_e_as_the_schematic_unfolds(cut, phase, last):
    rows, marks = accumulation()
    k = marks[cut] + (12 if cut == "SC" else 1)
    out = wyckoff.analyze(rows[:k])
    assert out["status"] == "structure" and out["phase"] == phase
    assert out["events"][-1]["event"] == last or cut == "SC"
    assert all(e["known_at"] <= rows[k - 1]["close_ts"] for e in out["events"])


def test_distribution_is_the_mirror_with_symmetric_labels():
    rows, _ = accumulation(with_delta=True)
    out = wyckoff.analyze(mirror(rows))
    assert out["schematic"] == "distribution" and out["phase"] == "E"
    assert [e["event"] for e in out["events"]] == \
        ["BC", "AR", "ST", "UTAD", "Test", "SOW", "LPSY", "Markdown"]
    assert out["ice"] == out["tr"]["support"] and out["creek"] is None
    bc = out["events"][0]
    assert bc["price"] == 200 - 74.0 and bc["delta_ratio"] > 0 and "highest high" in bc["rule"]
    assert "advance" in bc["rule"] and out["delta_used"] is True


@pytest.mark.parametrize("up", [True, False])
def test_clean_trend_is_unclear(up):
    out = wyckoff.analyze(trend(up=up))
    assert out["status"] == "unclear" and out["phase"] is None and out["events"] == []
    assert "trending or no clear structure" in out["reason"]


def test_breakdown_after_the_range_is_reported_unclear_not_forced():
    rows, marks = accumulation()
    rows = rows[:marks["ST"] + 10]
    last = rows[-1]["c"]
    from tests.wyckoff_fixtures import _bar
    for k in range(8):  # collapse far through support
        c = last - 2.0 * (k + 1)
        rows.append(_bar(len(rows), c + 2.0, c, 150))
    out = wyckoff.analyze(rows)
    assert out["status"] == "unclear" and "invalidated" in out["reason"]
    assert out["confirm"] is None


def test_short_input_is_unclear_with_reason():
    out = wyckoff.analyze(trend(n=30))
    assert out["status"] == "unclear" and "needs" in out["reason"]
