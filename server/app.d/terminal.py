"""
The rates terminal's Deephaven backend: the market simulator (rates_terminal, the same model as the in-page mock)
publishing ticks and every table the terminal's panels read, charts on demand through dh_chart_ext's chart API,
the hypothetical desk, workspaces, drawings, alerts, backtests and the update graph.

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
  chart_annotations, chart_alerts, alerts_fired, workspaces, backtest_runs (+ bt<N>_*)   saved under data/
  terminal_a                  version A: the terminal as one deephaven.ui dashboard
  b_universe, b_chart, ...    version B: each panel as its own deephaven.ui widget, for b.html

The market steps every TERMINAL_STEP_MS (500) on its own thread; analytics and the desk's P&L every second, the
relative-value monitor every minute. Run it with server/run.py (see README.md).
"""
import os
import pathlib
import sys
import time

import jpy
from deephaven import new_table, perfmon
from deephaven.column import bool_col, long_col, string_col

# The graph explorer's edges: ancestry is only logged for nodes built after this log is opened, so first
perfmon.update_performance_ancestors_log()

_app_dir = pathlib.Path(jpy.get_type("java.lang.System").getProperty("deephaven.application.dir"))
sys.path.insert(0, str(_app_dir.parent))  # server/: the rates_terminal package
_data_dir = pathlib.Path(os.environ.get("TERMINAL_DATA_DIR", _app_dir.parent / "data"))
_data_dir.mkdir(parents=True, exist_ok=True)

from dh_chart_ext import instruments as _dh_instruments  # noqa: E402
from dh_chart_ext import sessions as _dh_sessions  # noqa: E402
from dh_chart_ext.alerts import AlertEngine, alert_table, alerts_log  # noqa: E402
from dh_chart_ext.api import ChartApi  # noqa: E402
from dh_chart_ext.backtests import BacktestService  # noqa: E402
from dh_chart_ext.introspection import register_introspection  # noqa: E402
from dh_chart_ext.live import Clock  # noqa: E402
from dh_chart_ext.store import annotation_table  # noqa: E402
from dh_chart_ext.workspace import ChartSource  # noqa: E402
from dh_chart_ext.workspaces import WorkspaceStore  # noqa: E402

from rates_terminal.catalog import DATASETS  # noqa: E402
from rates_terminal.desk import Desk  # noqa: E402
from rates_terminal.live import LiveTables  # noqa: E402
from rates_terminal.market import Market  # noqa: E402
from rates_terminal.tapi import register_terminal, term_meta  # noqa: E402
from rates_terminal.ui import build as build_ui  # noqa: E402
from rates_terminal.universe import BY_SYM, FUTURES_ROLLS, INSTRUMENTS, SESSIONS  # noqa: E402

_t0 = time.time()
_market = Market()
_live = LiveTables(_market)
_desk = Desk(_market, _live)

# tick sizes for dh_chart_ext's chart replies and its spreads; each exchange's calendar for its session shading
_dh_instruments.INSTRUMENTS.update({i.sym: (i.tick, i.name) for i in INSTRUMENTS if i.tick})
_dh_sessions.SESSIONS.update({i.sym: SESSIONS[i.session] for i in INSTRUMENTS})
_dh_sessions.SESSIONS.update({r: SESSIONS[BY_SYM[r.split("-")[0]].session] for r in FUTURES_ROLLS})

# charts: the standard bar tables (every symbol) and nodes built on demand from them; daily and weekly from the long history
_symbols = [i.sym for i in INSTRUMENTS]
_source = ChartSource(_live.ticks, resolutions=("1m", "5m"), history=_live.long_ticks, symbols=_symbols)
chart_api = ChartApi(_source, meta=term_meta, lease_s=float(os.environ.get("CHART_API_LEASE_S", "90")))
chart_nodes = _source.nodes.cache.metrics_table()
bars_1m, bars_5m = _source.bars("1m"), _source.bars("5m")
bars_1d = _source.nodes.daily

# workspaces, drawings, alerts, backtests (bars_1m is the backtests' data), the update graph
_workspaces = WorkspaceStore(path=_data_dir / "workspaces.parquet")
_workspaces.register(chart_api)
workspaces = _workspaces.table
chart_annotations = annotation_table(path=_data_dir / "annotations.parquet")
chart_alerts = alert_table(path=_data_dir / "alerts.parquet")
# alerts watch the last price and the standard studies on the forming 5-minute bar (one resolution: ~200 symbols)
alert_engine = AlertEngine(chart_alerts, _source.alert_values(resolutions=("5m",)))
alerts_fired = alerts_log(chart_alerts)
_backtests = BacktestService(dh_port=int(os.environ.get("DH_PORT", "10050")), path=_data_dir / "strategies.parquet", archive_dir=_data_dir / "runs",
                             engine_linger_s=float(os.environ.get("BT_ENGINE_LINGER_S", "600")))
_backtests.register(chart_api)
backtest_runs = _backtests.runs
register_introspection(chart_api)

# every table the terminal reads, by name, and the catalog of them
_published = {**_live.tables(), **_desk.tables(), "bars_1m": bars_1m, "bars_5m": bars_5m, "bars_1d": bars_1d,
              "chart_nodes": chart_nodes, "chart_annotations": chart_annotations, "chart_alerts": chart_alerts, "alerts_fired": alerts_fired,
              "workspaces": workspaces, "backtest_runs": backtest_runs}
datasets = new_table([
    string_col("Id", [d["id"] for d in DATASETS]), string_col("Title", [d["title"] for d in DATASETS]), string_col("Group", [d["group"] for d in DATASETS]),
    string_col("Kind", [d["kind"] for d in DATASETS]), string_col("Table", [d.get("table", "") for d in DATASETS]),
    string_col("Frequency", [d["frequency"] for d in DATASETS]), bool_col("Live", [d["live"] for d in DATASETS]),
    long_col("Symbols", [len(d.get("symbols", [])) for d in DATASETS]), string_col("Description", [d["description"] for d in DATASETS]),
])
_published["datasets"] = datasets
register_terminal(chart_api, _market, _live, _desk, lambda: _published)
globals().update(_published)

# versions A and B: the same panels as deephaven.ui components (rates_terminal.ui). A is one dashboard, terminal_a
# (/iframe/widget/?name=terminal_a, or the IDE's panels menu); B is each panel on its own, b_<panel>, for b.html.
terminal_a, _b_panels, _link_hub = build_ui(source=_source, tables=_published, desk=_desk, market=_market,
                                            annotations=chart_annotations, alerts=chart_alerts)
globals().update(_b_panels)

# ---------------------------------------------------------------- the market's clock
_last = {"analytics": 0, "rv": 0}


def _step(now: int, _dt: int) -> None:
    prev = _market.t
    _market.live(now)
    dt = max(1, now - prev)
    _live.on_step(now, dt)
    _desk.tick(now, dt)
    if now - _last["analytics"] >= 1000:
        _last["analytics"] = now
        _live.refresh_analytics(now)
    if now - _last["rv"] >= 60_000:
        _last["rv"] = now
        _live.refresh_rv()


_clock = Clock(_step, int(os.environ.get("TERMINAL_STEP_MS", "500")), name="rates-terminal-market").start()
terminal_stats = _clock.stats
(_data_dir / "ready.txt").write_text(time.strftime("%Y-%m-%dT%H:%M:%S"))  # start.ps1 waits for it
print(f"rates terminal: {len(INSTRUMENTS)} symbols + {len(FUTURES_ROLLS)} rolls, {len(_published)} tables, up in {time.time() - _t0:.1f} s", flush=True)
