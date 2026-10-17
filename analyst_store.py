"""SQLite log of every analyst run plus an outcome evaluator for calibration.

Follows the liquidity_store pattern: one file under ``.data/``, created lazily,
``check_same_thread=False`` with a lock. Outcome rule (per row with a long/short
bias): starting from the analysis price, did a later closed bar reach +1 ATR in
the bias direction before -1 ATR against, within ``horizon_bars`` bars?
"""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_analyses (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            INTEGER NOT NULL,
    exchange      TEXT NOT NULL,
    symbol        TEXT NOT NULL,
    interval      TEXT NOT NULL,
    price         REAL,
    atr           REAL,
    bar_close_ts  INTEGER,
    long_pct      INTEGER,
    short_pct     INTEGER,
    bias          TEXT,
    confidence    TEXT,
    source        TEXT NOT NULL,
    status        TEXT NOT NULL,
    levels_json   TEXT,
    model         TEXT,
    latency_ms    INTEGER,
    snapshot_hash TEXT,
    horizon_bars  INTEGER,
    outcome       TEXT,
    outcome_ts    INTEGER
);
CREATE INDEX IF NOT EXISTS agent_analyses_pending ON agent_analyses(outcome, ts);
"""

# Columns added with the factor-scored split; older files are migrated in place.
EXTRA_COLUMNS = (("p_long", "REAL"), ("net", "REAL"), ("factors_json", "TEXT"),
                 ("agent_bias", "TEXT"), ("disagreement", "TEXT"))

OUTCOMES = ("win", "loss", "ambiguous", "timeout")


def evaluate_outcome(bias, entry, atr, bars, horizon_bars):
    """Score one analysis against later closed bars ``[{'h','l'}...]`` (oldest first).

    Returns 'win' / 'loss' / 'ambiguous' (both hit in one bar) / 'timeout' (neither
    within the horizon) / None when not enough bars exist yet or bias is neutral.
    """
    if bias not in ("long", "short") or not atr or atr <= 0 or not entry:
        return None
    up, down = entry + atr, entry - atr
    for bar in bars[:horizon_bars]:
        hit_up, hit_down = bar["h"] >= up, bar["l"] <= down
        if hit_up and hit_down:
            return "ambiguous"
        if hit_up:
            return "win" if bias == "long" else "loss"
        if hit_down:
            return "win" if bias == "short" else "loss"
    return "timeout" if len(bars) >= horizon_bars else None


def summarize(rows):
    """Hit-rate stats from rows of (source, bias, long_pct, short_pct, outcome)."""
    def block(items):
        n = len(items)
        wins = sum(1 for r in items if r[4] == "win")
        counts = {o: sum(1 for r in items if r[4] == o) for o in OUTCOMES}
        return {"n": n, **counts, "hit_rate": round(wins / n, 4) if n else None}
    scored = [r for r in rows if r[4] in OUTCOMES]
    out = {"evaluated": len(scored), "by_source": {}, "by_conviction": {}}
    for src in sorted({r[0] for r in scored}):
        out["by_source"][src] = block([r for r in scored if r[0] == src])
    for lo, hi in ((50, 60), (60, 70), (70, 101)):
        bucket = [r for r in scored
                  if lo <= max(r[2] or 0, r[3] or 0) < hi]
        out["by_conviction"][f"{lo}-{min(hi, 100)}"] = block(bucket)
    out["overall"] = block(scored)
    return out


def split_stats(rows):
    """Distribution of the computed p_long and factor usage (rows from AnalysisStore.stats).

    Rows logged before the factor-scored split have p_long NULL and are counted as legacy.
    """
    scored = [r for r in rows if r[5] is not None]
    ps = sorted(r[5] for r in scored)
    factor_lists = []
    for r in scored:
        try:
            factor_lists.append(json.loads(r[6] or "[]"))
        except ValueError:
            factor_lists.append([])
    hermes = [f for r, f in zip(scored, factor_lists) if r[0] == "hermes"]
    def citing(prefix):
        return sum(1 for fs in hermes
                   if any(s.startswith(prefix) for f in fs for s in f.get("source_fields", [])))
    with_funding = citing("funding.")
    n = len(ps)
    mean = sum(ps) / n if n else None
    hist = {f"{lo}-{lo + 10}": sum(1 for p in ps if lo <= p < lo + 10 or (lo == 90 and p == 100))
            for lo in range(0, 100, 10)}
    return {
        "n": n, "legacy_without_p_long": len(rows) - n,
        "p_long_min": round(ps[0], 2) if ps else None, "p_long_max": round(ps[-1], 2) if ps else None,
        "p_long_mean": round(mean, 2) if ps else None,
        "p_long_std": round((sum((p - mean) ** 2 for p in ps) / n) ** 0.5, 2) if ps else None,
        "distinct_long_pct": len({r[2] for r in scored}),
        "histogram": {k: v for k, v in hist.items() if v},
        "hermes_runs": len(hermes),
        "factors_per_hermes_run": round(sum(len(f) for f in hermes) / len(hermes), 2) if hermes else None,
        "hermes_runs_citing_funding": with_funding,
        "hermes_runs_citing_positioning": citing("positioning."),
        "disagreements": {s: sum(1 for r in scored if r[7] == s) for s in ("opposite", "neutral_mismatch")},
    }


class AnalysisStore:
    def __init__(self, path):
        self.path = Path(path) if str(path) != ":memory:" else path
        if isinstance(self.path, Path):
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(SCHEMA)
            have = {r[1] for r in self._conn.execute("PRAGMA table_info(agent_analyses)")}
            for name, kind in EXTRA_COLUMNS:
                if name not in have:
                    self._conn.execute(f"ALTER TABLE agent_analyses ADD COLUMN {name} {kind}")
            self._conn.commit()

    def record(self, row):
        cols = ("ts", "exchange", "symbol", "interval", "price", "atr", "bar_close_ts",
                "long_pct", "short_pct", "bias", "confidence", "source", "status",
                "levels_json", "model", "latency_ms", "snapshot_hash", "horizon_bars",
                "p_long", "net", "factors_json", "agent_bias", "disagreement")
        values = [row.get(c) for c in cols]
        values[cols.index("levels_json")] = json.dumps(row.get("levels") or [])
        values[cols.index("factors_json")] = json.dumps(row.get("factors") or [], ensure_ascii=False)
        with self._lock:
            cur = self._conn.execute(
                f"INSERT INTO agent_analyses ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                values)
            self._conn.commit()
            return cur.lastrowid

    def pending(self, now_ms, interval_ms_fn, limit=20):
        """Rows with a directional bias whose horizon has fully elapsed and no outcome yet."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, exchange, symbol, interval, price, atr, bar_close_ts, bias, horizon_bars "
                "FROM agent_analyses WHERE outcome IS NULL AND bias IN ('long','short') "
                "ORDER BY ts LIMIT 500").fetchall()
        due = []
        for r in rows:
            try:
                span = interval_ms_fn(r[3]) * (r[8] or 0)
            except ValueError:
                continue
            if r[6] is not None and r[6] + span <= now_ms:
                due.append(r)
        return due[:limit]

    def set_outcome(self, row_id, outcome, now_ms):
        with self._lock:
            self._conn.execute("UPDATE agent_analyses SET outcome=?, outcome_ts=? WHERE id=?",
                               (outcome, now_ms, row_id))
            self._conn.commit()

    def stats(self, exchange=None, symbol=None, interval=None):
        where, args = [], []
        for col, val in (("exchange", exchange), ("symbol", symbol), ("interval", interval)):
            if val:
                where.append(f"{col}=?")
                args.append(val)
        clause = f" WHERE {' AND '.join(where)}" if where else ""
        with self._lock:
            full = self._conn.execute(
                "SELECT source, bias, long_pct, short_pct, outcome, p_long, factors_json, "
                f"disagreement FROM agent_analyses{clause}", args).fetchall()
        rows = [r[:5] for r in full]
        pending = sum(1 for r in rows if r[4] is None and r[1] in ("long", "short"))
        out = summarize(rows)
        out.update(total=len(rows), pending=pending,
                   neutral=sum(1 for r in rows if r[1] == "neutral"),
                   split=split_stats(full))
        return out


def evaluate_pending(store, fetch_bars, now_ms, interval_ms_fn, limit=20):
    """Fetch later closed bars for due rows and store outcomes. Returns count evaluated.

    ``fetch_bars(exchange, symbol, interval, after_ms, count)`` → closed bars whose
    open time is ≥ after_ms, oldest first. Fetch errors leave the row pending.
    """
    done = 0
    for row_id, exchange, symbol, interval, price, atr_v, close_ts, bias, horizon in \
            store.pending(now_ms, interval_ms_fn, limit):
        try:
            bars = fetch_bars(exchange, symbol, interval, close_ts, horizon)
        except Exception:  # noqa: BLE001, S112 — row stays pending
            continue
        outcome = evaluate_outcome(bias, price, atr_v, bars, horizon)
        if outcome is not None:
            store.set_outcome(row_id, outcome, now_ms)
            done += 1
    return done
