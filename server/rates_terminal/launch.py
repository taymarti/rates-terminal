"""
Deephaven Community from Python, no Docker: the `deephaven-server` package embeds the engine's JVM in this process
and runs the terminal's app (app.d/) at startup.

Java, the first found of: an explicit java_home, $JAVA_HOME, the `jdk4py` package (a Java 21 runtime from PyPI, a
dependency of the wheel), the repo's runtime/jre, `java` on PATH.

Native Windows needs two workarounds, applied here (verified on 42.6):
  * the JVM's DLL folders are put on PATH, or the JVM crashes in jimage.dll;
  * web storage directories are made relative, or startup fails with "Invalid path: /layouts".
"""
from __future__ import annotations

import os
import pathlib
import shutil
from typing import Optional

PACKAGE = pathlib.Path(__file__).resolve().parent
APP_DIR = PACKAGE / "app.d"
REPO = PACKAGE.parents[1]  # in the repo: server/rates_terminal -> the repo root


def find_java_home(explicit: Optional[str] = None) -> tuple[Optional[pathlib.Path], str]:
    """(JAVA_HOME, where it came from), or (None, '') when there is no Java."""
    if explicit:
        return pathlib.Path(explicit), "--java-home"
    if os.environ.get("JAVA_HOME"):
        return pathlib.Path(os.environ["JAVA_HOME"]), "JAVA_HOME"
    try:
        import jdk4py

        return pathlib.Path(jdk4py.JAVA_HOME), "jdk4py"
    except ImportError:
        pass
    bundled = REPO / "runtime" / "jre"
    if (bundled / "bin").is_dir():
        return bundled, "runtime/jre"
    java = shutil.which("java")
    if java:
        return pathlib.Path(java).resolve().parent.parent, "PATH"
    return None, ""


def start_server(*, port: int = 10050, heap: str = "6g", data_dir: Optional[str] = None, java_home: Optional[str] = None,
                 psk: Optional[str] = None, jvm_args: tuple = ()):
    """Start Deephaven with the terminal's app; returns the running deephaven_server.Server once the app is up."""
    home, origin = find_java_home(java_home)
    if home is None:
        raise SystemExit("No Java found. Install the wheel's dependencies (jdk4py brings Java 21), set JAVA_HOME to a JDK or "
                         "JRE 17+, or pass --java-home.")
    os.environ["JAVA_HOME"] = str(home)
    os.environ["DH_PORT"] = str(port)  # the backtest service connects back to this port
    data = pathlib.Path(data_dir or os.environ.get("TERMINAL_DATA_DIR") or pathlib.Path.home() / ".rates-terminal").resolve()
    data.mkdir(parents=True, exist_ok=True)
    os.environ["TERMINAL_DATA_DIR"] = str(data)
    (data / "ready.txt").unlink(missing_ok=True)

    args = [f"-Xmx{heap}",
            f"-Dauthentication.psk={psk}" if psk else "-DAuthHandlers=io.deephaven.auth.AnonymousAuthenticationHandler",
            f"-Ddeephaven.application.dir={APP_DIR}", *jvm_args]
    if os.name == "nt":
        _windows_workarounds(args, home)
    print(f"Starting Deephaven on :{port} (Java: {home}, from {origin}; data: {data})...", flush=True)

    # The Server must be created and started BEFORE anything imports `deephaven`.
    from deephaven_server import Server

    os.chdir(data)  # web storage (layouts, notebooks) lands in the data directory
    server = Server(port=port, jvm_args=args)
    server.start()  # returns once the app scripts have run
    return server


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
