"""
L2, the workspace: a deephaven.ui component with a TradingView-style toolbar over one to four L1
charts (a multi-chart layout), each with a legend of its studies, and an optional watchlist. For
deephaven.ui front ends; React front ends render it as a deephaven.ui element (front-end group B), or
build their own on L0 (group C).

    source = ChartSource(ticks)                 # once, at app start: shared bar and quote tables
    globals().update(source.standard_tables())  # optional: publish L0's standard set for JS clients
    ws = chart_workspace(source)                # a deephaven.ui element; put it in a panel/dashboard

What runs where. Every setting (symbol, interval, studies and their parameters, layout, sync) is
Python state: a toolbar, legend or watchlist action is one call to a Python callback, which updates
the state; the panes whose settings changed rebuild their chart (filter the shared bars, add the
studies with update_by, compose a TvlChart), and Deephaven's plugin subscribes and draws it. Ticks
never go through Python: they stream to the browser over Barrage. Crosshair and time sync, drawings,
price lines and the legend's values are browser-only (the frame says which charts sync).

The state is saved per (`layout`, `user`) when an annotations table is given, and restored on open.

History: each chart holds a window of history (its lookback) behind a data.Lookback, and the range
menu sets the initial view inside it. Scrolling into the oldest loaded bar (or "go to date" before
it) calls Python to widen the lookback; the chart's table grows in place, so the view stays put.

Symbols can be spread or ratio expressions ("ES-NQ", "ZN/ZB", "2*ZN-ZB"): typed into the symbol
search, computed from the legs' ticks (data.spread_ticks), cached per source and shared.
"""
from __future__ import annotations

import json
import uuid
from typing import Optional, Sequence

from deephaven import ui
from deephaven.table import Table
from deephaven.time import dh_now

from .charts import STUDY_COLORS, price_chart, tick_chart
from .data import (
    STANDARD_RESOLUTIONS, STUDIES, STUDY_TYPES, Lookback, bars, for_symbol, in_range, is_expression, parse_expression,
    quotes, resolution_seconds, spread_ticks, standard_set, study_label, with_studies,
)
from .nodes import resolution_options, resolution_secs
from .sessions import SESSIONS, session_for, sessions_table
from .store import load_settings, save_settings

# the range menu: the initial view window (seconds; None: everything loaded)
RANGES = {"5m": 300, "15m": 900, "30m": 1800, "1h": 3600, "4h": 14400, "1D": 86400, "5D": 432000, "1M": 30 * 86400,
          "6M": 182 * 86400, "1Y": 365 * 86400, "All": None}
# history loaded at first, per resolution (seconds); scrolling back widens it, up to MAX_LOOKBACK
DEFAULT_LOOKBACK = {"tick": 3600, "1s": 4 * 3600, "5s": 12 * 3600, "1m": 2 * 86400, "5m": 5 * 86400}
MAX_LOOKBACK = 30 * 86400
COMPARE_COLORS = ["#2962ff", "#e91e63", "#00bcd4", "#8bc34a", "#ffeb3b"]
CHART_TYPES = {"candle": "Candles", "bar": "Bars", "line": "Line", "area": "Area"}
LAYOUTS = {"1": ("1 chart", 1), "2h": ("2 charts, side by side", 2), "2v": ("2 charts, stacked", 2),
           "3h": ("3 charts", 3), "4": ("4 charts", 4)}
DEFAULT_SYNC = {"symbol": False, "interval": False, "crosshair": True, "time": False}


