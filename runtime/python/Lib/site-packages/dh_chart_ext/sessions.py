"""
Trading sessions per instrument: when each market trades, and its regular (day) session inside that.
Plain data, so every front end uses the same calendar: deephaven.ui gets it as an element prop,
JS clients as the published `sessions` table (one row per symbol, the spec as JSON).

A spec, in the exchange's time zone:
  tz        IANA time zone of the times below
  open      the trading day's session start; later than `close` means it starts the evening before
            (CME Globex: 18:00 the previous day to 17:00, with an hour's halt every afternoon)
  close     the session end
  rth       [start, end] of the regular trading hours inside it (pit/day session), or None
  days      ISO weekdays that are trading days (Mon=1 .. Fri=5)
  name      a label for tooltips

Browsers turn a spec into shaded ranges (closed, extended), a market status, and the time to the next
change (@acme/chart-ext sessions.ts). Exchange holidays are not modelled.
"""
from __future__ import annotations

import json

_CME = {"tz": "America/Chicago", "open": "17:00", "close": "16:00", "days": [1, 2, 3, 4, 5]}


def _cme(rth: tuple[str, str] | None, name: str) -> dict:
    return {**_CME, "rth": list(rth) if rth else None, "name": name}


SESSIONS: dict[str, dict] = {
    # CME equity index futures: Globex Sun-Fri 17:00-16:00 CT; regular hours follow the cash market
    "ES": _cme(("08:30", "15:00"), "CME Globex, equity index"),
    "NQ": _cme(("08:30", "15:00"), "CME Globex, equity index"),
    "RTY": _cme(("08:30", "15:00"), "CME Globex, equity index"),
    "YM": _cme(("08:30", "15:00"), "CBOT Globex, equity index"),
    # CBOT Treasury futures: regular hours 07:20-14:00 CT
    "ZN": _cme(("07:20", "14:00"), "CBOT Globex, Treasuries"),
    "ZB": _cme(("07:20", "14:00"), "CBOT Globex, Treasuries"),
    "ZF": _cme(("07:20", "14:00"), "CBOT Globex, Treasuries"),
    "ZT": _cme(("07:20", "14:00"), "CBOT Globex, Treasuries"),
    # NYMEX / COMEX
    "CL": _cme(("08:00", "13:30"), "NYMEX Globex, crude oil"),
    "NG": _cme(("08:00", "13:30"), "NYMEX Globex, natural gas"),
    "GC": _cme(("07:20", "12:30"), "COMEX Globex, gold"),
    "SI": _cme(("07:25", "12:25"), "COMEX Globex, silver"),
    # CME FX futures
    "6E": _cme(("07:20", "14:00"), "CME Globex, FX"),
    "6J": _cme(("07:20", "14:00"), "CME Globex, FX"),
    # US Treasury cash (electronic, nearly 24h): New York day session as regular hours
    "UST10": {"tz": "America/New_York", "open": "18:00", "close": "17:30", "days": [1, 2, 3, 4, 5],
              "rth": ["08:20", "15:00"], "name": "US Treasuries, cash"},
    # Eurex Bund future
    "BUND": {"tz": "Europe/Berlin", "open": "01:10", "close": "22:00", "days": [1, 2, 3, 4, 5],
             "rth": ["08:00", "17:30"], "name": "Eurex, Euro-Bund"},
}


def session_for(sym: str) -> dict | None:
    """The spec for a symbol; for a spread or ratio, its first leg's ("ES-NQ" -> ES)."""
    if sym in SESSIONS:
        return SESSIONS[sym]
    import re

    m = re.search(r"\d*[A-Za-z][A-Za-z0-9_.]*", sym)
    return SESSIONS.get(m.group(0)) if m else None


def sessions_table():
    """One row per symbol: Sym, Spec (JSON). For JS clients (front-end group C)."""
    from deephaven import new_table
    from deephaven.column import string_col

    return new_table([string_col("Sym", list(SESSIONS)), string_col("Spec", [json.dumps(s) for s in SESSIONS.values()])])
