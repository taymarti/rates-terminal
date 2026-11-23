"""
Run the rates terminal's Deephaven server: Deephaven Community as a plain Python process (the `deephaven-server`
pip package, which embeds the engine's JVM in this process), with the terminal's app (app.d/terminal.py).

  runtime\\python\\python.exe server\\run.py                 port 10050, anonymous auth, 6 GB heap
  runtime\\python\\python.exe server\\run.py --port 10060 --heap 8g
  runtime\\python\\python.exe -i server\\run.py              the same, plus a Python REPL in the server's scope

Java: JAVA_HOME if set, else the Java runtime in runtime\\jre (bundled), else `java` on PATH.
Native Windows needs two workarounds, applied here (verified on 42.6, Java 17):
  * the JVM's DLL folders are put on PATH, or the JVM crashes in jimage.dll;
  * web storage directories are made relative, or startup fails with "Invalid path: /layouts".
"""
from __future__ import annotations

import argparse
import os
import pathlib
import shutil
import signal
import sys
import threading

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent


def find_java_home() -> pathlib.Path | None:
    if os.environ.get("JAVA_HOME"):
        return pathlib.Path(os.environ["JAVA_HOME"])
    bundled = ROOT / "runtime" / "jre"
    if (bundled / "bin").is_dir():
        return bundled
    java = shutil.which("java")
    return pathlib.Path(java).resolve().parent.parent if java else None


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=int(os.environ.get("DH_PORT", "10050")))
    ap.add_argument("--heap", default=os.environ.get("DH_HEAP", "6g"), help="JVM max heap, e.g. 6g")
    ap.add_argument("--psk", help="pre-shared key; omit for anonymous auth (localhost only!)")
    ap.add_argument("--app-dir", default=str(HERE / "app.d"), help="application-mode directory (*.app files)")
    ap.add_argument("--jvm-arg", action="append", default=[], help="extra JVM argument (repeatable)")
    args = ap.parse_args()

    java_home = find_java_home()
    if java_home is None:
        sys.exit("No Java found: set JAVA_HOME to a JDK or JRE 17+ (or keep runtime\\jre, which this repo ships).")
    os.environ["JAVA_HOME"] = str(java_home)
    os.environ["DH_PORT"] = str(args.port)  # the backtest service connects back to this port

    jvm_args = [f"-Xmx{args.heap}"]
    jvm_args.append(f"-Dauthentication.psk={args.psk}" if args.psk
                    else "-DAuthHandlers=io.deephaven.auth.AnonymousAuthenticationHandler")
    jvm_args.append(f"-Ddeephaven.application.dir={pathlib.Path(args.app_dir).resolve()}")
    jvm_args += args.jvm_arg
    if os.name == "nt":
        _windows_workarounds(jvm_args, java_home)

    # The Server must be created and started BEFORE anything imports `deephaven`.
    from deephaven_server import Server

    os.chdir(HERE)  # web storage (layouts, notebooks) lands in server/
    server = Server(port=args.port, jvm_args=jvm_args)
    server.start()
    print(f"\nDeephaven is up:  http://localhost:{args.port}/ide/"
          f"   version A: http://localhost:{args.port}/iframe/widget/?name=terminal_a"
          f"   ({'PSK' if args.psk else 'anonymous'} auth, Java {java_home})", flush=True)

    if sys.flags.interactive:
        return  # `python -i`: drop into a REPL; the server lives as long as the process
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    print("Ctrl+C to stop.", flush=True)
    stop.wait()


def _windows_workarounds(jvm_args: list, java_home: pathlib.Path) -> None:
    dll_dirs = [java_home / "bin", java_home / "bin" / "server"]
    os.environ["PATH"] = os.pathsep.join([str(d) for d in dll_dirs] + [os.environ.get("PATH", "")])
    for d in dll_dirs:
        if d.is_dir():
            os.add_dll_directory(str(d))
    for prop, value in (("web.storage.layout.directory", "layouts"),
                        ("web.storage.notebook.directory", "notebooks")):
        if not any(a.startswith(f"-D{prop}=") for a in jvm_args):
            jvm_args.append(f"-D{prop}={value}")


if __name__ == "__main__":
    main()