class ChartSource:
    """A ticks table and what is built from it once and shared by every workspace and client:
    bars at the standard resolutions, the symbols seen so far, and a quotes table for watchlists.

    Build it in the app script (not inside a component), so its tables live as long as the app.

    `history`: older ticks (the same columns, all before `ticks`) behind daily and weekly charts (nodes.ChartNodes).
    `symbols`: a fixed universe, so chart requests don't snapshot the ticks' distinct symbols."""

    def __init__(self, ticks: Table, resolutions: Sequence[str] = STANDARD_RESOLUTIONS, *, by: str = "Sym",
                 history: Optional[Table] = None, symbols: Optional[Sequence[str]] = None):
        self.ticks = ticks
        self.by = by
        self.resolutions = tuple(resolutions)
        self.history = history
        self._known = list(symbols) if symbols is not None else None
        self._bars = {r: bars(ticks, r, by=by) for r in self.resolutions}
        self._symbols = ticks.select_distinct(by)
        self.quotes = quotes(ticks, by=by)
        self._spreads: dict = {}
        self._standard: dict[str, Table] = {}
        self._values: Optional[Table] = None
        self._nodes = None

    @property
    def nodes(self):
        """The shared chart nodes (nodes.ChartNodes): bars and studies built on demand per request, shared by
        every workspace and API client asking for the same chart, released when none holds it."""
        if self._nodes is None:
            from .nodes import ChartNodes

            self._nodes = ChartNodes(self, history=self.history, symbols=self._known)
        return self._nodes

    def bars(self, resolution: str) -> Table:
        return self._bars[resolution]

    # an instrument is a symbol or a spread/ratio expression over symbols

    def instrument_ticks(self, sym: str, symbols: Sequence[str]) -> Table:
        """Ticks of one instrument (Timestamp, Price, ...), without the symbol column."""
        if not is_expression(sym):
            return for_symbol(self.ticks, sym, by=self.by)
        key = (sym, "tick")
        if key not in self._spreads:
            self._spreads[key] = spread_ticks(self.ticks, sym, symbols, by=self.by)
        return self._spreads[key].drop_columns(self.by)

    def instrument_bars(self, sym: str, resolution: str, symbols: Sequence[str]) -> Table:
        """Bars of one instrument at `resolution`; a spread's bars are built once and shared."""
        if not is_expression(sym):
            return for_symbol(self.bars(resolution), sym, by=self.by)
        key = (sym, resolution)
        if key not in self._spreads:
            tk = self._spreads.get((sym, "tick")) or spread_ticks(self.ticks, sym, symbols, by=self.by)
            self._spreads[(sym, "tick")] = tk
            self._spreads[key] = bars(tk, resolution, by=self.by, size_col=None)
        return self._spreads[key].drop_columns(self.by)

    def standard_bars(self, resolution: str) -> Table:
        """bars_<resolution> with every standard study (data.STUDIES), built on first use from the shared bars."""
        if resolution not in self._standard:
            self._standard[resolution] = with_studies(self.bars(resolution), list(STUDIES.values()), by=self.by)
        return self._standard[resolution]

    def standard(self) -> dict[str, Table]:
        """L0's standard set: {'bars_1m': ..., ...} at every standard resolution (data.standard_set's tables)."""
        return {f"bars_{r}": self.standard_bars(r) for r in self.resolutions}

    def alert_values(self, resolutions: Sequence[str] = ("1m", "5m")) -> Table:
        """What alerts watch, one row per (Sym, Source): Source "Last" is the last trade; "<column>@<res>"
        a standard study's value on the forming bar. Columns Sym, Source, Value, ValueNs."""
        if self._values is None:
            from deephaven import merge

            parts = [self.quotes.view([f"Sym = {self.by}", "Source = `Last`", "Value = Last", "ValueNs = epochNanos(Updated)"])]
            for res in resolutions:
                last = self.standard_bars(res).last_by(self.by)
                for col in self._study_columns():
                    parts.append(last.view([f"Sym = {self.by}", f"Source = `{col}@{res}`", f"Value = (double) {col}",
                                            "ValueNs = epochNanos(Updated)"]))
            self._values = merge(parts)
        return self._values

    def alert_sources(self, resolutions: Sequence[str] = ("1m", "5m")) -> list[dict]:
        """The sources an alert can watch, for UIs: [{key, label}]."""
        out = [{"key": "Last", "label": "Price (last trade)"}]
        for res in resolutions:
            out += [{"key": f"{c}@{res}", "label": f"{c.replace('_', ' ')} ({res})"} for c in self._study_columns()]
        return out

    @staticmethod
    def _study_columns() -> list[str]:
        return [c for s in STUDIES.values() if s.pane != "volume" for c in s.columns() if not c.endswith("_Mid")]

    @property
    def symbols_table(self) -> Table:
        """One row per symbol seen so far (ticking)."""
        return self._symbols

    def standard_tables(self) -> dict[str, Table]:
        """L0's standard set (bars_<res> with every standard study) for clients that only filter,
        `quotes` for their watchlists, `sessions` (trading hours per symbol) and `instruments` (tick sizes)."""
        from .instruments import instruments_table

        return {**self.standard(), "quotes": self.quotes, "sessions": sessions_table(), "instruments": instruments_table()}


