"""
Alerts, evaluated in the Deephaven engine: they fire on the server whether or not anyone has a chart
open. Browsers create and cancel them by writing to the alerts input table (as with drawings), draw
active ones on charts, and show a notification when one of theirs fires.

    chart_alerts = alert_table(path=".../alerts.parquet")      # in the app script
    engine = AlertEngine(chart_alerts, source.alert_values())  # keep a reference: it holds the listener

Schema (one row per alert, keyed by Id):
  Id, Owner, Sym      who set it on what (Sym can be a spread expression)
  Source              what is watched: "Last" (the last trade) or a study value, "<column>@<resolution>"
                      (e.g. "RSI_14@1m": the 1-minute RSI of the forming bar)
  Kind                "price" (a level), "line" (a trend line: the level moves along it), "study"
  Op                  "cross" (either way), "above" (crosses up), "below" (crosses down)
  Level               the level, for price and study alerts
  T0 P0 T1 P1         a line alert's two points (UTC seconds, price); DrawingId names the drawing
  Message             shown when it fires
  Status              "active", "triggered" or "cancelled" (cancel is an upsert, like annotations)
  CreatedMs TriggeredMs TriggerValue

How it is evaluated: the active alerts are joined with a live values table (one row per symbol and
source); a listener sees each changed row and compares the value with the level. A crossing is a
change of side between two observations, as in TradingView: an alert set below the price waits for
the price to come down through it. On a crossing it writes the row back as triggered (from a worker
thread: input-table writes cannot happen inside the update cycle). The work per cycle is one
comparison per active alert whose symbol ticked; no Python runs per tick per client.
"""
from __future__ import annotations

import math
import pathlib
import threading
import time
from typing import Optional

from deephaven import dtypes
from deephaven.table import Table

from .store import persistent_input_table

COLUMNS = {
    "Id": dtypes.string, "Owner": dtypes.string, "Sym": dtypes.string, "Source": dtypes.string,
    "Kind": dtypes.string, "Op": dtypes.string, "Level": dtypes.double,
    "T0": dtypes.double, "P0": dtypes.double, "T1": dtypes.double, "P1": dtypes.double, "DrawingId": dtypes.string,
    "Message": dtypes.string, "Status": dtypes.string,
    "CreatedMs": dtypes.int64, "TriggeredMs": dtypes.int64, "TriggerValue": dtypes.double,
}
KEYS = ["Id"]

_NULL_DOUBLE = -1.7976931348623157e308


def alert_table(path: Optional[str | pathlib.Path] = None):
    """A keyed input table of alerts; with `path`, kept on disk across restarts."""
    return persistent_input_table(COLUMNS, KEYS, path)


def _num(x) -> Optional[float]:
    if x is None:
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(v) or v <= _NULL_DOUBLE else v


def level_at(row: dict, t_sec: Optional[float]) -> Optional[float]:
    """The alert's level at time `t_sec`: fixed for price/study alerts, along the line for line alerts."""
    if row["Kind"] != "line":
        return _num(row["Level"])
    t0, p0, t1, p1 = (_num(row[k]) for k in ("T0", "P0", "T1", "P1"))
    if None in (t0, p0, t1, p1) or t_sec is None:
        return None
    if t1 == t0:
        return p0
    return p0 + (p1 - p0) * (t_sec - t0) / (t1 - t0)


def crossed(op: str, prev_side: int, side: int) -> bool:
    if prev_side == 0 or side == 0 or prev_side == side:
        return False
    return op == "cross" or (op == "above" and side > 0) or (op == "below" and side < 0)


class AlertEngine:
    """Evaluates `alerts` against `values` (Sym, Source, Value, ValueNs) on every update."""

    def __init__(self, alerts, values: Table, *, log: bool = True):
        from deephaven.execution_context import get_exec_ctx
        from deephaven.table_listener import listen

        self._ctx = get_exec_ctx()  # worker threads write with the creator's context
        self.alerts = alerts
        self.log = log
        self.active = alerts.where("Status == `active`").natural_join(values, on=["Sym", "Source"], joins=["Value", "ValueNs"])
        self._sides: dict[str, int] = {}
        self._fired: set[str] = set()
        self._cols = list(COLUMNS) + ["Value", "ValueNs"]
        self._handle = listen(self.active, self._on_update, do_replay=True)

    def _rows(self, part: dict) -> list[dict]:
        if not part:
            return []
        n = len(next(iter(part.values())))
        return [{k: part[k][i] for k in part} for i in range(n)]

    def _on_update(self, update, is_replay):
        fired = []
        for row in self._rows(update.added(self._cols)) + self._rows(update.modified(self._cols)):
            aid = str(row["Id"])
            value = _num(row["Value"])
            ns = row["ValueNs"]
            try:
                t_sec = int(ns) / 1e9 if ns is not None and int(ns) > 0 else None
            except (TypeError, ValueError):
                t_sec = None
            level = level_at(row, t_sec if t_sec is not None else time.time())
            if value is None or level is None:
                continue
            side = 1 if value > level else -1 if value < level else 0
            prev = self._sides.get(aid, 0)
            if side != 0:
                self._sides[aid] = side
            if aid not in self._fired and crossed(str(row["Op"]), prev, side):
                self._fired.add(aid)
                fired.append({**row, "TriggerValue": value})
        for row in self._rows(update.removed(["Id"])):
            self._sides.pop(str(row["Id"]), None)
        if fired:
            threading.Thread(target=self._write, args=(fired,), daemon=True).start()

    def _write(self, fired: list[dict]) -> None:
        from deephaven import new_table
        from deephaven.column import double_col, long_col, string_col

        now = int(time.time() * 1000)

        def s(k):
            return string_col(k, [None if r[k] is None else str(r[k]) for r in fired])

        def d(k):
            return double_col(k, [_num(r[k]) for r in fired])

        def lg(k):
            return long_col(k, [None if r[k] is None else int(r[k]) for r in fired])

        try:
            with self._ctx:
                self.alerts.add(new_table([
                    s("Id"), s("Owner"), s("Sym"), s("Source"), s("Kind"), s("Op"), d("Level"),
                    d("T0"), d("P0"), d("T1"), d("P1"), s("DrawingId"), s("Message"),
                    string_col("Status", ["triggered"] * len(fired)), lg("CreatedMs"),
                    long_col("TriggeredMs", [now] * len(fired)), double_col("TriggerValue", [r["TriggerValue"] for r in fired]),
                ]))
        finally:
            for r in fired:
                self._fired.discard(str(r["Id"]))
        if self.log:
            for r in fired:
                print(f"[alerts] {r['Owner']}: {r['Sym']} {r['Source']} {r['Op']} {r['Level']} "
                      f"(value {r['TriggerValue']:.4f}) {r['Message'] or ''}", flush=True)


def alerts_log(alerts: Table) -> Table:
    """Fired alerts, newest first: what a log or audit view shows."""
    return alerts.where("Status == `triggered`").sort_descending("TriggeredMs")
