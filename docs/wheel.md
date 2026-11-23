# rates-terminal

A rates trading terminal prototype on [Deephaven](https://deephaven.io): a simulated market of Treasuries, Treasury
futures, STIR, swaps and other datasets, indicators, a hypothetical desk (books, positions, risk, P&L attribution,
RFQs), and the same terminal built three ways on one server:

- **C**: React panels and our own charts from live tables and a chart API
- **B**: a React shell whose panels are deephaven.ui widgets from the server
- **A**: one deephaven.ui dashboard, written in Python

No Docker: Deephaven runs inside the Python process (`deephaven-server`), and a small aiohttp server serves the built
front ends and proxies Deephaven on the same origin.

## Install and run

Python 3.10 or later (tested on 3.12). Java comes from the `jdk4py` dependency (Java 21); `JAVA_HOME` or
`--java-home` overrides it.

```
python -m venv .venv
.venv\Scripts\pip install rates_terminal-0.1.0-py3-none-any.whl        (Windows)
.venv/bin/pip install rates_terminal-0.1.0-py3-none-any.whl            (Linux, macOS)
.venv\Scripts\rates-terminal
```

The first start takes a minute or two (the simulator builds two years of market history), then the browser opens on
version C. Ctrl+C stops it.

| Entry point | What it starts |
|---|---|
| `rates-terminal` | Deephaven with the app, and the web server, in one process; opens the browser |
| `rates-terminal-server` | Deephaven with the app only (version A, the IDE) |
| `rates-terminal-web` | the web server only (B and C), proxying to `--dh-url` (default `http://127.0.0.1:10050`); with no server, C runs on its in-page mock |
| `python -m rates_terminal [server\|web]` | the same three |

| URL (defaults) | |
|---|---|
| http://localhost:4070/ | C: React + table data |
| http://localhost:4070/b.html | B: React shell + server widgets |
| http://localhost:4070/iframe/widget/?name=terminal_a | A: deephaven.ui (opens in 20 to 40 seconds) |
| http://localhost:10050/ide/ | the Deephaven IDE |

Options: `--port` (Deephaven, 10050), `--web-port` (4070), `--heap` (6g), `--data-dir` (saved workspaces, drawings,
alerts and backtests; default `~/.rates-terminal`), `--java-home`, `--host` (the web server's address), `--no-browser`.

Source, the in-repo runtime and how it is built: https://github.com/taymarti/rates-terminal
