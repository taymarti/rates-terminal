"""
The market simulator, vectorized with numpy: a few factors drive every instrument, so they move together the way
they do (the same model as the browser mock, terminal/src/model/market.ts).

  curve      level, slope, curvature and a front-end policy factor -> every Treasury yield by tenor
  bonds      each CUSIP: the curve at its remaining term + its richness + idio (slow, fast); price from yield
  futures    the cheapest of the deliverable basket's forward prices over conversion factors
  STIR       SR3 and ZQ from the curve's implied forwards; swaps, breakevens, fixings, ETFs, cross-asset

History is generated at start (seeded by the date, so a restart shows the same past) as ticks for Deephaven:
  long ticks   two years before the intraday history, four a day (daily and weekly charts)
  ticks        five days every 20 seconds, then the last hour by the second; then live, every step
Data releases (08:30 / 10:00 ET), auctions, FOMC days and headlines jump the curve; volatility follows the New
York day. The market never closes in the simulation: session shading still follows each exchange's calendar.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Optional

import numpy as np

from .bonds import DAY_MS, accrued, price_from_yield
from .universe import BONDS, CONTRACTS, INSTRUMENTS, STIR, SWAP_TENORS, TENORS, Instrument, otr_for

YEAR_S = 365.25 * 86400
YEAR_MS = YEAR_S * 1000
BASE = [(1 / 12, 3.72), (0.25, 3.68), (0.5, 3.6), (1, 3.5), (2, 3.42), (3, 3.45), (5, 3.62), (7, 3.82), (10, 4.08), (20, 4.62), (30, 4.68)]
SWAP_SPREAD = [(1, -0.08), (2, -0.17), (5, -0.27), (10, -0.44), (30, -0.82)]
BEI_BASE = [(2, 2.45), (5, 2.33), (10, 2.28), (30, 2.24)]
L, Sl, C, P, SS, BEI, BUND, ES, NQ, CL, GC, EUR, JPY, MOVE, VIX, FUND = range(16)
NF = 16
VOL = np.array([0.85, 0.55, 0.35, 0.45, 0.12, 0.3, 0.45, 0.16, 0.1, 0.32, 0.15, 0.07, 0.09, 0.9, 1.4, 0.4])
KAPPA = np.array([0.6, 0.8, 1.2, 0.8, 2, 1.5, 1.5, 0.05, 0.5, 0.3, 0.1, 0.3, 0.3, 20, 20, 200])


def fnv(*xs) -> int:
    h = 2166136261
    for x in xs:
        for ch in str(x):
            h ^= ord(ch)
            h = (h * 16777619) & 0xFFFFFFFF
    return h


def interp(pts, t: float) -> float:
    t = max(t, 1 / 365)
    if t <= pts[0][0]:
        return pts[0][1]
    x = math.log(t)
    for i in range(1, len(pts)):
        if t <= pts[i][0]:
            x0, x1 = math.log(pts[i - 1][0]), math.log(pts[i][0])
            return pts[i - 1][1] + (pts[i][1] - pts[i - 1][1]) * (x - x0) / (x1 - x0)
    return pts[-1][1]


def loading(tenor: float) -> np.ndarray:
    """A tenor's curve loadings: base, level, slope, curvature, policy."""
    t = max(tenor, 1 / 365)
    lt = math.log(t / 2) / math.log(15)
    return np.array([interp(BASE, t), 1 - 0.8 * math.exp(-t / 0.35), max(-1.2, min(1.2, lt)), math.exp(-(math.log(t / 5) ** 2) / 0.8) - 0.3,
                     math.exp(-t / 1.2)])


def curve_at(F: np.ndarray, tenor: float) -> float:
    return float(loading(tenor) @ np.array([1.0, F[L], F[Sl], F[C], F[P]]))


def season(ms: float) -> float:
    h = ((ms / 3_600_000 - 5) % 24 + 24) % 24  # ET hour (EST)
    return 1.45 if 8.33 <= h < 15 else 0.9 if 15 <= h < 17 else 0.85 if 3 <= h < 8.33 else 0.5


# ----------------------------------------------------------------------------- events


@dataclass
class MarketEvent:
    t: int
    kind: str
    title: str
    jL: float
    jS: float
    jP: float
    importance: int
    consensus: str = ""
    actual: str = ""
    prior: str = ""


