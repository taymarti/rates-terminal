"""
Run the rates terminal from the repo, on the bundled runtime (runtime\\python, runtime\\jre): the same entry points as
the wheel's console scripts (rates_terminal.cli), with rates_terminal taken from server/ and saved state in server/data.

  runtime\\python\\python.exe -s -E server\\run.py            Deephaven + the web server (rates-terminal)
  runtime\\python\\python.exe -s -E server\\run.py server     Deephaven only (rates-terminal-server)
  runtime\\python\\python.exe -s -E server\\run.py web        the web server only (rates-terminal-web)

Options are the entry points' (--help).
"""
import os
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))  # rates_terminal, from source
os.environ.setdefault("TERMINAL_DATA_DIR", str(HERE / "data"))

from rates_terminal.cli import dispatch  # noqa: E402

if __name__ == "__main__":
    dispatch()
