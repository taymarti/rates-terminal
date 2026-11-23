"""
The market's tables in Deephaven: ticks (history, then live) that every chart, bar, study, alert and backtest is
built from by dh_chart_ext; and the tables the terminal's panels read, kept current by the app's thread.

  ticks, long_ticks          Timestamp Sym Price Size: intraday (five days + live) and the two years before (daily charts)
  quotes, watchlist_core     last, bid/ask, the day's open/high/low/change per symbol
  book, trades, volume_profile   depth (15 levels), time and sales (last 4000), today's volume at price
  instruments, sessions      reference data and exchange calendars
  ust_cash, futures_contracts, deliverable_basket, stir_strip, fomc_path, swap_curve, ust_curve, repo_rates
  rv_monitor                 every symbol against its own daily history
  econ_calendar, news, treasury_auctions, cftc_cot
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone

import numpy as np
from deephaven import merge
from dh_chart_ext.live import Keyed, Stream, ticks_from_matrix

from .bonds import DAY_MS, accrued, fmt32, risk, schedule, yield_from_price
from .market import Market, curve_at, events_between, fnv
from .universe import BONDS, BY_SYM, CONTRACT_BY_SYM, CONTRACTS, FOMC, FUTURES_ROLLS, INSTRUMENTS, ROOTS, SESSIONS, STIR, SWAP_TENORS, TENORS, WATCHLIST_CORE, \
    otr_for, price_text

YEAR_MS = 365.25 * DAY_MS
TICK_COLS = [("Timestamp", "T"), ("Sym", "S"), ("Price", "D"), ("Size", "D")]


def _r(v, d):
    return None if v is None or not math.isfinite(v) else round(float(v), d)


class LiveTables:
    def __init__(self, market: Market):
        self.m = market
        m = market
        syms = [i.sym for i in m.inst]
        h = m.history
        self.live_ticks = Stream("ticks", TICK_COLS)
        self.ticks = merge([ticks_from_matrix(syms, h["t"], h["v"], h["q"]), self.live_ticks.table])
        self.long_ticks = ticks_from_matrix(syms, h["long_t"], h["long_v"], h["long_q"])

        # reference
        self.instruments = Keyed([("Sym", "S"), ("Name", "S"), ("Dataset", "S"), ("Kind", "S"), ("Unit", "S"), ("Tick", "D"), ("Notation", "S"),
                                  ("Multiplier", "D"), ("Session", "S"), ("HasVolume", "B"), ("Description", "S")], ["Sym"], "instruments")
        self.instruments.put([{"Sym": i.sym, "Name": i.name, "Dataset": i.dataset, "Kind": i.kind, "Unit": i.unit, "Tick": i.tick,
                               "Notation": "32nds" if i.thirty_seconds else "decimal", "Multiplier": i.multiplier, "Session": SESSIONS[i.session]["name"],
                               "HasVolume": i.has_volume, "Description": i.description or i.expr or ""} for i in INSTRUMENTS])
        self.sessions = Keyed([("Sym", "S"), ("Spec", "S")], ["Sym"], "sessions")
        self.sessions.put([{"Sym": i.sym, "Spec": json.dumps(SESSIONS[i.session])} for i in INSTRUMENTS])
        # the calendar rolls (front minus deferred): expressions, charted from their legs' ticks; quoted here too
        self.rolls = [(r, *r.split("-")) for r in FUTURES_ROLLS]
        self.instruments.put([{"Sym": r, "Name": f"{a} Dec/Mar roll", "Dataset": "futures_rolls", "Kind": "spread", "Unit": "price", "Tick": BY_SYM[a].tick,
                               "Notation": "32nds", "Multiplier": BY_SYM[a].multiplier, "Session": SESSIONS[BY_SYM[a].session]["name"], "HasVolume": False,
                               "Description": f"{a} minus {b}"} for r, a, b in self.rolls])
        self.sessions.put([{"Sym": r, "Spec": json.dumps(SESSIONS[BY_SYM[a].session])} for r, a, _ in self.rolls])

        # quotes, books, tape
        qcols = [("Sym", "S"), ("Name", "S"), ("Kind", "S"), ("Last", "D"), ("LastText", "S"), ("Bid", "D"), ("Ask", "D"), ("Chg", "D"), ("ChgPct", "D"),
                 ("ChgBp", "D"), ("Open", "D"), ("High", "D"), ("Low", "D"), ("PrevClose", "D"), ("Volume", "D"), ("Updated", "T")]
        self.quotes = Keyed(qcols, ["Sym"], "quotes")
        self.core = Keyed(qcols, ["Sym"], "watchlist_core")
        self.traded = [i for i in m.inst if i.has_volume and i.kind in ("future", "stir", "equity", "commodity", "fx", "etf")]
        self.book = Keyed([("Sym", "S"), ("Level", "I"), ("BidPx", "D"), ("BidSz", "D"), ("BidOrders", "I"), ("AskPx", "D"), ("AskSz", "D"),
                           ("AskOrders", "I")], ["Sym", "Level"], "book")
        self.trades = Stream("trades", [("Timestamp", "T"), ("Sym", "S"), ("Price", "D"), ("PriceText", "S"), ("Size", "D"), ("Side", "S"), ("Venue", "S")], ring=4000)
        self.profile = Keyed([("Sym", "S"), ("Price", "D"), ("Volume", "D"), ("BuyVolume", "D"), ("SellVolume", "D")], ["Sym", "Price"], "volume_profile")
        self._profile: dict = {}
        self._rng = np.random.default_rng(fnv("book", m.start))
        self._sizes = {}
        self._side = {}
        for i in self.traded:
            base = self._lot(i) * 6
            self._sizes[i.sym] = [np.round(base * (0.6 + np.arange(15) * 0.18) * (0.6 + self._rng.random(15) * 0.8)) for _ in range(2)]
        self._seed_profile()

        # analytics
        self.ust = Keyed([("Cusip", "S"), ("Security", "S"), ("Type", "S"), ("Term", "S"), ("Status", "S"), ("Coupon", "D"), ("Maturity", "T"), ("Years", "D"),
                          ("BidPx", "S"), ("AskPx", "S"), ("Price", "D"), ("Yield", "D"), ("ChgBp", "D"), ("RichBp", "D"), ("ModDur", "D"), ("DV01", "D"),
                          ("Convexity", "D"), ("Accrued", "D"), ("RepoSpecial", "D"), ("OutstandingBn", "D")], ["Cusip"], "ust_cash")
        self.fut = Keyed([("Sym", "S"), ("Root", "S"), ("Contract", "S"), ("Last", "S"), ("Price", "D"), ("Chg", "S"), ("Volume", "D"), ("OpenInterest", "D"),
                          ("CTD", "S"), ("CF", "D"), ("DV01", "D"), ("ImpliedRepo", "D"), ("NetBasis32", "D"), ("FirstNotice", "T"), ("DaysToFND", "I"),
                          ("Roll", "S")], ["Sym"], "futures_contracts")
        self.basket = Keyed([("Contract", "S"), ("Cusip", "S"), ("Security", "S"), ("Coupon", "D"), ("Maturity", "T"), ("CF", "D"), ("Price", "S"), ("Yield", "D"),
                             ("GrossBasis32", "D"), ("Carry32", "D"), ("NetBasis32", "D"), ("ImpliedRepo", "D"), ("Rank", "I"), ("CTD", "B")], ["Contract", "Cusip"], "deliverable_basket")
        self.stir = Keyed([("Sym", "S"), ("Contract", "S"), ("Price", "D"), ("ImpliedRate", "D"), ("ChgBp", "D"), ("Volume", "D"), ("Start", "T"), ("End", "T")], ["Sym"], "stir_strip")
        self.fomc = Keyed([("Meeting", "T"), ("ImpliedRate", "D"), ("CumulativeBp", "D"), ("ThisMeetingBp", "D"), ("ProbCut", "D"), ("Priced", "S")], ["Meeting"], "fomc_path")
        self.swaps = Keyed([("Tenor", "S"), ("Years", "D"), ("SwapRate", "D"), ("Treasury", "D"), ("SwapSpreadBp", "D"), ("ChgBp", "D")], ["Tenor"], "swap_curve")
        self.curve = Keyed([("Tenor", "S"), ("Years", "D"), ("Sym", "S"), ("Cusip", "S"), ("Yield", "D"), ("Chg1D", "D"), ("Chg1W", "D"), ("Chg1M", "D"),
                            ("Chg3M", "D")], ["Tenor"], "ust_curve")
        self.repo = Keyed([("Kind", "S"), ("Term", "S"), ("Cusip", "S"), ("Security", "S"), ("Rate", "D"), ("SpreadToGCbp", "D")], ["Kind", "Term", "Cusip"], "repo_rates")
        self.rv = Keyed([("Sym", "S"), ("Name", "S"), ("Dataset", "S"), ("Last", "D"), ("Chg1D", "D"), ("Z20", "D"), ("Z60", "D"), ("Pct1Y", "D"), ("RVol20", "D"),
                         ("RSI14", "D"), ("Trend", "S"), ("Signal", "S")], ["Sym"], "rv_monitor")
        self._specials = {b.cusip: 0.12 + np.random.default_rng(fnv("sp", b.cusip)).random() * 0.4 for b in BONDS if b.age == 0 and b.type != "Bill"}
        self._oi = {c.sym: round(c.root.vol_per_min * (900 if c.front else 2100) * (0.8 + np.random.default_rng(fnv("oi", c.sym)).random() * 0.4)) for c in CONTRACTS}

        # calendar, news, auctions, positioning
        self.calendar = Keyed([("Time", "T"), ("Country", "S"), ("Event", "S"), ("Importance", "S"), ("Consensus", "S"), ("Prior", "S"), ("Actual", "S"),
                               ("CurveMoveBp", "D"), ("Status", "S")], ["Time", "Event"], "econ_calendar")
        now = m.t
        self.calendar.put([self._cal_row(e, now) for e in events_between(now - 7 * DAY_MS, now + 7 * DAY_MS) if e.kind != "headline"])
        self.news = Stream("news", [("Timestamp", "T"), ("Source", "S"), ("Headline", "S"), ("Kind", "S"), ("Importance", "I"), ("MoveBp", "D")], ring=500)
        self.news.put([self._news_row(e) for e in m.applied[-60:]])
        self._pending_events: list = []
        m.on_event.append(self._pending_events.append)
        self.auctions = Keyed([("Date", "T"), ("Security", "S"), ("Term", "S"), ("SizeBn", "D"), ("HighYield", "D"), ("WIYield", "D"), ("TailBp", "D"),
                               ("BidToCover", "D"), ("IndirectPct", "D"), ("DirectPct", "D"), ("DealerPct", "D"), ("Status", "S")], ["Date", "Term"], "treasury_auctions")
        self._auctions(now)
        self.cot = Keyed([("Week", "T"), ("Contract", "S"), ("AssetMgrNet", "D"), ("LevFundsNet", "D"), ("DealerNet", "D"), ("OpenInterest", "D"),
                          ("ChgLevFunds", "D")], ["Week", "Contract"], "cftc_cot")
        self._cot(now)
        self.refresh_market(now, 500)
        self.refresh_analytics(now)
        self.refresh_rv()

    # ---------------------------------------------------------------- helpers

    @staticmethod
    def _lot(i) -> int:
        return max(1, round((i.vol_per_min or 100) / 25))

    def _seed_profile(self):
        """Today's volume at price from the intraday history (each step's volume at its price, split by direction)."""
        h, m = self.m.history, self.m
        today = int(m.t // DAY_MS)
        sel = (h["t"] // 86400) == today
        rows = {}
        for i in self.traded:
            k = m.index[i.sym]
            tick = i.tick or 0.01
            v, q = h["v"][sel, k], h["q"][sel, k]
            prev = np.r_[v[:1], v[:-1]]
            for px, vol, up in zip(np.round(v / tick) * tick, q / self._lot(i) / 6, v >= prev):
                if not math.isfinite(px):
                    continue
                key = (i.sym, round(float(px), 9))
                r = rows.setdefault(key, [0.0, 0.0, 0.0])
                r[0] += round(vol)
                r[1] += round(vol * (0.6 if up else 0.4))
                r[2] = r[0] - r[1]
        self._profile = rows
        self.profile.put([{"Sym": s, "Price": p, "Volume": a[0], "BuyVolume": a[1], "SellVolume": a[2]} for (s, p), a in rows.items()])

    def _cal_row(self, e, now):
        done = e.t <= now
        return {"Time": e.t, "Country": "US", "Event": e.title, "Importance": "★" * e.importance + "☆" * (3 - e.importance), "Consensus": e.consensus,
                "Prior": e.prior, "Actual": e.actual if done else "", "CurveMoveBp": _r(e.jL * 100, 1) if done else None, "Status": "released" if done else "upcoming"}

    @staticmethod
    def _news_row(e):
        text = (f"US {e.title}: {e.actual} (consensus {e.consensus}, prior {e.prior})" if e.kind == "release" else
                f"Treasury {e.title} results: {e.actual}" if e.kind == "auction" else
                f"FOMC decision: {e.consensus}; statement and projections out" if e.kind == "fomc" else e.title)
        return {"Timestamp": e.t, "Source": "MockWire", "Headline": text, "Kind": e.kind, "Importance": e.importance, "MoveBp": _r(e.jL * 100, 2)}

    def _auctions(self, now):
        rows = []
        for w in range(-10, 3):
            for term, size, tenor in [("2Y", 69, 2), ("5Y", 70, 5), ("7Y", 44, 7), ("10Y", 39, 10), ("30Y", 22, 30)]:
                if term in ("10Y", "30Y") and abs(w) % 4 != 1:
                    continue
                if term in ("2Y", "5Y", "7Y") and abs(w) % 4 != 0:
                    continue
                t = now + w * 7 * DAY_MS + (0 if term == "2Y" else DAY_MS if term == "5Y" else 2 * DAY_MS)
                g = np.random.default_rng(fnv("auc", term, w))
                wi = curve_at(self.m.F, tenor) - 0.08 * (w / 10)
                tail = g.standard_normal() * 0.9
                up = t > now
                rows.append({"Date": t, "Security": f"{term} {'bond' if term == '30Y' else 'note'}", "Term": term, "SizeBn": size,
                             "HighYield": None if up else round(wi + tail / 100, 3), "WIYield": round(wi, 3), "TailBp": None if up else round(tail, 1),
                             "BidToCover": None if up else round(2.55 - tail * 0.12 + g.standard_normal() * 0.08, 2),
                             "IndirectPct": None if up else round(66 - tail * 4 + g.standard_normal() * 3, 1),
                             "DirectPct": None if up else round(18 + g.standard_normal() * 2.5, 1),
                             "DealerPct": None if up else round(15 + tail * 3 + g.standard_normal() * 1.5, 1), "Status": "Upcoming" if up else "Results"})
        self.auctions.put(sorted(rows, key=lambda r: r["Date"]))

    def _cot(self, now):
        rows = []
        dt = datetime.fromtimestamp(now / 1000, tz=timezone.utc)
        tuesday = (now // DAY_MS - (dt.weekday() - 1) % 7) * DAY_MS
        for r in [x.root for x in ROOTS] + ["SR3"]:
            lev = -(np.random.default_rng(fnv("lev", r)).random() * 800_000 + 200_000)
            for w in range(25, -1, -1):
                g = np.random.default_rng(fnv("cot", r, w))
                chg = g.standard_normal() * 40_000
                lev += chg
                am = -lev * 0.85 + g.standard_normal() * 30_000
                rows.append({"Week": tuesday - w * 7 * DAY_MS, "Contract": r, "AssetMgrNet": round(am), "LevFundsNet": round(lev), "DealerNet": round(-(am + lev) * 0.7),
                             "OpenInterest": round(abs(lev) * 3.4 + 1e6), "ChgLevFunds": round(chg)})
        self.cot.put(rows)

    # ---------------------------------------------------------------- each step

    def on_step(self, now: int, dt_ms: int):
        """After a market step: the step's ticks, quotes, books and trades; news and calendar for events that happened."""
        m = self.m
        rows = [{"Timestamp": now, "Sym": i.sym, "Price": float(m.vals[k]), "Size": float(m.vols[k])} for k, i in enumerate(m.inst) if math.isfinite(m.vals[k])]
        self.live_ticks.put(rows)
        self.refresh_market(now, dt_ms)
        if self._pending_events:
            evs, self._pending_events[:] = list(self._pending_events), []
            self.news.put([self._news_row(e) for e in evs])
            self.calendar.put([self._cal_row(e, now + 1000) for e in evs if e.kind != "headline"])

    def refresh_market(self, now: int, dt_ms: int):
        m = self.m
        rows = []
        for k, i in enumerate(m.inst):
            v = float(m.vals[k])
            if not math.isfinite(v):
                continue
            d = m.day_bar(i.sym)
            prev = d["prev_close"] if d else float("nan")
            chg = v - prev
            spread = 0.001 if i.kind in ("yield", "swap") else (1 / 128 if i.bond and i.bond.age == 0 else 1 / 64) if i.kind == "bond" else (i.tick or 0)
            yl = i.unit in ("%", "bp")
            rows.append({
                "Sym": i.sym, "Name": i.name, "Kind": i.kind, "Last": v, "LastText": price_text(i, v),
                "Bid": v + spread / 2 if yl else v - (spread / 2 if i.kind == "bond" else 0), "Ask": v - spread / 2 if yl else v + (spread / 2 if i.kind == "bond" else spread),
                "Chg": chg if math.isfinite(chg) else None, "ChgPct": 100 * chg / abs(prev) if math.isfinite(chg) and prev else None,
                "ChgBp": (chg * 100 if i.unit == "%" else chg if i.unit == "bp" else None) if math.isfinite(chg) else None,
                "Open": d["open"] if d else None, "High": d["high"] if d else None, "Low": d["low"] if d else None, "PrevClose": prev if math.isfinite(prev) else None,
                "Volume": d["volume"] if d else 0, "Updated": now,
            })
        for r, a, b in self.rolls:
            va, vb, da, db = m.value(a), m.value(b), m.day_bar(a), m.day_bar(b)
            v, prev = va - vb, da["prev_close"] - db["prev_close"]
            if not math.isfinite(v):
                continue
            chg = v - prev if math.isfinite(prev) else None
            rows.append({"Sym": r, "Name": f"{a} Dec/Mar roll", "Kind": "spread", "Last": v, "LastText": fmt32(v, BY_SYM[a].tick) if v >= 0 else f"-{fmt32(-v, BY_SYM[a].tick)}",
                         "Bid": v - BY_SYM[a].tick / 2, "Ask": v + BY_SYM[a].tick / 2, "Chg": chg, "ChgPct": None, "ChgBp": None, "Open": da["open"] - db["open"],
                         "High": None, "Low": None, "PrevClose": prev if math.isfinite(prev) else None, "Volume": 0, "Updated": now})
        self.quotes.put(rows)
        self.core.put([r for r in rows if r["Sym"] in WATCHLIST_CORE])
        book, trades, prof = [], [], []
        g = self._rng
        for i in self.traded:
            tick = i.tick or 0.01
            last = m.value(i.sym)
            if not math.isfinite(last):
                continue
            bid0 = last - tick if self._side.get(i.sym, 1) > 0 else last
            ask0 = bid0 + tick
            lot = self._lot(i)
            base = lot * 6
            target = base * (0.6 + np.arange(15) * 0.18)
            sizes = self._sizes[i.sym]
            for a in sizes:
                a[:] = np.maximum(1, np.round(a + (target - a) * 0.08 + (g.random(15) - 0.5) * target * 0.25))
            for lv in range(15):
                book.append({"Sym": i.sym, "Level": lv + 1, "BidPx": bid0 - lv * tick, "BidSz": float(sizes[0][lv]), "BidOrders": max(1, round(sizes[0][lv] / lot / 2)),
                             "AskPx": ask0 + lv * tick, "AskSz": float(sizes[1][lv]), "AskOrders": max(1, round(sizes[1][lv] / lot / 2))})
            lam = min(30.0, (i.vol_per_min or 100) / 60 * dt_ms / 1000 / lot)
            for _ in range(min(12, int(g.poisson(lam)))):
                buy = g.random() < 0.5
                self._side[i.sym] = 1 if buy else -1
                px = round((ask0 if buy else bid0) / tick) * tick
                size = max(1, round(g.exponential(lot)))
                trades.append({"Timestamp": now - int(g.random() * dt_ms), "Sym": i.sym, "Price": px, "PriceText": price_text(i, px), "Size": size,
                               "Side": "Buy" if buy else "Sell", "Venue": "ARCA" if i.kind == "etf" else "XEUR" if i.session == "EUREX" else "GLBX"})
                key = (i.sym, round(px, 9))
                r = self._profile.setdefault(key, [0.0, 0.0, 0.0])
                r[0] += size
                r[1 if buy else 2] += size
                prof.append({"Sym": i.sym, "Price": key[1], "Volume": r[0], "BuyVolume": r[1], "SellVolume": r[2]})
        self.book.put(book)
        if trades:
            self.trades.put(sorted(trades, key=lambda r: r["Timestamp"]))
            self.profile.put(prof)

    # ---------------------------------------------------------------- each second

    def refresh_analytics(self, now: int):
        m = self.m
        settle = now // DAY_MS * DAY_MS + DAY_MS
        rows = []
        for b in BONDS:
            y = m.yield_of(b.cusip)
            if not math.isfinite(y):
                continue
            rk = risk(b, y, settle)
            i = BY_SYM[b.cusip]
            d = m.day_bar(b.cusip)
            prev_y = yield_from_price(b, d["prev_close"], settle) if d and math.isfinite(d["prev_close"]) else float("nan")
            tenor = (b.maturity - now) / YEAR_MS
            fitted = curve_at(m.F, tenor) if b.type != "TIPS" else y
            half = 1 / 256 if b.age == 0 else 1 / 128
            rows.append({"Cusip": b.cusip, "Security": b.name, "Type": b.type, "Term": b.term,
                         "Status": "OTR" if b.age == 0 else "1st off" if b.age == 1 else "2nd off" if b.age == 2 else "old", "Coupon": b.coupon, "Maturity": b.maturity,
                         "Years": _r(tenor, 2), "BidPx": price_text(i, rk.price - half), "AskPx": price_text(i, rk.price + half), "Price": rk.price, "Yield": _r(y, 4),
                         "ChgBp": _r((y - prev_y) * 100, 2), "RichBp": _r((y - fitted) * 100, 2), "ModDur": _r(rk.mod_dur, 3), "DV01": _r(rk.dv01, 2),
                         "Convexity": _r(rk.convexity, 3), "Accrued": _r(rk.accrued, 5), "RepoSpecial": _r(self._specials[b.cusip] * 100, 1) if b.cusip in self._specials else None,
                         "OutstandingBn": b.outstanding})
        self.ust.put(rows)
        frows, brows = [], []
        for c in CONTRACTS:
            if not c.basket:
                continue
            F = m.value(c.sym)
            i = BY_SYM[c.sym]
            d = m.day_bar(c.sym)
            days = max(1.0, (c.delivery_month - now) / DAY_MS)
            items = []
            for x in m.basket(c, now):
                ai_del = accrued(x["bond"], c.delivery_month)
                coupons = sum(1 for t in schedule(x["bond"].maturity) if now < t <= c.delivery_month) * x["bond"].coupon / 2
                gross = x["price"] - F * x["cf"]
                net = gross - x["carry"]
                irr = (F * x["cf"] + ai_del + coupons - (x["price"] + x["ai"])) / (x["price"] + x["ai"]) * 360 / days
                items.append({**x, "gross": gross, "net": net, "irr": irr * 100})
            items.sort(key=lambda a: a["net"] / a["cf"])
            for rank, x in enumerate(items):
                brows.append({"Contract": c.sym, "Cusip": x["bond"].cusip, "Security": x["bond"].name, "Coupon": x["bond"].coupon, "Maturity": x["bond"].maturity,
                              "CF": x["cf"], "Price": fmt32(x["price"]), "Yield": _r(x["y"], 4), "GrossBasis32": _r(x["gross"] * 32, 2), "Carry32": _r(x["carry"] * 32, 2),
                              "NetBasis32": _r(x["net"] * 32, 2), "ImpliedRepo": _r(x["irr"], 3), "Rank": rank + 1, "CTD": rank == 0})
            ctd = items[0]
            cr = risk(ctd["bond"], ctd["y"], settle)
            fnd = math.ceil((c.first_notice - now) / DAY_MS)
            chg = F - d["prev_close"] if d and math.isfinite(d["prev_close"]) else None
            frows.append({"Sym": c.sym, "Root": c.root.root, "Contract": f"{c.root.name} {c.label}", "Last": price_text(i, F), "Price": F,
                          "Chg": f"{'+' if chg >= 0 else '-'}{fmt32(abs(chg), c.root.tick)}" if chg is not None else "", "Volume": d["volume"] if d else 0,
                          "OpenInterest": self._oi[c.sym], "CTD": ctd["bond"].name, "CF": ctd["cf"], "DV01": _r(cr.dv01 / ctd["cf"] * c.root.multiplier / 10_000, 2),
                          "ImpliedRepo": _r(ctd["irr"], 3), "NetBasis32": _r(ctd["net"] * 32, 2), "FirstNotice": c.first_notice, "DaysToFND": fnd,
                          "Roll": (f"rolling ({round(c.volume_share * 100)}% of volume left)" if fnd <= 10 else "front") if c.front else
                          ("next (taking volume)" if fnd < 120 else "deferred")})
        self.fut.put(frows)
        self.basket.put(brows)
        self.basket_rows = brows
        srows = []
        for s in STIR:
            v = m.value(s.sym)
            d = m.day_bar(s.sym)
            srows.append({"Sym": s.sym, "Contract": s.label, "Price": _r(v, 4), "ImpliedRate": _r(100 - v, 4),
                          "ChgBp": _r(-(v - d["prev_close"]) * 100, 2) if d else None, "Volume": d["volume"] if d else 0, "Start": s.start, "End": s.end})
        self.stir.put(srows)
        effr = m.value("EFFR")

        def fwd(ms):
            t = max(1 / 52, (ms - now) / YEAR_MS)
            return curve_at(m.F, t) + t * (curve_at(m.F, t + 0.05) - curve_at(m.F, max(1 / 365, t - 0.05))) / 0.1 - 0.07

        prev_rate = effr
        fr = []
        for mt in FOMC:
            if mt < now - DAY_MS:
                continue
            rate = fwd(mt + 14 * DAY_MS)
            step = (rate - prev_rate) * 100
            fr.append({"Meeting": mt, "ImpliedRate": _r(rate, 3), "CumulativeBp": _r((rate - effr) * 100, 1), "ThisMeetingBp": _r(step, 1),
                       "ProbCut": _r(max(0, min(100, -step / 25 * 100)), 0),
                       "Priced": "cut" if step < -20 else "partial cut" if step < -5 else "hike risk" if step > 5 else "hold"})
            prev_rate = rate
        self.fomc.put(fr)
        sw = []
        for sym, t in SWAP_TENORS:
            v = m.value(sym)
            tsy = curve_at(m.F, t)
            d = m.day_bar(sym)
            sw.append({"Tenor": f"{t}Y", "Years": t, "SwapRate": _r(v, 4), "Treasury": _r(tsy, 4), "SwapSpreadBp": _r((v - tsy) * 100, 2),
                       "ChgBp": _r((v - d["prev_close"]) * 100, 2) if d else None})
        self.swaps.put(sw)
        cv = []
        for sym, years, label in TENORS:
            y = m.value(sym)
            o = otr_for(sym)
            cv.append({"Tenor": label, "Years": years, "Sym": sym, "Cusip": o.cusip if o else "", "Yield": _r(y, 4),
                       **{f"Chg{k}": _r((y - m.value_at(sym, now - days * DAY_MS)) * 100, 2) for k, days in (("1D", 1), ("1W", 7), ("1M", 30), ("3M", 91))}})
        self.curve.put(cv)
        gc = m.value("GCREPO")
        rp = [{"Kind": "GC", "Term": term, "Cusip": "", "Security": "General collateral (Treasuries)", "Rate": _r(gc + adj, 3), "SpreadToGCbp": _r(adj * 100, 1)}
              for term, adj in [("O/N", 0), ("T/N", 0.01), ("1W", 0.015), ("2W", 0.02), ("1M", 0.0), ("3M", -0.06)]]
        rp += [{"Kind": "Special", "Term": "O/N", "Cusip": c, "Security": BY_SYM[c].name, "Rate": _r(gc - s, 3), "SpreadToGCbp": _r(-s * 100, 1)} for c, s in self._specials.items()]
        self.repo.put(rp)

    def refresh_rv(self):
        m = self.m
        rows = []
        for k, i in enumerate(m.inst):
            closes = m.daily_closes(k)
            if len(closes) < 30:
                continue
            last = closes[-1]

            def z(n):
                w = closes[-n:]
                sd = w.std()
                return (last - w.mean()) / sd if sd else 0.0

            pct = 100 * float((closes <= last).mean())
            add = i.unit in ("%", "bp")
            c21 = closes[-21:]
            rets = np.diff(c21) * (100 if i.unit == "%" else 1) if add else np.diff(np.log(np.where(c21 > 0, c21, np.nan)))
            sd = float(np.sqrt(np.nanmean(rets ** 2)))
            dlt = np.diff(closes)
            g = lo = 0.0
            for x in dlt:
                g = (g * 13 + max(0.0, x)) / 14
                lo = (lo * 13 + max(0.0, -x)) / 14
            rsi = 100.0 if lo == 0 else 100 - 100 / (1 + g / lo)
            e20 = e50 = closes[0]
            for c in closes:
                e20 += 2 / 21 * (c - e20)
                e50 += 2 / 51 * (c - e50)
            z60 = z(60)
            trend = ("▲ strong up" if last > e20 else "▲ up") if e20 > e50 else ("▼ strong down" if last < e20 else "▼ down")
            word = ("cheap (high yield)" if z60 > 1.5 else "rich (low yield)" if z60 < -1.5 else "") if i.kind in ("yield", "swap") else \
                ("stretched high" if z60 > 1.5 else "stretched low" if z60 < -1.5 else "")
            sig = (f"overbought{', ' + word if word else ''}" if rsi > 70 else f"oversold{', ' + word if word else ''}" if rsi < 30 else word)
            rows.append({"Sym": i.sym, "Name": i.name, "Dataset": i.dataset, "Last": float(last), "Chg1D": float(last - closes[-2]), "Z20": _r(z(20), 2), "Z60": _r(z60, 2),
                         "Pct1Y": _r(pct, 0), "RVol20": _r(sd * math.sqrt(252) * (1 if add else 100), 2), "RSI14": _r(rsi, 1), "Trend": trend, "Signal": sig})
        self.rv.put(rows)

    def tables(self) -> dict:
        """Everything to publish, by name."""
        return {
            "ticks": self.ticks, "long_ticks": self.long_ticks, "instruments": self.instruments.table, "sessions": self.sessions.table, "quotes": self.quotes.table,
            "watchlist_core": self.core.table, "book": self.book.table, "trades": self.trades.table, "volume_profile": self.profile.table,
            "ust_cash": self.ust.table, "futures_contracts": self.fut.table, "deliverable_basket": self.basket.table, "stir_strip": self.stir.table,
            "fomc_path": self.fomc.table, "swap_curve": self.swaps.table, "ust_curve": self.curve.table, "repo_rates": self.repo.table, "rv_monitor": self.rv.table,
            "econ_calendar": self.calendar.table, "news": self.news.table, "treasury_auctions": self.auctions.table, "cftc_cot": self.cot.table,
        }