_RELEASES = [
    ("Initial Jobless Claims", lambda d: d.weekday() == 3, 8.5, 2, 0.02, "K", 228, 9),
    ("Nonfarm Payrolls", lambda d: d.weekday() == 4 and d.day <= 7, 8.5, 3, 0.07, "K", 135, 45),
    ("CPI m/m", lambda d: 10 <= d.day <= 14 and d.weekday() == 2, 8.5, 3, 0.06, "%", 0.25, 0.08),
    ("Retail Sales m/m", lambda d: 15 <= d.day <= 17 and 1 <= d.weekday() <= 3, 8.5, 2, 0.035, "%", 0.3, 0.3),
    ("Core PCE m/m", lambda d: d.day >= 26 and d.weekday() == 4, 8.5, 3, 0.05, "%", 0.22, 0.06),
    ("ISM Manufacturing", lambda d: d.day <= 3 and d.weekday() == 0, 10, 2, 0.03, "", 49.5, 1.2),
    ("JOLTS Job Openings", lambda d: 2 <= d.day <= 8 and d.weekday() == 1, 10, 2, 0.025, "M", 7.4, 0.3),
    ("Consumer Confidence", lambda d: d.day >= 24 and d.weekday() == 1, 10, 1, 0.015, "", 96, 4),
    ("Durable Goods Orders m/m", lambda d: 23 <= d.day <= 27 and d.weekday() == 2, 8.5, 2, 0.025, "%", 0.6, 1.5),
    ("UMich Sentiment (final)", lambda d: 22 <= d.day <= 28 and d.weekday() == 4, 10, 1, 0.012, "", 71, 2.5),
]
_HEADLINES = [
    'Fed Governor says policy is "well positioned" for incoming data', "Treasury refunding: coupon sizes seen unchanged through next year",
    "Dealer survey: year-end balance sheet constraints tighter than last year", "Japan 30Y yield touches multi-decade high; US long end follows",
    "Fed regional president flags upside inflation risks from tariffs", "Large block trade in TY Mar futures; curve steepener interest reported",
    "Repo: GC trades firm into month-end; SOFR seen higher", "Oil slides on OPEC+ supply headlines; breakevens lower",
    'ECB speaker: further easing "not off the table"; Bund rallies', "Fed Vice Chair: balance-sheet runoff could end sooner than planned",
    "US 7Y auction preview: WI trading 0.5bp cheap to the curve", "Real money buying reported in 20Y sector",
    "Equities wobble on tech earnings; Treasuries catch a bid", "CTA models flip to short duration across the curve",
    "Strong demand for SOFR Mar27/Jun27 call spreads",
]
_FOMC_DAYS = {int(datetime(2026, 12, 9, tzinfo=timezone.utc).timestamp() * 1000), int(datetime(2027, 1, 27, tzinfo=timezone.utc).timestamp() * 1000)}


def _et(day_ms: int, hour: float) -> int:
    return day_ms + int(round((hour + 5) * 3_600_000))


def events_between(frm: int, to: int) -> list[MarketEvent]:
    """Every scheduled event in [frm, to): releases, auctions, FOMC decisions, headlines. Deterministic."""
    out: list[MarketEvent] = []
    day0 = frm // DAY_MS * DAY_MS
    day = day0
    while day < to + DAY_MS:
        dt = datetime.fromtimestamp(day / 1000, tz=timezone.utc)
        if dt.weekday() < 5:
            for title, when, et, imp, scale, unit, mean, sd in _RELEASES:
                if not when(dt):
                    continue
                t = _et(day, et)
                if not frm <= t < to:
                    continue
                g = np.random.default_rng(fnv(title, day))
                z = g.standard_normal()
                dec = 2 if unit == "%" else 1 if mean < 20 else 0
                cons = mean + g.standard_normal() * sd * 0.3
                act = cons + z * sd
                prior = mean + g.standard_normal() * sd
                sign = -1 if "Claims" in title else 1
                out.append(MarketEvent(t, "release", title, sign * z * scale, -sign * z * scale * 0.35, sign * z * scale * 0.6, imp,
                                       f"{cons:.{dec}f}{unit}", f"{act:.{dec}f}{unit}", f"{prior:.{dec}f}{unit}"))
            wd, dom = dt.weekday(), dt.day
            auction = ("13W/26W bill auction" if wd == 0 else "2Y note auction" if wd == 1 and 22 <= dom <= 28 else
                       "5Y note auction" if wd == 2 and 22 <= dom <= 28 else "7Y note auction" if wd == 3 and 22 <= dom <= 28 else None)
            if auction:
                t = _et(day, 13)
                if frm <= t < to:
                    tail = np.random.default_rng(fnv(auction, day)).standard_normal() * 0.8
                    out.append(MarketEvent(t, "auction", auction, tail * 0.008, 0, 0, 1 if "bill" in auction else 2,
                                           actual=f"{'+' if tail >= 0 else ''}{tail:.1f}bp tail"))
        if day in _FOMC_DAYS:
            t = _et(day, 14)
            if frm <= t < to:
                out.append(MarketEvent(t, "fomc", "FOMC rate decision", 0.02, -0.02, -0.12, 3, consensus="3.25-3.50%"))
        day += DAY_MS
    slot = int(6.5 * 60_000)
    s = frm // slot * slot
    while s < to:
        g = np.random.default_rng(fnv("news", s))
        t = s + int(g.random() * slot)
        if frm <= t < to:
            out.append(MarketEvent(t, "headline", _HEADLINES[int(g.random() * len(_HEADLINES))], g.standard_normal() * 0.0015,
                                   g.standard_normal() * 0.001, g.standard_normal() * 0.0012, 1))
        s += slot
    return sorted(out, key=lambda e: e.t)