# ----------------------------------------------------------------------------- elements

def toolbar_element(value: dict, options: dict, on_change):
    """The TradingView-style toolbar (@acme/chart-react's ChartToolbar, rendered by the dh-chart-ext JS
    plugin as `acme.chart.Toolbar`). Controlled: `value` and `options` use the component's camelCase
    keys; `on_change(patch)` gets the changed keys."""
    from deephaven.ui.elements import BaseElement

    return BaseElement("acme.chart.Toolbar", value=value, options=options, on_change=on_change)


def frame_element(chart, *, title: str, studies: list, study_types: dict, on_study_change, multi: bool, active: bool,
                  on_activate, sync_crosshair: str, sync_time: str, view_window: Optional[int] = None,
                  bar_seconds: Optional[int] = None, session: Optional[dict] = None, on_history=None,
                  history_complete: bool = False):
    """One chart with its legend and navigation (`acme.chart.Frame`): study rows report show/hide,
    settings and remove through `on_study_change(patch)`; in a multi-chart layout a click calls
    `on_activate()`. `view_window` (seconds) is the view to show; scrolling into the oldest bar
    calls `on_history(before)` (before: UTC seconds to reach, or None for "more").
    `bar_seconds` drives the bar-close countdown; `session` the market status."""
    from deephaven.ui.elements import BaseElement

    return BaseElement(
        "acme.chart.Frame", chart, title=title, studies=studies, study_types=study_types, on_study_change=on_study_change,
        multi=multi, active=active, on_activate=on_activate, sync_crosshair=sync_crosshair, sync_time=sync_time,
        view_window=view_window, bar_seconds=bar_seconds, session=session, on_history=on_history,
        history_complete=history_complete,
    )


def services_element(*, annotations: Optional[Table] = None, alerts: Optional[Table] = None,
                     sessions: Optional[dict] = None, user: Optional[str] = None):
    """Page services for the chart extensions (`acme.chart.Services`; draws only notifications):
    the annotation store, the alert store (alert lines, context-menu alerts, toasts when one fires)
    and the session calendar. `user` names the user; None lets the browser use a per-browser id."""
    from deephaven.ui.elements import BaseElement

    props = {k: v for k, v in {"annotations": annotations, "alerts": alerts, "sessions": sessions, "owner": user}.items()
             if v is not None}
    return BaseElement("acme.chart.Services", **props)


def alerts_panel_element(*, symbol: Optional[str], sources: list, symbols: list):
    """The alerts panel (`acme.chart.AlertsPanel`): the user's alerts, and a form for new ones on the
    symbol shown. Reads and writes the page's alert store (from services_element)."""
    from deephaven.ui.elements import BaseElement

    return BaseElement("acme.chart.AlertsPanel", symbol=symbol, sources=sources, symbols=symbols)


def watchlist_element(table: Table, *, value: Optional[str], on_select, title: str = "Watchlist", symbol_column: str = "Sym"):
    """The watchlist (`acme.chart.Watchlist`) over a quotes table (data.quotes); a click calls on_select(symbol)."""
    from deephaven.ui.elements import BaseElement

    return BaseElement("acme.chart.Watchlist", table=table, value=value, on_select=on_select, title=title,
                       symbol_column=symbol_column)


# ----------------------------------------------------------------------------- state

def new_study(type_: str, params: Optional[dict] = None, color: Optional[str] = None, visible: bool = True) -> dict:
    """A study instance: {id, type, params, color, visible}. Several of one type can coexist."""
    st = STUDY_TYPES[type_]
    return {"id": uuid.uuid4().hex[:8], "type": type_, "params": {**st.params, **(params or {})}, "color": color,
            "visible": visible}


