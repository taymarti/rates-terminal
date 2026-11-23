"""
The universe: every symbol the terminal knows, the Treasury cash bonds by CUSIP, the futures contracts with their
deliverable baskets, the STIR strip, FOMC dates and exchange calendars. Mock data shaped like late November 2026
(the Dec/Mar Treasury futures roll under way, first notice Nov 30). Mirrors terminal/src/model/universe.ts.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from .bonds import Bond, conversion_factor


def d(s: str) -> int:
    return int(datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000)


@dataclass
class Instrument:
    sym: str
    name: str
    dataset: str
    kind: str  # yield spread rate future stir bond etf index fx equity commodity swap
    unit: str  # % bp price index
    tick: Optional[float]
    decimals: int
    has_volume: bool
    session: str
    thirty_seconds: bool = False
    tenor: Optional[float] = None
    root: Optional[str] = None
    month: Optional[str] = None
    multiplier: Optional[float] = None
    bond: Optional[Bond] = None
    expr: Optional[str] = None
    vol_per_min: Optional[float] = None
    description: str = ""


def _cme(rth, name):
    return {"tz": "America/Chicago", "open": "17:00", "close": "16:00", "days": [1, 2, 3, 4, 5], "rth": list(rth), "name": name}


SESSIONS = {
    "CBOT_UST": _cme(("07:20", "14:00"), "CBOT Globex, Treasury futures"),
    "CME_STIR": _cme(("07:20", "14:00"), "CME Globex, SOFR and Fed funds futures"),
    "CME_EQ": _cme(("08:30", "15:00"), "CME Globex, equity index"),
    "NYMEX": _cme(("08:00", "13:30"), "NYMEX Globex, crude oil"),
    "COMEX": _cme(("07:20", "12:30"), "COMEX Globex, gold"),
    "CME_FX": _cme(("07:20", "14:00"), "CME Globex, FX"),
    "UST_CASH": {"tz": "America/New_York", "open": "18:00", "close": "17:30", "days": [1, 2, 3, 4, 5], "rth": ["08:20", "15:00"],
                 "name": "US Treasuries, cash (BrokerTec/eSpeed)"},
    "SWAPS": {"tz": "America/New_York", "open": "19:00", "close": "17:00", "days": [1, 2, 3, 4, 5], "rth": ["08:00", "16:00"], "name": "SOFR swaps (SEF)"},
    "NYSE": {"tz": "America/New_York", "open": "04:00", "close": "20:00", "days": [1, 2, 3, 4, 5], "rth": ["09:30", "16:00"], "name": "NYSE Arca"},
    "CBOE": {"tz": "America/New_York", "open": "03:15", "close": "16:15", "days": [1, 2, 3, 4, 5], "rth": ["09:30", "16:15"], "name": "Cboe index"},
    "EUREX": {"tz": "Europe/Berlin", "open": "01:10", "close": "22:00", "days": [1, 2, 3, 4, 5], "rth": ["08:00", "17:30"], "name": "Eurex, Euro-Bund"},
    "FIXING": {"tz": "America/New_York", "open": "08:00", "close": "17:00", "days": [1, 2, 3, 4, 5], "rth": ["08:00", "09:00"],
               "name": "NY Fed reference rates (published 08:00 ET)"},
}

# ----------------------------------------------------------------------------- cash Treasuries

_ALNUM = "0123456789ABCDEFGHJKLMNPQRSTUVWXYZ"


def _cusip(prefix: str, n: int) -> str:
    body = prefix + _ALNUM[(n // len(_ALNUM)) % len(_ALNUM)] + _ALNUM[n % len(_ALNUM)]
    total = 0
    for i, ch in enumerate(body):
        v = int(ch) if ch.isdigit() else ord(ch) - 55
        if i % 2 == 1:
            v *= 2
        total += v // 10 + v % 10
    return body + str((10 - total % 10) % 10)


_FRAC = {0.0: "", 0.125: " 1/8", 0.25: " 1/4", 0.375: " 3/8", 0.5: " 1/2", 0.625: " 5/8", 0.75: " 3/4", 0.875: " 7/8"}


def _name(kind: str, coupon: float, maturity: int) -> str:
    dt = datetime.fromtimestamp(maturity / 1000, tz=timezone.utc)
    md = f"{dt.month:02d}/{dt.day:02d}/{str(dt.year)[2:]}"
    w = int(coupon)
    c = f"{w}{_FRAC.get(round(coupon - w, 3), '')}"
    return f"B 0 {md}" if kind == "Bill" else f"TII {c} {md}" if kind == "TIPS" else f"T {c} {md}"


_SEEDS = [
    ("Bill", "4W", 0, "2026-12-17", "2026-11-19", 0, 92, 0), ("Bill", "8W", 0, "2027-01-19", "2026-11-17", 0, 86, 0),
    ("Bill", "13W", 0, "2027-02-18", "2026-11-19", 0, 81, 0), ("Bill", "26W", 0, "2027-05-20", "2026-11-19", 0, 74, 0),
    ("Bill", "52W", 0, "2027-11-04", "2026-11-05", 0, 48, 0),
    ("Note", "2Y", 3.5, "2028-08-31", "2026-08-31", 2, 71, 0.6), ("Note", "2Y", 3.5, "2028-09-30", "2026-09-30", 1, 70, 0.3),
    ("Note", "2Y", 3.375, "2028-10-31", "2026-10-31", 0, 69, -0.4),
    ("Note", "3Y", 3.5, "2029-09-15", "2026-09-15", 2, 58, 0.5), ("Note", "3Y", 3.375, "2029-10-15", "2026-10-15", 1, 58, 0.2),
    ("Note", "3Y", 3.5, "2029-11-15", "2026-11-15", 0, 58, -0.5),
    ("Note", "5Y", 4.0, "2031-02-28", "2026-02-28", 8, 70, 1.2), ("Note", "5Y", 3.75, "2031-05-31", "2026-05-31", 5, 70, 0.9),
    ("Note", "5Y", 3.875, "2031-08-31", "2026-08-31", 2, 70, 0.6), ("Note", "5Y", 3.75, "2031-09-30", "2026-09-30", 1, 70, 0.3),
    ("Note", "5Y", 3.625, "2031-10-31", "2026-10-31", 0, 70, -0.6),
    ("Note", "7Y", 3.875, "2033-08-15", "2023-08-15", 12, 110, 2.1), ("Note", "7Y", 4.0, "2033-09-30", "2026-09-30", 1, 44, 0.4),
    ("Note", "7Y", 3.875, "2033-10-31", "2026-10-31", 0, 44, -0.5),
    ("Note", "10Y", 4.5, "2033-11-15", "2023-11-15", 12, 120, 2.4), ("Note", "10Y", 4.0, "2034-02-15", "2024-02-15", 11, 125, 2.2),
    ("Note", "10Y", 4.375, "2034-05-15", "2024-05-15", 10, 125, 2.0), ("Note", "10Y", 3.875, "2034-08-15", "2024-08-15", 9, 126, 1.9),
    ("Note", "10Y", 4.0, "2036-02-15", "2026-02-15", 3, 92, 0.9), ("Note", "10Y", 4.375, "2036-05-15", "2026-05-15", 2, 92, 0.7),
    ("Note", "10Y", 4.25, "2036-08-15", "2026-08-15", 1, 92, 0.4), ("Note", "10Y", 4.125, "2036-11-15", "2026-11-15", 0, 58, -1.1),
    ("Bond", "30Y", 4.5, "2042-02-15", "2012-02-15", 30, 35, 3.5), ("Bond", "30Y", 3.375, "2042-08-15", "2012-08-15", 29, 33, 3.2),
    ("Bond", "30Y", 4.625, "2044-11-15", "2014-11-15", 25, 34, 2.8), ("Bond", "30Y", 3.0, "2047-05-15", "2017-05-15", 19, 40, 2.4),
    ("Bond", "20Y", 4.625, "2046-02-15", "2026-02-15", 3, 22, 1.5), ("Bond", "20Y", 4.75, "2046-05-15", "2026-05-15", 2, 22, 1.2),
    ("Bond", "20Y", 4.75, "2046-08-15", "2026-08-15", 1, 22, 0.9), ("Bond", "20Y", 4.625, "2046-11-15", "2026-11-15", 0, 16, -0.4),
    ("Bond", "30Y", 2.25, "2052-02-15", "2022-02-15", 18, 60, 3.0), ("Bond", "30Y", 4.125, "2053-08-15", "2023-08-15", 13, 70, 2.0),
    ("Bond", "30Y", 4.5, "2054-11-15", "2024-11-15", 8, 72, 1.5), ("Bond", "30Y", 4.75, "2055-05-15", "2025-05-15", 6, 70, 1.2),
    ("Bond", "30Y", 4.875, "2056-08-15", "2026-08-15", 1, 60, 0.6), ("Bond", "30Y", 4.75, "2056-11-15", "2026-11-15", 0, 30, -0.9),
    ("TIPS", "5Y", 1.625, "2031-10-15", "2026-10-15", 0, 48, 0), ("TIPS", "10Y", 1.875, "2036-07-15", "2026-07-15", 0, 52, 0),
]

BONDS: list[Bond] = []
for _i, (_k, _term, _c, _mat, _iss, _age, _out, _rich) in enumerate(_SEEDS):
    _m = d(_mat)
    _prefix = "912797" if _k == "Bill" else "912810" if _k == "Bond" else "91282C"
    BONDS.append(Bond(_cusip(_prefix, 400 + _i * 7), _name(_k, _c, _m), _k, _c, _m, d(_iss), _term, _age, _out, _rich))
BOND_BY_CUSIP = {b.cusip: b for b in BONDS}

# ----------------------------------------------------------------------------- futures


@dataclass
class Root:
    root: str
    name: str
    basket: tuple
    tick: float
    multiplier: float
    tenor: float
    vol_per_min: float


ROOTS = [
    Root("ZT", "2-Year T-Note", (1.75, 2.0), 1 / 256, 2000, 2, 900), Root("Z3N", "3-Year T-Note", (2.75, 3.0), 1 / 256, 2000, 3, 40),
    Root("ZF", "5-Year T-Note", (4.1667, 5.25), 1 / 128, 1000, 5, 1100), Root("ZN", "10-Year T-Note", (6.5, 8.0), 1 / 64, 1000, 7, 1700),
    Root("TN", "Ultra 10-Year T-Note", (9.4167, 10.0), 1 / 64, 1000, 10, 320), Root("TWE", "20-Year T-Bond", (19.1667, 19.9167), 1 / 32, 1000, 20, 45),
    Root("ZB", "U.S. T-Bond", (15, 25), 1 / 32, 1000, 16, 420), Root("UB", "Ultra T-Bond", (25, 30), 1 / 32, 1000, 28, 330),
]


@dataclass
class Contract:
    sym: str
    root: Root
    code: str
    label: str
    delivery_month: int
    first_notice: int
    last_trade: int
    volume_share: float
    front: bool
    basket: list = field(default_factory=list)  # [(Bond, cf)]


_MONTHS = [("Z6", "Dec 2026", "2026-12-01", "2026-11-30", "2026-12-18", 0.32), ("H7", "Mar 2027", "2027-03-01", "2027-02-26", "2027-03-19", 0.68)]

CONTRACTS: list[Contract] = []
for _r in ROOTS:
    _front = d(_MONTHS[0][2])
    _bonds = [b for b in BONDS if b.type not in ("Bill", "TIPS") and b.issue < _front
              and _r.basket[0] - 1e-6 <= (b.maturity - _front) / (365.25 * 86_400_000) <= _r.basket[1] + 1e-6]
    for _j, (_code, _label, _dlv, _fnd, _ltd, _share) in enumerate(_MONTHS):
        _dm = d(_dlv)
        CONTRACTS.append(Contract(_r.root if _j == 0 else f"{_r.root}{_code}", _r, _code, _label, _dm, d(_fnd), d(_ltd), _share, _j == 0,
                                  [(b, conversion_factor(b, _dm)) for b in _bonds]))
CONTRACT_BY_SYM = {c.sym: c for c in CONTRACTS}


@dataclass
class Stir:
    sym: str
    kind: str
    label: str
    start: int
    end: int


STIR = [
    Stir("ZQZ6", "ZQ", "30-Day Fed Funds Dec 2026", d("2026-12-01"), d("2026-12-31")), Stir("ZQF7", "ZQ", "30-Day Fed Funds Jan 2027", d("2027-01-01"), d("2027-01-31")),
    Stir("ZQG7", "ZQ", "30-Day Fed Funds Feb 2027", d("2027-02-01"), d("2027-02-28")),
    Stir("SR3Z6", "SR3", "3-Month SOFR Dec 2026", d("2026-12-16"), d("2027-03-17")), Stir("SR3H7", "SR3", "3-Month SOFR Mar 2027", d("2027-03-17"), d("2027-06-16")),
    Stir("SR3M7", "SR3", "3-Month SOFR Jun 2027", d("2027-06-16"), d("2027-09-15")), Stir("SR3U7", "SR3", "3-Month SOFR Sep 2027", d("2027-09-15"), d("2027-12-15")),
    Stir("SR3Z7", "SR3", "3-Month SOFR Dec 2027", d("2027-12-15"), d("2028-03-15")), Stir("SR3H8", "SR3", "3-Month SOFR Mar 2028", d("2028-03-15"), d("2028-06-21")),
]
STIR_BY_SYM = {s.sym: s for s in STIR}
FOMC = [d(x) for x in ["2026-12-09", "2027-01-27", "2027-03-17", "2027-04-28", "2027-06-16", "2027-07-28", "2027-09-22", "2027-10-27", "2027-12-08"]]

TENORS = [("US1M", 1 / 12, "1M"), ("US3M", 0.25, "3M"), ("US6M", 0.5, "6M"), ("US1Y", 1, "1Y"), ("US2Y", 2, "2Y"), ("US3Y", 3, "3Y"),
          ("US5Y", 5, "5Y"), ("US7Y", 7, "7Y"), ("US10Y", 10, "10Y"), ("US20Y", 20, "20Y"), ("US30Y", 30, "30Y")]
SWAP_TENORS = [("SOFR1Y", 1), ("SOFR2Y", 2), ("SOFR5Y", 5), ("SOFR10Y", 10), ("SOFR30Y", 30)]


def otr_for(sym: str) -> Optional[Bond]:
    t = next((x for x in TENORS if x[0] == sym), None)
    if not t:
        return None
    label = t[2]
    alias = {"1M": "4W", "3M": "13W", "6M": "26W", "1Y": "52W"}.get(label)
    return next((b for b in BONDS if b.age == 0 and b.type != "TIPS" and (b.term == label or b.term == alias)), None)


# ----------------------------------------------------------------------------- the symbol list


def _I(**kw) -> Instrument:
    return Instrument(**kw)


INSTRUMENTS: list[Instrument] = []
INSTRUMENTS += [_I(sym=s, name=f"US Treasury {l} yield (on-the-run)", dataset="ust_yields", kind="yield", unit="%", tick=0.001, decimals=3,
                   has_volume=False, session="UST_CASH", tenor=t) for s, t, l in TENORS]
for _s, _n, _e in [("T3M10Y", "3M/10Y curve (bp)", "100*(US10Y-US3M)"), ("T2S10S", "2s10s curve (bp)", "100*(US10Y-US2Y)"),
                   ("T5S30S", "5s30s curve (bp)", "100*(US30Y-US5Y)"), ("T2S5S10S", "2s5s10s butterfly (bp)", "100*(2*US5Y-US2Y-US10Y)"),
                   ("T5S10S30S", "5s10s30s butterfly (bp)", "100*(2*US10Y-US5Y-US30Y)")]:
    INSTRUMENTS.append(_I(sym=_s, name=_n, dataset="curve_spreads", kind="spread", unit="bp", tick=0.1, decimals=1, has_volume=False, session="UST_CASH", expr=_e))
INSTRUMENTS += [_I(sym=s, name=f"SOFR OIS swap {t}Y", dataset="swaps", kind="swap", unit="%", tick=0.001, decimals=3, has_volume=False, session="SWAPS", tenor=t)
                for s, t in SWAP_TENORS]
for _s, _n, _e in [("SS2Y", "2Y swap spread (bp)", "100*(SOFR2Y-US2Y)"), ("SS10Y", "10Y swap spread (bp)", "100*(SOFR10Y-US10Y)"),
                   ("SS30Y", "30Y swap spread (bp)", "100*(SOFR30Y-US30Y)")]:
    INSTRUMENTS.append(_I(sym=_s, name=_n, dataset="swaps", kind="spread", unit="bp", tick=0.1, decimals=1, has_volume=False, session="SWAPS", expr=_e))
INSTRUMENTS += [
    _I(sym="TIPS5Y", name="5Y TIPS real yield", dataset="inflation", kind="yield", unit="%", tick=0.001, decimals=3, has_volume=False, session="UST_CASH", tenor=5),
    _I(sym="TIPS10Y", name="10Y TIPS real yield", dataset="inflation", kind="yield", unit="%", tick=0.001, decimals=3, has_volume=False, session="UST_CASH", tenor=10),
    _I(sym="BEI5Y", name="5Y breakeven inflation", dataset="inflation", kind="spread", unit="%", tick=0.001, decimals=3, has_volume=False, session="UST_CASH", expr="US5Y-TIPS5Y"),
    _I(sym="BEI10Y", name="10Y breakeven inflation", dataset="inflation", kind="spread", unit="%", tick=0.001, decimals=3, has_volume=False, session="UST_CASH", expr="US10Y-TIPS10Y"),
    _I(sym="SOFR", name="SOFR (overnight fixing)", dataset="funding", kind="rate", unit="%", tick=0.01, decimals=2, has_volume=False, session="FIXING"),
    _I(sym="EFFR", name="Effective fed funds rate", dataset="funding", kind="rate", unit="%", tick=0.01, decimals=2, has_volume=False, session="FIXING"),
    _I(sym="GCREPO", name="GC repo, overnight (DVP)", dataset="funding", kind="rate", unit="%", tick=0.01, decimals=2, has_volume=False, session="UST_CASH"),
]
for _c in CONTRACTS:
    INSTRUMENTS.append(_I(sym=_c.sym, name=f"{_c.root.name} {_c.label}", dataset="ust_futures", kind="future", unit="price", tick=_c.root.tick, thirty_seconds=True,
                          decimals=6, has_volume=True, session="CBOT_UST", root=_c.root.root, month=_c.code, multiplier=_c.root.multiplier, tenor=_c.root.tenor,
                          vol_per_min=_c.root.vol_per_min * _c.volume_share,
                          description=f"{_c.code}: first notice {datetime.fromtimestamp(_c.first_notice / 1000, tz=timezone.utc).date()}"))
for _i, _st in enumerate(STIR):
    INSTRUMENTS.append(_I(sym=_st.sym, name=_st.label, dataset="stir", kind="stir", unit="price", tick=0.0025 if _st.kind == "ZQ" or _i == 3 else 0.005, decimals=4,
                          has_volume=True, session="CME_STIR", root=_st.kind, multiplier=4167 if _st.kind == "ZQ" else 2500,
                          vol_per_min=120 if _st.kind == "ZQ" else 900 / (1 + (_i - 3) * 0.6)))
INSTRUMENTS += [
    _I(sym="TLT", name="iShares 20+ Year Treasury Bond ETF", dataset="etfs", kind="etf", unit="price", tick=0.01, decimals=2, has_volume=True, session="NYSE", tenor=20, vol_per_min=52000),
    _I(sym="IEF", name="iShares 7-10 Year Treasury Bond ETF", dataset="etfs", kind="etf", unit="price", tick=0.01, decimals=2, has_volume=True, session="NYSE", tenor=8, vol_per_min=21000),
    _I(sym="SHY", name="iShares 1-3 Year Treasury Bond ETF", dataset="etfs", kind="etf", unit="price", tick=0.01, decimals=2, has_volume=True, session="NYSE", tenor=2, vol_per_min=9000),
    _I(sym="ES", name="E-mini S&P 500 Dec 2026", dataset="cross_asset", kind="equity", unit="price", tick=0.25, decimals=2, has_volume=True, session="CME_EQ", vol_per_min=2400),
    _I(sym="NQ", name="E-mini Nasdaq-100 Dec 2026", dataset="cross_asset", kind="equity", unit="price", tick=0.25, decimals=2, has_volume=True, session="CME_EQ", vol_per_min=700),
    _I(sym="CL", name="Crude Oil Jan 2027", dataset="cross_asset", kind="commodity", unit="price", tick=0.01, decimals=2, has_volume=True, session="NYMEX", vol_per_min=900),
    _I(sym="GC", name="Gold Feb 2027", dataset="cross_asset", kind="commodity", unit="price", tick=0.1, decimals=1, has_volume=True, session="COMEX", vol_per_min=320),
    _I(sym="6E", name="Euro FX Dec 2026", dataset="cross_asset", kind="fx", unit="price", tick=0.00005, decimals=5, has_volume=True, session="CME_FX", vol_per_min=300),
    _I(sym="6J", name="Japanese Yen Dec 2026", dataset="cross_asset", kind="fx", unit="price", tick=0.0000005, decimals=7, has_volume=True, session="CME_FX", vol_per_min=220),
    _I(sym="FGBL", name="Euro-Bund Dec 2026", dataset="cross_asset", kind="future", unit="price", tick=0.01, decimals=2, has_volume=True, session="EUREX", vol_per_min=800),
    _I(sym="MOVE", name="ICE BofA MOVE Index (Treasury implied vol)", dataset="volatility", kind="index", unit="index", tick=0.01, decimals=2, has_volume=False, session="UST_CASH"),
    _I(sym="VIX", name="Cboe Volatility Index", dataset="volatility", kind="index", unit="index", tick=0.01, decimals=2, has_volume=False, session="CBOE"),
]
for _b in BONDS:
    INSTRUMENTS.append(_I(sym=_b.cusip, name=f"{_b.name}{' (OTR)' if _b.age == 0 else ''}", dataset="ust_cash", kind="bond", unit="price", tick=1 / 256,
                          thirty_seconds=_b.type != "Bill", decimals=4 if _b.type == "Bill" else 6, has_volume=False, session="UST_CASH", bond=_b,
                          description=f"{_b.term} {_b.type}{', on-the-run' if _b.age == 0 else ', 1st off-the-run' if _b.age == 1 else ''}"))
for _b in BONDS:
    INSTRUMENTS.append(_I(sym=f"{_b.cusip}.Y", name=f"{_b.name} yield", dataset="ust_cash_yields", kind="yield", unit="%", tick=0.001, decimals=3,
                          has_volume=False, session="UST_CASH", description=f"the yield of {_b.cusip}"))
for _b in BONDS:
    INSTRUMENTS.append(_I(sym=f"{_b.cusip}.IDIO", name=f"{_b.name} idio (bp)", dataset="ust_idio", kind="spread", unit="bp", tick=0.01, decimals=2,
                          has_volume=False, session="UST_CASH", description=f"{_b.cusip} rich (-) or cheap (+) to the fitted curve, bp"))

BY_SYM = {i.sym: i for i in INSTRUMENTS}
FUTURES_ROLLS = ["ZT-ZTH7", "ZF-ZFH7", "ZN-ZNH7", "TN-TNH7", "ZB-ZBH7", "UB-UBH7"]
WATCHLIST_CORE = ["US2Y", "US5Y", "US10Y", "US30Y", "T2S10S", "T5S30S", "ZT", "ZF", "ZN", "TN", "ZB", "UB", "SR3H7", "SOFR10Y", "SS10Y", "BEI10Y", "TLT",
                  "ES", "GC", "6J", "MOVE"]


def session_for(sym: str) -> Optional[dict]:
    """The calendar of a symbol; a spread's is its first leg's."""
    import re

    if sym in BY_SYM:
        return SESSIONS[BY_SYM[sym].session]
    m = re.search(r"\d*[A-Za-z][A-Za-z0-9_.]*", sym)
    return SESSIONS[BY_SYM[m.group(0)].session] if m and m.group(0) in BY_SYM else None


def price_text(i: Optional[Instrument], v) -> str:
    from .bonds import fmt32
    import math

    if v is None or not math.isfinite(v):
        return ""
    if i is None:
        return f"{v:.4f}"
    if i.thirty_seconds:
        return fmt32(v, i.tick or 1 / 256)
    return f"{v:.{i.decimals}f}"


def meta(sym: str) -> dict:
    """What a chart needs to show an instrument: unit, notation, decimals, name; for a typed spread, from its legs."""
    import re

    i = BY_SYM.get(sym)
    if i:
        return {"name": i.name, "unit": i.unit, "tick": i.tick, "thirty_seconds": i.thirty_seconds, "decimals": i.decimals, "has_volume": i.has_volume,
                "kind": i.kind, "dataset": i.dataset}
    legs = [BY_SYM[s] for s in re.findall(r"\d*[A-Za-z][A-Za-z0-9_.]*", sym) if s in BY_SYM]
    first = legs[0] if legs else None
    ratio = "/" in sym
    simple = bool(re.match(r"^[A-Z0-9.]+-[A-Z0-9.]+$", sym))
    return {"name": f"{'Ratio' if ratio else 'Spread'} {sym}", "unit": (first.unit if first else "price"), "tick": None,
            "thirty_seconds": bool(first and not ratio and simple and all(l.thirty_seconds for l in legs)),
            "decimals": 5 if ratio else (4 if first and first.unit == "price" else max(2, first.decimals if first else 2)),
            "has_volume": False, "kind": "spread", "dataset": "user_spreads"}
