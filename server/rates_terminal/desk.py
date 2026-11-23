"""
A hypothetical Treasury trading desk on the simulated market (the same desk as the browser mock,
terminal/src/server/desk.ts): books with positions in cash bonds and futures, client RFQs arriving all day (some
won, then often hedged with futures), and from those:

  books            one row per book: positions, gross, DV01 against its limit, VaR, P&L today
  positions        per book and instrument: quantity, price, DV01, idio, carry, P&L today (start-of-day and new trades)
  book_risk        per book: DV01 by key-rate bucket, curve-factor exposures (level, slope, curvature, policy), VaR
  pnl_attribution  per book: P&L since 07:00 ET split into carry, roll, level, slope, curvature, policy, idio, basis,
                   new trades, fees and residual
  pnl_timeseries   each book's P&L through the day
  rfq_blotter, desk_trades, rfq_flow   client RFQs, the desk's executions, RFQ volume per CUSIP per 5 minutes
  security_master, cusip_auctions      per CUSIP: outstanding, SOMA holdings, free float, street volume, desk
                   position, idio, deliverability; each issue's auction and reopenings

All of it is made up (seeded by the date), shaped like the real thing. Attribution is risk-based: each start-of-day
position's yield change is split into the curve factors' moves at its tenor and the rest (idio); futures' rest is
basis; P&L on trades done today is its own line.
"""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import numpy as np
from dh_chart_ext.live import Keyed, Stream

from .bonds import DAY_MS, accrued, risk
from .market import Market, fnv, loading
from .universe import BOND_BY_CUSIP, BONDS, BY_SYM, CONTRACT_BY_SYM, CONTRACTS, STIR_BY_SYM, price_text

YEAR_MS = 365.25 * DAY_MS


@dataclass
class BookDef:
    id: str
    name: str
    strategy: str
    trader: str
    dv01_limit: int


BOOKS = [
    BookDef("FRONT", "Front end", "Bills, 2s and 3s; client flow", "AK", 250_000),
    BookDef("BELLY", "Belly", "5s and 7s; client flow", "MS", 350_000),
    BookDef("TENS", "Tens", "10s and ultras; client flow", "JL", 400_000),
    BookDef("LONG", "Long end", "20s and 30s; client flow", "RP", 300_000),
    BookDef("TIPS", "Inflation", "TIPS against nominals (breakevens)", "DC", 150_000),
    BookDef("BASIS", "Basis", "Long CTDs against futures, to delivery", "NW", 150_000),
    BookDef("RV", "Relative value", "2s5s10s butterfly, off-the-run against on-the-run", "EH", 200_000),
    BookDef("MACRO", "Macro overlay", "Directional futures and SOFR strip", "TB", 500_000),
]
COMPONENTS = ["Carry", "Roll", "Level", "Slope", "Curvature", "Policy", "Idio", "Basis", "NewTrades", "Fees", "Residual"]
FACTOR_DAILY_BP = {k: v / math.sqrt(252) * 100 for k, v in (("Level", 0.85), ("Slope", 0.55), ("Curvature", 0.35), ("Policy", 0.45))}
IDIO_DAILY_BP = 0.6
BUCKETS = [("Bills", 0.5), ("2Y", 2), ("5Y", 5), ("10Y", 10), ("30Y", 30)]
CLIENTS = ["Pension-07", "AssetMgr-12", "AssetMgr-31", "HedgeFund-03", "HedgeFund-19", "BankTsy-05", "Insurer-02", "CentralBank-01", "MMF-09", "Corp-14",
           "SovWealth-04", "RIA-22"]
VENUES = ["D2C-1", "D2C-1", "D2C-2", "Voice", "Single-dealer"]


@dataclass
class Pos:
    book: str
    sym: str
    qty: float
    sod_qty: float
    sod_px: float
    trades: int = 0


@dataclass
class Trade:
    id: str
    t: int
    book: str
    sym: str
    qty: float
    px: float
    fee: float
    source: str
    ref: str
    cpty: str


def _kind(sym):
    b = BOND_BY_CUSIP.get(sym)
    if b:
        return "Bill" if b.type == "Bill" else "TIPS" if b.type == "TIPS" else "Cash"
    return "STIR" if sym in STIR_BY_SYM else "Future"


def _unit(sym):
    return "$mm" if sym in BOND_BY_CUSIP else "contracts"


def _iso(ms) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _day(ms) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


def _r(v, d=0):
    if v is None or not math.isfinite(v):
        return None
    return round(float(v), d) if d else round(float(v))


