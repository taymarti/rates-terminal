"""
Saved workspaces (an app shell's layouts: which panels, where, with what settings), per user, behind the
request API. The layout itself is the front end's business: the server stores it as JSON and hands it back.

    workspaces = WorkspaceStore(path=app_dir / "workspaces.parquet")
    workspaces.register(chart_api)          # adds the methods below to any rpc.Api
    workspace_table = workspaces.table      # publish it to see every saved workspace (an operator view)

Methods (the user is the stream's, from `hello`; before hello it is "anonymous"):

  workspaces        {}                       -> [{name, saved_at}, ...] (this user's, newest first)
  workspace_load    {name}                   -> {name, layout, saved_at}   (not_found if there is none)
  workspace_save    {name, layout}           -> {name, saved_at}
  workspace_delete  {name}                   -> {deleted: bool}

Reads come from memory; every write also goes to a keyed input table (User, Name), which with `path` is
saved to parquet and loaded on start.
"""
from __future__ import annotations

import json
import pathlib
import threading
import time
from typing import Optional

from deephaven import dtypes, new_table
from deephaven.column import datetime_col, string_col

from .rpc import Api, ApiError
from .store import epoch_ms, persistent_input_table

COLUMNS = {"User": dtypes.string, "Name": dtypes.string, "Layout": dtypes.string, "SavedAt": dtypes.Instant}
KEYS = ["User", "Name"]

MAX_NAME = 64
MAX_LAYOUT_BYTES = 256 * 1024
MAX_PER_USER = 100


class WorkspaceStore:
    def __init__(self, path: Optional[str | pathlib.Path] = None):
        self.table = persistent_input_table(COLUMNS, KEYS, path)
        self._lock = threading.Lock()
        self._by_user: dict[str, dict[str, tuple[str, int]]] = {}  # user -> name -> (layout JSON, saved_at ms)
        for row in self.table.iter_dict():  # Instants come back as Java Instants (42.6)
            self._by_user.setdefault(row["User"], {})[row["Name"]] = (row["Layout"], epoch_ms(row["SavedAt"]))

    # direct use (an app's own code, tests)

    def list(self, user: str) -> list[dict]:
        with self._lock:
            items = list(self._by_user.get(user, {}).items())
        return [{"name": n, "saved_at": at} for n, (_l, at) in sorted(items, key=lambda kv: -kv[1][1])]

    def load(self, user: str, name: str) -> Optional[dict]:
        with self._lock:
            hit = self._by_user.get(user, {}).get(name)
        if hit is None:
            return None
        return {"name": name, "layout": json.loads(hit[0]), "saved_at": hit[1]}

    def save(self, user: str, name: str, layout) -> dict:
        name = (name or "").strip()
        if not name or len(name) > MAX_NAME:
            raise ApiError(f"a workspace name is 1 to {MAX_NAME} characters", "bad_params")
        text = json.dumps(layout, separators=(",", ":"))
        if len(text.encode()) > MAX_LAYOUT_BYTES:
            raise ApiError(f"a layout is at most {MAX_LAYOUT_BYTES // 1024} KB", "limit")
        at = int(time.time() * 1000)
        with self._lock:
            mine = self._by_user.setdefault(user, {})
            if name not in mine and len(mine) >= MAX_PER_USER:
                raise ApiError(f"at most {MAX_PER_USER} workspaces per user", "limit")
            mine[name] = (text, at)
        self.table.add(new_table([
            string_col("User", [user]), string_col("Name", [name]), string_col("Layout", [text]),
            datetime_col("SavedAt", [at * 1_000_000]),
        ]))
        return {"name": name, "saved_at": at}

    def delete(self, user: str, name: str) -> bool:
        with self._lock:
            mine = self._by_user.get(user, {})
            if name not in mine:
                return False
            del mine[name]
        self.table.delete(new_table([string_col("User", [user]), string_col("Name", [name])]))
        return True

    # as request methods

    def register(self, api: Api) -> None:
        store = self

        @api.method
        def workspaces(ctx):
            """This user's saved workspaces, newest first."""
            return store.list(ctx.user)

        @api.method
        def workspace_load(ctx, name: str):
            """A saved workspace's layout."""
            hit = store.load(ctx.user, name)
            if hit is None:
                raise ApiError(f"no workspace {name!r}", "not_found")
            return hit

        @api.method
        def workspace_save(ctx, name: str, layout: dict):
            """Save (or replace) a workspace's layout."""
            return store.save(ctx.user, name, layout)

        @api.method
        def workspace_delete(ctx, name: str):
            """Delete a saved workspace."""
            return {"deleted": store.delete(ctx.user, name)}
