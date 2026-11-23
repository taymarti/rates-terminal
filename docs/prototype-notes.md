# Prototype notes

> The notes kept while the terminal was built inside dh-charts (`terminal/`, `terminal/server/`, `web/` for version B), before it moved to this repo and its generic parts moved into the dh-charts packages. Paths and run commands refer to that layout; the findings (speeds of A, B and C, what B needs to host deephaven.ui, the plotly-express bug, the weak link hub) still hold.

# Rates terminal (prototype)

A standalone trading terminal for a universe of Treasury cash bonds, Treasury and STIR futures, swaps, funding,
raw market data and cross-asset context. It is built from this repo's packages: the app shell, the
chart extensions, the React chart kit and the Deephaven bindings.

It has two backends with the same tables and the same `chart_api`:
- **A real Deephaven server** (`terminal/server`, below): the market simulator, every table, the desk and the chart
  API run in Python on Deephaven Community; charts are dh_chart_ext's shared nodes. The page uses it when it is up.
- **The in-page mock** (`src/mock`, `src/server`): the same market and tables in TypeScript, behind a mock of the
  Deephaven JS API. The page falls back to it when no server answers, so the terminal still runs with nothing else.

```powershell
npm --prefix terminal install
npm --prefix terminal run dev          # http://localhost:4070  (?reset: the default layout; ?user=alice)
npm --prefix terminal run build        # a static dist/ that any static file server can host
```

```powershell
# the server (from dh-charts; a JDK 17 in JAVA_HOME, dh-chart-ext installed in the server's Python environment)
python ..\deephaven-compare\server\run_server.py --port 10050 --heap 6g --app-dir terminal\server\app.d
```

The dev server (and `npm run preview`) proxies `/jsapi`, `/arrow.*` and `/io.deephaven.*` to `http://localhost:10050`
(`VITE_PROXY_URL` to change it). `?server=mock` uses the mock even when the server is up; `?server=dh` never falls
back. The status chip says which one the page is on: **DEEPHAVEN** with the round trip to `chart_api`, or **MOCK**
with the mock's cycle time.

`build` needs `shell/node_modules` too (dockview): `npm --prefix shell install` once.

## Three front ends on one server: A, B and C

The same terminal exists in all three front-end groups of the repo README, on the same server and the same tables.
Each page has an **A · B · C** switch in its header.