class Desk:
    def __init__(self, market: Market, live):
        m = self.m = market
        self.live = live
        self.g = np.random.default_rng(fnv("desk", m.sod_ms // DAY_MS))
        now = m.t
        bond = lambda term, age=0, type_=None: next(b for b in BONDS if b.term == term and b.age == age and (not type_ or b.type == type_))  # noqa: E731
        seed = [
            ("FRONT", bond("2Y").cusip, 350), ("FRONT", bond("2Y", 1).cusip, -150), ("FRONT", bond("3Y").cusip, 120), ("FRONT", bond("26W").cusip, 500),
            ("FRONT", bond("4W").cusip, -200), ("FRONT", "ZT", -900),
            ("BELLY", bond("5Y").cusip, 250), ("BELLY", bond("5Y", 8).cusip, -100), ("BELLY", bond("7Y").cusip, 150), ("BELLY", "ZF", -1200),
            ("TENS", bond("10Y").cusip, 200), ("TENS", bond("10Y", 1).cusip, -80), ("TENS", bond("10Y", 12).cusip, 60), ("TENS", "ZN", -1100), ("TENS", "TN", 150),
            ("LONG", bond("30Y").cusip, -60), ("LONG", bond("20Y").cusip, 90), ("LONG", bond("30Y", 18).cusip, 40), ("LONG", "UB", -40), ("LONG", "ZB", 30),
            ("TIPS", bond("10Y", 0, "TIPS").cusip, 100), ("TIPS", bond("10Y").cusip, -95), ("TIPS", bond("5Y", 0, "TIPS").cusip, 60), ("TIPS", bond("5Y").cusip, -55),
            ("RV", bond("5Y").cusip, 200), ("RV", bond("2Y").cusip, -240), ("RV", bond("10Y").cusip, -56), ("RV", bond("10Y", 2).cusip, 70), ("RV", bond("10Y", 3).cusip, -70),
            ("MACRO", "ZN", 800), ("MACRO", "UB", -100), ("MACRO", "SR3H7", 2500), ("MACRO", "SR3Z7", -1500), ("MACRO", "ZQF7", 600),
        ]
        # the basis book: long each CTD, short the contract weighted by its conversion factor (face / $100k x CF)
        for root in ("ZN", "ZF", "ZB"):
            c = self._ctd(CONTRACT_BY_SYM[root], now)
            face = 120 if root == "ZB" else 300 if root == "ZF" else 500
            seed += [("BASIS", c["bond"].cusip, face), ("BASIS", root, -round(face * 10 * c["cf"]))]
        self.positions: dict[tuple, Pos] = {}
        for book, sym, qty in seed:
            p = self.positions.get((book, sym)) or Pos(book, sym, 0, 0, m.sod_value(sym))
            p.qty += qty
            p.sod_qty += qty
            self.positions[(book, sym)] = p
        self.trades: list[Trade] = []
        self.unit_risk: dict = {}
        self.stats: dict = {}
        self.street: dict = {}
        self.liq = [(6 if b.age == 0 else 2 if b.age <= 2 else 1) * (1.5 if b.type == "Bill" else 1) for b in BONDS]
        self.liq_sum = sum(self.liq)
        self.next_rfq = 1
        self.next_trade = 1
        self.rfqs: deque = deque(maxlen=2500)
        self.flow: dict = {}
        self.deliverable: dict = {}
        for c in CONTRACTS:
            for b, _cf in c.basket:
                self.deliverable.setdefault(b.cusip, []).append(c.sym)
        self.soma_pct = {}
        for b in BONDS:
            u = np.random.default_rng(fnv("soma", b.cusip)).random()
            self.soma_pct[b.cusip] = 4 + u * 8 if b.type == "Bill" else 1 + u * 4 if b.age == 0 else 8 + u * 10 if b.age <= 3 else 18 + u * 22
        self.master_rows: dict = {}
        self._rfq_out: list = []
        self._trade_out: list = []
        self._flow_out: dict = {}

        self.rfq_t = Stream("rfq_blotter", [("Time", "T"), ("Id", "S"), ("Client", "S"), ("Venue", "S"), ("Sym", "S"), ("Security", "S"), ("ClientSide", "S"),
                                            ("SizeMM", "D"), ("Level", "S"), ("Yield", "D"), ("Status", "S"), ("Book", "S"), ("CoverBp", "D"), ("RespondSec", "D")], ring=2500)
        self.trade_t = Stream("desk_trades", [("Time", "T"), ("Id", "S"), ("Book", "S"), ("Sym", "S"), ("Security", "S"), ("Side", "S"), ("Qty", "D"), ("Unit", "S"),
                                              ("Price", "D"), ("PriceText", "S"), ("Source", "S"), ("Ref", "S"), ("Counterparty", "S"), ("Fee", "D")])
        self.flow_t = Keyed([("Timestamp", "T"), ("Sym", "S"), ("Requests", "I"), ("NotionalMM", "D"), ("DoneMM", "D"), ("ClientBuyMM", "D"), ("ClientSellMM", "D")],
                            ["Timestamp", "Sym"], "rfq_flow")
        self.master_t = Keyed([("Cusip", "S"), ("Security", "S"), ("Type", "S"), ("Term", "S"), ("Status", "S"), ("Coupon", "D"), ("Maturity", "T"), ("Issue", "T"),
                               ("OutstandingBn", "D"), ("SomaBn", "D"), ("SomaPct", "D"), ("FreeFloatBn", "D"), ("StreetVolBn", "D"), ("DeskPositionMM", "D"),
                               ("IdioBp", "D"), ("Deliverable", "S"), ("CTDFor", "S"), ("RfqCount", "I"), ("RfqNotionalMM", "D"), ("HitRatioPct", "D")], ["Cusip"],
                              "security_master")
        self.auctions_t = Keyed([("Cusip", "S"), ("Security", "S"), ("Date", "T"), ("Kind", "S"), ("SizeBn", "D"), ("HighYield", "D"), ("WIYield", "D"), ("TailBp", "D"),
                                 ("BidToCover", "D"), ("IndirectPct", "D"), ("DirectPct", "D"), ("DealerPct", "D")], ["Cusip", "Kind"], "cusip_auctions")
        self.books_t = Keyed([("Book", "S"), ("Name", "S"), ("Strategy", "S"), ("Trader", "S"), ("Positions", "I"), ("GrossMM", "D"), ("DV01", "D"), ("DV01Limit", "D"),
                              ("LimitUsePct", "D"), ("VaR99", "D"), ("PnlToday", "D"), ("TradesToday", "I")], ["Book"], "books")
        self.pos_t = Keyed([("Book", "S"), ("Sym", "S"), ("Security", "S"), ("Kind", "S"), ("Qty", "D"), ("Unit", "S"), ("Price", "D"), ("PriceText", "S"), ("Yield", "D"),
                            ("DV01", "D"), ("Tenor", "D"), ("IdioBp", "D"), ("CarryDay", "D"), ("PnlToday", "D"), ("PnlSod", "D"), ("PnlNew", "D"), ("SodQty", "D"),
                            ("Trades", "I")], ["Book", "Sym"], "positions")
        self.risk_t = Keyed([("Book", "S"), ("DV01", "D")] + [(f"KRD_{b}", "D") for b, _ in BUCKETS] +
                            [("Level", "D"), ("Slope", "D"), ("Curvature", "D"), ("Policy", "D"), ("VaR99", "D"), ("IdioRisk", "D")], ["Book"], "book_risk")
        self.pnl_t = Keyed([("Book", "S"), ("Total", "D")] + [(c, "D") for c in COMPONENTS], ["Book"], "pnl_attribution")
        self.series_t = Stream("pnl_timeseries", [("Timestamp", "T"), ("Book", "S"), ("Pnl", "D")])

        self.auction_rows = self._auctions(now)
        self.auctions_t.put(self.auction_rows)
        self.last_compute = 0
        self.last_series = 0
        self.next_at = now + 3000
        m.on_sod.append(self._new_day)
        self._seed_day(now)
        self.compute(now)

    # ---------------------------------------------------------------- instrument math

    def _ctd(self, c, now):
        return min(self.m.basket(c, now), key=lambda x: x["implied"])

    @staticmethod
    def _price_factor(sym):
        if sym in BOND_BY_CUSIP:
            return 10_000
        i = BY_SYM.get(sym)
        return (i.multiplier if i and i.multiplier else 1000)

    def _tenor(self, sym, now):
        b = BOND_BY_CUSIP.get(sym)
        if b:
            return max(1 / 365, (b.maturity - now) / YEAR_MS)
        s = STIR_BY_SYM.get(sym)
        if s:
            return max(1 / 52, ((s.start + s.end) / 2 - now) / YEAR_MS)
        c = CONTRACT_BY_SYM.get(sym)
        if c:
            x = self._ctd(c, now)
            return (x["bond"].maturity - now) / YEAR_MS if x else c.root.tenor
        return 5

    def _refresh_risk(self, now):
        """Risk per unit: DV01 ($ per bp, positive = gains when yields fall), carry ($ per day), tenor, yield, idio."""
        m = self.m
        out = {}
        settle = now // DAY_MS * DAY_MS + DAY_MS
        repo = m.repo()
        for sym in {p.sym for p in self.positions.values()}:
            b = BOND_BY_CUSIP.get(sym)
            if b:
                y = m.yield_of(sym)
                r = risk(b, y, settle)
                carry = b.coupon / 100 * 1e6 / 365 - repo / 100 * (r.dirty / 100) * 1e6 / 360
                out[sym] = {"dv01": r.dv01, "carry": carry, "tenor": self._tenor(sym, now), "y": y, "idio": m.value(f"{sym}.IDIO")}
                continue
            s = STIR_BY_SYM.get(sym)
            if s:
                out[sym] = {"dv01": 25 if s.kind == "SR3" else 41.67, "carry": 0, "tenor": self._tenor(sym, now), "y": 100 - m.value(sym), "idio": float("nan")}
                continue
            c = CONTRACT_BY_SYM.get(sym)
            if c:
                x = self._ctd(c, now)
                cr = risk(x["bond"], x["y"], settle)
                out[sym] = {"dv01": cr.dv01 / x["cf"] * c.root.multiplier / 10_000, "carry": 0, "tenor": (x["bond"].maturity - now) / YEAR_MS, "y": x["y"],
                            "idio": float("nan")}
        self.unit_risk = out

    # ---------------------------------------------------------------- client RFQs

    def _pick_bond(self):
        u = self.g.random() * self.liq_sum
        for b, w in zip(BONDS, self.liq):
            u -= w
            if u <= 0:
                return b
        return BONDS[0]

    def _book_for(self, b, now):
        if b.type == "TIPS":
            return "TIPS"
        t = (b.maturity - now) / YEAR_MS
        return "FRONT" if t < 3.5 else "BELLY" if t < 8 else "TENS" if t < 12 else "LONG"

    @staticmethod
    def _hedge_for(tenor):
        return "ZT" if tenor < 3 else "ZF" if tenor < 6 else "ZN" if tenor < 8.5 else "TN" if tenor < 13 else "ZB" if tenor < 24 else "UB"

    def _book(self, t: Trade):
        self.trades.append(t)
        p = self.positions.get((t.book, t.sym)) or Pos(t.book, t.sym, 0, 0, self.m.sod_value(t.sym))
        p.qty += t.qty
        p.trades += 1
        self.positions[(t.book, t.sym)] = p
        i = BY_SYM.get(t.sym)
        self._trade_out.append({"Time": t.t, "Id": t.id, "Book": t.book, "Sym": t.sym, "Security": i.name if i else t.sym, "Side": "Buy" if t.qty > 0 else "Sell",
                           "Qty": abs(t.qty), "Unit": _unit(t.sym), "Price": t.px, "PriceText": price_text(i, t.px), "Source": t.source, "Ref": t.ref,
                           "Counterparty": t.cpty, "Fee": t.fee})

    def _rfq(self, t: int, historical: bool):
        """One client RFQ at time t: its outcome, and if the desk wins it, the trade and maybe a futures hedge."""
        m, g = self.m, self.g
        b = self._pick_bond()
        px = m.value_at(b.cusip, t) if historical else m.value(b.cusip)
        y = m.value_at(f"{b.cusip}.Y", t) if historical else m.yield_of(b.cusip)
        if not math.isfinite(px):
            return
        size = max(1, round(math.exp(2.2 + g.standard_normal() * 0.9) * (1.6 if b.age == 0 else 1)))
        client_buys = g.random() < 0.5
        u = g.random()
        status = "Done" if u < 0.36 else "Traded away" if u < 0.8 else "Covered" if u < 0.92 else "Passed"
        rid = f"R{self.next_rfq:05d}"
        self.next_rfq += 1
        half = (1 / 256 if b.age == 0 else 1 / 128) / 2
        level = px + half if client_buys else px - half
        bk = self._book_for(b, t)
        cover = round(g.random() * 10) / 10 if status in ("Covered", "Traded away") else None
        row = {"Time": t, "Id": rid, "Client": CLIENTS[int(g.random() * len(CLIENTS))], "Venue": VENUES[int(g.random() * len(VENUES))], "Sym": b.cusip,
               "Security": b.name, "ClientSide": "Buy" if client_buys else "Sell", "SizeMM": size, "Level": price_text(BY_SYM.get(b.cusip), level),
               "Yield": round(y, 4) if math.isfinite(y) else None, "Status": status, "Book": bk if status == "Done" else "", "CoverBp": cover,
               "RespondSec": round((1.5 + g.random() * 6) * 10) / 10}
        self.rfqs.append(row)
        self._rfq_out.append(row)
        s = self.stats.setdefault(b.cusip, {"n": 0, "notional": 0, "done": 0, "buy": 0, "sell": 0})
        s["n"] += 1
        s["notional"] += size
        s["buy" if client_buys else "sell"] += size
        if status == "Done":
            s["done"] += size
        bucket = t // 300_000 * 300_000
        f = self.flow.setdefault((bucket, b.cusip), {"Timestamp": bucket, "Sym": b.cusip, "Requests": 0, "NotionalMM": 0, "DoneMM": 0, "ClientBuyMM": 0, "ClientSellMM": 0})
        f["Requests"] += 1
        f["NotionalMM"] += size
        f["DoneMM"] += size if status == "Done" else 0
        f["ClientBuyMM" if client_buys else "ClientSellMM"] += size
        self._flow_out[(bucket, b.cusip)] = f
        if status != "Done":
            return
        # the desk takes the other side; most of the time it hedges the DV01 with the matching future
        self._book(Trade(f"T{self.next_trade:05d}", t, bk, b.cusip, -size if client_buys else size, level, 0, "RFQ", rid, "client"))
        self.next_trade += 1
        if g.random() < 0.6 and b.type != "Bill":
            tenor = (b.maturity - t) / YEAR_MS
            fut = self._hedge_for(tenor)
            ur, fr = self.unit_risk.get(b.cusip), self.unit_risk.get(fut)
            bond_dv01 = (ur["dv01"] if ur else tenor * 90) * size
            fut_dv01 = fr["dv01"] if fr else tenor * 9
            n = round(bond_dv01 / fut_dv01)
            if n:
                fpx = m.value_at(fut, t + 2000) if historical else m.value(fut)
                self._book(Trade(f"T{self.next_trade:05d}", t + 2000, bk, fut, n if client_buys else -n, fpx, abs(n) * 1.25, "Hedge", rid, "CME"))
                self.next_trade += 1

    def _flush_rfqs(self):
        if self._trade_out:
            self.trade_t.put(self._trade_out)
            self._trade_out = []
        if self._rfq_out:
            self.rfq_t.put(self._rfq_out)
            self._rfq_out = []
        if self._flow_out:
            self.flow_t.put(list(self._flow_out.values()))
            self._flow_out = {}

    # ---------------------------------------------------------------- per-CUSIP reference

    def _auctions(self, now):
        rows = []
        for b in BONDS:
            reopenings = 2 if b.term in ("10Y", "30Y", "20Y") else 0
            for k in range(reopenings + 1):
                t = b.issue - 5 * DAY_MS + k * 30 * DAY_MS
                if t > now:
                    continue
                g = np.random.default_rng(fnv("auc", b.cusip, k))
                tail = g.standard_normal() * 0.9
                y = self.m.value_at(f"{b.cusip}.Y", t)
                wi = (y if math.isfinite(y) and y else b.coupon) - tail / 100
                rows.append({"Cusip": b.cusip, "Security": b.name, "Date": t, "Kind": "Original" if k == 0 else f"Reopening {k}",
                             "SizeBn": round(b.outstanding / (2.4 if reopenings else 1) * (1 if k == 0 else 0.7), 1), "HighYield": round(wi + tail / 100, 3),
                             "WIYield": round(wi, 3), "TailBp": round(tail, 1), "BidToCover": round(2.55 - tail * 0.12 + g.standard_normal() * 0.08, 2),
                             "IndirectPct": round(66 - tail * 4 + g.standard_normal() * 3, 1), "DirectPct": round(18 + g.standard_normal() * 2.5, 1),
                             "DealerPct": round(15 + tail * 3 + g.standard_normal() * 1.5, 1)})
        return rows

    # ---------------------------------------------------------------- P&L and risk

    def compute(self, now):
        """The P&L of every position now, split by source; and the books' risk."""
        m = self.m
        sod = m.sod
        if not sod:
            return None
        self._refresh_risk(now)
        elapsed = max(0.0, (now - sod["ms"]) / DAY_MS)
        dF = {"Level": m.F[0] - sod["F"][0], "Slope": m.F[1] - sod["F"][1], "Curvature": m.F[2] - sod["F"][2], "Policy": m.F[3] - sod["F"][3]}
        zero = lambda: {c: 0.0 for c in COMPONENTS + ["Total"]}  # noqa: E731
        by_book: dict = {}
        risk_by: dict = {}
        idio_var: dict = {}
        new_pnl: dict = {}
        fees: dict = {}
        for t in self.trades:
            px = m.value(t.sym)
            new_pnl[(t.book, t.sym)] = new_pnl.get((t.book, t.sym), 0) + t.qty * (px - t.px) * self._price_factor(t.sym)
            fees[t.book] = fees.get(t.book, 0) + t.fee
        pos_rows = []
        for p in self.positions.values():
            ur = self.unit_risk.get(p.sym)
            px = m.value(p.sym)
            f = self._price_factor(p.sym)
            a = by_book.setdefault(p.book, zero())
            sod_pnl = p.sod_qty * (px - p.sod_px) * f if math.isfinite(p.sod_px) else 0.0
            dv01_sod = (ur["dv01"] if ur else 0) * p.sod_qty
            tenor = ur["tenor"] if ur else 5
            w = loading(tenor)
            parts = {"Level": -dv01_sod * dF["Level"] * w[1] * 100, "Slope": -dv01_sod * dF["Slope"] * w[2] * 100,
                     "Curvature": -dv01_sod * dF["Curvature"] * w[3] * 100, "Policy": -dv01_sod * dF["Policy"] * w[4] * 100}
            curve = sum(parts.values())
            is_bond = p.sym in BOND_BY_CUSIP
            idio = basis = carry = roll = 0.0
            if is_bond:
                dy = (m.yield_of(p.sym) - m.sod_yield(p.sym)) * 100
                carry = (ur["carry"] if ur else 0) * p.sod_qty * elapsed
                # roll: the start-of-day curve, read a little shorter (the bond aged since the open)
                w_sod = loading(tenor + elapsed / 365.25)
                dy_roll = sum((w[k] - w_sod[k]) * (1 if k == 0 else sod["F"][k - 1]) for k in range(5))
                roll = -dv01_sod * dy_roll * 100
                # idio: the yield change the curve and the roll don't explain
                idio = -dv01_sod * dy - curve - roll
            else:
                basis = sod_pnl - curve
            residual = sod_pnl - (curve + idio + basis + roll)  # convexity and pull to par
            nt = new_pnl.get((p.book, p.sym), 0.0)
            for k, v in parts.items():
                a[k] += v
            a["Idio"] += idio
            a["Basis"] += basis
            a["Carry"] += carry
            a["Roll"] += roll
            a["Residual"] += residual
            a["NewTrades"] += nt
            total = sod_pnl + carry + nt
            dv01 = (ur["dv01"] if ur else 0) * p.qty
            r = risk_by.setdefault(p.book, {"DV01": 0.0, "Level": 0.0, "Slope": 0.0, "Curvature": 0.0, "Policy": 0.0, "Gross": 0.0, "n": 0,
                                            **{f"KRD_{b}": 0.0 for b, _ in BUCKETS}})
            if p.qty:
                r["n"] += 1
            r["DV01"] += dv01
            # $mm face: cash as held; a Treasury future's face is its multiplier x 100 ($100k for ZN), SOFR futures $1mm
            r["Gross"] += abs(p.qty) if is_bond or p.sym in STIR_BY_SYM else abs(p.qty) * (f / 10_000)
            r["Level"] += dv01 * w[1]
            r["Slope"] += dv01 * w[2]
            r["Curvature"] += dv01 * w[3]
            r["Policy"] += dv01 * w[4]
            for bi, (name, at) in enumerate(BUCKETS):
                prev = BUCKETS[bi - 1][1] if bi > 0 else -math.inf
                nxt = BUCKETS[bi + 1][1] if bi < len(BUCKETS) - 1 else math.inf
                wt = 0.0
                if prev < tenor <= at:
                    wt = 1 if prev == -math.inf else (tenor - prev) / (at - prev)
                elif at < tenor < nxt:
                    wt = 1 if nxt == math.inf else (nxt - tenor) / (nxt - at)
                elif bi == 0 and tenor <= at:
                    wt = 1
                r[f"KRD_{name}"] += dv01 * wt
            if is_bond:
                idio_var[p.book] = idio_var.get(p.book, 0) + (dv01 * IDIO_DAILY_BP) ** 2
            i = BY_SYM.get(p.sym)
            pos_rows.append({"Book": p.book, "Sym": p.sym, "Security": i.name if i else p.sym, "Kind": _kind(p.sym), "Qty": p.qty, "Unit": _unit(p.sym), "Price": px,
                             "PriceText": price_text(i, px), "Yield": round(ur["y"], 4) if ur and math.isfinite(ur["y"]) else None, "DV01": round(dv01),
                             "Tenor": round(tenor, 2) if ur else 0, "IdioBp": round(ur["idio"], 2) if ur and math.isfinite(ur["idio"]) else None,
                             "CarryDay": round((ur["carry"] if ur else 0) * p.qty), "PnlToday": _r(total), "PnlSod": _r(sod_pnl + carry), "PnlNew": _r(nt),
                             "SodQty": p.sod_qty, "Trades": p.trades})
        self.pos_t.put(pos_rows)
        allc = zero()
        pnl_rows = []
        for b in BOOKS:
            a = by_book.get(b.id) or zero()
            a["Fees"] = -fees.get(b.id, 0)
            a["Total"] = sum(a[c] for c in COMPONENTS)
            for c in COMPONENTS + ["Total"]:
                allc[c] += a[c]
            pnl_rows.append({"Book": b.id, **{k: _r(v) for k, v in a.items()}})
        pnl_rows.append({"Book": "ALL", **{k: _r(v) for k, v in allc.items()}})
        self.pnl_t.put(pnl_rows)
        risk_rows, book_rows = [], []
        tot: dict = {}
        idio_all = 0.0
        trades_by = {}
        for p in self.positions.values():
            trades_by[p.book] = trades_by.get(p.book, 0) + p.trades
        for b in BOOKS:
            r = risk_by.get(b.id, {})
            gt = lambda k: r.get(k, 0.0)  # noqa: E731
            fv = math.sqrt(sum((gt(k) * FACTOR_DAILY_BP[k]) ** 2 for k in FACTOR_DAILY_BP))
            iv = math.sqrt(idio_var.get(b.id, 0))
            idio_all += idio_var.get(b.id, 0)
            var99 = 2.33 * math.sqrt(fv * fv + iv * iv)
            risk_rows.append({"Book": b.id, "DV01": round(gt("DV01")), **{f"KRD_{n}": round(gt(f"KRD_{n}")) for n, _ in BUCKETS},
                              **{k: round(gt(k)) for k in FACTOR_DAILY_BP}, "VaR99": round(var99), "IdioRisk": round(2.33 * iv)})
            for k in ["DV01", *FACTOR_DAILY_BP, *[f"KRD_{n}" for n, _ in BUCKETS]]:
                tot[k] = tot.get(k, 0) + gt(k)
            book_rows.append({"Book": b.id, "Name": b.name, "Strategy": b.strategy, "Trader": b.trader, "Positions": r.get("n", 0), "GrossMM": round(gt("Gross")),
                              "DV01": round(gt("DV01")), "DV01Limit": b.dv01_limit, "LimitUsePct": round(100 * abs(gt("DV01")) / b.dv01_limit), "VaR99": round(var99),
                              "PnlToday": next(x["Total"] for x in pnl_rows if x["Book"] == b.id), "TradesToday": trades_by.get(b.id, 0)})
        fv_all = math.sqrt(sum((tot.get(k, 0) * FACTOR_DAILY_BP[k]) ** 2 for k in FACTOR_DAILY_BP))
        var_all = 2.33 * math.sqrt(fv_all * fv_all + idio_all)
        risk_rows.append({"Book": "ALL", **{k: round(v) for k, v in tot.items()}, "VaR99": round(var_all), "IdioRisk": round(2.33 * math.sqrt(idio_all))})
        book_rows.append({"Book": "ALL", "Name": "Desk", "Strategy": "All books", "Trader": "", "Positions": sum(r["Positions"] for r in book_rows),
                          "GrossMM": sum(r["GrossMM"] for r in book_rows), "DV01": round(tot.get("DV01", 0)), "DV01Limit": sum(b.dv01_limit for b in BOOKS),
                          "LimitUsePct": round(100 * abs(tot.get("DV01", 0)) / 1_500_000), "VaR99": round(var_all), "PnlToday": _r(allc["Total"]),
                          "TradesToday": len(self.trades)})
        self.risk_t.put(risk_rows)
        self.books_t.put(book_rows)
        # per CUSIP
        desk_pos: dict = {}
        for p in self.positions.values():
            desk_pos[p.sym] = desk_pos.get(p.sym, 0) + p.qty
        ctd_for: dict = {}
        for c in CONTRACTS:
            if c.basket:
                ctd_for.setdefault(self._ctd(c, now)["bond"].cusip, []).append(c.sym)
        rows = []
        for b in BONDS:
            if b.issue > now:
                continue
            s = self.stats.get(b.cusip)
            pct = self.soma_pct[b.cusip]
            rows.append({"Cusip": b.cusip, "Security": b.name, "Type": b.type, "Term": b.term,
                         "Status": "OTR" if b.age == 0 else "1st off" if b.age == 1 else "2nd off" if b.age == 2 else "old", "Coupon": b.coupon,
                         "Maturity": b.maturity, "Issue": b.issue, "OutstandingBn": b.outstanding, "SomaBn": round(b.outstanding * pct / 100, 1),
                         "SomaPct": round(pct, 1), "FreeFloatBn": round(b.outstanding * (1 - pct / 100), 1), "StreetVolBn": round(self.street.get(b.cusip, 0), 2),
                         "DeskPositionMM": desk_pos.get(b.cusip, 0), "IdioBp": _r(m.value(f"{b.cusip}.IDIO"), 2),
                         "Deliverable": ", ".join(self.deliverable.get(b.cusip, [])), "CTDFor": ", ".join(ctd_for.get(b.cusip, [])),
                         "RfqCount": s["n"] if s else 0, "RfqNotionalMM": s["notional"] if s else 0,
                         "HitRatioPct": round(100 * s["done"] / s["notional"]) if s and s["notional"] else None})
        self.master_rows = {r["Cusip"]: r for r in rows}
        self.master_t.put(rows)
        return pnl_rows

    # ---------------------------------------------------------------- the day so far, then live

    def _seed_day(self, now):
        m, g = self.m, self.g
        sod = m.sod
        if not sod:
            return
        self.trades.clear()
        self._refresh_risk(now)
        for b, w in zip(BONDS, self.liq):
            self.street[b.cusip] = w * 1.6 * (now - sod["ms"]) / 3_600_000
        # RFQs since the open, one every ~25 s on average
        t = sod["ms"] + int(g.random() * 25_000)
        while t < now:
            self._rfq(t, True)
            t += int(-math.log(1 - g.random()) * 25_000) + 500
        self._flush_rfqs()
        # the P&L path, every 5 minutes, from the positions held then
        rows = []
        t = sod["ms"] + 300_000
        while t < now:
            per: dict = {}
            for p in self.positions.values():
                px = m.value_at(p.sym, t)
                if math.isfinite(px) and math.isfinite(p.sod_px):
                    per[p.book] = per.get(p.book, 0) + p.sod_qty * (px - p.sod_px) * self._price_factor(p.sym)
            for tr in self.trades:
                if tr.t > t:
                    continue
                px = m.value_at(tr.sym, t)
                if math.isfinite(px):
                    per[tr.book] = per.get(tr.book, 0) + tr.qty * (px - tr.px) * self._price_factor(tr.sym) - tr.fee
            for b in BOOKS:
                rows.append({"Timestamp": t, "Book": b.id, "Pnl": round(per.get(b.id, 0))})
            rows.append({"Timestamp": t, "Book": "ALL", "Pnl": round(sum(per.values()))})
            t += 300_000
        self.series_t.put(rows)

    def tick(self, now: int, dt_ms: int):
        """Each market step: RFQs due, the street's volume; each second the P&L and risk; every 30 s the P&L path."""
        g = self.g
        while now >= self.next_at:
            self._rfq(self.next_at, False)
            self.next_at += int(-math.log(1 - g.random()) * 25_000) + 500
        self._flush_rfqs()
        for b, w in zip(BONDS, self.liq):
            self.street[b.cusip] = self.street.get(b.cusip, 0) + w * 1.6 * (0.5 + g.random()) * dt_ms / 3_600_000
        if now - self.last_compute >= 1000:
            self.last_compute = now
            rows = self.compute(now)
            if rows and now - self.last_series >= 30_000:
                self.last_series = now
                self.series_t.put([{"Timestamp": now, "Book": r["Book"], "Pnl": r["Total"]} for r in rows])

    def _new_day(self):
        """A new trading day: today's positions become the start of the day."""
        for p in self.positions.values():
            p.sod_qty = p.qty
            p.sod_px = self.m.sod_value(p.sym)
            p.trades = 0
        self.trades.clear()

    # ---------------------------------------------------------------- one security, everything about it

    def security(self, sym: str) -> Optional[dict]:
        m = self.m
        now = m.t
        cusip = sym[:-2] if sym.endswith(".Y") else sym[:-5] if sym.endswith(".IDIO") else sym
        fut = CONTRACT_BY_SYM.get(cusip)
        via = None
        if fut and fut.basket:
            via = f"CTD of {fut.sym}"
            cusip = self._ctd(fut, now)["bond"].cusip
        b = BOND_BY_CUSIP.get(cusip)
        if not b:
            return None
        mr = self.master_rows.get(cusip, {})
        ur = self.unit_risk.get(cusip)
        y = m.yield_of(cusip)
        settle = now // DAY_MS * DAY_MS + DAY_MS
        r = risk(b, y, settle)
        px = m.value(cusip)
        sod_px = m.sod_value(cusip)
        books = []
        for p in self.positions.values():
            if p.sym != cusip or not (p.qty or p.sod_qty):
                continue
            pnl = p.sod_qty * (px - p.sod_px) * 10_000 + sum(t.qty * (px - t.px) * 10_000 for t in self.trades if t.book == p.book and t.sym == cusip)
            books.append({"book": p.book, "qty": p.qty, "sod_qty": p.sod_qty, "dv01": round(r.dv01 * p.qty), "pnl_today": _r(pnl), "trades": p.trades})
        closes = list(m.daily_closes(m.index[f"{cusip}.IDIO"], 21))
        idio = m.value(f"{cusip}.IDIO")
        mean = sum(closes) / max(1, len(closes))
        sd = math.sqrt(sum((c - mean) ** 2 for c in closes) / max(1, len(closes)))
        ctd = [x for x in self.live.basket_rows if x["Cusip"] == cusip]
        auc = [{**a, "Date": _day(a["Date"])} for a in self.auction_rows if a["Cusip"] == cusip]
        blotter = [{**x, "Time": _iso(x["Time"])} for x in self.rfqs if x["Sym"] == cusip][-15:][::-1]
        s = self.stats.get(cusip)
        fc = fut or next((c for c in CONTRACTS if c.front and any(x.cusip == cusip for x, _ in c.basket)), None)
        dbar = m.day_bar(fut.sym) if fut else None
        return {
            "cusip": cusip, "security": b.name, "type": b.type, "term": b.term, "coupon": b.coupon, "maturity": _day(b.maturity), "issue": _day(b.issue),
            "status": mr.get("Status"), "via": via, "price": price_text(BY_SYM.get(cusip), px), "price_decimal": px, "yield": _r(y, 4),
            "chg_bp": _r((y - m.sod_yield(cusip)) * 100, 2) if math.isfinite(sod_px) else None, "dv01_per_mm": _r(r.dv01, 2), "mod_duration": _r(r.mod_dur, 3),
            "accrued": _r(accrued(b, settle), 5),
            "amount": {"outstanding_bn": b.outstanding, "soma_bn": mr.get("SomaBn"), "soma_pct": mr.get("SomaPct"), "free_float_bn": mr.get("FreeFloatBn")},
            "desk": {"position_mm": sum(x["qty"] for x in books), "dv01": sum(x["dv01"] for x in books), "pnl_today": sum(x["pnl_today"] or 0 for x in books),
                     "books": books},
            "idio": {"bp": _r(idio, 2), "z20": _r((idio - mean) / sd, 2) if sd else 0, "chg_1d_bp": _r(idio - closes[-2], 2) if len(closes) > 1 else None,
                     "rich_cheap": "rich" if idio < -0.5 else "cheap" if idio > 0.5 else "fair"},
            "ctd": [{"contract": x["Contract"], "cf": x["CF"], "rank": x["Rank"], "ctd": x["CTD"], "net_basis_32": x["NetBasis32"], "gross_basis_32": x["GrossBasis32"],
                     "implied_repo": x["ImpliedRepo"]} for x in ctd],
            "hedge_future": fc.sym if fc else None,
            "auctions": auc,
            "rfq": {"today": s["n"] if s else 0, "notional_mm": s["notional"] if s else 0, "done_mm": s["done"] if s else 0, "client_buy_mm": s["buy"] if s else 0,
                    "client_sell_mm": s["sell"] if s else 0, "hit_ratio_pct": round(100 * s["done"] / s["notional"]) if s and s["notional"] else None,
                    "recent": blotter},
            "market": {"street_volume_bn": round(self.street.get(cusip, 0), 2), "listed": {"sym": fut.sym, "volume": dbar["volume"] if dbar else 0} if fut else None},
            "charts": {"price": cusip, "yield": f"{cusip}.Y", "idio": f"{cusip}.IDIO"},
            "carry_per_mm_day": round(ur["carry"]) if ur else None,
        }

    def tables(self) -> dict:
        return {"books": self.books_t.table, "positions": self.pos_t.table, "book_risk": self.risk_t.table, "pnl_attribution": self.pnl_t.table,
                "pnl_timeseries": self.series_t.table, "rfq_blotter": self.rfq_t.table, "desk_trades": self.trade_t.table, "rfq_flow": self.flow_t.table,
                "security_master": self.master_t.table, "cusip_auctions": self.auctions_t.table}
