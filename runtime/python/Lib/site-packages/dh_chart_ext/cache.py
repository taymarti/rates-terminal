"""
A cache of shared DAG nodes: tables built on demand for a request, shared by every client that makes
the same request, and released when the last one lets go.

    cache = NodeCache(grace_s=60)
    table = cache.acquire(key, build)   # build(deps) -> Table, run once per key while it is held
    ...
    cache.release(key)                  # once per acquire

A node is held by reference counts: each acquire adds one, each release removes one. At zero it is kept
for `grace_s` seconds (a reload or a layout switch reuses it), then its liveness scope is released and the
engine stops updating it, unless a client still holds an export of it. Everything a build creates is held
by that node's scope; a build that acquires other nodes (2m bars on 1m bars, a study on bars) appends
their keys to `deps`, and they are released when the node is evicted, so a parent outlives its children.

Builds run outside the cache's lock. Requests for a key that is being built wait for the same build.

Limits: at most `max_nodes` nodes (held or idle); a request that would build one more raises NodeLimit
after evicting idle nodes first. `metrics_table()` is a live table of the nodes (Key, Refs, Hits,
BuiltS, IdleS, Deps), refreshed by the sweeper, for dashboards.
"""
from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import Future
from typing import Any, Callable, Hashable

from deephaven.execution_context import get_exec_ctx
from deephaven.liveness_scope import LivenessScope

log = logging.getLogger(__name__)


class NodeLimit(RuntimeError):
    """The cache is full of held nodes."""

    api_code = "limit"  # how rpc.Api reports it


class _Entry:
    __slots__ = ("future", "scope", "refs", "idle_since", "deps", "built_s", "created", "hits")

    def __init__(self):
        self.future: Future = Future()
        self.scope = LivenessScope()
        self.refs = 0
        self.idle_since: float | None = None
        self.deps: list = []
        self.built_s: float | None = None
        self.created = time.time()
        self.hits = 0


