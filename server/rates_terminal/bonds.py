"""
Treasury math: prices and yields of fixed-coupon bonds (semiannual), accrued interest, risk (duration, DV01,
convexity), the CBOT conversion factor, 32nds notation. Close to market conventions, not settlement-grade
(day counts approximated). The same formulas as the browser mock (terminal/src/model/bonds.ts).
"""
from __future__ import annotations

import math
from bisect import bisect_right
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from dh_chart_ext.instruments import fmt32, parse32  # noqa: F401 (Treasury notation, used across the app)

DAY_MS = 86_400_000
YEAR_MS = 365.25 * DAY_MS


@dataclass(frozen=True)
class Bond:
    cusip: str
    name: str
    type: str  # Bill, Note, Bond, TIPS
    coupon: float  # % a year
    maturity: int  # UTC ms
    issue: int
    term: str
    age: int  # 0 = on-the-run
    outstanding: float  # $bn
    rich_bp: float


_schedules: dict[int, list[int]] = {}


def _add_months(ms: int, months: int) -> int:
    d = datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
    y, m = divmod(d.year * 12 + d.month - 1 + months, 12)
    import calendar

    day = min(d.day, calendar.monthrange(y, m + 1)[1])
    return int(datetime(y, m + 1, day, tzinfo=timezone.utc).timestamp() * 1000)


def schedule(maturity: int) -> list[int]:
    """Every semiannual coupon date of a maturity, ascending, 40 years back (computed once per maturity)."""
    s = _schedules.get(maturity)
    if s is None:
        s = sorted(_add_months(maturity, -6 * k) for k in range(81))
        _schedules[maturity] = s
    return s


def accrued(b: Bond, settle: int) -> float:
    if b.coupon == 0:
        return 0.0
    s = schedule(b.maturity)
    i = bisect_right(s, settle)
    nxt = s[i] if i < len(s) else b.maturity
    prev = s[i - 1] if i > 0 else settle
    return b.coupon / 2 * ((settle - prev) / max(DAY_MS, nxt - prev))


def price_from_yield(b: Bond, y: float, settle: int) -> float:
    """Clean price per 100 from a yield in %, semiannual compounding (bills: simple money-market)."""
    t = (b.maturity - settle) / YEAR_MS
    if t <= 0:
        return 100.0
    if b.coupon == 0:
        return 100.0 / (1 + y / 100 * t)
    s = schedule(b.maturity)
    i0 = bisect_right(s, settle)
    n = len(s) - i0
    if n <= 0:
        return 100.0
    nxt = s[i0]
    prev = s[i0 - 1] if i0 > 0 else nxt - 182.5 * DAY_MS
    w = (nxt - settle) / max(DAY_MS, nxt - prev)
    v = 1 / (1 + y / 200)
    c = b.coupon / 2
    vw = v ** w
    annuity = n if abs(1 - v) < 1e-12 else (1 - v ** n) / (1 - v)
    pv = c * vw * annuity + 100 * vw * v ** (n - 1)
    return pv - b.coupon / 2 * ((settle - prev) / max(DAY_MS, nxt - prev))


def yield_from_price(b: Bond, price: float, settle: int) -> float:
    y = b.coupon or 4.0
    for _ in range(40):
        p = price_from_yield(b, y, settle)
        d = (price_from_yield(b, y + 0.0005, settle) - price_from_yield(b, y - 0.0005, settle)) / 0.001
        if not d:
            break
        step = (p - price) / d
        y -= step
        if abs(step) < 1e-10:
            break
    return y


@dataclass
class Risk:
    price: float
    dirty: float
    accrued: float
    mod_dur: float
    mac_dur: float
    dv01: float  # $ per bp per $1mm face
    convexity: float


def risk(b: Bond, y: float, settle: int) -> Risk:
    p = price_from_yield(b, y, settle)
    ai = accrued(b, settle)
    up = price_from_yield(b, y + 0.01, settle)
    dn = price_from_yield(b, y - 0.01, settle)
    dirty = p + ai
    dpdy = (dn - up) / 0.02  # price points per 1% of yield
    mod = dpdy * 100 / dirty
    conv = (up + dn - 2 * p) / (dirty * 1e-8) / 100
    return Risk(p, dirty, ai, mod, mod * (1 + y / 200), dpdy / 100 * 10_000, conv)


def conversion_factor(b: Bond, delivery_month: int) -> float:
    """CBOT conversion factor: the clean price at 6% of the bond with its term rounded down to whole quarters, / 100."""
    months = int((b.maturity - delivery_month) / YEAR_MS * 12 // 3 * 3)
    synth = Bond(b.cusip, b.name, b.type, b.coupon, _add_months(delivery_month, months), b.issue, b.term, b.age, b.outstanding, b.rich_bp)
    return round(price_from_yield(synth, 6.0, delivery_month) / 100, 4)


