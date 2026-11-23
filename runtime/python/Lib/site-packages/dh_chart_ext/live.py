"""
How an app writes its own data into Deephaven, from Python:

  Keyed(cols, keys)          a keyed table the app upserts (quotes, analytics, positions): one row per key, latest wins
                             (rows go through a table publisher into a blink table; last_by keeps each key's latest)
  Stream(name, cols, ring)   rows appended through a table publisher: append-only, or the last `ring` rows (a tape)
  ticks_from_matrix(...)     a static ticks table (Timestamp, Sym, Price, Size) from a steps x symbols price matrix
  Clock(step, period_ms)     the app's own thread: step(now_ms, dt_ms) every period, inside the app's execution context

Columns are (name, type) with type S string, D double, L long, I int, B bool, T Instant (UTC ms in, nanoseconds out).
Writes come from the app's own thread, inside the app's execution context. Neither blocks: a publisher queues the rows
for the next update cycle (an input table's add would wait for it, once per table).
"""
from __future__ import annotations

import math
import threading
import time
import traceback
from typing import Callable, Optional, Sequence

import numpy as np
from deephaven import dtypes, empty_table, new_table
from deephaven.column import bool_col, datetime_col, double_col, int_col, long_col, string_col
from deephaven.stream import blink_to_append_only
from deephaven.stream.table_publisher import table_publisher
from deephaven.table import Table
from deephaven.table_factory import ring_table

_TYPES = {"S": dtypes.string, "D": dtypes.double, "L": dtypes.int64, "I": dtypes.int32, "B": dtypes.bool_, "T": dtypes.Instant}


def _num(v):
    return None if v is None or (isinstance(v, float) and math.isnan(v)) else float(v)


def _int(v):
    return None if v is None or (isinstance(v, float) and not math.isfinite(v)) else int(v)


def _ts(v):
    return None if v is None or (isinstance(v, float) and not math.isfinite(v)) else int(v) * 1_000_000


_COL = {
    "S": lambda n, vs: string_col(n, [None if v is None else str(v) for v in vs]),
    "D": lambda n, vs: double_col(n, [_num(v) for v in vs]),
    "L": lambda n, vs: long_col(n, [_int(v) for v in vs]),
    "I": lambda n, vs: int_col(n, [_int(v) for v in vs]),
    "B": lambda n, vs: bool_col(n, [None if v is None else bool(v) for v in vs]),
    "T": lambda n, vs: datetime_col(n, [_ts(v) for v in vs]),
}


def to_table(cols: Sequence[tuple[str, str]], rows: Sequence[dict]) -> Table:
    """A static table of `rows` (dicts; a missing key is null) with columns `cols`."""
    return new_table([_COL[t](n, [r.get(n) for r in rows]) for n, t in cols])


class Keyed:
    """A keyed table the app upserts into: one row per key, the latest written."""

    _n = 0

    def __init__(self, cols: Sequence[tuple[str, str]], keys: Sequence[str], name: Optional[str] = None):
        self.cols = list(cols)
        Keyed._n += 1
        blink, self.publisher = table_publisher(name or f"keyed_{Keyed._n}", {n: _TYPES[t] for n, t in self.cols})
        self.table = blink.last_by(list(keys)).view([n for n, _ in self.cols])

    def put(self, rows: Sequence[dict]):
        if rows:
            self.publisher.add(to_table(self.cols, rows))


class Stream:
    """Rows appended through a table publisher: the whole history, or a ring of the last `ring` rows."""

    def __init__(self, name: str, cols: Sequence[tuple[str, str]], ring: Optional[int] = None):
        self.cols = list(cols)
        blink, self.publisher = table_publisher(name, {n: _TYPES[t] for n, t in self.cols})
        self.table = ring_table(blink, ring) if ring else blink_to_append_only(blink)

    def put(self, rows: Sequence[dict]):
        if rows:
            self.publisher.add(to_table(self.cols, rows))


def ticks_from_matrix(syms: Sequence[str], t_s: np.ndarray, prices: np.ndarray, sizes: np.ndarray) -> Table:
    """A static ticks table (Timestamp, Sym, Price, Size) in time order from `prices` and `sizes` (steps x symbols;
    NaN: no tick) at times `t_s` (UTC seconds, one per step). Built by formulas over Java arrays: fast for millions
    of rows."""
    n = len(syms)
    mask = np.isfinite(prices).ravel()
    h_t = dtypes.array(dtypes.int64, (np.repeat(np.asarray(t_s).astype(np.int64), n) * 1_000_000_000)[mask])
    h_i = dtypes.array(dtypes.int32, np.tile(np.arange(n, dtype=np.int32), len(t_s))[mask])
    h_p = dtypes.array(dtypes.double, prices.ravel()[mask])
    h_q = dtypes.array(dtypes.double, sizes.ravel()[mask])
    h_s = dtypes.array(dtypes.string, list(syms))
    return empty_table(int(mask.sum())).update([
        "Timestamp = epochNanosToInstant(h_t[(int) ii])", "Sym = h_s[h_i[(int) ii]]", "Price = h_p[(int) ii]", "Size = h_q[(int) ii]",
    ])


class Clock:
    """The app's own thread: `step(now_ms, dt_ms)` every `period_ms`, inside the execution context it was made in
    (make it in the app script). A step that raises is counted and printed (the first 20), and the clock goes on.

        clock = Clock(step, 500, name="market")
        clock.start()
        clock.stats   # {"steps", "last_ms", "avg_ms", "errors"}: how long a step takes
    """

    def __init__(self, step: Callable[[int, int], None], period_ms: int = 500, *, name: str = "app-clock"):
        from deephaven.execution_context import get_exec_ctx

        self.step = step
        self.period_ms = period_ms
        self.name = name
        self.stats = {"steps": 0, "last_ms": 0.0, "avg_ms": 0.0, "errors": 0}
        self._ctx = get_exec_ctx()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> "Clock":
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name=self.name, daemon=True)
            self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        prev = int(time.time() * 1000)
        with self._ctx:
            while not self._stop.is_set():
                t0 = time.perf_counter()
                now = int(time.time() * 1000)
                try:
                    self.step(now, max(1, now - prev))
                except Exception:  # noqa: BLE001 (keep the clock running; say what failed)
                    self.stats["errors"] += 1
                    if self.stats["errors"] <= 20:
                        traceback.print_exc()
                prev = now
                ms = (time.perf_counter() - t0) * 1000
                s = self.stats
                s["steps"] += 1
                s["last_ms"] = ms
                s["avg_ms"] = s["avg_ms"] * 0.95 + ms * 0.05
                self._stop.wait(max(0.02, (self.period_ms - ms) / 1000))