def _next_color(studies: list) -> str:
    used = {s.get("color") for s in studies}
    return next((c for c in STUDY_COLORS if c not in used), STUDY_COLORS[len(studies) % len(STUDY_COLORS)])


def add_study(studies: list, type_: str, params: Optional[dict] = None) -> list:
    # Volume keeps its own grey (None); lines get the next unused house color
    color = None if type_ == "Volume" else _next_color(studies)
    return [*studies, new_study(type_, params, color)]


def studies_from_keys(keys: Sequence[str]) -> list:
    """Instances from standard keys ("EMA_20", "BB_20_2", "Volume"): the arguments' and v1 settings' form."""
    out: list = []
    for k in keys:
        t, *vals = str(k).split("_")
        if t not in STUDY_TYPES:
            continue
        defaults = STUDY_TYPES[t].params
        params = {name: type(defaults[name])(float(v)) for name, v in zip(defaults, vals)}
        out = add_study(out, t, params)
    return out


def _cast_params(type_: str, params: dict) -> dict:
    defaults = STUDY_TYPES[type_].params
    out = {}
    for k, d in defaults.items():
        try:
            v = type(d)(float(params.get(k, d)))
        except (TypeError, ValueError):
            v = d
        out[k] = max(v, 1) if isinstance(d, int) else (v if v > 0 else d)
    return out


def new_pane(symbol, resolution, range_, chart_type, studies, scale) -> dict:
    return {"symbol": symbol, "resolution": resolution, "range": range_, "chartType": chart_type,
            "studies": studies, "scale": scale, "compare": []}


def restore_state(saved: dict, default_pane: dict, watchlist: bool) -> dict:
    """The workspace state from saved settings (v2; v1 was one pane with study keys), else defaults."""
    if saved.get("version") == 2 and saved.get("panes"):
        panel = saved.get("panel", "watchlist" if saved.get("watchlist", watchlist) else None)
        return {
            "version": 2,
            "layout": saved.get("layout") if saved.get("layout") in LAYOUTS else "1",
            "active": int(saved.get("active", 0)),
            "sync": {**DEFAULT_SYNC, **(saved.get("sync") or {})},
            "panel": panel if panel in PANELS else None,
            "panes": [{**default_pane, **{k: v for k, v in p.items() if k != "lookback"},
                       "studies": [s for s in p.get("studies", []) if s.get("type") in STUDY_TYPES]}
                      for p in saved["panes"]],
        }
    pane = dict(default_pane)
    if saved:  # v1
        pane.update({k2: saved[k1] for k1, k2 in [("symbol", "symbol"), ("resolution", "resolution"), ("range", "range"),
                                                  ("chart_type", "chartType"), ("scale", "scale")] if k1 in saved})
        if "studies" in saved:
            pane["studies"] = studies_from_keys(saved["studies"])
    return {"version": 2, "layout": "1", "active": 0, "sync": dict(DEFAULT_SYNC),
            "panel": "watchlist" if watchlist else None, "panes": [pane]}


PANELS = {"watchlist": "Watchlist", "alerts": "Alerts"}


def _pane_patch(pane: dict, patch: dict) -> dict:
    p = dict(pane)
    for k in ("symbol", "resolution", "range", "chartType", "scale"):
        if k in patch:
            p[k] = patch[k]
    if "symbol" in patch or "resolution" in patch:
        p.pop("lookback", None)  # history loaded for the old instrument does not carry over
    if patch.get("addStudy") in STUDY_TYPES:
        p["studies"] = add_study(p["studies"], patch["addStudy"])
    if patch.get("addCompare"):
        cur = p.get("compare") or []
        sym = str(patch["addCompare"])
        if sym != p.get("symbol") and all(c["sym"] != sym for c in cur):
            used = {c["color"] for c in cur}
            color = next((c for c in COMPARE_COLORS if c not in used), COMPARE_COLORS[0])
            p["compare"] = [*cur, {"sym": sym, "color": color, "visible": True}]
    return p


