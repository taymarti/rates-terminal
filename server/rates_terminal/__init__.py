"""
The rates terminal: a rates trading terminal prototype on Deephaven (a simulated Treasury and futures market) with
three front ends on one server: A (deephaven.ui), B (a React shell hosting server widgets) and C (React + table data).

  rates-terminal           the Deephaven server and the web server in one process   (rates_terminal.cli:main)
  rates-terminal-server    the Deephaven server only (and version A)                 (rates_terminal.cli:server_main)
  rates-terminal-web       the web server only: B and C, proxied to a server        (rates_terminal.cli:web_main)
  python -m rates_terminal the same as rates-terminal ([server|web] for the others)

  rates_terminal.app       the Deephaven app (application mode calls build())
  rates_terminal.launch    Deephaven from Python (deephaven-server): Java, JVM arguments, Windows workarounds
  rates_terminal.web       the web server: the built front ends, and a reverse proxy to Deephaven on the same origin
"""
__version__ = "0.1.0"
