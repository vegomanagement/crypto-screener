"""Lease-driven poller for Hyperliquid top-N accounts (see hl_whales for the pure maths)."""
from __future__ import annotations

import threading
import time
from collections import deque
from urllib.request import Request, urlopen

from hl_whales import LEADERBOARD_URL, aggregate_coin, detect_changes, parse_leaderboard_stream, parse_positions
from positioning import NormaliseError, fmt

HL_INFO = "https://api.hyperliquid.xyz/info"
LABEL = "Hyperliquid top-{n} accounts by account value (public leaderboard), not all whales"
ERROR_BACKOFF_S = 10
MAX_BACKOFF_S = 600
LEADERBOARD_RETRY_S = 600   # a failed hourly refresh is retried at most this often


def _open_leaderboard():
    req = Request(LEADERBOARD_URL, headers={"User-Agent": "crypto-screener-positioning/1.0"})
    return urlopen(req, timeout=60)  # noqa: S310 - fixed https URL


class WhaleTracker:
    def __init__(self, client, *, top_n=50, poll_interval_s=60, leaderboard_ttl_s=3600,
                 lease_ms=180_000, open_leaderboard=None, clock=time.time, sleep=time.sleep,
                 max_changes=300):
        self.client = client
        self.top_n = top_n
        self.poll_interval_s = poll_interval_s
        self.leaderboard_ttl_s = leaderboard_ttl_s
        self.lease_ms = lease_ms
        self._open_leaderboard = open_leaderboard or _open_leaderboard
        self._clock, self._sleep = clock, sleep
        self._lock = threading.RLock()
        self._poll_lock = threading.Lock()
        self._leases = {}
        self._thread = None
        self.leaderboard = None          # {"rows", "total_rows", "fetched_at", "duration_s"}
        self.states = {}                 # address -> {coin: position} from the last poll
        self.last_poll = None
        self.polls = 0
        self.changes = deque(maxlen=max_changes)
        self.error = None
        self._failures = 0
        self._retry_at_ms = 0

    def _now_ms(self):
        return int(self._clock() * 1000)

    # ----- leaderboard -----
    def load_leaderboard(self, force=False):
        """Top-N rows, refreshed after ``leaderboard_ttl_s``.

        When a refresh fails and an older leaderboard exists, the old address list keeps being
        polled (``refresh_error`` says why) and the ~38 MB download is retried at most every
        ``LEADERBOARD_RETRY_S`` instead of on every poll.
        """
        with self._lock:
            lb = self.leaderboard
            now = self._clock()
            if lb and not force and now - lb["fetched_at"] < self.leaderboard_ttl_s:
                return lb
            if lb and not force and now - lb.get("refresh_failed_at", float("-inf")) < LEADERBOARD_RETRY_S:
                return lb
        t0 = self._clock()
        try:
            stream = self._open_leaderboard()
            try:
                rows, total = parse_leaderboard_stream(stream, self.top_n)
            finally:
                stream.close()
        except Exception as exc:  # noqa: BLE001 - fall back to the previous list when there is one
            with self._lock:
                if self.leaderboard is None:
                    raise
                self.leaderboard = {**self.leaderboard, "refresh_failed_at": self._clock(),
                                    "refresh_error": f"{type(exc).__name__}: {str(exc)[:160]}"}
                return self.leaderboard
        lb = {"rows": rows, "total_rows": total, "fetched_at": self._clock(),
              "duration_s": round(self._clock() - t0, 2)}
        with self._lock:
            self.leaderboard = lb
        return lb

    # ----- polling -----
    def poll_once(self):
        """One sequential pass over the top-N accounts (client paces HL at <= 1 req/s)."""
        if not self._poll_lock.acquire(blocking=False):
            return None
        try:
            lb = self.load_leaderboard()
            started = self._now_ms()
            states, errors = {}, []
            for row in lb["rows"]:
                try:
                    state = self.client.request_json(HL_INFO, body={"type": "clearinghouseState",
                                                                    "user": row["address"]})
                    states[row["address"]] = parse_positions(state)
                except (NormaliseError, Exception) as exc:  # noqa: BLE001 - keep polling the rest
                    errors.append({"address": row["address"], "error": str(exc)[:120]})
            finished = self._now_ms()
            with self._lock:
                prev = self.states
                new_changes = detect_changes(prev, states, finished) if self.polls else []
                self.changes.extend(new_changes)
                self.states = states
                self.polls += 1
                self.last_poll = {"started_at": started, "finished_at": finished,
                                  "duration_ms": finished - started, "requested": len(lb["rows"]),
                                  "ok": len(states), "failed": len(errors), "errors": errors[:5],
                                  "changes_detected": len(new_changes), "poll_number": self.polls}
                self.error = None
            return self.last_poll
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                self.error = f"{type(exc).__name__}: {str(exc)[:160]}"
            return None
        finally:
            self._poll_lock.release()

    # ----- leases / worker -----
    def watch(self, owner="positioning"):
        with self._lock:
            now = self._now_ms()
            self._leases = {o: e for o, e in self._leases.items() if e > now}
            if owner not in self._leases and len(self._leases) >= 20:
                raise ValueError("too many whale subscriptions")
            self._leases[owner] = now + self.lease_ms

    def wanted(self):
        with self._lock:
            now = self._now_ms()
            self._leases = {o: e for o, e in self._leases.items() if e > now}
            return bool(self._leases)

    def running(self):
        return bool(self._thread and self._thread.is_alive())

    def ensure_running(self):
        with self._lock:
            if self.wanted() and not self.running():
                self._thread = threading.Thread(target=self._run, name="hl-whales", daemon=True)
                self._thread.start()

    def _run(self):
        while self.wanted():
            last = self.last_poll
            now = self._now_ms()
            due = (not last or now - last["started_at"] >= self.poll_interval_s * 1000) \
                and now >= self._retry_at_ms
            if due:
                self.poll_once()
                if self.error:
                    # Exponential backoff (10 s → 10 min): a failing poll usually means the
                    # leaderboard download failed, and retrying it every 10 s re-fetches ~38 MB.
                    self._failures += 1
                    delay_s = min(ERROR_BACKOFF_S * 2 ** (self._failures - 1), MAX_BACKOFF_S)
                    self._retry_at_ms = self._now_ms() + int(delay_s * 1000)
                else:
                    self._failures = 0
            else:
                self._sleep(1)

    # ----- view -----
    def view(self, coin, changes_limit=30):
        with self._lock:
            lb = self.leaderboard
            states = dict(self.states)
            last = dict(self.last_poll) if self.last_poll else None
            changes = [c for c in self.changes if c["coin"] == coin][-changes_limit:][::-1]
        agg = aggregate_coin(states, coin) if last else None
        reason = None if last else (self.error or "whale tracker warming up (first poll ~1 min)")
        return {"label": LABEL.format(n=self.top_n), "coin": coin, "venue": "hyperliquid",
                "source": "GET stats-data.hyperliquid.xyz/Mainnet/leaderboard + POST api.hyperliquid.xyz/info "
                          "clearinghouseState",
                "top_n": self.top_n, "timestamp": last["finished_at"] if last else None,
                "leaderboard": ({"fetched_at": int(lb["fetched_at"] * 1000), "total_rows": lb["total_rows"],
                                 "duration_s": lb["duration_s"], "refresh_error": lb.get("refresh_error"),
                                 "min_account_value": fmt(lb["rows"][-1]["account_value"]) if lb["rows"] else None}
                                if lb else None),
                "last_poll": last, "polls": self.polls, "aggregate": agg, "changes": changes,
                "status": {"running": self.running(), "watched": self.wanted(), "error": self.error,
                           "poll_interval_s": self.poll_interval_s},
                "reason": reason}