def apply_toolbar(state: dict, patch: dict, symbols: Sequence[str]) -> dict:
    """The state after a toolbar (or watchlist) change: pane settings go to the active pane, or to
    every pane for symbol/interval when synced; layout, sync and watchlist are workspace settings."""
    s = dict(state)
    panes = list(s["panes"])
    n = LAYOUTS[s["layout"]][1]
    active = min(s["active"], n - 1)
    sync = s["sync"]
    for i in range(len(panes)):
        mine = {k: v for k, v in patch.items() if i == active or (k == "symbol" and sync["symbol"]) or
                (k == "resolution" and sync["interval"])}
        if mine:
            panes[i] = _pane_patch(panes[i], mine)
    if "layout" in patch and patch["layout"] in LAYOUTS:
        s["layout"] = patch["layout"]
        want = LAYOUTS[s["layout"]][1]
        base = panes[active]
        while len(panes) < want:
            # a new pane copies the active one, on the next symbol not yet shown (unless symbols are synced)
            shown = {p["symbol"] for p in panes}
            sym = base["symbol"] if sync["symbol"] else next((x for x in symbols if x not in shown), base["symbol"])
            panes.append({**base, "symbol": sym, "studies": [{**st, "id": uuid.uuid4().hex[:8]} for st in base["studies"]]})
        s["active"] = min(active, want - 1)
    if "sync" in patch:
        s["sync"] = {**sync, **{k: bool(v) for k, v in patch["sync"].items() if k in DEFAULT_SYNC}}
        if s["sync"]["symbol"] and not sync["symbol"]:  # turning symbol sync on lines the panes up
            panes = [{**p, "symbol": panes[active]["symbol"]} for p in panes]
        if s["sync"]["interval"] and not sync["interval"]:
            panes = [{**p, "resolution": panes[active]["resolution"]} for p in panes]
    if "panel" in patch:
        s["panel"] = patch["panel"] if patch["panel"] in PANELS else None
    if "watchlist" in patch:  # the toolbar's older single toggle
        s["panel"] = "watchlist" if patch["watchlist"] else None
    s["panes"] = panes
    return s


def valid_instrument(sym, symbols: Sequence[str]) -> bool:
    """A known symbol, or an expression over known symbols."""
    if not is_expression(str(sym)):
        return str(sym) in symbols
    try:
        parse_expression(str(sym), symbols)
        return True
    except ValueError:
        return False


def apply_study_patch(pane: dict, patch: dict) -> dict:
    """The pane after a legend action: {id, visible} / {id, remove} / {id, params, color}.
    Compared symbols have ids "cmp:<sym>"."""
    pid = str(patch.get("id", ""))
    if pid.startswith("cmp:"):
        sym = pid[4:]
        cmp_ = []
        for c in pane.get("compare") or []:
            if c["sym"] != sym:
                cmp_.append(c)
            elif not patch.get("remove"):
                cmp_.append({**c, **({"visible": bool(patch["visible"])} if "visible" in patch else {}),
                             **({"color": str(patch["color"])} if patch.get("color") else {})})
        return {**pane, "compare": cmp_}
    out = []
    for st in pane["studies"]:
        if st["id"] != patch.get("id"):
            out.append(st)
            continue
        if patch.get("remove"):
            continue
        st = dict(st)
        if "visible" in patch:
            st["visible"] = bool(patch["visible"])
        if patch.get("params"):
            st["params"] = _cast_params(st["type"], patch["params"])
        if patch.get("color"):
            st["color"] = str(patch["color"])
        out.append(st)
    return {**pane, "studies": out}


def _interval_group(resolution: str) -> str:
    if resolution == "tick":
        return "Ticks"
    secs = resolution_secs(resolution)
    return "Seconds" if secs < 60 else "Minutes" if secs < 3600 else "Hours" if secs < 86400 else "Days"


# ----------------------------------------------------------------------------- components

def default_lookback(resolution: str, range_: str) -> Optional[int]:
    """Seconds of history to load first: the resolution's default, at least twice the view window."""
    window = RANGES.get(range_)
    if window is None:
        return None  # "All": everything there is
    return max(DEFAULT_LOOKBACK.get(resolution, 86400), 2 * window)


