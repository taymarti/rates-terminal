"""
The rates terminal's Deephaven app: the market simulator publishing ticks and every table the terminal's panels read,
charts on demand through dh_chart_ext's chart API, the hypothetical desk, workspaces, drawings, alerts, backtests,
the update graph, and versions A and B's deephaven.ui panels. Deephaven runs it at startup (application mode:
app.d/terminal.py calls build() and publishes what it returns).

  ticks                       Timestamp Sym Price Size: five days (20-second steps, the last hour by the second), then live
  long_ticks                  the two years before, four a day (daily and weekly charts)
  bars_1m, bars_5m, bars_1d   bars of every symbol (charts roll up from these; finer ones are built per symbol from ticks)
  quotes, watchlist_core, book, trades, volume_profile, instruments, sessions
  ust_cash, futures_contracts, deliverable_basket, stir_strip, fomc_path, swap_curve, ust_curve, repo_rates, rv_monitor
  econ_calendar, news, treasury_auctions, cftc_cot
  books, positions, book_risk, pnl_attribution, pnl_timeseries, rfq_blotter, desk_trades, rfq_flow, security_master,
  cusip_auctions              the desk (rates_terminal.desk)
  datasets                    the catalog
  chart_api                   dh_chart_ext's chart API with the terminal's methods (rates_terminal.tapi)
  chart_nodes                 the shared chart nodes, live
  chart_annotations, chart_alerts, alerts_fired, workspaces, backtest_runs (+ bt<N>_*)   saved in the data directory
  terminal_a                  version A: the terminal as one deephaven.ui dashboard
  b_universe, b_chart, ...    version B: each panel as its own deephaven.ui widget, for b.html

The market steps every TERMINAL_STEP_MS (500) on its own thread; analytics and the desk's P&L every second, the
relative-value monitor every minute. Saved state goes to TERMINAL_DATA_DIR (the launcher sets it; see cli.py).
"""
from __future__ import annotations

import os
import pathlib
import time


def data_dir() -> pathlib.Path:
    d = pathlib.Path(os.environ.get("TERMINAL_DATA_DIR") or pathlib.Path.home() / ".rates-terminal")
    d.mkdir(parents=True, exist_ok=True)
    return d


