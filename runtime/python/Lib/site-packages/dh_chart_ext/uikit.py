"""
Pieces for an app's deephaven.ui panels:

  LinkHub          a selection several widgets share on the server ({sym, dataset, book, ...}): for panels published
                   as separate widgets (front-end group B), which share no React state; each one follows the hub
  linked(hub, f)   a component that renders f(selection) and re-renders when the hub's selection changes
  row_value        a value from a deephaven.ui table's row-press payload
  whole, dec, signed, POS, NEG
                   ui.TableFormat lists: thousands separators, n decimals, green when positive and red when negative
"""
from __future__ import annotations

import threading
import weakref
from typing import Callable

from deephaven import ui

POS = "#26a69a"
NEG = "#ef5350"


def row_value(row, col):
    """A value from a deephaven.ui row-press payload ({col: {value, text}} or plain)."""
    v = (row or {}).get(col)
    return v.get("value") if isinstance(v, dict) else v


def whole(*cols):
    """Thousands separators, no decimals (dollars, DV01, sizes)."""
    return [ui.TableFormat(cols=list(cols), value="#,##0")]


def dec(n, *cols):
    """Thousands separators and `n` decimals."""
    return [ui.TableFormat(cols=list(cols), value="#,##0." + "0" * n)]


def signed(*cols):
    """Green when positive, red when negative."""
    return [ui.TableFormat(cols=list(cols), if_=f"{c} > 0", color=POS) for c in cols] + \
           [ui.TableFormat(cols=list(cols), if_=f"{c} < 0", color=NEG) for c in cols]


class LinkHub:
    """A selection widgets share on the server: {key: value}. Every open widget subscribes; a change re-renders them.
    One hub is one selection for everyone who has the widgets open (make one per user to have one each).

    Subscribers are held weakly: a widget whose browser went away is not always unmounted (its effect cleanup, which
    unsubscribes, may never run), and the hub must not keep it, or re-render it, forever. The widget's own cleanup
    function holds the subscriber, so it lives exactly as long as the widget's render context."""

    def __init__(self, **initial):
        self._v = dict(initial)
        self._subs: list = []  # weakref.ref to each subscriber
        self._lock = threading.Lock()

    def snapshot(self) -> dict:
        with self._lock:
            return dict(self._v)

    def set(self, key: str, value):
        with self._lock:
            if value is None or self._v.get(key) == value:
                return
            self._v[key] = value
            self._subs = [r for r in self._subs if r() is not None]
            subs, snap = [r() for r in self._subs], dict(self._v)
        for cb in subs:
            if cb is None:
                continue
            try:
                cb(snap)
            except Exception as e:  # noqa: BLE001 (a closed widget)
                print("link hub listener failed:", e)

    def setter(self, key: str) -> Callable:
        return lambda v: self.set(key, v)

    def subscribe(self, cb) -> Callable[[], None]:
        ref = weakref.ref(cb)
        with self._lock:
            self._subs.append(ref)
        return lambda: (cb, self._unsubscribe(ref))  # holding cb here keeps it alive while the widget is

    def _unsubscribe(self, ref):
        with self._lock:
            self._subs = [r for r in self._subs if r is not ref and r() is not None]

    @property
    def subscribers(self) -> int:
        with self._lock:
            return sum(1 for r in self._subs if r() is not None)


def linked(hub: LinkHub, render: Callable[[dict], object]):
    """A deephaven.ui element that renders `render(selection)` and follows the hub. Publish one per widget:

        b_chart = linked(hub, lambda v: chart_panel(v["sym"], hub.setter("sym")))
    """

    @ui.component
    def follower():
        v, set_v = ui.use_state(hub.snapshot)

        def follow():
            def on_change(snap):
                set_v(snap)

            return hub.subscribe(on_change)

        ui.use_effect(follow, [])
        return render(v)

    return follower()
