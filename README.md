# Rates terminal

A trading-terminal prototype for US rates on [Deephaven](https://deephaven.io): a simulated market of Treasuries,
Treasury futures, STIR, swaps and other datasets (chartable and table-only), indicators on them, a hypothetical desk
(books, positions, risk, P&L attribution, RFQs), and the same terminal built three ways on one server:

| | Version | What draws the panels | URL |
|---|---|---|---|
| **C** | React + table data | this repo's React panels and our own charts, from live tables and the chart API | http://localhost:4070/ |
| **B** | React shell + server widgets | our dockable shell; each panel is a deephaven.ui widget from the server | http://localhost:4070/b.html |
| **A** | deephaven.ui | one deephaven.ui dashboard, written in Python, served by Deephaven | http://localhost:4070/iframe/widget/?name=terminal_a |

C also runs with no server at all, on a mock Deephaven server that runs in the page.

It all runs from Python, with no Docker. Deephaven runs inside a Python process (the `deephaven-server` package). A
small Python web server (aiohttp) serves the built front ends and passes Deephaven's paths through on the same port,
so A, B and C are all on :4070. Node.js is only needed to change the front ends.

There are two ways to run it: **from this repo** on the bundled runtime (nothing to install), or **as a wheel**
installed into any Python environment.

## Run it from the repo

**Windows 10 or 11 (x64)**; nothing else. The repo carries the built front ends (`dist/`), a Python runtime with the
Deephaven server and its plugins (`runtime/python/`) and a Java runtime (`runtime/jre/`).

```powershell
git clone https://github.com/taymarti/rates-terminal
```

```powershell
cd rates-terminal; .\start.cmd
```

`start.cmd` (or `.\start.ps1`) opens a window running Deephaven and the web server; the browser opens on version C once
the server is up (a minute or two: it builds two years of market history). Close the window, or run `.\stop.ps1`, to stop.

| Option | |
|---|---|
| `.\start.ps1 -ServerOnly` | Deephaven only (version A on :10050, the IDE) |
| `.\start.ps1 -WebOnly` | the web server only: C runs on its in-page mock with no server |
| `.\start.ps1 -Dev` | Deephaven, plus the front ends from source with hot reload (Vite's dev server; needs Node.js 20+) |
| `.\start.ps1 -Hidden` | no window; output in `logs\`; stop with `.\stop.ps1` |
| `-ServerPort 10060 -WebPort 4080 -Heap 8g -NoBrowser` | other ports, a bigger JVM heap (default 6 GB), no browser |

If PowerShell refuses to run scripts, use `start.cmd`, or once: `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`.

The same by hand: `server\run.py` runs the wheel's entry points (below) on the bundled runtime, with `rates_terminal`
from `server\` and saved state in `server\data\`:

```powershell
.\runtime\python\python.exe -s -E server\run.py
```

```powershell
.\runtime\python\python.exe -s -E server\run.py server
```

```powershell
.\runtime\python\python.exe -s -E server\run.py web
```

## Install it as a wheel

`release/rates_terminal-0.1.7-py3-none-any.whl` is the whole app in one wheel: the Python package, the built front
ends, and the two Deephaven plugins that aren't on PyPI (`dh_chart_ext` and an acme build of Deephaven's TradingView
plugin). Its other dependencies come from PyPI: `deephaven-server`, deephaven.ui, plotly express, aiohttp,
`install-jdk`. Any OS with Python 3.10+ (tested: Windows, Python 3.12).

```powershell
python -m venv .venv
```

```powershell
.venv\Scripts\pip install release\rates_terminal-0.1.7-py3-none-any.whl
```

```powershell
.venv\Scripts\rates-terminal
```

The install takes a few minutes (Deephaven's server and its jars, pyarrow, pandas: several hundred MB). Deephaven
needs a **JDK** 17+ (it compiles query formulas, so a runtime without the compiler won't do). The launcher uses
`--java-home`, `JAVA_HOME` or `java` on PATH if it has the compiler. Otherwise the first start downloads Temurin 21
(about 200 MB, from Adoptium) into `~/.rates-terminal/jdk`, once.

| Entry point | Starts |
|---|---|
| `rates-terminal` | Deephaven with the app, and the web server, in one process; opens the browser on C |
| `rates-terminal-server` | Deephaven with the app only (A on :10050, the IDE) |
| `rates-terminal-web` | the web server only (B and C), proxying to `--dh-url` (default `http://127.0.0.1:10050`) |
| `python -m rates_terminal [server\|web]` | the same three |

Options (`--help` on each): `--port` (Deephaven, 10050), `--web-port` (4070), `--heap` (6g), `--data-dir` (saved
workspaces, drawings, alerts, strategies and backtest runs; default `~/.rates-terminal`), `--java-home`, `--psk`,
`--host` (the web server's address; `0.0.0.0` for the network), `--no-browser`.

Build the wheel again after changing anything (the front ends first):

```powershell
npm run build; .\runtime\python\python.exe -s -E -m pip wheel . --no-deps -w release
```

`pyproject.toml` declares the package, its dependencies and entry points; `hatch_build.py` adds `dist/` (as
`rates_terminal/web`) and unpacks `vendor/python/*.whl` into the wheel.

## Using it

- **C** (`/`): pick a dataset in the Universe, click a symbol, and the charts, curve, DOM and security panels follow
  (link groups: the coloured chip in each panel's header). The chart takes any symbol or a typed expression
  (`ZN-ZNH7`, `100*(US10Y-US2Y)`), any resolution from 1s to 1W, indicators with parameters, drawings, alerts and
  compare. "+ Panel" adds the bond calculator, correlation heatmap, tables, the update graph, strategies and
  backtests. Workspaces (layouts) are saved per user on the server. `?user=alice` for another user, `?reset` for the
  default layout, `?server=mock` for the in-page mock even when the server is up.
- **B** (`/b.html`): the same panels as server widgets in our shell. They link through the server, so one click moves
  every open B page (one selection per server, not per browser).
- **A** (`/iframe/widget/?name=terminal_a`): one deephaven.ui dashboard; it opens in 20 to 40 seconds. A click updates
  the tables in 3 to 5 seconds and the chart in about 15 (every interaction is a round trip and a re-render on the server).

## What is in the repo

The app is mock data and presentation: the simulated market and its tables, and the panels that show them. The
generic parts (the shell, charts, bindings, the in-page mock server, widget hosting, the chart API and its nodes on the
server) are libraries from [dh-charts](https://github.com/taymarti/dh-charts), installed here as packages.

```
src/                    the front ends (versions C and B)
  main.tsx              C: the shell with the terminal's panels; the server if it is up, else the in-page mock
  b-main.tsx, b.tsx     B: Deephaven's bootstrap, then the shell with one server widget per panel
  panels/               C's panels: universe, chart, curve, bond, DOM, heatmap, books & P&L, security
  model/                the simulated market (TypeScript): universe, bond math, the simulator
  mock/                 the in-page backend: the market's tables, the desk, chart_api's terminal methods
server/
  run.py                runs rates_terminal's entry points from the repo on the bundled runtime
  rates_terminal/       the Python package (the wheel's)
    cli.py              the entry points: rates-terminal, rates-terminal-server, rates-terminal-web
    launch.py           Deephaven from Python (deephaven-server): finding a JDK, JVM arguments, Windows workarounds
    web.py              the web server: the built front ends, and a same-origin proxy to Deephaven (websockets too)
    app.py              the Deephaven app: the market, every table, chart_api, A and B (app.d/ runs it at startup)
    market.py ...       the simulated market (Python, the same model as src/model), the desk, the catalog,
                        chart_api's terminal methods (tapi.py), the deephaven.ui panels for A and B (ui.py)
  data/                 what users save when run from the repo (not committed)
dist/                   the built front ends (npm run build)
release/                the wheel
pyproject.toml, hatch_build.py   how the wheel is built
vendor/npm/             the dh-charts packages as tarballs (package.json installs them from here)
vendor/python/          the two plugins not on PyPI, as wheels (runtime\python has them installed; the wheel carries them)
runtime/python/         CPython 3.12 with deephaven-server 42.6, deephaven.ui, plotly express, the TradingView
                        plugin, dh_chart_ext and aiohttp (a plain Python folder, not a virtual environment: it runs from anywhere)
runtime/jre/            Java 17 (Azul Zulu, trimmed with jlink; it keeps the compiler Deephaven needs)
node_modules/           the front ends' packages (to change and build them)
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

Start Deephaven (`.\start.ps1 -ServerOnly`), then:

```powershell
npm run dev          # C and B from source on :4070 with hot reload, proxied to the server on :10050 (needs Node.js 20+)
npm run typecheck
npm run build        # dist/, which the Python web server and the wheel serve
```

The TypeScript model (`src/model`) and the Python one (`server/rates_terminal`) are the same simulator written twice
(the mock in the page, the real one on the server). Change both together.

To take newer dh-charts packages: in each dh-charts package folder, `npm pack --pack-destination <this repo>\vendor\npm`,
then `npm install` here. For a newer `dh_chart_ext`: build its JS plugin (`npm run build` in `dh-plugin\js`), then
`pip wheel dh-plugin --no-deps -w vendor\python` and
`.\runtime\python\python.exe -s -E -m pip install --no-deps --force-reinstall vendor\python\dh_chart_ext-0.2.1-py3-none-any.whl`.

### Rebuilding the runtime

`runtime\python` is a copy of a python.org CPython 3.12 install (without its own site-packages, tests, Tk and docs)
with the server's packages in `Lib\site-packages`: `deephaven-server==42.6`, `deephaven-plugin-ui==0.42.2`,
`deephaven-plugin-plotly-express==0.20.1`, `aiohttp`, and the two wheels in `vendor\python`. `runtime\jre` is
`jlink --add-modules ALL-MODULE-PATH --strip-debug --no-man-pages --no-header-files --compress=2` of a Zulu 17 JDK. Any
JDK 17+ works instead: set `JAVA_HOME`.

## Notes from building it

[docs/prototype-notes.md](docs/prototype-notes.md) has the notes kept while the terminal was built inside dh-charts:
the backend's design, how A, B and C compare (speeds, what B needs to host deephaven.ui widgets, a plotly-express
bug that stalls a dashboard), and what each version is missing.

## Known limits

- B's selection is one per server: two B pages follow each other.
- A and B have no bond calculator or correlation heatmap (C has both).
- "Own engine" backtests start a second Deephaven engine per run; only in-engine runs were tried with this repo.
- The bundled runtime is Windows-only; elsewhere, use the wheel.
