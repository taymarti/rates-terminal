"""
Deephaven Community from Python, no Docker: the `deephaven-server` package embeds the engine's JVM in this process
and runs the terminal's app (app.d/) at startup.

Java: Deephaven compiles query formulas at runtime, so it needs a JDK (the jdk.compiler module), not just a runtime.
The first of these that has one: --java-home, $JAVA_HOME, the repo's runtime/jre, `java` on PATH, a JDK downloaded
earlier into ~/.rates-terminal/jdk. With none, a Temurin 21 JDK is downloaded there once (the `install-jdk` package,
from Adoptium), so a fresh virtual environment needs nothing else.

Native Windows needs two workarounds, applied here (verified on 42.6):
  * the JVM's DLL folders are put on PATH, or the JVM crashes in jimage.dll;
  * web storage directories are made relative, or startup fails with "Invalid path: /layouts".
"""
from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
from typing import Optional

PACKAGE = pathlib.Path(__file__).resolve().parent
APP_DIR = PACKAGE / "app.d"
REPO = PACKAGE.parents[1]  # in the repo: server/rates_terminal -> the repo root
JDK_DIR = pathlib.Path(os.environ.get("TERMINAL_JDK_DIR") or pathlib.Path.home() / ".rates-terminal" / "jdk")
JDK_VERSION = "21"


def has_compiler(home: pathlib.Path) -> bool:
    """Whether the Java at `home` has the jdk.compiler module (a JDK, or a runtime jlinked with it)."""
    java = home / "bin" / ("java.exe" if os.name == "nt" else "java")
    if not java.is_file():
        return False
    try:
        out = subprocess.run([str(java), "--list-modules"], capture_output=True, text=True, timeout=60).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return "jdk.compiler@" in out


def find_java_home(explicit: Optional[str] = None, download: bool = True) -> tuple[Optional[pathlib.Path], str]:
    """(JAVA_HOME, where it came from), or (None, '') when there is no JDK and download is off."""
    if explicit:
        return pathlib.Path(explicit), "--java-home"
    candidates = []
    if os.environ.get("JAVA_HOME"):
        candidates.append((pathlib.Path(os.environ["JAVA_HOME"]), "JAVA_HOME"))
    candidates.append((REPO / "runtime" / "jre", "runtime/jre"))
    java = shutil.which("java")
    if java:
        candidates.append((pathlib.Path(java).resolve().parent.parent, "PATH"))
    if JDK_DIR.is_dir():
        candidates += [(d, f"downloaded earlier ({JDK_DIR})") for d in sorted(JDK_DIR.iterdir(), reverse=True) if (d / "bin").is_dir()]
    for home, origin in candidates:
        if not (home / "bin").is_dir():
            continue
        if has_compiler(home):
            return home, origin
        print(f"Not using the Java from {origin} ({home}): it has no Java compiler, and Deephaven needs a JDK.", flush=True)
    if not download:
        return None, ""
    return _download_jdk(), f"downloaded ({JDK_DIR})"


def _download_jdk() -> pathlib.Path:
    import ssl

    import certifi
    import jdk  # the install-jdk package (urllib)

    print(f"No JDK found: downloading Temurin {JDK_VERSION} (about 200 MB, once) into {JDK_DIR}...", flush=True)
    JDK_DIR.mkdir(parents=True, exist_ok=True)
    # urllib checks certificates against the OS store, which can lack an issuer the download needs (pip, which
    # installed this package, uses certifi's bundle): use that bundle for the download
    default = ssl._create_default_https_context
    ssl._create_default_https_context = lambda *a, **k: ssl.create_default_context(cafile=certifi.where())
    try:
        home = pathlib.Path(jdk.install(JDK_VERSION, path=str(JDK_DIR)))
    finally:
        ssl._create_default_https_context = default
    print(f"JDK installed: {home}", flush=True)
    return home


def start_server(*, port: int = 10050, heap: str = "6g", data_dir: Optional[str] = None, java_home: Optional[str] = None,
                 psk: Optional[str] = None, jvm_args: tuple = ()):
    """Start Deephaven with the terminal's app; returns the running deephaven_server.Server once the app is up."""
    home, origin = find_java_home(java_home)
    if home is None:
        raise SystemExit("No JDK found: set JAVA_HOME to a JDK 17+, or pass --java-home.")
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
