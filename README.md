# Rates terminal

A trading-terminal prototype for US rates on [Deephaven](https://deephaven.io): a simulated market of Treasuries,
Treasury futures, STIR, swaps and other datasets (chartable and table-only), indicators on them, a hypothetical desk
(books, positions, risk, P&L attribution, RFQs), and the same terminal built three ways on one server:

| | Version | What draws the panels | URL |
|---|---|---|---|
| **C** | React + table data | this repo's React panels and our own charts, from live tables and the chart API | http://localhost:4070/ |
| **B** | React shell + server widgets | our dockable shell; each panel is a deephaven.ui widget from the server | http://localhost:4070/b.html |
| **A** | deephaven.ui | one deephaven.ui dashboard, written in Python, served by Deephaven | http://localhost:10050/iframe/widget/?name=terminal_a |

C also runs with no server at all, on a mock Deephaven server that runs in the page.

Everything needed to run it is in the repo: the built web app (`dist/`), a Python runtime with the Deephaven server
and its plugins (`runtime/python/`), a Java runtime (`runtime/jre/`), and `node_modules/`. Nothing is downloaded at start.

## Start it

You need **Windows 10 or 11 (x64)** and **[Node.js](https://nodejs.org) 20 or later**. Python and Java are bundled.

```powershell
git clone https://github.com/taymarti/rates-terminal
```

```powershell
cd rates-terminal; .\start.cmd
```

`start.cmd` (or `.\start.ps1`) opens two windows, the Deephaven server and the web app, waits for the server and opens
version C in your browser. The first start takes a minute or two while the server builds two years of market history.
Close the two windows (or run `.\stop.ps1`) to stop.

| Option | |
|---|---|
| `.\start.ps1 -WebOnly` | the web app only: C runs on its in-page mock (no server, no A or B) |
| `.\start.ps1 -ServerOnly` | the Deephaven server only, and version A |
| `.\start.ps1 -Dev` | the web app from source with hot reload (Vite's dev server) instead of `dist/` |
| `.\start.ps1 -Hidden` | no windows; logs in `logs\`; stop with `.\stop.ps1` |
| `-ServerPort 10060 -WebPort 4080 -Heap 8g` | other ports, a bigger JVM heap (default 6 GB) |

If PowerShell refuses to run scripts, use `start.cmd`, or once: `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`.

The same, by hand:

```powershell
$env:JAVA_HOME = "$PWD\runtime\jre"; .\runtime\python\python.exe -s -E server\run.py
```

```powershell
node node_modules\vite\bin\vite.js preview
```

The Deephaven IDE is at http://localhost:10050/ide/ (every table the terminal publishes is there).

## Using it

- **C** (`/`): pick a dataset in the Universe, click a symbol, and the charts, curve, DOM and security panels follow
  (link groups: the coloured chip in each panel's header). The chart takes any symbol or a typed expression
  (`ZN-ZNH7`, `100*(US10Y-US2Y)`), any resolution from 1s to 1W, indicators with parameters, drawings, alerts and
  compare. "+ Panel" adds the bond calculator, correlation heatmap, tables, the update graph, strategies and
  backtests. Workspaces (layouts) are saved per user on the server. `?user=alice` for another user, `?reset` for the
  default layout, `?server=mock` for the in-page mock even when the server is up.
- **B** (`/b.html`): the same panels as server widgets in our shell. They link through the server, so one click moves
  every open B page (one selection per server, not per browser).
- **A**: one deephaven.ui dashboard; it opens in 20 to 40 seconds. A click updates the tables in 3 to 5 seconds and the
  chart in about 15 (every interaction is a round trip and a re-render on the server).

## What is in the repo

The app is mock data and presentation: the simulated market and its tables, and the panels that show them. The
generic parts (the shell, charts, bindings, the in-page mock server, widget hosting, the chart API and its nodes on the
server) are libraries from [dh-charts](https://github.com/taymarti/dh-charts), installed here as packages.

```
src/                    the web app (versions C and B)
  main.tsx              C: the shell with the terminal's panels; the server if it is up, else the in-page mock
  b-main.tsx, b.tsx     B: Deephaven's bootstrap, then the shell with one server widget per panel
  panels/               C's panels: universe, chart, curve, bond, DOM, heatmap, books & P&L, security
  model/                the simulated market (TypeScript): universe, bond math, the simulator
  mock/                 the in-page backend: the market's tables, the desk, chart_api's terminal methods
server/                 the Deephaven server (Python)
  run.py                starts Deephaven with the app (finds runtime\jre, applies the Windows workarounds)
  app.d/terminal.py     the app: builds the market, publishes every table, chart_api, A and B
  rates_terminal/       the simulated market (Python, the same model as model/), the desk, the catalog,
                        chart_api's terminal methods (tapi.py), the deephaven.ui panels for A and B (ui.py)
  data/                 what users save: workspaces, drawings, alerts, strategies, backtest runs (not committed)
dist/                   the built web app (npm run build)
vendor/npm/             the dh-charts packages as tarballs (package.json installs them from here)
vendor/python/          dh_chart_ext's wheel (installed in runtime\python)
runtime/python/         CPython 3.12 with deephaven-server 42.6, deephaven.ui, plotly express, the TradingView
                        plugin and dh_chart_ext (a plain Python folder, not a virtual environment: it runs from anywhere)
runtime/jre/            Java 17 (Azul Zulu, trimmed with jlink)
node_modules/           the web app's packages
```

From dh-charts:

| Package | Used for |
|---|---|
| `@acme/app-shell` | the dockable shell, workspaces, link groups, the Deephaven session in React, the grid, generic panels, the chart workspace |
| `@acme/chart-ext` | chart features as Lightweight Charts plugins, indicators, spread expressions, price notation (32nds, %, bp) |
| `@acme/chart-react` | the TradingView-style toolbar, legend, watchlist, alerts panel |
| `@acme/dh-bindings` | stores over Deephaven tables, row subscriptions, the chart API client |
| `@acme/dh-mock` | the in-page Deephaven server: update graph, JS API, chart nodes, stores, backtests |
| `@acme/dh-widgets` | Deephaven's own widgets (deephaven.ui, plotly, TradingView) inside our shell (version B) |
| `dh_chart_ext` (Python) | the chart API and its shared nodes, workspaces, annotations, alerts, backtests, the update graph, live tables (`dh_chart_ext.live`), the server-side link hub (`dh_chart_ext.uikit`), the deephaven.ui chart workspace |

## Changing it

```powershell
npm run dev          # C and B from source on :4070, proxied to the server on :10050
npm run typecheck
npm run build        # dist/
```

The TypeScript model (`src/model`) and the Python one (`server/rates_terminal`) are the same simulator written twice
(the mock in the page, the real one on the server). Change both together.

To take newer dh-charts packages: in each dh-charts package folder, `npm pack --pack-destination <this repo>\vendor\npm`,
then `npm install` here. For a newer `dh_chart_ext`: build its JS plugin (`npm run build` in `dh-plugin\js`), then
`pip wheel dh-plugin --no-deps -w vendor\python` and
`.\runtime\python\python.exe -s -E -m pip install --no-deps --force-reinstall vendor\python\dh_chart_ext-0.1.0-py3-none-any.whl`.

### Rebuilding the runtime

`runtime\python` is a copy of a python.org CPython 3.12 install (without its own site-packages, tests, Tk and docs)
with the server's packages in `Lib\site-packages` (`deephaven-server==42.6`, `deephaven-plugin-ui==0.42.2`,
`deephaven-plugin-plotly-express==0.20.1`, `deephaven-plugin-tradingview-lightweight` (an acme build), and
`vendor\python\dh_chart_ext`). `runtime\jre` is `jlink --add-modules ALL-MODULE-PATH --strip-debug --no-man-pages
--no-header-files --compress=2` of a Zulu 17 JDK. Any Java 17+ works instead: set `JAVA_HOME` and `run.py` uses it.

## Notes from building it

[docs/prototype-notes.md](docs/prototype-notes.md) has the notes kept while the terminal was built inside dh-charts:
the backend's design, how A, B and C compare (speeds, what B needs to host deephaven.ui widgets, a plotly-express
bug that stalls a dashboard), and what each version is missing.

## Known limits

- B's selection is one per server: two B pages follow each other.
- A and B have no bond calculator or correlation heatmap (C has both).
- "Own engine" backtests start a second Deephaven engine per run; only in-engine runs were tried with this repo.
- Windows only as committed: `runtime\` holds Windows binaries. On Linux or macOS, install the packages listed above
  into a Python 3.12 environment, set `JAVA_HOME`, and run `server/run.py` with it.