_TOKEN = re.compile(r"\d+(?:\.\d+)?(?![A-Z_])|[A-Z0-9_.]*[A-Z][A-Z0-9_.]*|[-+*/()]")


# ----------------------------------------------------------------------------- the market


class Market:
    def __init__(self, now_ms: Optional[int] = None):
        now = now_ms or int(datetime.now(tz=timezone.utc).timestamp() * 1000)
        self.inst: list[Instrument] = list(INSTRUMENTS)
        self.index = {i.sym: k for k, i in enumerate(self.inst)}
        n = len(self.inst)
        self.F = np.zeros(NF)
        self.idio = np.zeros(n)
        self.vals = np.full(n, np.nan)
        self.vols = np.zeros(n)
        nb = len(BONDS)
        self.bond_idx = {b.cusip: i for i, b in enumerate(BONDS)}
        self.slow = np.zeros(nb)
        self.slow_due = 0.0
        self.bond_y = np.full(nb, np.nan)
        self._tay = {"day": np.full(nb, -1.0), "y0": np.zeros(nb), "p0": np.zeros(nb), "d1": np.zeros(nb), "d2": np.zeros(nb)}
        self.b_k = np.array([self.index[b.cusip] for b in BONDS])
        self.b_ky = np.array([self.index[f"{b.cusip}.Y"] for b in BONDS])
        self.b_ki = np.array([self.index[f"{b.cusip}.IDIO"] for b in BONDS])
        self.b_mat = np.array([b.maturity for b in BONDS], dtype=float)
        self.b_issue = np.array([b.issue for b in BONDS], dtype=float)
        self.b_rich = np.array([b.rich_bp / 100 for b in BONDS])
        self.b_bill = np.array([b.type == "Bill" for b in BONDS])
        use_noise = {"bond", "swap", "stir", "future"}
        self.noisy = np.array([k for k, i in enumerate(self.inst) if i.kind in use_noise or i.sym.startswith("TIPS") or (i.kind == "yield" and i.sym in dict((t[0], 1) for t in TENORS) and not otr_for(i.sym))])
        self.traded = np.array([k for k, i in enumerate(self.inst) if i.has_volume and i.vol_per_min])
        self.vpm = np.array([self.inst[k].vol_per_min for k in self.traded])
        tk = [(k, i.tick) for k, i in enumerate(self.inst) if i.tick and not i.expr and i.kind not in ("yield", "swap", "spread", "rate")]
        self.ticked = np.array([k for k, _ in tk])
        self.ticks_of = np.array([t for _, t in tk])
        self.exprs: list[tuple[int, Callable]] = []
        for k, i in enumerate(self.inst):
            if i.expr:
                self.exprs.append((k, self._compile(i.expr)))
        self._ix = {k: self.index[k] for k in ["SOFR", "EFFR", "GCREPO", "TLT", "IEF", "SHY", "ES", "NQ", "CL", "GC", "6E", "6J", "FGBL", "MOVE", "VIX"]}
        self.rng = np.random.default_rng(1)
        self.dc = None
        self.applied: list[MarketEvent] = []
        self.on_event: list[Callable[[MarketEvent], None]] = []
        # the start of the trading day (07:00 ET = 12:00 UTC) and the market then
        self.sod_ms = (now - 12 * 3_600_000) // DAY_MS * DAY_MS + 12 * 3_600_000
        self.sod: Optional[dict] = None
        self.on_sod: list[Callable[[], None]] = []
        self.t = now
        self.start = now
        self.history = self._generate(now)
        self._build_daily()

    # ---------------------------------------------------------------- helpers

    def _compile(self, expr: str) -> Callable:
        toks = _TOKEN.findall(expr.replace(" ", "").upper())
        src = "".join(f"V[:, {self.index[t]}]" if re.search(r"[A-Z]", t) else t for t in toks)
        return eval(f"lambda V: {src}")  # noqa: S307 (our own expressions)

    def value(self, sym: str) -> float:
        k = self.index.get(sym)
        return float("nan") if k is None else float(self.vals[k])

    def yield_of(self, cusip: str) -> float:
        i = self.bond_idx.get(cusip)
        return float("nan") if i is None else float(self.bond_y[i])

    def sod_value(self, sym: str) -> float:
        k = self.index.get(sym)
        return float("nan") if k is None or self.sod is None else float(self.sod["vals"][k])

    def sod_yield(self, cusip: str) -> float:
        i = self.bond_idx.get(cusip)
        return float("nan") if i is None or self.sod is None else float(self.sod["bond_y"][i])

    def repo(self) -> float:
        v = self.value("GCREPO")
        return v if math.isfinite(v) else 3.65

    def bond_price(self, bi: int, y: float, ms: float) -> float:
        b = BONDS[bi]
        if b.coupon == 0:
            return 100 / (1 + y / 100 * max(0.0, (b.maturity - ms) / YEAR_MS))
        return price_from_yield(b, y, int(ms // DAY_MS * DAY_MS))

    def basket(self, c, ms: float) -> list[dict]:
        """A contract's deliverables now: price, carry to delivery, forward price over conversion factor."""
        r = self.repo()
        days = max(0.0, (c.delivery_month - ms) / DAY_MS)
        out = []
        for bond, cf in c.basket:
            bi = self.bond_idx[bond.cusip]
            y = float(self.bond_y[bi])
            price = float(self.vals[self.b_k[bi]])
            ai = accrued(bond, int(ms // DAY_MS * DAY_MS))
            carry = (bond.coupon - r * (price + ai) / 100) * days / 365
            out.append({"bond": bond, "cf": cf, "y": y, "price": price, "ai": ai, "carry": carry, "implied": (price - carry) / cf})
        return out

    # ---------------------------------------------------------------- dynamics

    def _step_factors(self, dt_s: float, ms: float):
        F = self.F
        g = self.rng
        dt = dt_s / YEAR_S
        n = g.standard_normal(NF)
        zL = n[0]
        z = np.empty(NF)
        z[L] = zL
        z[Sl] = -0.3 * zL + 0.954 * n[1]
        z[C] = n[2]
        z[P] = 0.55 * zL + 0.835 * n[3]
        z[SS] = 0.2 * zL + 0.98 * n[4]
        z[BEI] = 0.45 * zL + 0.89 * n[5]
        z[BUND] = n[6]
        z[ES] = -0.3 * zL + 0.954 * n[7]
        z[NQ] = n[8]
        z[CL] = 0.15 * zL + 0.99 * n[9]
        z[GC] = -0.3 * zL + 0.954 * n[10]
        z[EUR] = -0.2 * zL + 0.98 * n[11]
        z[JPY] = -0.5 * zL + 0.866 * n[12]
        z[MOVE] = 0.3 * abs(zL) - 0.24 + 0.95 * n[13]
        z[VIX] = -0.7 * z[ES] + 0.714 * n[14]
        z[FUND] = n[15]
        # exact Ornstein-Uhlenbeck steps (stable at any step size: six hours of history, a quarter second live)
        decay = np.exp(-KAPPA * dt)
        F[:] = F * decay + VOL * season(ms) * np.sqrt((1 - decay * decay) / (2 * KAPPA)) * z
        if len(self.noisy):
            a = math.exp(-4000 * dt)
            self.idio[self.noisy] = self.idio[self.noisy] * a + 0.25 * season(ms) * math.sqrt((1 - a * a) / 8000) * g.standard_normal(len(self.noisy))
        self.slow_due += dt_s
        if self.slow_due >= 60:
            sdt = self.slow_due / YEAR_S
            self.slow_due = 0.0
            a = math.exp(-12 * sdt)
            self.slow = self.slow * a + 0.05 * math.sqrt((1 - a * a) / 24) * g.standard_normal(len(self.slow))

    def _apply_event(self, e: MarketEvent):
        self.F[L] += e.jL
        self.F[Sl] += e.jS
        self.F[P] += e.jP
        if e.kind != "headline":
            self.F[MOVE] += min(0.12, abs(e.jL) * 2.5)
        self.applied.append(e)
        del self.applied[:-400]
        for cb in self.on_event:
            try:
                cb(e)
            except Exception as ex:  # noqa: BLE001
                print("event listener failed:", ex)

    def _day(self, ms: float) -> dict:
        """What valuation needs that only changes once a day (tenors, loadings, accrued, carry terms, fixings), as
        matrices so a block of steps values with a few matrix products."""
        day = int(ms // DAY_MS)
        if self.dc is not None and self.dc["day"] == day:
            return self.dc
        yr = lambda t: (t - ms) / YEAR_MS  # noqa: E731
        ix = self.index
        tenors = (self.b_mat - ms) / YEAR_MS
        W = np.array([loading(t) for t in tenors])
        bei = np.array([interp(BEI_BASE, t) if b.type == "TIPS" else 0.0 for b, t in zip(BONDS, tenors)])
        otr_k, otr_b, otr_W = [], [], []
        for s, t, _ in TENORS:
            o = otr_for(s)
            otr_k.append(ix[s])
            otr_b.append(self.bond_idx[o.cusip] if o else -1)
            otr_W.append(loading(t))
        st = [(ix[s.sym], s.kind == "ZQ", max(1 / 52, yr((s.start + s.end) / 2))) for s in STIR]
        fut_k, it_b, it_cf, it_ai, it_days, it_cpn, starts = [], [], [], [], [], [], []
        for c in CONTRACTS:
            if not c.basket:
                continue
            fut_k.append(ix[c.sym])
            starts.append(len(it_b))
            for bond, cf in c.basket:
                it_b.append(self.bond_idx[bond.cusip])
                it_cf.append(cf)
                it_ai.append(accrued(bond, day * DAY_MS))
                it_days.append(max(0.0, (c.delivery_month - ms) / DAY_MS))
                it_cpn.append(bond.coupon)
        g = np.random.default_rng(fnv("fix", day))
        dom = datetime.fromtimestamp(day * 86400, tz=timezone.utc).day
        self.dc = {
            "day": day, "W": W, "bei": bei, "tips_mask": bei > 0, "exists": (self.b_issue <= ms) & (tenors > 0),
            "otr_k": np.array(otr_k), "otr_b": np.array(otr_b), "otr_W": np.array(otr_W),
            "sw_k": np.array([ix[s] for s, _ in SWAP_TENORS]), "sw_W": np.array([loading(t) for _, t in SWAP_TENORS]),
            "sw_ss": np.array([interp(SWAP_SPREAD, t) for _, t in SWAP_TENORS]), "sw_w": np.array([0.5 + 0.5 * min(1, t / 10) for _, t in SWAP_TENORS]),
            "tp_k": np.array([ix["TIPS5Y"], ix["TIPS10Y"]]), "tp_W": np.array([loading(5), loading(10)]), "tp_bei": np.array([interp(BEI_BASE, 5), interp(BEI_BASE, 10)]),
            "tp_m": np.array([1.1, 1.0]),
            "st_k": np.array([k for k, _, _ in st]), "st_t": np.array([t for _, _, t in st]), "st_adj": np.array([-0.07 if zq else 0.03 for _, zq, _ in st]),
            "st_W0": np.array([loading(t) for _, _, t in st]), "st_Wu": np.array([loading(t + 0.05) for _, _, t in st]),
            "st_Wd": np.array([loading(max(1 / 365, t - 0.05)) for _, _, t in st]),
            "etf_W": np.array([loading(20), loading(8), loading(2)]),
            "fut_k": np.array(fut_k), "it_b": np.array(it_b), "it_cf": np.array(it_cf), "it_ai": np.array(it_ai), "it_days": np.array(it_days),
            "it_cpn": np.array(it_cpn), "starts": np.array(starts),
            "sofr": 3.62 + g.standard_normal() * 0.015 + (0.06 if dom >= 28 or dom <= 1 else 0), "effr": 3.58,
        }
        return self.dc

    def _block_prices(self, Y: np.ndarray, ts_ms: np.ndarray, dc: dict) -> np.ndarray:
        """Bond prices for a block of steps within one day: exact at the block's first step, a second-order Taylor
        expansion in yield after it (a day's moves are small); bills exactly."""
        P = np.full(Y.shape, np.nan)
        settle = int(ts_ms[0] // DAY_MS * DAY_MS)
        for bi in np.nonzero(dc["exists"])[0]:
            b = BONDS[bi]
            col = Y[:, bi]
            if b.coupon == 0:
                P[:, bi] = 100 / (1 + col / 100 * np.maximum(0, (b.maturity - ts_ms) / YEAR_MS))
                continue
            y0 = float(col[0])
            if not math.isfinite(y0):
                continue
            p0 = price_from_yield(b, y0, settle)
            if len(col) == 1:
                P[0, bi] = p0
                continue
            up = price_from_yield(b, y0 + 0.05, settle)
            dn = price_from_yield(b, y0 - 0.05, settle)
            dy = col - y0
            P[:, bi] = p0 + (up - dn) / 0.1 * dy + 0.5 * (up + dn - 2 * p0) / 0.0025 * dy * dy
        return P

    def _valuate_block(self, ts_ms: np.ndarray, Fb: np.ndarray, Ib: np.ndarray, Sb: np.ndarray):
        """Every instrument's value at each step of a block (one UTC day): factors Fb (T x 16), fast idio Ib
        (T x noisy), slow idio Sb (T x bonds). Returns values (T x n) and bond yields (T x bonds)."""
        dc = self._day(float(ts_ms[0]))
        T, n = len(ts_ms), len(self.inst)
        V = np.full((T, n), np.nan)
        idf = np.zeros((T, n))
        if len(self.noisy):
            idf[:, self.noisy] = Ib
        f = np.column_stack([np.ones(T), Fb[:, L], Fb[:, Sl], Fb[:, C], Fb[:, P]])
        ex = dc["exists"]
        idio_b = self.b_rich + Sb + idf[:, self.b_k] * 0.8
        Y = f @ dc["W"].T + idio_b - np.where(dc["tips_mask"], dc["bei"] + Fb[:, [BEI]], 0.0)
        Y[:, ~ex] = np.nan
        V[:, self.b_k] = self._block_prices(Y, ts_ms, dc)
        V[:, self.b_ky] = Y
        V[:, self.b_ki] = np.where(ex, idio_b * 100, np.nan)
        # the on-the-run benchmarks are their issues' yields; before an issue existed, the curve at its tenor
        curve = f @ dc["otr_W"].T + idf[:, dc["otr_k"]] * 0.8
        own = np.where(dc["otr_b"] >= 0, 1, 0).astype(bool)
        oy = np.full(curve.shape, np.nan)
        oy[:, own] = Y[:, dc["otr_b"][own]]
        V[:, dc["otr_k"]] = np.where(np.isfinite(oy), oy, curve)
        V[:, dc["sw_k"]] = f @ dc["sw_W"].T + dc["sw_ss"] + Fb[:, [SS]] * dc["sw_w"] + idf[:, dc["sw_k"]]
        V[:, dc["tp_k"]] = f @ dc["tp_W"].T - dc["tp_bei"] - Fb[:, [BEI]] * dc["tp_m"] + idf[:, dc["tp_k"]]
        ix = self._ix
        sofr = np.round(dc["sofr"] + Fb[:, P] * 0.05, 2)
        V[:, ix["SOFR"]] = sofr
        V[:, ix["EFFR"]] = np.round(dc["effr"] + Fb[:, P] * 0.03, 2)
        repo = sofr + 0.02 + Fb[:, FUND] * 0.02
        V[:, ix["GCREPO"]] = repo
        fwd = f @ dc["st_W0"].T + dc["st_t"] * (f @ dc["st_Wu"].T - f @ dc["st_Wd"].T) / 0.1
        V[:, dc["st_k"]] = 100 - (fwd + dc["st_adj"]) + idf[:, dc["st_k"]] * 0.3
        if len(dc["fut_k"]):
            pb = V[:, self.b_k[dc["it_b"]]]
            carry = (dc["it_cpn"] - repo[:, None] * (pb + dc["it_ai"]) / 100) * dc["it_days"] / 365
            imp = np.where(np.isfinite(pb), (pb - carry) / dc["it_cf"], np.inf)
            V[:, dc["fut_k"]] = np.minimum.reduceat(imp, dc["starts"], axis=1) - 0.04 + idf[:, dc["fut_k"]] * 0.01
        ey = f @ dc["etf_W"].T
        V[:, ix["TLT"]] = 88.4 * np.exp(-16.3 * (ey[:, 0] - 4.62) / 100)
        V[:, ix["IEF"]] = 95.1 * np.exp(-7.3 * (ey[:, 1] - 3.9) / 100)
        V[:, ix["SHY"]] = 82.6 * np.exp(-1.9 * (ey[:, 2] - 3.42) / 100)
        V[:, ix["ES"]] = 6810 * np.exp(Fb[:, ES])
        V[:, ix["NQ"]] = 25150 * np.exp(1.2 * Fb[:, ES] + Fb[:, NQ])
        V[:, ix["CL"]] = 61.2 * np.exp(Fb[:, CL])
        V[:, ix["GC"]] = 4080 * np.exp(Fb[:, GC])
        V[:, ix["6E"]] = 1.1585 * np.exp(Fb[:, EUR])
        V[:, ix["6J"]] = 0.006555 * np.exp(Fb[:, JPY])
        by = (2.7 + 0.55 * Fb[:, L] + Fb[:, BUND]) / 100
        df = (1 + by) ** -8.5
        V[:, ix["FGBL"]] = 6 / by * (1 - df) + 100 * df
        V[:, ix["MOVE"]] = 82 * np.exp(Fb[:, MOVE])
        V[:, ix["VIX"]] = 16.4 * np.exp(Fb[:, VIX])
        if len(self.ticked):
            V[:, self.ticked] = np.round(V[:, self.ticked] / self.ticks_of) * self.ticks_of
        for k, fn in self.exprs:
            V[:, k] = fn(V)
        return V, Y

    def _block_volumes(self, ts_ms: np.ndarray, dts: np.ndarray) -> np.ndarray:
        Q = np.zeros((len(ts_ms), len(self.inst)))
        if len(self.traded):
            s = np.array([season(t) for t in ts_ms])
            Q[:, self.traded] = np.round(self.vpm * (s * dts / 60)[:, None] * (0.4 + self.rng.random((len(ts_ms), len(self.traded))) * 1.2))
        return Q

    def _check_sod(self, ms: float):
        if self.sod is not None and ms < self.sod_ms + DAY_MS:
            return
        if self.sod is not None:
            self.sod_ms += DAY_MS
        if ms < self.sod_ms:
            return
        self.sod = {"ms": self.sod_ms, "F": self.F.copy(), "vals": self.vals.copy(), "bond_y": self.bond_y.copy()}
        for cb in self.on_sod:
            cb()

    # ---------------------------------------------------------------- history

    def _paths(self, times_s, dts, events, reseed_at=None, reseed=None):
        """Factor, fast-idio and slow-idio paths at each step, applying events before the step they precede."""
        T = len(times_s)
        Fp, Ip, Sp = np.empty((T, NF)), np.empty((T, len(self.noisy))), np.empty((T, len(BONDS)))
        ev = 0
        for s in range(T):
            t = int(times_s[s])
            if reseed_at is not None and t == reseed_at:
                self.rng = np.random.default_rng(reseed)
            while ev < len(events) and events[ev].t <= t * 1000:
                self._apply_event(events[ev])
                ev += 1
            self._step_factors(float(dts[s]), t * 1000)
            Fp[s] = self.F
            Ip[s] = self.idio[self.noisy]
            Sp[s] = self.slow
        return Fp, Ip, Sp

    def _valuate_steps(self, times_s, Fp, Ip, Sp):
        """Values and bond yields for every step, a UTC day at a time."""
        V = np.empty((len(times_s), len(self.inst)))
        Y = np.empty((len(times_s), len(BONDS)))
        days = times_s // 86400
        cut = np.r_[0, np.nonzero(np.diff(days))[0] + 1, len(times_s)]
        for a, b in zip(cut[:-1], cut[1:]):
            V[a:b], Y[a:b] = self._valuate_block(times_s[a:b] * 1000.0, Fp[a:b], Ip[a:b], Sp[a:b])
        return V, Y

    def _generate(self, now: int) -> dict:
        import time as _time

        t0 = _time.time()
        now_s = now // 1000
        day_start = now_s // 86400 * 86400
        t1m = day_start - 4 * 86400
        t1s = (now_s - 3600) // 60 * 60
        tday = day_start - 730 * 86400
        # phase A: two years, four steps a day, bridged so the factors end at today's levels (0)
        self.rng = np.random.default_rng(fnv("A", t1m))
        step_a = 6 * 3600
        long_t = np.arange(tday + step_a, t1m + 1, step_a, dtype=np.int64)
        Fa, Ia, Sa = self._paths(long_t - step_a, np.full(len(long_t), step_a), [])
        Fa -= np.outer(np.arange(1, len(long_t) + 1) / len(long_t), Fa[-1])
        long_v, _ = self._valuate_steps(long_t, Fa, Ia, Sa)
        long_q = self._block_volumes(long_t * 1000.0, np.full(len(long_t), float(step_a)))
        # phase B: 20-second steps to the last hour; phase C: the last hour by the second
        self.F[:] = 0
        self.idio[:] = 0
        self.rng = np.random.default_rng(fnv("B", t1m))
        events = events_between(t1m * 1000 - 20_000, now + 1)
        tick_t = np.r_[np.arange(t1m, t1s, 20), np.arange(t1s, now_s + 1)].astype(np.int64)
        dts = np.where(tick_t < t1s, 20.0, 1.0)
        Fp, Ip, Sp = self._paths(tick_t, dts, events, reseed_at=t1s, reseed=fnv("C", now))
        tick_v, tick_y = self._valuate_steps(tick_t, Fp, Ip, Sp)
        tick_q = self._block_volumes(tick_t * 1000.0, dts)
        # the market now, and at the start of the day
        self.vals, self.bond_y, self.vols = tick_v[-1].copy(), tick_y[-1].copy(), tick_q[-1].copy()
        i = int(np.searchsorted(tick_t * 1000, self.sod_ms))
        if i < len(tick_t):
            self.sod = {"ms": self.sod_ms, "F": Fp[i].copy(), "vals": tick_v[i].copy(), "bond_y": tick_y[i].copy()}
        self.t = max(int(tick_t[-1]) * 1000, now)
        print(f"rates terminal market: {len(self.inst)} symbols, {len(long_t)} + {len(tick_t)} steps of history in {_time.time() - t0:.1f} s", flush=True)
        return {"long_t": long_t, "long_v": long_v, "long_q": long_q, "t": tick_t, "v": tick_v, "q": tick_q}

    # ---------------------------------------------------------------- live

    def live(self, now: int):
        """Advance to `now` (UTC ms): events in between, one step, every value and the step's volumes."""
        dt_ms = max(1, min(5000, now - self.t))
        for e in events_between(self.t, now):
            self._apply_event(e)
        self._step_factors(dt_ms / 1000, now)
        V, Y = self._valuate_block(np.array([float(now)]), self.F[None, :], self.idio[self.noisy][None, :], self.slow[None, :])
        self.vals, self.bond_y = V[0], Y[0]
        self.vols = self._block_volumes(np.array([float(now)]), np.array([dt_ms / 1000]))[0]
        self.t = now
        self._check_sod(now)
        self._roll_daily()

    # ---------------------------------------------------------------- history lookups (analytics)

    def value_at(self, sym: str, ms: float) -> float:
        """A symbol's value at or before `ms`: from the intraday history, else the long history, else now."""
        k = self.index.get(sym)
        if k is None:
            return float("nan")
        h = self.history
        sec = ms / 1000
        if sec >= h["t"][-1]:
            return float(self.vals[k])
        if sec >= h["t"][0]:
            i = int(np.searchsorted(h["t"], sec, side="right")) - 1
            return float(h["v"][i, k])
        i = int(np.searchsorted(h["long_t"], sec, side="right")) - 1
        return float(h["long_v"][max(0, i), k])

    def _build_daily(self):
        """Daily closes (UTC days) of every symbol, oldest first, and today's open/high/low/volume: from the history,
        then kept current by live()."""
        h = self.history
        rows_t, rows_v = [], []
        for t_arr, v_arr in ((h["long_t"], h["long_v"]), (h["t"], h["v"])):
            days = t_arr // 86400
            last = np.r_[np.nonzero(np.diff(days))[0], len(days) - 1]
            for i in last:
                rows_t.append(int(days[i]))
                rows_v.append(v_arr[i])
        self.daily_days = rows_t
        self.daily_c = [np.array(r, dtype=float) for r in rows_v]
        today = int(self.t // DAY_MS)
        it = h["t"] // 86400
        sel = it == today
        v, q = h["v"][sel], h["q"][sel]
        n = len(self.inst)
        if len(v):
            self.today = {"day": today, "open": v[0].copy(), "high": np.nanmax(v, axis=0), "low": np.nanmin(v, axis=0), "volume": q.sum(axis=0)}
        else:
            self.today = {"day": today, "open": self.vals.copy(), "high": self.vals.copy(), "low": self.vals.copy(), "volume": np.zeros(n)}
        self.prev_close = self.daily_c[-2] if len(self.daily_c) > 1 and self.daily_days[-1] == today else self.daily_c[-1]

    def _roll_daily(self):
        """Live: today's bar and the daily closes follow the latest values; a new UTC day starts a new row."""
        today = int(self.t // DAY_MS)
        v = self.vals
        if today != self.today["day"]:
            self.prev_close = self.daily_c[-1].copy()
            self.today = {"day": today, "open": v.copy(), "high": v.copy(), "low": v.copy(), "volume": np.zeros(len(v))}
            self.daily_days.append(today)
            self.daily_c.append(v.copy())
        else:
            self.today["high"] = np.fmax(self.today["high"], v)
            self.today["low"] = np.fmin(self.today["low"], v)
            self.today["open"] = np.where(np.isfinite(self.today["open"]), self.today["open"], v)
            if self.daily_days[-1] == today:
                self.daily_c[-1] = v.copy()
            else:
                self.daily_days.append(today)
                self.daily_c.append(v.copy())
        self.today["volume"] = self.today["volume"] + self.vols

    def daily_closes(self, k: int, days: int = 260) -> np.ndarray:
        """A symbol's daily closes, oldest first, ending with today's latest value (NaN days left out)."""
        col = np.array([r[k] for r in self.daily_c[-days:]])
        return col[np.isfinite(col)]

    def day_bar(self, sym: str) -> Optional[dict]:
        """Today's (UTC) open, high, low, last, volume and the previous day's close."""
        k = self.index.get(sym)
        if k is None:
            return None
        td = self.today
        return {"open": float(td["open"][k]), "high": float(td["high"][k]), "low": float(td["low"][k]), "close": float(self.vals[k]),
                "volume": float(td["volume"][k]), "prev_close": float(self.prev_close[k])}

    def factors(self) -> dict:
        return {"level": float(self.F[L]), "slope": float(self.F[Sl]), "curvature": float(self.F[C]), "policy": float(self.F[P])}