@ui.component
def chart_pane(source: ChartSource, pane: dict, index: int, active: bool, multi: bool, sync_crosshair: str,
               sync_time: str, on_activate, on_pane, symbols: list):
    """One chart of the workspace, with its legend. Rebuilds only when its own settings change;
    loading more history moves its Lookback instead, so the chart and the user's view stay."""
    sym, res, rng, ctype, scl = pane["symbol"], pane["resolution"], pane["range"], pane["chartType"], pane["scale"]
    compare = [c for c in pane.get("compare") or [] if c.get("visible", True)] if res != "tick" else []
    studies_key = json.dumps([pane["studies"], compare], sort_keys=True)

    # history: how far back this chart's table reaches. The pane's "lookback" (seconds) grows as the
    # user scrolls back; a new symbol or interval starts over (apply_toolbar drops it)
    base = default_lookback(res, rng)
    key = f"{sym}|{res}"
    secs = None if base is None else max(base, int(pane.get("lookback") or 0))
    if secs is not None and secs > MAX_LOOKBACK:
        secs = None
    lookback = ui.use_memo(lambda: Lookback(None if secs is None else dh_now().minusSeconds(secs)), [])

    def move_lookback():
        want = None if secs is None else dh_now().minusSeconds(secs)
        if (want is None) != (lookback.since is None) or (want is not None and abs(
                want.getEpochSecond() - lookback.since.getEpochSecond()) > 60):
            lookback.set(want)

    ui.use_effect(move_lookback, [secs, key])

    def on_history(before=None):
        import time as _t

        cur = secs if secs is not None else MAX_LOOKBACK
        if before is not None:
            want = int(_t.time() - float(before)) + 3600  # reach the date, plus an hour before it
        else:
            want = cur * 2  # scrolling back: double what is loaded
        on_pane(index, lambda p: {**p, "lookback": max(cur, want)})

    # a spread has no volume: its Volume study is left out (and kept for when the pane shows a symbol again)
    usable = [s for s in pane["studies"] if not (s["type"] == "Volume" and sym and is_expression(sym))]
    shown = [s for s in usable if s.get("visible", True)] if res != "tick" else []

    # the chart's tables are shared nodes (source.nodes): this symbol's bars with the shown studies, and each
    # compared symbol's bars. Another pane or API client asking for the same chart gets the same tables; they
    # are held while this pane shows them (released when its settings change or it unmounts)
    nodes = source.nodes
    node_key = json.dumps([sym, res, [[s["type"], s["params"]] for s in shown], [c["sym"] for c in compare]], sort_keys=True)

    def acquire():
        if sym is None or res == "tick":
            return None
        main = nodes.chart(sym, res, [{"type": s["type"], "params": s["params"]} for s in shown], symbols=symbols)
        cmps = [nodes.bars(c["sym"], res, symbols=symbols) for c in compare]
        return {"keys": [main[0], *(k for k, _ in cmps)], "table": main[1], "compare": [t for _, t in cmps]}

    held = ui.use_memo(acquire, [node_key])

    def hold():
        keys = held["keys"] if held else []
        return lambda: [nodes.release(k) for k in keys]

    ui.use_effect(hold, [node_key])

    def build():
        if sym is None:
            return ui.text("Waiting for data...")
        if res == "tick":
            return tick_chart(in_range(source.instrument_ticks(sym, symbols), lookback), title=sym, scale=scl)
        return price_chart(
            held["table"], since=lookback, chart_type=ctype, scale=scl, title=sym,
            studies=[STUDY_TYPES[s["type"]].build(s["params"]) for s in shown], colors=[s.get("color") for s in shown],
            compare=[(c["sym"], t, c["color"]) for c, t in zip(compare, held["compare"])],
        )

    chart = ui.use_memo(build, [node_key, ctype, studies_key, scl])

    legend = [] if res == "tick" else [
        {"id": s["id"], "type": s["type"], "params": s["params"], "label": study_label(s["type"], s["params"]),
         "columns": STUDY_TYPES[s["type"]].build(s["params"]).columns(), "color": s.get("color"),
         "visible": s.get("visible", True)}
        for s in usable
    ] + [
        {"id": f"cmp:{c['sym']}", "label": c["sym"], "columns": [c["sym"]], "color": c["color"],
         "visible": c.get("visible", True), "kind": "compare"}
        for c in (pane.get("compare") or [])
    ]
    study_types = {k: {"label": t.label, "params": t.params} for k, t in STUDY_TYPES.items()}

    return frame_element(
        ui.view(chart, flex_grow=1, min_height=0, min_width=0),
        title=sym or "", studies=legend, study_types=study_types,
        on_study_change=lambda patch: on_pane(index, lambda p: apply_study_patch(p, patch)),
        multi=multi, active=active, on_activate=lambda: on_activate(index),
        sync_crosshair=sync_crosshair, sync_time=sync_time,
        view_window=RANGES.get(rng), bar_seconds=None if res == "tick" else resolution_secs(res),
        session=session_for(sym) if sym else None, on_history=on_history, history_complete=secs is None,
    )