class NodeCache:
    """Shared tables keyed by a canonical request; reference-counted, evicted after a grace period."""

    def __init__(self, grace_s: float = 60.0, sweep_s: float = 5.0, max_nodes: int = 2000):
        self._lock = threading.Lock()
        self._entries: dict[Hashable, _Entry] = {}
        self.grace_s = grace_s
        self.max_nodes = max_nodes
        self._metrics = None
        self._metric_keys: set[str] = set()
        self._ctx = get_exec_ctx()  # the app's context, for evictions on the sweeper thread
        self._stop = threading.Event()
        self._sweeper = threading.Thread(target=self._sweep_loop, args=(sweep_s,), name="node-cache-sweeper", daemon=True)
        self._sweeper.start()

    def acquire(self, key: Hashable, build: Callable[[list], Any]) -> Any:
        """The node for `key`, built with `build(deps)` if it is not cached. Pair with one release(key)."""
        with self._lock:
            full = key not in self._entries and len(self._entries) >= self.max_nodes
        if full:
            self.evict_idle(older_than=0)  # make room from nodes nobody holds
        with self._lock:
            if key not in self._entries and len(self._entries) >= self.max_nodes:
                raise NodeLimit(f"the node cache is full ({self.max_nodes} nodes held)")
            e = self._entries.get(key)
            owner = e is None
            if owner:
                e = self._entries[key] = _Entry()
            else:
                e.hits += 1
            e.refs += 1
            e.idle_since = None
        if owner:
            t0 = time.perf_counter()
            try:
                with e.scope.open():  # what the build creates is held by this node's scope
                    value = build(e.deps)
            except BaseException as ex:
                with self._lock:
                    if self._entries.get(key) is e:
                        del self._entries[key]
                for d in e.deps:
                    self.release(d)
                e.scope.release()
                e.future.set_exception(ex)
                raise
            e.built_s = time.perf_counter() - t0
            e.future.set_result(value)
            print(f"[node-cache] built {key} in {e.built_s:.2f}s", flush=True)
        try:
            return e.future.result()
        except BaseException:
            if not owner:  # the build failed; this request's reference goes with it
                self.release(key)
            raise

    def release(self, key: Hashable, *, idle_since: float | None = None) -> None:
        """Drop one reference to `key` (a key that is not cached is ignored)."""
        with self._lock:
            e = self._entries.get(key)
            if e is None or e.refs <= 0:
                return
            e.refs -= 1
            if e.refs == 0:
                e.idle_since = time.monotonic() if idle_since is None else idle_since

    def evict_idle(self, older_than: float | None = None) -> int:
        """Evict nodes nobody holds that have been idle `older_than` seconds (default the grace period)."""
        limit = self.grace_s if older_than is None else older_than
        now = time.monotonic()
        dead: list[tuple[Hashable, _Entry, float]] = []
        with self._lock:
            for k, e in list(self._entries.items()):
                if e.refs == 0 and e.idle_since is not None and now - e.idle_since >= limit:
                    del self._entries[k]
                    dead.append((k, e, e.idle_since))
        for k, e, since in dead:
            try:
                e.scope.release()
            except Exception:  # noqa: BLE001 (released twice: nothing to do)
                log.exception("[node-cache] release failed")
            print(f"[node-cache] evicted {k}", flush=True)
            for d in e.deps:
                # nothing but this node used the parent since it went idle: it has been idle as long
                self.release(d, idle_since=since)
        return len(dead)

    def stats(self) -> list[dict]:
        """One row per cached node: key, refs, hits, build seconds, idle seconds."""
        now = time.monotonic()
        with self._lock:
            return [
                {"key": list(k) if isinstance(k, tuple) else k, "refs": e.refs, "hits": e.hits,
                 "built_s": None if e.built_s is None else round(e.built_s, 3),
                 "idle_s": None if e.idle_since is None else round(now - e.idle_since, 1),
                 "deps": [list(d) if isinstance(d, tuple) else d for d in e.deps]}
                for k, e in self._entries.items()
            ]

    def metrics_table(self):
        """A live table of the cached nodes (one row per node), refreshed every sweep."""
        if self._metrics is None:
            from deephaven import dtypes as dht
            from deephaven.table_factory import input_table

            self._metrics = input_table({"Key": dht.string, "Refs": dht.int32, "Hits": dht.int32, "BuiltS": dht.double,
                                         "IdleS": dht.double, "Deps": dht.string}, key_cols="Key")
            self._publish_metrics()
        return self._metrics

    def _publish_metrics(self) -> None:
        if self._metrics is None:
            return
        from deephaven import new_table
        from deephaven.column import double_col, int_col, string_col

        rows = self.stats()
        name = lambda k: "|".join(str(x) for x in k) if isinstance(k, list) else str(k)  # noqa: E731
        keys = [name(r["key"]) for r in rows]
        gone = self._metric_keys - set(keys)
        if rows:
            self._metrics.add(new_table([
                string_col("Key", keys), int_col("Refs", [r["refs"] for r in rows]), int_col("Hits", [r["hits"] for r in rows]),
                double_col("BuiltS", [r["built_s"] if r["built_s"] is not None else float("nan") for r in rows]),
                double_col("IdleS", [r["idle_s"] if r["idle_s"] is not None else float("nan") for r in rows]),
                string_col("Deps", [", ".join(name(d) for d in r["deps"]) for r in rows]),
            ]))
        if gone:
            self._metrics.delete(new_table([string_col("Key", sorted(gone))]))
        self._metric_keys = set(keys)

    def close(self) -> None:
        self._stop.set()

    def _sweep_loop(self, every: float) -> None:
        while not self._stop.wait(every):
            try:
                with self._ctx:
                    # evicting a node can idle its parents; sweep until nothing more goes
                    while self.evict_idle():
                        pass
                    self._publish_metrics()
            except Exception:  # noqa: BLE001
                log.exception("[node-cache] sweep failed")
