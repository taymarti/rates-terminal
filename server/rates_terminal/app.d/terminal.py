"""
Deephaven's application mode runs this at startup (terminal.app names it): the rates terminal's app
(rates_terminal.app.build), published as the server's global variables. See rates_terminal/app.py for what they are.
"""
try:
    import rates_terminal  # noqa: F401 (installed: the wheel; or on sys.path: the repo's server/run.py puts it there)
except ImportError:  # run straight from the repo without run.py: rates_terminal is two folders up
    import pathlib
    import sys

    import jpy

    sys.path.insert(0, str(pathlib.Path(jpy.get_type("java.lang.System").getProperty("deephaven.application.dir")).parents[1]))

from rates_terminal.app import build as _build  # noqa: E402

globals().update(_build())
