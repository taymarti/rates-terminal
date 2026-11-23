"""
The rates terminal's entry points (console scripts of the wheel; server/run.py runs them from the repo):

  rates-terminal           Deephaven (with the app) and the web server in one process, then the browser
  rates-terminal-server    Deephaven only: version A, the IDE, and the tables and chart_api for a separate web server
  rates-terminal-web       the web server only: B and C, proxied to a running Deephaven (--dh-url); C falls back to its
                           in-page mock when there is none
"""
from __future__ import annotations

import argparse
import os
import signal
import sys
import threading
import webbrowser

DEFAULT_PORT = 10050
DEFAULT_WEB_PORT = 4070


def _server_args(ap: argparse.ArgumentParser) -> None:
    ap.add_argument("--port", type=int, default=int(os.environ.get("DH_PORT", DEFAULT_PORT)), help="Deephaven's port (default 10050)")
    ap.add_argument("--heap", default=os.environ.get("DH_HEAP", "6g"), help="the JVM's max heap (default 6g)")
    ap.add_argument("--data-dir", default=None, help="where workspaces, drawings, alerts and backtests are saved "
                                                     "(default $TERMINAL_DATA_DIR, else ~/.rates-terminal)")
    ap.add_argument("--java-home", default=None, help="a JDK 17+ (default: $JAVA_HOME, runtime/jre, java on PATH; else Temurin 21, downloaded once into ~/.rates-terminal/jdk)")
    ap.add_argument("--psk", default=None, help="a pre-shared key for Deephaven (default anonymous: keep it on localhost)")
    ap.add_argument("--jvm-arg", action="append", default=[], help="an extra JVM argument (repeatable)")


def _web_args(ap: argparse.ArgumentParser, *, standalone: bool) -> None:
    ap.add_argument("--web-port" if not standalone else "--port", dest="web_port", type=int,
                    default=int(os.environ.get("WEB_PORT", DEFAULT_WEB_PORT)), help="the web server's port (default 4070)")
    ap.add_argument("--host", default="localhost", help="the web server's address (default localhost; 0.0.0.0 for the network)")
    ap.add_argument("--web-root", default=None, help="the built front ends (default: the package's, or the repo's dist/)")


def _urls(port: int, web_port: int | None) -> str:
    lines = []
    if web_port:
        lines += [f"  C  http://localhost:{web_port}/          React + table data (the in-page mock when Deephaven is down)",
                  f"  B  http://localhost:{web_port}/b.html    React shell + server widgets"]
    if port:
        # the web server proxies Deephaven's embedded-widget pages, so A is on its port too
        lines += [f"  A  http://localhost:{web_port or port}/iframe/widget/?name=terminal_a   deephaven.ui (opens in 20-40 s)",
                  f"     http://localhost:{port}/ide/   the Deephaven IDE"]
    return "\n".join(lines)


def _wait_forever() -> None:
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    print("Ctrl+C to stop.", flush=True)
    stop.wait()


def main(argv=None) -> None:
    """Deephaven with the app, and the web server, in this process; then open version C."""
    ap = argparse.ArgumentParser(prog="rates-terminal", description=main.__doc__)
    _server_args(ap)
    _web_args(ap, standalone=False)
    ap.add_argument("--no-web", action="store_true", help="don't start the web server (the same as rates-terminal-server)")
    ap.add_argument("--no-browser", action="store_true", help="don't open the browser")
    a = ap.parse_args(argv)

    from .launch import start_server

    start_server(port=a.port, heap=a.heap, data_dir=a.data_dir, java_home=a.java_home, psk=a.psk, jvm_args=tuple(a.jvm_arg))
    if not a.no_web:
        from .web import start_in_thread

        start_in_thread(a.web_port, f"http://127.0.0.1:{a.port}", host=a.host, root=a.web_root)
    print("\nThe rates terminal is up:\n" + _urls(a.port, None if a.no_web else a.web_port) + "\n", flush=True)
    if not a.no_browser:
        webbrowser.open(f"http://localhost:{a.web_port}/" if not a.no_web else f"http://localhost:{a.port}/ide/")
    _wait_forever()


def server_main(argv=None) -> None:
    """Deephaven with the app only: version A, the IDE, and the server for a separate rates-terminal-web."""
    ap = argparse.ArgumentParser(prog="rates-terminal-server", description=server_main.__doc__)
    _server_args(ap)
    a = ap.parse_args(argv)
    from .launch import start_server

    start_server(port=a.port, heap=a.heap, data_dir=a.data_dir, java_home=a.java_home, psk=a.psk, jvm_args=tuple(a.jvm_arg))
    print("\nDeephaven is up:\n" + _urls(a.port, None) + "\n", flush=True)
    if sys.flags.interactive:
        return  # python -i: a REPL in the server's process
    _wait_forever()


def web_main(argv=None) -> None:
    """The web server only: versions B and C, with Deephaven's paths proxied to --dh-url on the same origin."""
    ap = argparse.ArgumentParser(prog="rates-terminal-web", description=web_main.__doc__)
    _web_args(ap, standalone=True)
    ap.add_argument("--dh-url", default=os.environ.get("DH_URL", f"http://127.0.0.1:{DEFAULT_PORT}"),
                    help="the Deephaven server to proxy to (default http://127.0.0.1:10050)")
    ap.add_argument("--open", action="store_true", help="open the browser")
    a = ap.parse_args(argv)
    from .web import serve

    print(f"Serving the rates terminal on http://localhost:{a.web_port}/ (Deephaven: {a.dh_url})", flush=True)
    if a.open:
        threading.Timer(1.0, lambda: webbrowser.open(f"http://localhost:{a.web_port}/")).start()
    serve(a.web_port, a.dh_url, host=a.host, root=a.web_root)


def dispatch(argv=None) -> None:
    """`python -m rates_terminal [server|web] ...` (and server/run.py): the first argument picks the entry point."""
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "server":
        return server_main(argv[1:])
    if argv and argv[0] == "web":
        return web_main(argv[1:])
    if argv and argv[0] == "all":
        argv = argv[1:]
    return main(argv)