def build() -> dict:
    """Build the whole app; returns what to publish (and, under "_app", what must stay alive)."""
    from deephaven import new_table, perfmon
    from deephaven.column import bool_col, long_col, string_col

    # The graph explorer's edges: ancestry is only logged for nodes built after this log is opened, so first
    perfmon.update_performance_ancestors_log()

    from dh_chart_ext import instruments as dh_instruments
    from dh_chart_ext import sessions as dh_sessions
    from dh_chart_ext.alerts import AlertEngine, alert_table, alerts_log
    from dh_chart_ext.api import ChartApi
    from dh_chart_ext.backtests import BacktestService
    from dh_chart_ext.introspection import register_introspection
    from dh_chart_ext.live import Clock
    from dh_chart_ext.store import annotation_table
    from dh_chart_ext.workspace import ChartSource
    from dh_chart_ext.workspaces import WorkspaceStore

    from .catalog import DATASETS
    from .desk import Desk
    from .live import LiveTables
    from .market import Market
    from .tapi import register_terminal, term_meta
    from .ui import build as build_ui
    from .universe import BY_SYM, FUTURES_ROLLS, INSTRUMENTS, SESSIONS

    t0 = time.time()
    data = data_dir()
    market = Market()
    live = LiveTables(market)
    desk = Desk(market, live)

    # tick sizes for dh_chart_ext's chart replies and its spreads; each exchange's calendar for its session shading
    dh_instruments.INSTRUMENTS.update({i.sym: (i.tick, i.name) for i in INSTRUMENTS if i.tick})
    dh_sessions.SESSIONS.update({i.sym: SESSIONS[i.session] for i in INSTRUMENTS})
    dh_sessions.SESSIONS.update({r: SESSIONS[BY_SYM[r.split("-")[0]].session] for r in FUTURES_ROLLS})

    # charts: the standard bar tables (every symbol) and nodes built on demand from them; daily and weekly from the long history
    source = ChartSource(live.ticks, resolutions=("1m", "5m"), history=live.long_ticks, symbols=[i.sym for i in INSTRUMENTS])
    chart_api = ChartApi(source, meta=term_meta, lease_s=float(os.environ.get("CHART_API_LEASE_S", "90")))
    bars_1m, bars_5m = source.bars("1m"), source.bars("5m")

    # workspaces, drawings, alerts, backtests (bars_1m is the backtests' data), the update graph
    ws_store = WorkspaceStore(path=data / "workspaces.parquet")
    ws_store.register(chart_api)
    chart_annotations = annotation_table(path=data / "annotations.parquet")
    chart_alerts = alert_table(path=data / "alerts.parquet")
    # alerts watch the last price and the standard studies on the forming 5-minute bar (one resolution: ~200 symbols)
    alert_engine = AlertEngine(chart_alerts, source.alert_values(resolutions=("5m",)))
    backtests = BacktestService(dh_port=int(os.environ.get("DH_PORT", "10050")), path=data / "strategies.parquet", archive_dir=data / "runs",
                                engine_linger_s=float(os.environ.get("BT_ENGINE_LINGER_S", "600")))
    backtests.register(chart_api)
    register_introspection(chart_api)

    # every table the terminal reads, by name, and the catalog of them
    published = {**live.tables(), **desk.tables(), "bars_1m": bars_1m, "bars_5m": bars_5m, "bars_1d": source.nodes.daily,
                 "chart_nodes": source.nodes.cache.metrics_table(), "chart_annotations": chart_annotations, "chart_alerts": chart_alerts,
                 "alerts_fired": alerts_log(chart_alerts), "workspaces": ws_store.table, "backtest_runs": backtests.runs}
    published["datasets"] = new_table([
        string_col("Id", [d["id"] for d in DATASETS]), string_col("Title", [d["title"] for d in DATASETS]), string_col("Group", [d["group"] for d in DATASETS]),
        string_col("Kind", [d["kind"] for d in DATASETS]), string_col("Table", [d.get("table", "") for d in DATASETS]),
        string_col("Frequency", [d["frequency"] for d in DATASETS]), bool_col("Live", [d["live"] for d in DATASETS]),
        long_col("Symbols", [len(d.get("symbols", [])) for d in DATASETS]), string_col("Description", [d["description"] for d in DATASETS]),
    ])
    register_terminal(chart_api, market, live, desk, lambda: published)

    # versions A and B: the same panels as deephaven.ui components (rates_terminal.ui). A is one dashboard, terminal_a
    # (/iframe/widget/?name=terminal_a, or the IDE's panels menu); B is each panel on its own, b_<panel>, for b.html.
    terminal_a, b_panels, link_hub = build_ui(source=source, tables=published, desk=desk, market=market,
                                              annotations=chart_annotations, alerts=chart_alerts)

    # the market's clock
    last = {"analytics": 0, "rv": 0}

    def step(now: int, _dt: int) -> None:
        prev = market.t
        market.live(now)
        dt = max(1, now - prev)
        live.on_step(now, dt)
        desk.tick(now, dt)
        if now - last["analytics"] >= 1000:
            last["analytics"] = now
            live.refresh_analytics(now)
        if now - last["rv"] >= 60_000:
            last["rv"] = now
            live.refresh_rv()

    clock = Clock(step, int(os.environ.get("TERMINAL_STEP_MS", "500")), name="rates-terminal-market").start()
    (data / "ready.txt").write_text(time.strftime("%Y-%m-%dT%H:%M:%S"))  # start.ps1 waits for it
    print(f"rates terminal: {len(INSTRUMENTS)} symbols + {len(FUTURES_ROLLS)} rolls, {len(published)} tables, up in {time.time() - t0:.1f} s"
          f" (data: {data})", flush=True)
    keep = dict(market=market, live=live, desk=desk, source=source, workspaces=ws_store, alert_engine=alert_engine,
                backtests=backtests, link_hub=link_hub, clock=clock)
    return {**published, "chart_api": chart_api, "terminal_a": terminal_a, **b_panels, "terminal_stats": clock.stats, "_app": keep}