def _grid(layout: str, cells: list):
    def row(*c):
        return ui.flex(*c, direction="row", gap="size-50", flex_grow=1, min_height=0, min_width=0)

    def col(*c):
        return ui.flex(*c, direction="column", gap="size-50", flex_grow=1, min_height=0, min_width=0)

    if layout == "2h":
        return row(*cells[:2])
    if layout == "2v":
        return col(*cells[:2])
    if layout == "3h":
        return row(cells[0], col(cells[1], cells[2]))
    if layout == "4":
        return col(row(cells[0], cells[1]), row(cells[2], cells[3]))
    return col(cells[0])


@ui.component
def chart_workspace(
    source: ChartSource,
    symbol: Optional[str] = None,
    resolution: str = "1s",
    range_: str = "30m",
    chart_type: str = "candle",
    studies: Sequence[str] = ("Volume",),
    scale: str = "normal",
    annotations: Optional[Table] = None,
    user: Optional[str] = None,
    layout: str = "default",
    watchlist: bool = False,
    alerts: Optional[Table] = None,
    follow: Optional[str] = None,
    on_symbol=None,
    resolutions: Optional[Sequence[str]] = None,
):
    """Toolbar + one to four charts + optional watchlist. The arguments are the first-time defaults
    (`studies` as standard keys: "EMA_20", "BB_20_2", "RSI_14", "Volume", ...).

    follow, on_symbol: link the workspace to other panels. When `follow` changes, the active chart shows that
    symbol; when the user picks a symbol here (toolbar or watchlist), `on_symbol(sym)` is called.
    resolutions: the toolbar's interval menu (default: tick and the standard ones).

    annotations: the app's annotation table (store.annotation_table); with it, price lines and
    drawings persist per symbol, per `user` (None: a per-browser id), and the workspace's state
    (layout, panes, studies, sync, watchlist) is saved per (`layout`, `user`) and restored on open.
    Give each panel of a dashboard its own `layout`. With user=None the state is saved under
    "default", shared by every anonymous viewer of that layout (Community has no user identity).

    alerts: the app's alert table (alerts.alert_table, evaluated by an alerts.AlertEngine); with it,
    charts offer alerts (context menu, Alt+A, on drawings), show them, and the Alerts panel lists them."""
    owner = user or "default"
    symbols = sorted(str(s) for s in ui.use_column_data(source.symbols_table)) if source.symbols_table is not None else []

    def initial():
        saved = (load_settings(annotations, layout, owner) if annotations is not None else None) or {}
        return restore_state(saved, new_pane(symbol, resolution, range_, chart_type, studies_from_keys(studies), scale), watchlist)

    state, set_state = ui.use_state(initial)
    wid = ui.use_memo(lambda: uuid.uuid4().hex[:8], [])

    # save the state when it changes (not on the first render: nothing changed yet)
    state_key = json.dumps(state, sort_keys=True)
    mounted = ui.use_ref(False)

    def save():
        if not mounted.current:
            mounted.current = True
            return
        if annotations is not None:
            save_settings(annotations, layout, owner, state)

    ui.use_effect(save, [state_key])

    n = LAYOUTS.get(state["layout"], LAYOUTS["1"])[1]
    multi = n > 1
    active = min(state["active"], n - 1)
    # panes without a symbol yet (first open, no data) take the first symbols
    panes = [p if p["symbol"] else {**p, "symbol": symbols[i % len(symbols)] if symbols else None}
             for i, p in enumerate(state["panes"][:n])]
    cur = panes[active]

    def on_change(patch: dict):
        patch = dict(patch)
        for k in ("symbol", "addCompare"):  # ignore unknown symbols and unreadable expressions
            if k in patch and not valid_instrument(patch[k], symbols):
                patch.pop(k)
        set_state(lambda s: apply_toolbar(s, patch, symbols))
        if on_symbol is not None and patch.get("symbol") and patch["symbol"] != cur["symbol"]:
            on_symbol(patch["symbol"])

    def follow_symbol():
        if follow and follow != cur["symbol"] and valid_instrument(follow, symbols):
            set_state(lambda s: apply_toolbar(s, {"symbol": follow}, symbols))

    ui.use_effect(follow_symbol, [follow, len(symbols)])

    def on_pane(i: int, f):
        set_state(lambda s: {**s, "panes": [f(p) if j == i else p for j, p in enumerate(s["panes"])]})

    def on_activate(i: int):
        set_state(lambda s: s if s["active"] == i else {**s, "active": i})

    sync = state["sync"]
    cells = [
        chart_pane(source, p, i, i == active, multi, "page" if sync["crosshair"] else "none",
                   f"ws-{wid}" if sync["time"] else "none", on_activate, on_pane, symbols)
        for i, p in enumerate(panes)
    ]

    # the toolbar shows and edits the active pane
    options = {
        "symbols": [{"key": s, "label": s} for s in symbols],
        "resolutions": [{"key": r, "label": "Tick" if r == "tick" else r, "group": _interval_group(r)}
                        for r in (resolutions or ["tick", *resolution_options()])],
        "ranges": [{"key": k, "label": k} for k in RANGES],
        "chartTypes": [{"key": k, "label": v} for k, v in CHART_TYPES.items()],
        "studies": [] if cur["resolution"] == "tick" else [
            {"key": k, "label": t.label, "group": t.group} for k, t in STUDY_TYPES.items()
        ],
        "addStudies": True,
        "expressions": True,
        "compare": cur["resolution"] != "tick",
        "scales": True,
        "layouts": [{"key": k, "label": v[0]} for k, v in LAYOUTS.items()],
        "sync": True,
        "panels": [{"key": k, "label": v} for k, v in PANELS.items() if k != "alerts" or alerts is not None],
    }
    effective_scale = "percent" if [c for c in cur.get("compare") or [] if c.get("visible", True)] and cur["scale"] == "normal" \
        else cur["scale"]
    value = {
        "symbol": cur["symbol"], "resolution": cur["resolution"], "range": cur["range"], "chartType": cur["chartType"],
        "studies": [s["type"] for s in cur["studies"]], "scale": effective_scale, "layout": state["layout"],
        "sync": sync, "panel": state.get("panel") or "",
    }

    main = ui.flex(toolbar_element(value, options, on_change), _grid(state["layout"], cells),
                   direction="column", gap="size-0", flex_grow=1, min_width=0, height="100%")
    body = [main]
    panel = state.get("panel")
    if panel == "watchlist":
        body.append(ui.view(watchlist_element(source.quotes, value=cur["symbol"], on_select=lambda s: on_change({"symbol": s})),
                            width="size-3400", flex_shrink=0, height="100%"))
    elif panel == "alerts" and alerts is not None:
        body.append(ui.view(alerts_panel_element(symbol=cur["symbol"], sources=source.alert_sources(), symbols=symbols),
                            width="size-3600", flex_shrink=0, height="100%"))
    parts = [ui.flex(*body, direction="row", gap="size-0", flex_grow=1, height="100%", min_height=0)]
    # draws only notifications: feeds the chart extensions their stores and the session calendar
    parts.append(services_element(annotations=annotations, alerts=alerts, sessions=SESSIONS, user=user))
    return ui.flex(*parts, direction="column", gap="size-0", flex_grow=1, height="100%")
