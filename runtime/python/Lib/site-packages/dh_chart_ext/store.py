"""
P, persistence: chart annotations (price lines now; drawings and settings next) in one Deephaven table,
so every front end reads and writes the same rows:

  A  deephaven.ui         the workspace passes the table to the `acme.chart.Annotations` element
  B  React + Deephaven UI the app fetches the published table and builds a store (@acme/chart-ext)
  C  React, table data    the same, around its own charts

    chart_annotations = annotation_table(path=".../annotations.parquet")   # in the app script

Schema (one row per annotation, keyed by ChartKey + Id):
  ChartKey   what the annotation belongs to, usually the symbol (drawings by time and price carry
             across resolutions and chart types)
  Id         client-generated unique id
  Kind       "price_line", later "trend_line", "rect", ...
  Owner      who made it
  Shared     visible to everyone (else only to Owner)
  Deleted    soft delete: clients delete with an ordinary upsert, and the row stays as history
  Spec       JSON: the kind's parameters, e.g. {"price": 101.25}
  UpdatedMs  epoch milliseconds of the last change (client clock)

Who "Owner" is: Community has no user identity, so the client supplies it (the workspace's `user`
argument, a URL parameter, or a per-browser id). That is attribution, not access control. On
Enterprise, take it from the authenticated user and enforce visibility with row-level ACLs on Owner.

Durability: a Community input table lives in memory. With `path`, the table is loaded from a parquet
snapshot at startup and written back after every change (fine for hundreds of annotations; a busy
desk wants Enterprise's persistent input tables instead).
"""
from __future__ import annotations

import pathlib
from typing import Optional

from deephaven import dtypes
from deephaven.table import Table
from deephaven.table_factory import InputTable, input_table

COLUMNS = {
    "ChartKey": dtypes.string,
    "Id": dtypes.string,
    "Kind": dtypes.string,
    "Owner": dtypes.string,
    "Shared": dtypes.bool_,
    "Deleted": dtypes.bool_,
    "Spec": dtypes.string,
    "UpdatedMs": dtypes.int64,
}
KEYS = ["ChartKey", "Id"]

# the listeners keeping snapshots on disk; held so they are not garbage-collected
_autosaves: list = []


def annotation_table(path: Optional[str | pathlib.Path] = None) -> InputTable:
    """A keyed input table of chart annotations; with `path`, loaded from and saved to a parquet file."""
    return persistent_input_table(COLUMNS, KEYS, path)


def epoch_ms(v) -> int:
    """Milliseconds since the epoch from an Instant column value as Table.iter_dict gives it (a Java Instant
    on 42.6), a datetime, or a numpy datetime64; 0 for null."""
    if v is None:
        return 0
    if hasattr(v, "toEpochMilli"):
        return int(v.toEpochMilli())
    if hasattr(v, "timestamp"):
        return int(v.timestamp() * 1000)
    try:
        import numpy as np

        return int(np.datetime64(v, "ms").astype("int64"))
    except Exception:  # noqa: BLE001
        return 0


def persistent_input_table(columns: dict, keys: list, path: Optional[str | pathlib.Path] = None) -> InputTable:
    """A keyed input table; with `path`, loaded from a parquet snapshot and written back on every change."""
    t = input_table(col_defs=columns, key_cols=keys)
    if path is None:
        return t
    from deephaven import parquet
    from deephaven.table_listener import listen

    p = pathlib.Path(path).resolve()
    uri = p.as_uri()  # parquet on Windows needs file:/// URIs
    if p.exists():
        t.add(parquet.read(uri).view(list(columns)))

    def save(update, is_replay):  # runs in the update cycle, holding the lock: snapshot is consistent
        p.parent.mkdir(parents=True, exist_ok=True)
        parquet.write(t.snapshot(), uri)

    _autosaves.append(listen(t, save, do_replay=False))
    return t


def visible(t: Table, owner: str) -> Table:
    """The annotations `owner` sees: not deleted, and theirs or shared."""
    return t.where(["!Deleted", f"Shared || Owner == `{owner}`"])


# ----------------------------------------------------------------------------- settings
# Saved UI state (a workspace's toolbar, later chart settings) lives in the same table, as one row per
# (layout, owner): ChartKey "settings:<layout>", Id = owner, Kind "settings", Spec = the state as JSON.

SETTINGS_KIND = "settings"


def _settings_key(layout: str) -> str:
    return f"settings:{layout}"


def load_settings(t: Table, layout: str, owner: str) -> Optional[dict]:
    """The settings `owner` last saved for `layout`, or None. Reads a snapshot (call it once, e.g. on mount)."""
    import json

    from deephaven.pandas import to_pandas

    df = to_pandas(
        t.where([f"ChartKey = `{_settings_key(layout)}`", f"Id = `{owner}`", "!Deleted"]).view("Spec").snapshot()
    )
    if df.empty:
        return None
    try:
        return json.loads(df["Spec"].iloc[0])
    except (TypeError, ValueError):
        return None


def save_settings(t: InputTable, layout: str, owner: str, spec: dict) -> None:
    """Save `spec` as `owner`'s settings for `layout` (an upsert)."""
    import json
    import time

    from deephaven import new_table
    from deephaven.column import bool_col, long_col, string_col

    t.add(
        new_table([
            string_col("ChartKey", [_settings_key(layout)]), string_col("Id", [owner]),
            string_col("Kind", [SETTINGS_KIND]), string_col("Owner", [owner]),
            bool_col("Shared", [False]), bool_col("Deleted", [False]),
            string_col("Spec", [json.dumps(spec, sort_keys=True)]), long_col("UpdatedMs", [int(time.time() * 1000)]),
        ])
    )


def annotations_element(table: Table, owner: Optional[str] = None):
    """The deephaven.ui element that hands `table` to the page's chart extensions (rendered by the
    dh-chart-ext JS plugin as `acme.chart.Annotations`; it draws nothing). `owner` names the user;
    None lets the browser use a per-browser id."""
    from deephaven.ui.elements import BaseElement

    props = {"table": table}
    if owner is not None:
        props["owner"] = owner
    return BaseElement("acme.chart.Annotations", **props)