| | Where | What draws it | Run |
|---|---|---|---|
| **A** deephaven.ui | `server/rates_terminal/ui.py`, published as `terminal_a` | Deephaven's own web UI: a deephaven.ui dashboard (golden-layout panels), Deephaven's grid, its TradingView plugin for charts (dh_chart_ext's workspace), plotly express for the curve and P&L | `http://localhost:10050/iframe/widget/?name=terminal_a`, or the IDE's Panels menu |
| **B** React + server UI objects | `web/src/TerminalB.tsx` (in `web/`, which has Deephaven's widget bootstrap) | our shell (dockview: tabs, floating, pop-out, saved workspaces); every panel is one of the server's `b_*` deephaven.ui widgets, drawn by the plugins the page loads from the server | `npm --prefix web run dev:terminal`, then `http://localhost:4071` |
| **C** React + table data | `terminal/src` (this app) | our shell, our grid, our own Lightweight Charts on the JS API's subscriptions and `chart_api` | `npm --prefix terminal run dev`, then `http://localhost:4070` |

What the comparison shows:
- **A and B share one set of panels.** `ui.py` writes each panel once as a deephaven.ui component that takes what it
  shows (symbol, dataset, book) and callbacks to change it. A puts them in one dashboard with that state at its
  root, so a row clicked in one panel drives the others in that browser. B publishes them one by one.
- **B's widgets can't share client state**, because each is its own server-rendered element. They link through the
  server instead (`LinkHub`): a click in one sets the selection there and every open B panel follows. That selection
  is one per server, not per browser (Community has no user identity to key it by), so two B pages follow each
  other. A and C have none of this: A's state is per dashboard, and C's link groups are per page.
- **Every interaction in A and B is a server round trip** (a Python callback, then a re-render and new table
  subscriptions); C changes a chart or a filter in the browser and only asks the server for new nodes.
- **A and B use Deephaven's chart plugin and grid**, with our extensions attached (drawings, sessions, alerts,
  sync); C uses our grid and draws its charts itself. The panels differ in detail: C has the bond calculator and the
  correlation heatmap, which A and B don't have yet.
- **Speed, measured here** (one machine, the server busy with its market thread): a click in A's universe reaches
  the tables in 3–5 s and the chart in about 15 s (the chart switches in a second render, after an effect); in B the
  linked widgets follow in 5–10 s (each re-renders on the server and resubscribes its tables); in C it is
  immediate in the page, and a new chart node takes well under a second. A opens in 20–40 s (every panel at once;
  the most after a restart), B in about 20 s, C in about 10 s.

What it took to host deephaven.ui widgets in our own shell (B), beyond `WidgetView`:
- A `FiberProvider` above each widget and the widget descriptor passed to the plugin component (as in `web/`'s
  `?widget=`; `widgets.tsx`).
- deephaven.ui's **table** assumes Deephaven's golden-layout dashboard around it: it reads the dashboard's
  `LayoutManager` (its event hub), the dashboard id (its grid linker keeps state in redux under it) and the panel id.
  Without them it throws, and with no error boundary one table took the whole page down. `TerminalB.tsx` gives each
  widget a stand-in layout manager (an event hub only), a page-wide dashboard id and its own panel id, inside an
  error boundary. Charts, plots and the workspace needed none of this.
- **plotly express 0.20 serves no line figure that combines `by=` with `log_x`, `markers` or `title`** (each alone
  works; the figure builds on the server but never reaches the browser, and in a dashboard it stalls every panel).
  The curve figure uses `by=` alone.
- deephaven.ui does not always unmount a component when its browser goes away, so effect cleanups may never run.
  The B link hub holds its subscribers weakly for that reason.

C's code is React with our own Lightweight Charts charts on table data (Barrage subscriptions) and the `chart_api`
request API, inside our own app shell. On the real server it is a plain group C app; the mock only stands in for the
server (A and B need the server).

## What's in it

**The universe** (Universe panel, `chart_api.datasets`): 31 datasets in 10 groups. A dataset is either
**chartable** (its symbols draw as bars at any resolution, with indicators) or **table only** (snapshots,
reference data, books, analytics). Pick one to see its description, its live symbols, its table, and the
indicators its symbols can take. Then chart a symbol with the chosen indicators, open the table, compare the
symbols, or open their correlation heatmap. A click sends a symbol to the linked panels.

| Group | Chartable | Table only |
|---|---|---|
| Rates: cash | OTR yields 1M–30Y, curve spreads and flies (3M10Y, 2s10s, 5s30s, 2s5s10s, 5s10s30s), 43 cash bonds by CUSIP (price in 32nds), TLT/IEF/SHY | `ust_cash` (yield, DV01, duration, convexity, richness, repo), `ust_curve` |
| Rates: futures | ZT, Z3N, ZF, ZN, TN, TWE, ZB, UB in Dec 2026 and Mar 2027 (first notice Nov 30: the roll is under way), the Dec/Mar rolls | `futures_contracts` (CTD, CF, DV01, implied repo, roll status), `deliverable_basket` (gross/net basis, carry, IRR, CTD rank) |
| Rates: money markets | SR3 strip, ZQ, SOFR / EFFR / GC repo | `stir_strip`, `fomc_path` (the priced path per meeting), `repo_rates` (GC by term, OTR specials) |
| Rates: swaps & inflation | SOFR OIS swaps 1Y–30Y, swap spreads, TIPS real yields, breakevens | `swap_curve` |
| Cross-asset | ES, NQ, CL, GC, 6E, 6J, Euro-Bund, MOVE, VIX | |
| Market data (raw) | | `book` (15 levels), `trades` (time and sales), `volume_profile`, `quotes`, `bars_1m`, `bars_1d` |
| Analytics | | `rv_monitor`: every symbol against its own history (z-scores, 1Y percentile, realized vol, RSI, trend, a signal) |
| Calendar & news | | `econ_calendar` (actuals fill in as they print), `news` (headline tape), `treasury_auctions` |
| Positioning | | `cftc_cot` (asset managers, leveraged funds, dealers; 26 weeks) |
| Reference & system | | `instruments`, `chart_nodes`, `chart_annotations`, `chart_alerts`, `backtest_runs` |

Any spread, ratio or butterfly typed in a chart's symbol search becomes a symbol with full history:
`ZN-ZNH7`, `ZN/ZB`, `2*ZF-ZN`, `100*(US10Y-US2Y)`.

**Indicators** (15): EMA, SMA, WMA, Bollinger, Keltner, Donchian, VWAP, Volume, RSI, Stochastic, MACD, ROC,
ATR, realized volatility and z-score. They are instances with parameters (add EMA 20 and EMA 50; change them
from the legend), and each is offered only where it makes sense: no VWAP or Volume on a yield or a spread.
For yields and spreads, ROC is a change in bp and realized vol is in bp a year.

**Panels.** New ones:
- **Chart**: any symbol or spread; candles, bars, line or area; 1s to 1W. Prices in 32nds for Treasury
  futures and cash, yields in %, spreads in bp. Also legend study settings, drawings and price lines (saved
  per user; a colleague's shared lines show), alerts, session shading, bar countdown, go-to-date, compare,
  and side panels (watchlist, alerts, an instrument card).
- **Yield curve**: Lightweight Charts' yield-curve chart, with now, 1D, 1W and 1M ago and the swap curve,
  plus tenor changes and curve spreads.
- **Bond analytics**: for the linked CUSIP, the on-the-run issue for a benchmark yield, or the CTD for a
  future. Price and yield calculator (what-if), risk, position DV01, carry and roll-down, ±100bp
  scenarios, futures hedge, and the deliverable basket.
- **DOM / time and sales**: a price ladder with depth bars, volume at price split by aggressor, the last
  trade, book imbalance, and the tape.
- **Correlation**: a heatmap of daily changes, with presets (curve, futures, swaps, cross-asset). A
  double-click charts the pair.

**The desk (hypothetical data)**, from `server/desk.ts`:
- Eight books (front end, belly, tens, long end, inflation, basis, relative value, macro overlay), seeded with
  positions in cash and futures.
- Client RFQs about every 25 seconds. About a third are won and booked to the book for the tenor, and most of
  those are then hedged with the matching future.
- **Books & P&L** panel: pick a book or the whole desk.
  - P&L since 07:00 ET, attributed to carry, roll-down, curve level / slope / curvature / front end, idio,
    futures basis, new trades, fees and the residual (convexity).
  - DV01 by key-rate bucket and exposure to each curve factor.
  - DV01 limit use, 1-day 99% VaR, and the positions. A row click sends the symbol to the linked panels.
  - Buttons open the P&L path, the RFQ blotter and the executions.
- **Security** panel, for the linked CUSIP (a benchmark gives its on-the-run issue; a future gives its CTD):
  - amount outstanding, SOMA holdings and free float
  - the desk's position by book, with DV01 and P&L
  - price, yield and idio (rich/cheap to the curve, with z-score)
  - deliverability, CF, net basis and implied repo
  - auction results and reopenings
  - client RFQs, hit ratio and client buy/sell; street volume, and the future's exchange volume
  - chart buttons for price, yield, idio, gross basis against the future, RFQ flow and the future's volume
- Each bond's yield and idio are chartable series (`<CUSIP>.Y`, `<CUSIP>.IDIO`).
- Tables: `books`, `positions`, `book_risk`, `pnl_attribution`, `pnl_timeseries`, `rfq_blotter`,
  `desk_trades`, `rfq_flow`, `security_master`, `cusip_auctions`.

**Closing things**: every panel group has a ✕ that closes all its tabs. A floating or popped-out group (backtest
results open as one) also has ⇲ to dock it back into the layout. The go-to-date popup and alert toasts have a ✕ too.

**Themes**: Black (TradingView's current near-black, dotted grid) and Midnight (deep blue, the charts on a vertical
gradient), switched live from the top bar and remembered in this browser. Every chart follows: the terminal's,
the classic workspace's, and the series charts.

From the shell, unchanged: watchlist, table (any table: server-side sort, filters, viewport), the classic chart
workspace, series chart, request form (any `chart_api` method from `describe`), update graph, and strategies with
backtests. Also docking (tabs, splits, floating, pop-out, maximize), saved workspaces per user, and link groups.

## How it works

```
 src/mock/engine.ts   tables that tick: RowSource (objects, keyed upserts, input tables), SeriesSource (columnar bars);
                      changes delivered once per 250 ms cycle, like Deephaven's update graph; cost measured per node
 src/mock/dh.ts       the Deephaven JS API the bindings use: CoreClient, IdeConnection (getObject, field updates),
                      Table (findColumn, subscribe, setViewport, applyFilter / applySort, copy, selectDistinct,
                      inputTable), FilterValue, Sort, DateWrapper, Widget
 src/mock/rpc.ts      the request-API protocol of dh_chart_ext.rpc (JSON per stream, reply tables, holds)
 src/model/           the market: a curve factor model (level, slope, curvature, policy) drives every instrument;
                      bond math (price/yield, risk, conversion factors, 32nds), the universe, indicators, history tiers
 src/server/          what the server publishes: chart nodes (shared, ref-counted, 60 s grace), the tables, chart_api,
                      alerts engine, annotations and workspaces (in localStorage), backtests (a Web Worker per run)
 src/panels/          the terminal's panels
```

The seam is `connect({ dh })` in `@acme/dh-bindings`: given a JS API module, it uses it instead of loading
`/jsapi/dh-core.js` from a server. Every shell component then runs unmodified on the mock.

The market is generated at start, seeded by the date so a reload shows the same past:
- two years of 3-hour steps into the daily tier, bridged to today's levels
- five days of 15-second steps into the minute tier
- the last hour by the second

Then it ticks every cycle. Data releases (08:30 and 10:00 ET), auctions, FOMC days and headlines move the curve.
Volatility follows the New York day.

## The Deephaven backend (`terminal/server`)

```
 app.d/terminal.app, terminal.py   the application-mode script: builds everything below, publishes it, starts the clock
 rates_terminal/universe.py        the instruments (197 symbols, CUSIPs identical to the mock's), sessions, contracts
 rates_terminal/bonds.py           Treasury math: price/yield (closed form), accrued, risk, conversion factors, 32nds
 rates_terminal/market.py          the simulator, vectorized with numpy (exact Ornstein-Uhlenbeck steps, so history can
                                   step six hours at a time); history is valued a UTC day at a time with matrix products
 rates_terminal/live.py            ticks, long_ticks and the market's tables (quotes, book, trades, analytics, calendar…)
 rates_terminal/desk.py            the hypothetical desk: books, positions, risk, P&L attribution, RFQs, security master
 rates_terminal/catalog.py         the dataset catalog
 rates_terminal/tapi.py            the terminal's chart_api methods (datasets, instruments, bond, curve, ctd, scenario,
                                   correlation, accrued, security, studies with panes and levels)
 rates_terminal/charts.py          chart nodes: dh_chart_ext's, plus daily and weekly bars from the two-year history
 rates_terminal/dhtables.py        how the app writes: Keyed (table publisher → blink → last_by: an upsert table) and
                                   Stream (table publisher → append-only, or a ring of the last N rows)
```

What it is in Deephaven terms:
- **History is tables, built at start.** `ticks` is five days of 20-second steps and the last hour by the second
  (4.2 million rows over 197 symbols), made with one `empty_table(n).update(...)` over Java arrays from numpy, merged
  with the live ticks (a table publisher). `long_ticks` is the two years before, four steps a day. Same seed as the
  mock: a date gives the same past on both.
- **Bars and charts are engine work.** `bars_1m` and `bars_5m` cover every symbol (dh_chart_ext's `ChartSource`),
  `bars_1d` comes from the long history plus the ticks, and a chart is a shared node: a filter of those, a rollup of
  them, or per-symbol bars from the ticks under a minute; spreads (`ZN-ZNH7`, `100*(US10Y-US2Y)`) are as-of priced from
  their legs' ticks. Studies are `update_by` on the node. `chart_nodes` shows what is held.
- **The app's own tables are written by one Python thread** inside the app's execution context: every 500 ms the
  market steps and publishes its ticks, quotes, book and trades; analytics and the desk's P&L every second; the
  relative-value monitor every minute. Writes go through table publishers, which never block (an input table's
  `add` waits for the next update cycle, once per table).
- **Everything else is dh_chart_ext**: workspaces, drawings and alerts are kept in `app.d/*.parquet`, alerts are
  evaluated by the engine on the last price and the standard studies of the forming 5-minute bar, and backtests are
  real runs: the strategy (Python, the runner's DSL) runs in its own process and streams its trades, orders,
  positions, signals and equity into `bt<N>_*` tables. The graph panel shows the server's real update graph.

Measured on this machine: the history takes about 5 s, the whole app script about 50 s (most of it Deephaven building
the bar tables over 4.5 million rows); a step of the market thread averages 60–70 ms of its 500 ms; the page
connects in about 10 s (the JS API is 8 MB).

## Gaps and simplifications

- **The mock market never closes.** Session shading, market status and countdowns follow each exchange's
  real calendar, but prices tick around the clock so there is always something to watch.
- **The desk is invented.** Books, positions, clients, RFQs, SOMA holdings and auction results are generated,
  seeded by the date. The attribution is risk-based (DV01 x each factor's move at the position's tenor), so its
  residual holds convexity and pull to par.
- **On the mock, strategies are JavaScript** (same DSL as the Python runner: `on_start`, `on_bar`, `on_end`, `ctx.*`)
  and run in a Web Worker. "Own engine" runs need a second Deephaven engine; the mock refuses them with a message
  (the panel used to wait for that engine forever). On the server they are Python in the runner's own process;
  own-engine runs are wired up (dh_chart_ext starts the engine) but were not tried here.
- **The two backends are kept in step by hand.** The simulator, the desk and the catalog exist in TypeScript
  (`src/model`, `src/server`) and in Python (`server/rates_terminal`), with the same seeds and CUSIPs. Random draws
  differ (numpy against the mock's generator), so the paths are alike but not the same.
- **On the server, bars under a minute are built per symbol on demand** (from the ticks), and 1-second bars only
  exist where the ticks are by the second: the last hour of history and live.
- **Treasury math is close, not settlement-grade.** Day counts are approximated. Implied repo ignores
  reinvestment. Delivery is assumed on the first day of the delivery month. Holidays are not modelled.
- **Synthetic microstructure.** Order books and the tape are simulated alongside the bars; bar volume and
  tape volume are not reconciled. Typed spreads' highs and lows come from their legs' bars.
- **History depth**: 1-second bars cover a rolling one to two hours, minute bars five days, daily bars two
  years. Charts load all of a symbol's history, so no scroll-back paging is needed.
- **The mock JS API covers what this repo uses.** Not covered: tree, rollup and partitioned tables, totals,
  custom columns, the console.
- **One 1 MB bundle** (300 KB gzipped). Nothing is code-split yet.

What building it showed about the real design (front-end group C, own shell):
- The chart API needs **instrument metadata** in its reply: unit, notation (32nds), decimals, has-volume. The
  mock's `chart` reply carries them, and `dh_chart_ext.api.ChartApi` now takes `meta=` for it (below).
- A **catalog method** (datasets: chartable vs table, symbols, table, default indicators) is what makes a
  universe browser possible without hard-coding names in the front end.
- **Indicator applicability belongs to the server.** Which studies an instrument can take (volume-based,
  additive units) follows from its metadata.
- Two shared-package fixes came out of it (below).

## Changes to shared packages

- `shell/src/Shell.tsx`: the group header read the link group from `panel.api.getParameters()`, which
  dockview 8.8 leaves empty until the first `updateParameters`. Panels that never update their params showed
  no link chip. It now reads `panel.params`.
- `react/src/ChartLegend.tsx`: the legend formatted prices with `toFixed(precision)` and ignored a series'
  custom price formatter. It now uses the formatter when there is one, so Treasury prices read in 32nds.
- `ext/src/theme.ts` (new): chart colours from CSS custom properties (`--acme-chart-bg`, `-bg-bottom` for a
  gradient, `-grid`, `-grid-style`, `-text`, `-border`, `-crosshair`), with `followTheme(chart, el)` and
  `notifyThemeChange()`. The shell's `RawChart` and `SeriesPanel` use it. With no properties set, the
  defaults are the colours they had, so other hosts look the same.
- `ext/src/countdown.ts`, `ext/src/nav.ts`: both measured the layout on every data change
  (`getBoundingClientRect`, the price scale's width), which forced the browser to lay out the whole page
  inside every tick. A price update cost about 10 ms, and the terminal's update cycle about 90 ms. That
  work now runs on the next animation frame: 0.4 ms per update, about 10–20 ms per cycle. This helps every
  host that uses these plugins.
- `shell/src/Shell.tsx` (header actions): every group has a close button (all its tabs). Floating and
  popped-out groups had no header buttons at all, so a backtest's results window could only be closed tab by
  tab; they now also have "dock into the layout". `ext/src/nav.ts` (go to date) and `ext/src/alerts.ts`
  (toasts) got close buttons as well.
- `ext/src/nav.ts` (hidden tabs): a chart whose data arrived while its tab was hidden (no width) kept every bar
  squeezed into a few pixels once shown, because the view window was applied at zero width. It is applied again
  when the chart first gets a size, unless the user has moved the view. The mock answered too fast to show it.
- `dh-plugin` (`dh_chart_ext`), for the server: nine more studies (WMA, Keltner, Donchian, Stochastic, MACD with a
  histogram, rate of change, ATR, realized volatility, z-score); a study's guide levels and histogram columns in the
  `chart` reply; weekly resolutions (`1w`); `ChartApi(meta=...)` to add instrument metadata to every chart reply;
  study keys write a decimal point as `p` (`KC_20_1p5`), as the front end does.
- `shell/src/shell.css`, `shell/src/grid/DataGrid.tsx`: the grid moved from a monospace font to the UI font with
  tabular figures. Cell borders became row rules. Rows are taller (26 px), headers quieter, and hover and
  highlight softer. Change and P&L columns are coloured by sign, numbers or text. A time at midnight UTC shows
  as a date, and milliseconds only show when there are any. The font can be set with `--acme-font-ui`.
